# The agent runner — executes compliance scans asynchronously after a GitHub webhook fires.
# Flow: webhook receives PR → enqueue_scan() schedules background task → _run_scan_sync()
# fetches files, runs RAG + LLM validation, saves results, posts GitHub PR comment.
from __future__ import annotations
import asyncio
import json
import logging
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .github_client import fetch_file_content, post_pull_request_comment
from . import storage
from .schemas import AgentRunIngestRequest, AgentViolation

# Import validate_code functions for in-process RAG-enabled validation
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from validate_code import normalize_tasks_config, run_checks, Finding

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
VALIDATOR_PATH = REPO_ROOT / "validate_code.py"
_running: set[str] = set()


def enqueue_scan(pr_id: str) -> None:
    try:
        loop = asyncio.get_running_loop()
        # Called from an async context — schedule directly.
        loop.create_task(_run_scan_task(pr_id))
    except RuntimeError:
        # Called from a sync route handler running in FastAPI's thread pool.
        # Use run_coroutine_threadsafe to safely schedule on the main event loop.
        try:
            loop = asyncio.get_event_loop()
            asyncio.run_coroutine_threadsafe(_run_scan_task(pr_id), loop)
        except Exception as exc:
            logger.warning("Cannot enqueue scan for %s: %s", pr_id, exc)


async def _run_scan_task(pr_id: str) -> None:
    if pr_id in _running:
        return
    _running.add(pr_id)
    try:
        await asyncio.to_thread(_run_scan_sync, pr_id)
    except Exception:  # pragma: no cover
        logger.exception("Agent runner failed for %s", pr_id)
    finally:
        _running.discard(pr_id)


def _run_scan_sync(pr_id: str) -> None:
    record = storage.load_pull_request_record(pr_id)
    if not record:
        logger.warning("No PR record found for %s", pr_id)
        return
    tasks_payload = storage.load_latest_tasks_payload()
    if not tasks_payload:
        logger.warning("No task set available; skipping scan for %s", pr_id)
        return
    tasks = tasks_payload["tasks"]
    if not tasks:
        logger.warning("Task list empty; skipping scan for %s", pr_id)
        return
    changed_files = record.changed_files or []
    if not changed_files:
        logger.info("No changed files for %s; marking passed.", pr_id)
        _save_runner_result(pr_id, [], True, len(tasks))
        return

    ref = record.head_sha or record.head_branch
    files_data = []
    for path in changed_files:
        content = fetch_file_content(record.repository, path, ref)
        if content is None:
            logger.warning("Unable to fetch %s for %s", path, pr_id)
            continue
        files_data.append((path, content))
    if not files_data:
        logger.warning("No file contents fetched for %s", pr_id)
        return

    # Get RAG retriever if documents have been ingested
    retriever = None
    try:
        from .main import get_retriever
        r = get_retriever()
        if r.vector_store.size > 0:
            retriever = r
    except Exception:
        pass

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        temp_files: List[Path] = []
        path_map: Dict[str, str] = {}
        for rel_path, content in files_data:
            safe_rel = rel_path.lstrip("/\\")
            dest = tmp_path / safe_rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
            temp_files.append(dest)
            path_map[str(dest)] = rel_path

        start = datetime.now(timezone.utc)
        try:
            tasks_cfg = normalize_tasks_config(tasks)
            findings, summary = run_checks(tasks_cfg, temp_files, tmp_path, retriever=retriever)
        except Exception as exc:
            logger.error("Validation failed for %s: %s", pr_id, exc)
            _post_error_comment(record.repository, record.number, str(exc))
            _save_error_result(pr_id, str(exc))
            return

    passed = len(findings) == 0
    violations = _build_violations_from_findings(findings, tasks, path_map)
    _save_runner_result(pr_id, violations, passed, len(tasks), start)
    _post_github_comment(record.repository, record.number, violations, passed, len(tasks))


def _build_violations_from_findings(findings: List[Finding], tasks: List[dict], path_map: Dict[str, str]) -> List[AgentViolation]:
    if not findings:
        return []
    title_map = {task.get("title") or task.get("name"): task for task in tasks}
    id_map = {task.get("id"): task for task in tasks}
    violations: List[AgentViolation] = []
    for finding in findings:
        task_info = title_map.get(finding.task) or id_map.get(finding.task)
        severity = (task_info or {}).get("severity", "warning")
        task_id = (task_info or {}).get("id", finding.task or "task")
        rel_path = path_map.get(finding.file, finding.file)
        violations.append(
            AgentViolation(
                taskId=task_id,
                message=finding.message,
                file=rel_path,
                line=finding.line,
                severity=severity,
                suggestedFix=finding.fix,
            )
        )
    return violations


