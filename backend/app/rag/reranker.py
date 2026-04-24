"""Reranker stub — returns top_k candidates by FAISS score order."""

from __future__ import annotations

from typing import Any, Dict, List


def rerank(
    query: str,
    candidates: List[Dict[str, Any]],
    top_k: int = 3,
    text_key: str = "text",
) -> List[Dict[str, Any]]:
    return candidates[:top_k]
