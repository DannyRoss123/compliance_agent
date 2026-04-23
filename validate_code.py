#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import glob
import hashlib
import json
import os
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import yaml  # Optional, for YAML task files
except Exception:
    yaml = None


# ===================== Data types =====================

@dataclass
class Finding:
    task: str
    file: str
    line: int
    column: int
    message: str
    fix: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "task": self.task,
            "file": self.file,
            "line": self.line,
            "column": self.column,
            "message": self.message,
            "fix": self.fix,
        }


# ===================== Utilities ======================

TEXT_EXT_BLOCKLIST = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".gz", ".tar", ".rar", ".bmp", ".ico"}

DEFAULT_OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_OPENAI_MODEL: str = "gpt-4o-mini"
DEFAULT_MAX_CODE_CHARS = 8000
OPENAI_API_URL = DEFAULT_OPENAI_API_URL
OPENAI_MODEL = DEFAULT_OPENAI_MODEL
MAX_CODE_CHARS = DEFAULT_MAX_CODE_CHARS


def read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ""


def list_changed_files_git(base: str, head: str) -> List[Path]:
    try:
        res = subprocess.run(
            ["git", "diff", "--name-only", f"{base}..{head}"],
            capture_output=True,
            text=True,
            check=True,
        )
        files = [Path(line.strip()) for line in res.stdout.splitlines() if line.strip()]
        return [p for p in files if p.exists()]
    except Exception:
        return []


def load_tasks(tasks_path: Path) -> Any:
    text = read_text(tasks_path)
    if tasks_path.suffix.lower() in {".yml", ".yaml"}:
        if yaml is None:
            raise RuntimeError("Install 'pyyaml' or use a JSON tasks file.")
        return yaml.safe_load(text)
    return json.loads(text)


def normalize_tasks_config(tasks_cfg: Any) -> Dict[str, Any]:
    if isinstance(tasks_cfg, dict):
        cfg = dict(tasks_cfg)
        cfg.setdefault("rules", [])
        return cfg
    if isinstance(tasks_cfg, list):
        rules = []
        for task in tasks_cfg:
            rule = _convert_guardian_task(task)
            if rule:
                rules.append(rule)
        return {"rules": rules}
    raise RuntimeError("Unsupported tasks configuration format.")


