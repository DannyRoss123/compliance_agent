"""Per-chunk task extraction with embedding-based deduplication.

Instead of sending entire documents to the LLM at once, we extract
compliance tasks from each chunk individually. This:
1. Scales to any document size
2. Links each task to its source chunk for traceability
3. Avoids the "lost in the middle" problem with long contexts
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

TASK_EXTRACTION_PROMPT = """You are an expert code compliance analyst. Given a section of a company policy document, extract any concrete, verifiable coding rules or requirements.

For each rule you find, output a JSON object with:
- "title": short name for the rule
- "description": detailed description of what must be checked
- "category": one of "Security", "Error Handling", "Code Quality", "Testing", "Documentation", "Naming Convention"
- "severity": one of "critical", "warning", "info"
- "checkType": what kind of check this is (e.g., "Pattern Detection", "Naming Convention", "Coverage Check")
- "fileTypes": list of file glob patterns this applies to (e.g., ["*.py", "*.js"])
- "exampleViolation": a short code example that violates this rule
- "suggestedFix": the corrected version of the example

IMPORTANT:
- Only extract CONCRETE, CHECKABLE rules. Skip vague guidance like "follow best practices."
- If the text contains no checkable rules, return an empty array.
- Return ONLY a JSON array. No markdown, no explanation.

Document section:
"""


def _cosine_similarity_matrix(embeddings: np.ndarray) -> np.ndarray:
    """Compute pairwise cosine similarity matrix.

    Given embeddings of shape (n, d), returns an (n, n) matrix where
    entry [i, j] = cosine_similarity(embeddings[i], embeddings[j]).

    Cosine similarity measures the angle between vectors, ignoring magnitude.
    For normalized vectors: cos_sim = dot(a, b). For unnormalized:
    cos_sim = dot(a, b) / (||a|| * ||b||).

    """
    # Normalize to unit vectors
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms = np.maximum(norms, 1e-12)
    normalized = embeddings / norms
    # Cosine similarity = dot product of normalized vectors
    return normalized @ normalized.T


def deduplicate_tasks_embedding(
    tasks: List[Dict[str, Any]],
    embedding_client: Any,
    similarity_threshold: float = 0.85,
) -> List[Dict[str, Any]]:
    """Remove semantically duplicate tasks using embedding cosine similarity.
    Returns:
        Deduplicated task list (first occurrence of each cluster kept).
    """
    if len(tasks) <= 1:
        return tasks

    # Build text representations for embedding
    texts = []
    for task in tasks:
        title = task.get("title", "")
        desc = task.get("description", "")
        texts.append(f"{title}. {desc}".strip())

    # Embed all task descriptions in one batch
    embeddings = embedding_client.embed_texts(texts)
    sim_matrix = _cosine_similarity_matrix(embeddings)

    # Greedy selection: keep a task if it's not too similar to any kept task
    unique_indices = []
    for i in range(len(tasks)):
        is_dup = False
        for j in unique_indices:
            if sim_matrix[i, j] > similarity_threshold:
                is_dup = True
                logger.debug(
                    "Task '%s' is duplicate of '%s' (cosine=%.3f)",
                    tasks[i].get("title", ""), tasks[j].get("title", ""),
                    sim_matrix[i, j],
                )
                break
        if not is_dup:
            unique_indices.append(i)

    unique = [tasks[i] for i in unique_indices]
    logger.info(
        "Embedding-based dedup: %d tasks -> %d unique (threshold=%.2f)",
        len(tasks), len(unique), similarity_threshold,
    )
    return unique


def _deduplicate_tasks_lexical(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Fallback: remove near-duplicate tasks based on word-overlap similarity."""

    if len(tasks) <= 1:
        return tasks

    unique = []
    seen_titles = []

    for task in tasks:
        title = task.get("title", "").lower().strip()
        title_words = set(title.split())

        is_dup = False
        for seen in seen_titles:
            seen_words = set(seen.split())
            if not title_words or not seen_words:
                continue
            overlap = len(title_words & seen_words)
            similarity = overlap / max(len(title_words), len(seen_words))
            if similarity > 0.8:
                is_dup = True
                break

        if not is_dup:
            unique.append(task)
            seen_titles.append(title)

    logger.info("Lexical dedup: %d tasks -> %d unique", len(tasks), len(unique))
    return unique


def deduplicate_tasks(
    tasks: List[Dict[str, Any]],
    embedding_client: Any = None,
    similarity_threshold: float = 0.85,
) -> List[Dict[str, Any]]:
    """Remove duplicate tasks, preferring embedding-based dedup when available. """
    if len(tasks) <= 1:
        return tasks

    # Try embedding-based dedup first
    if embedding_client is None:
        try:
            from .embeddings import EmbeddingClient
            client = EmbeddingClient()
            if client.api_key:
                embedding_client = client
        except Exception:
            pass

    if embedding_client is not None:
        try:
            return deduplicate_tasks_embedding(tasks, embedding_client, similarity_threshold)
        except Exception as exc:
            logger.warning("Embedding dedup failed, falling back to lexical: %s", exc)

    return _deduplicate_tasks_lexical(tasks)


def extract_tasks_from_chunk(
    chunk_text: str,
    doc_id: str,
    chunk_index: int,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Extract compliance tasks from a single document chunk."""
    key = api_key or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY required for task extraction")

    model_name = model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    api_url = os.environ.get("OPENAI_API_URL", "https://api.openai.com/v1/chat/completions")

    messages = [
        {"role": "system", "content": "You extract coding compliance rules from policy documents. Always respond with a JSON array only."},
        {"role": "user", "content": TASK_EXTRACTION_PROMPT + chunk_text},
    ]

    payload = {
        "model": model_name,
        "messages": messages,
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        api_url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")
        logger.error("Task extraction API error %d: %s", exc.code, detail)
        return []
    except urllib.error.URLError as exc:
        logger.error("Task extraction failed: %s", exc)
        return []

    try:
        parsed = json.loads(body)
        content = parsed["choices"][0]["message"]["content"]
        result = json.loads(content)
    except (json.JSONDecodeError, KeyError, IndexError) as exc:
        logger.error("Failed to parse task extraction response: %s", exc)
        return []

    # Handle both {"tasks": [...]} and [...] formats
    if isinstance(result, dict):
        tasks = result.get("tasks", result.get("rules", []))
    elif isinstance(result, list):
        tasks = result
    else:
        return []

    # Attach source chunk reference to each task
    enriched = []
    for i, task in enumerate(tasks):
        if not isinstance(task, dict):
            continue
        task["id"] = f"{doc_id}_chunk{chunk_index}_task{i}"
        task["source_chunk"] = {
            "doc_id": doc_id,
            "chunk_index": chunk_index,
            "text": chunk_text,
        }
        task["docReference"] = f"{doc_id}, chunk {chunk_index}"
        enriched.append(task)

    return enriched


def extract_tasks_from_chunks(
    chunks: List[Dict[str, Any]],
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Extract tasks from multiple chunks."""
    all_tasks = []
    for chunk in chunks:
        tasks = extract_tasks_from_chunk(
            chunk_text=chunk["text"],
            doc_id=chunk["doc_id"],
            chunk_index=chunk["chunk_index"],
            api_key=api_key,
            model=model,
        )
        all_tasks.extend(tasks)
        logger.info(
            "Extracted %d tasks from %s chunk %d",
            len(tasks), chunk["doc_id"], chunk["chunk_index"],
        )

    return deduplicate_tasks(all_tasks)