def _build_violations(findings: List[dict], tasks: List[dict], path_map: Dict[str, str]) -> List[AgentViolation]:
    if not findings:
        return []
    title_map = {task.get("title") or task.get("name"): task for task in tasks}
    id_map = {task.get("id"): task for task in tasks}
    violations: List[AgentViolation] = []
    for finding in findings:
        task_name = finding.get("task")
        task_info = title_map.get(task_name) or id_map.get(task_name)
        severity = (task_info or {}).get("severity", "warning")
        task_id = (task_info or {}).get("id", task_name or "task")
        temp_path = finding.get("file")
        rel_path = path_map.get(temp_path, temp_path)
        violations.append(
            AgentViolation(
                taskId=task_id,
                message=finding.get("message", "Task violation detected."),
                file=rel_path,
                line=int(finding.get("line", 1)),
                severity=severity,
                suggestedFix=finding.get("fix"),
            )
        )
    return violations


def _save_runner_result(pr_id: str, violations: List[AgentViolation], passed: bool, task_count: int, start_time: Optional[datetime] = None) -> None:
    status = "passed" if passed else ("critical" if any(v.severity == "critical" for v in violations) else "warnings")
    start = start_time or datetime.now(timezone.utc)
    end = datetime.now(timezone.utc)
    notes = "All tasks passed." if passed else f"Detected {len(violations)} violation(s)."
    request = AgentRunIngestRequest(
        pullRequestId=pr_id,
        status=status,
        started_at=start,
        completed_at=end,
        task_count=task_count,
        source="backend_runner",
        notes=notes,
        violations=violations,
    )
    storage.save_scan_result(request.to_record())
    logger.info("Stored scan result for %s (%s)", pr_id, status)


def _post_error_comment(repo: str, pr_number: int, error_detail: str) -> None:
    if not repo or not pr_number:
        return
    body = (
        "## ⚠️ Guardians — Scan failed\n\n"
        "The compliance scan could not complete. Check that `OPENAI_API_KEY` and `OPENAI_MODEL` "
        "are set correctly in the backend environment.\n\n"
        f"<details><summary>Error detail</summary>\n\n```\n{error_detail}\n```\n</details>"
    )
    post_pull_request_comment(repo, pr_number, body)


def _save_error_result(pr_id: str, error_detail: str) -> None:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    request = AgentRunIngestRequest(
        pullRequestId=pr_id,
        status="error",
        started_at=now,
        completed_at=now,
        task_count=0,
        source="backend_runner",
        notes=f"Scan failed: {error_detail[:200]}",
        violations=[],
    )
    storage.save_scan_result(request.to_record())


def _post_github_comment(repo: str, pr_number: int, violations: List[AgentViolation], passed: bool, task_count: int) -> None:
    if not repo or not pr_number:
        return

    if passed:
        body = (
            "## ✅ Guardians — All checks passed\n\n"
            f"**{task_count}/{task_count}** compliance tasks satisfied. No violations detected."
        )
    else:
        failed_tasks = len(set(v.task_id for v in violations))
        passed_count = task_count - failed_tasks
        severity_icon = {"critical": "🔴", "warning": "🟡", "info": "🔵"}

        rows = "\n".join(
            f"| {severity_icon.get(v.severity, '⚪')} {v.severity} "
            f"| `{v.file}` | {v.line} | {v.message} |"
            for v in violations
        )

        fixes = "\n\n".join(
            f"**`{v.file}:{v.line}`** — {v.suggested_fix}"
            for v in violations
            if v.suggested_fix
        )

        body = (
            f"## ❌ Guardians — {len(violations)} violation(s) detected\n\n"
            f"**{passed_count}/{task_count}** compliance tasks passed.\n\n"
            "| Severity | File | Line | Issue |\n"
            "|----------|------|------|-------|\n"
            f"{rows}\n"
        )
        if fixes:
            body += f"\n<details>\n<summary>Suggested fixes</summary>\n\n{fixes}\n</details>\n"

    ok = post_pull_request_comment(repo, pr_number, body)
    if ok:
        logger.info("Posted compliance comment on %s#%s", repo, pr_number)
    else:
        logger.warning("Could not post comment on %s#%s", repo, pr_number)