def _convert_guardian_task(task: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(task, dict):
        return None
    tid = task.get("id")
    return {
        "id": tid,
        "name": task.get("title", tid or "task"),
        "description": task.get("description"),
        "category": task.get("category"),
        "severity": task.get("severity"),
        "checkType": task.get("checkType"),
        "file_globs": task.get("fileTypes") or ["*"],
        "exampleViolation": task.get("exampleViolation"),
        "suggestedFix": task.get("suggestedFix"),
        "docReference": task.get("docReference"),
        "ai_spec": dict(task),
    }


def _rule_applies_to_file(rule: Dict[str, Any], rel_path: str) -> bool:
    globs = rule.get("file_globs")
    if not globs:
        return True
    return any(fnmatch.fnmatch(rel_path, pat) for pat in globs)


def _rule_name(rule: Dict[str, Any]) -> str:
    for key in ("name", "title", "id", "type", "task"):
        val = rule.get(key)
        if val:
            return str(val)
    return "task"


def load_env_file(dotenv_path: Path) -> None:
    if not dotenv_path.is_file():
        return
    try:
        text = dotenv_path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value and ((value[0] == value[-1]) and value.startswith(("'", '"'))):
            value = value[1:-1]
        os.environ.setdefault(key, value)


def refresh_openai_settings() -> None:
    global OPENAI_API_URL, OPENAI_MODEL, MAX_CODE_CHARS
    OPENAI_API_URL = os.environ.get("OPENAI_API_URL", DEFAULT_OPENAI_API_URL)
    OPENAI_MODEL = os.environ.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
    max_chars_val = os.environ.get("VALIDATOR_MAX_CODE_CHARS")
    if max_chars_val is None:
        MAX_CODE_CHARS = DEFAULT_MAX_CODE_CHARS
    else:
        try:
            MAX_CODE_CHARS = int(max_chars_val)
        except ValueError:
            MAX_CODE_CHARS = DEFAULT_MAX_CODE_CHARS


refresh_openai_settings()


# ===================== AI-assisted evaluation ==========
AI_SYSTEM_PROMPT = (
    "You are CodeGuardian, an exacting code-compliance reviewer. "
    "For each task in the provided JSON array you must decide whether the file satisfies the requirement. "
    "When REFERENCE DOCUMENTATION is provided, ground your decisions in those specific policy excerpts. "
    "Cite the relevant doc section in your explanation when a reference supports your finding. "
    "Always respond with a JSON object that contains a 'tasks' array. "
    "Each entry must include: 'internalRef' (the integer provided), 'compliant' (boolean), 'explanation' (string), "
    "'citations' (array of doc_id strings used), "
    "and 'violations' (array of {message,line,column,fix}). Line/column numbers are 1-indexed; use null when unknown."
)


def _truncate_code(code: str, limit: Optional[int] = None) -> Tuple[str, bool]:
    if limit is None:
        limit = MAX_CODE_CHARS
    if len(code) <= limit:
        return code, False
    head = limit // 2
    tail = limit - head
    snippet = code[:head] + "\n...\n" + code[-tail:]
    return snippet, True


def _build_ai_messages(
    task_payloads: List[Dict[str, Any]],
    file_rel: str,
    code: str,
    rag_context: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, str]]:
    tasks_json = json.dumps(task_payloads, indent=2, ensure_ascii=False)
    code_snippet, truncated = _truncate_code(code)
    language = Path(file_rel).suffix.lstrip(".") or "text"
    user_parts = [
        "Tasks JSON:",
        tasks_json,
        "",
    ]

    # Inject RAG-retrieved document context when available
    if rag_context:
        user_parts.append("REFERENCE DOCUMENTATION (retrieved from company policy docs):")
        for i, ctx in enumerate(rag_context):
            doc_id = ctx.get("doc_id", f"doc_{i}")
            score = ctx.get("score", 0)
            text = ctx.get("text", "")
            user_parts.append(f"--- [{doc_id}] (relevance score: {score:.2f}) ---")
            user_parts.append(text)
            user_parts.append("")
        user_parts.append("Use the above references to ground your compliance decisions.")
        user_parts.append("")

    user_parts.extend([
        f"File: {file_rel}",
        f"Source code ({language}):",
        "```",
        code_snippet,
        "```",
        "Return JSON with a 'tasks' array matching the provided internalRef entries.",
    ])
    if truncated:
        user_parts.append("NOTE: Source truncated for length; focus on the visible code.")
    user_content = "\n".join(user_parts)
    return [
        {"role": "system", "content": AI_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def _call_openai_chat(messages: List[Dict[str, str]], model: Optional[str] = None) -> str:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Set OPENAI_API_KEY in your environment to run AI-based validation.")
    model_name = model or OPENAI_MODEL or DEFAULT_OPENAI_MODEL
    payload = {
        "model": model_name,
        "messages": messages,
        "response_format": {"type": "json_object"},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        OPENAI_API_URL,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")
        raise RuntimeError(f"OpenAI API error {exc.code}: {detail.strip()}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Failed to call OpenAI API: {exc}") from exc
    parsed = json.loads(body)
    try:
        return parsed["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"Unexpected OpenAI response: {body}") from exc


def _as_int(value: Any, default: int) -> int:
    try:
        if value is None:
            raise ValueError
        return int(value)
    except Exception:
        return default


TASK_KEYS_TO_INCLUDE = [
    "id",
    "name",
    "title",
    "description",
    "severity",
    "category",
    "checkType",
    "exampleViolation",
    "suggestedFix",
    "docReference",
]


def _task_summary(rule: Dict[str, Any], idx: int) -> Dict[str, Any]:
    base = dict(rule.get("ai_spec") or rule)
    summary: Dict[str, Any] = {k: base.get(k) for k in TASK_KEYS_TO_INCLUDE if base.get(k)}
    summary["internalRef"] = idx
    summary["name"] = summary.get("name") or summary.get("title") or _rule_name(rule)
    summary.setdefault("description", rule.get("description") or rule.get("message"))
    summary.setdefault("suggestedFix", rule.get("suggestedFix") or rule.get("fix"))
    return summary


def evaluate_tasks_with_ai(
    task_payloads: List[Dict[str, Any]],
    file_rel: str,
    code: str,
    rag_context: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    if not task_payloads:
        return []
    messages = _build_ai_messages(task_payloads, file_rel, code, rag_context=rag_context)
    response_text = _call_openai_chat(messages)
    try:
        ai_result = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"AI response was not valid JSON: {exc}") from exc
    tasks = ai_result.get("tasks")
    if not isinstance(tasks, list):
        raise RuntimeError("AI response missing 'tasks' array.")
    return tasks


# ===================== Runner =========================

def run_checks(tasks_cfg: Dict[str, Any], files: List[Path], repo_root: Path, retriever: Any = None) -> Tuple[List[Finding], Dict[str, Any]]:
    findings: List[Finding] = []
    rules = tasks_cfg.get("rules", [])
    failed_rule_indexes: Set[int] = set()

    global_includes = tasks_cfg.get("include")
    global_excludes = tasks_cfg.get("exclude")

    def included(p: Path) -> bool:
        try:
            rel = p.resolve().relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            rel = p.resolve().as_posix()
        if global_includes and not any(fnmatch.fnmatch(rel, pat) for pat in global_includes):
            return False
        if global_excludes and any(fnmatch.fnmatch(rel, pat) for pat in global_excludes):
            return False
        return True

    for p in files:
        if not p.is_file():
            continue
        if p.suffix.lower() in TEXT_EXT_BLOCKLIST:
            continue
        if not included(p):
            continue
        code = read_text(p)
        try:
            file_rel = p.resolve().relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            file_rel = p.resolve().as_posix()
        applicable: List[Tuple[int, Dict[str, Any], Dict[str, Any]]] = []
        for idx, rule in enumerate(rules):
            if not _rule_applies_to_file(rule, file_rel):
                continue
            applicable.append((idx, rule, _task_summary(rule, idx)))
        if not applicable:
            continue
        # RAG context assembly: combine FAISS retrieval + linked source chunks,
        # then rerank all candidates with a cross-encoder to get the top 3.
        rag_context = None
        all_candidates = []
        seen_texts = set()

        # Source 1: Linked source chunks from tasks (direct lookup)
        for _, rule, _ in applicable:
            chunk_info = rule.get("source_chunk") or (rule.get("ai_spec") or {}).get("source_chunk")
            if chunk_info and isinstance(chunk_info, dict):
                text = chunk_info.get("text", "")
                text_hash = hashlib.sha256(text.encode()).hexdigest()
                if text_hash not in seen_texts and text.strip():
                    seen_texts.add(text_hash)
                    all_candidates.append({
                        "doc_id": chunk_info.get("doc_id", "unknown"),
                        "text": text,
                        "source": "linked_chunk",
                    })

        # Source 2: FAISS similarity search (retrieves additional relevant chunks)
        if retriever is not None:
            try:
                task_descriptions = " ".join(
                    summary.get("description", "") for _, _, summary in applicable
                )
                faiss_results = retriever.query_for_code(code, task_descriptions, top_k=10)
                for result in faiss_results:
                    text = result.get("text", "")
                    text_hash = hashlib.sha256(text.encode()).hexdigest()
                    if text_hash not in seen_texts and text.strip():
                        seen_texts.add(text_hash)
                        all_candidates.append({
                            "doc_id": result.get("doc_id", "unknown"),
                            "text": text,
                            "source": "faiss_retrieval",
                            "faiss_score": result.get("score", 0.0),
                        })
            except Exception:
                pass

        # Rerank all candidates with cross-encoder, take top 3
        if all_candidates:
            rerank_query = " ".join(
                summary.get("description", "") for _, _, summary in applicable
            )
            try:
                # Import reranker — try both path configurations
                try:
                    from backend.app.rag.reranker import rerank as _rerank
                except ImportError:
                    from app.rag.reranker import rerank as _rerank
                rag_context = _rerank(rerank_query, all_candidates, top_k=3)
            except (ImportError, Exception):
                # Reranker not available — use candidates as-is
                rag_context = all_candidates[:3]

        try:
            ai_task_results = evaluate_tasks_with_ai(
                [summary for (_, _, summary) in applicable], file_rel, code, rag_context=rag_context
            )
        except Exception as exc:
            message = f"AI evaluation failed: {exc}"
            for idx, rule, _ in applicable:
                failed_rule_indexes.add(idx)
                findings.append(Finding(_rule_name(rule), file_rel, 1, 0, message, rule.get("suggestedFix") or rule.get("fix")))
            continue
        results_by_ref = {res.get("internalRef"): res for res in ai_task_results if isinstance(res, dict) and "internalRef" in res}
        for idx, rule, summary in applicable:
            result = results_by_ref.get(idx)
            task_name = _rule_name(rule)
            if not result:
                failed_rule_indexes.add(idx)
                findings.append(Finding(task_name, file_rel, 1, 0, "AI response missing for this task.", summary.get("suggestedFix")))
                continue
            compliant = bool(result.get("compliant"))
            if compliant:
                continue
            failed_rule_indexes.add(idx)
            violations = result.get("violations") or []
            if isinstance(violations, list) and violations:
                for violation in violations:
                    if not isinstance(violation, dict):
                        continue
                    line = _as_int(violation.get("line"), 1)
                    column = _as_int(violation.get("column"), 0)
                    message = violation.get("message") or result.get("explanation") or summary.get("description") or "Task violation detected."
                    fix = violation.get("fix") or summary.get("suggestedFix") or rule.get("fix")
                    findings.append(Finding(task_name, file_rel, line, column, message, fix))
                continue
            explanation = result.get("explanation") or summary.get("description") or "Task violation detected."
            fix = summary.get("suggestedFix") or rule.get("fix")
            findings.append(Finding(task_name, file_rel, 1, 0, explanation, fix))

    total_rules = len(rules)
    passed_rule_names = [_rule_name(rule) for idx, rule in enumerate(rules) if idx not in failed_rule_indexes]
    summary = {
        "total_rules": total_rules,
        "passed_rules": len(passed_rule_names),
        "passed_rule_names": passed_rule_names,
    }
    return findings, summary


def format_human(findings: List[Finding], summary: Dict[str, Any]) -> str:
    all_good = len(findings) == 0
    total_rules = summary.get("total_rules", 0)
    passed_rules = summary.get("passed_rules", 0)
    icon = "✅" if all_good else "❌"
    lines = [f"{icon} ALL_TASKS_MET: {all_good}"]
    lines.append(f"Tasks Passed: {passed_rules}/{total_rules}")
    passed_names = summary.get("passed_rule_names") or []
    if passed_names:
        lines.append("Passed Task Names: " + ", ".join(passed_names))
    lines.append("")
    if not findings:
        return "\n".join(lines)
    for finding in findings:
        lines.append(f"- [{finding.task}] {finding.file}:{finding.line}:{finding.column} — {finding.message}")
        if finding.fix:
            lines.append(textwrap.indent("Fix:\n" + finding.fix.strip(), "    "))
    return "\n".join(lines)


def main():
    start_time = time.perf_counter()
    ap = argparse.ArgumentParser(description="AI-driven code validator")
    ap.add_argument("--tasks", required=True, help="Path to tasks file (.yml/.yaml or .json)")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--files", nargs="+", help="Explicit file globs/paths to validate")
    group.add_argument("--git-diff", nargs=2, metavar=("BASE", "HEAD"), help="Validate files changed between two git refs")
    ap.add_argument("--json", action="store_true", help="Emit JSON findings instead of human text")
    ap.add_argument("--out-file", help="Write the report to this file instead of stdout")
    args = ap.parse_args()

    repo_root = Path.cwd()
    load_env_file(repo_root / ".env")
    refresh_openai_settings()

    tasks_start = time.perf_counter()
    tasks_cfg = normalize_tasks_config(load_tasks(Path(args.tasks)))
    tasks_load_time = time.perf_counter() - tasks_start

    files_start = time.perf_counter()
    if args.files:
        files_strs: List[str] = []
        for token in args.files:
            matches = glob.glob(token, recursive=True)
            if matches:
                files_strs.extend(matches)
            else:
                files_strs.append(token)
        seen: List[str] = []
        files = []
        for candidate in files_strs:
            path = Path(candidate)
            if path.is_file() and str(path) not in seen:
                seen.append(str(path))
                files.append(path)
    else:
        base, head = args.git_diff
        files = list_changed_files_git(base, head)
    files_collect_time = time.perf_counter() - files_start

    checks_start = time.perf_counter()
    findings, summary = run_checks(tasks_cfg, files, repo_root)
    checks_time = time.perf_counter() - checks_start
    all_good = len(findings) == 0

    out_file_path = Path(args.out_file) if args.out_file else None
    auto_json = bool(out_file_path and out_file_path.suffix.lower() == ".json" and not args.json)
    want_json = args.json or auto_json

    render_start = time.perf_counter()
    if want_json:
        payload = {
            "ALL_TASKS_MET": all_good,
            "tasks": {
                "passed": summary.get("passed_rules", 0),
                "total": summary.get("total_rules", 0),
                "passed_names": summary.get("passed_rule_names", []),
            },
            "findings": [finding.as_dict() for finding in findings],
        }
        report = json.dumps(payload, indent=2)
    else:
        report = format_human(findings, summary)

    if out_file_path:
        text = report if report.endswith("\n") else report + "\n"
        out_file_path.write_text(text, encoding="utf-8")
        if not want_json:
            print(f"Report written to {out_file_path} (text)")
    else:
        print(report)
    render_time = time.perf_counter() - render_start

    total_time = time.perf_counter() - start_time
    timing_message = (
        "Timings — tasks: {:.2f}s, files: {:.2f}s, checks: {:.2f}s, output: {:.2f}s, total: {:.2f}s".format(
            tasks_load_time, files_collect_time, checks_time, render_time, total_time
        )
    )
    if want_json:
        print(timing_message, file=sys.stderr)
    else:
        print(timing_message)

    # If you want the script to exit non-zero when findings exist, uncomment:
    # if findings:
    #     sys.exit(1)


if __name__ == "__main__":
    main()
