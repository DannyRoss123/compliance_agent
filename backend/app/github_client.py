from __future__ import annotations
import base64
import logging
from typing import List, Optional
import requests

from .config import GITHUB_ACCESS_TOKEN

logger = logging.getLogger(__name__)

API_BASE = "https://api.github.com"


def _auth_headers() -> dict:
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_ACCESS_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_ACCESS_TOKEN}"
    return headers


def fetch_pull_request_files(repo_full_name: str, pr_number: int) -> List[str]:
    if not repo_full_name:
        return []
    params = {"per_page": 100, "page": 1}
    filenames: List[str] = []
    while True:
        resp = requests.get(
            f"{API_BASE}/repos/{repo_full_name}/pulls/{pr_number}/files",
            headers=_auth_headers(),
            params=params,
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning("GitHub API files request failed (%s): %s", resp.status_code, resp.text)
            break
        data = resp.json()
        if not data:
            break
        filenames.extend(item.get("filename") for item in data if item.get("filename"))
        if len(data) < params["per_page"]:
            break
        params["page"] += 1
    return filenames


def post_pull_request_comment(repo_full_name: str, pr_number: int, body: str) -> bool:
    if not repo_full_name or not pr_number:
        return False
    resp = requests.post(
        f"{API_BASE}/repos/{repo_full_name}/issues/{pr_number}/comments",
        headers={**_auth_headers(), "Content-Type": "application/json"},
        json={"body": body},
        timeout=15,
    )
    if resp.status_code not in (200, 201):
        logger.warning("Failed to post PR comment (%s): %s", resp.status_code, resp.text)
        return False
    return True


def fetch_file_content(repo_full_name: str, path: str, ref: Optional[str]) -> Optional[str]:
    if not repo_full_name or not path:
        return None
    params = {"ref": ref} if ref else {}
    resp = requests.get(
        f"{API_BASE}/repos/{repo_full_name}/contents/{path}",
        headers=_auth_headers(),
        params=params,
        timeout=15,
    )
    if resp.status_code != 200:
        logger.warning(
            "GitHub API content request failed (%s) for %s@%s: %s",
            resp.status_code,
            path,
            ref,
            resp.text,
        )
        return None
    data = resp.json()
    encoding = data.get("encoding")
    content = data.get("content")
    if encoding == "base64" and content:
        try:
            return base64.b64decode(content).decode("utf-8", errors="ignore")
        except Exception as exc:
            logger.warning("Unable to decode content for %s: %s", path, exc)
            return None
    logger.warning("Unexpected content encoding for %s: %s", path, encoding)
    return None
