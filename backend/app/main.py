# FastAPI application entry point. Defines all API routes, CORS config, and the GitHub
# webhook handler. The RAG retriever singleton is initialized lazily on first document upload
# and persisted to disk so it survives deploys.
from __future__ import annotations
import logging
import os
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse
import html
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .config import API_TOKEN, DATA_DIR, ensure_directories
from .database import init_db
from .security import verify_github_signature
from .github_client import fetch_pull_request_files
from .schemas import (
    TaskIngestRequest,
    TaskIngestResponse,
    PullRequestListResponse,
    PullRequestSummary,
    PullRequestDetail,
    PullRequestIngestRequest,
    AgentRunIngestRequest,
    AgentRunRecord,
)
from . import storage
from .runner import enqueue_scan

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    force=True,
)
logger = logging.getLogger(__name__)

ensure_directories()
init_db()

# RAG retriever singleton — initialized lazily on first document ingest
_retriever = None


def get_retriever():
    global _retriever
    if _retriever is None:
        from .rag import Retriever
        _retriever = Retriever()
        # Try to load persisted index
        index_dir = DATA_DIR / "vector_index"
        if (index_dir / "index.faiss").exists():
            try:
                _retriever.load(index_dir)
                logger.info("Loaded persisted vector index")
            except Exception:
                logger.warning("Failed to load persisted vector index, starting fresh")
    return _retriever

app = FastAPI(title="Guardians API", version="0.1.0")

_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:4173",
]
_extra = os.getenv("ALLOWED_ORIGINS", "")
_allow_all = _extra.strip() == "*"
if _extra and not _allow_all:
    _ALLOWED_ORIGINS.extend(o.strip() for o in _extra.split(",") if o.strip())

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _allow_all else _ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=False if _allow_all else True,
)


def require_token(authorization: Optional[str] = Header(default=None)) -> None:
    if not API_TOKEN:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1]
    if token != API_TOKEN:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


@app.get("/healthz")
def healthcheck():
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": app.version,
    }


@app.post("/tasks", response_model=TaskIngestResponse, status_code=status.HTTP_201_CREATED)
def ingest_tasks(payload: TaskIngestRequest, _: None = Depends(require_token)):
    if not payload.tasks:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="At least one task is required")
    metadata = storage.save_task_set(payload.tasks)
    return metadata


@app.get("/tasks/current")
def get_current_tasks():
    latest = storage.load_latest_tasks_payload()
    if not latest:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No task sets found")
    return {
        "metadata": latest["metadata"],
        "tasks": latest["tasks"],
    }


@app.get("/pull-requests", response_model=PullRequestListResponse)
def list_pull_requests():
    items = storage.load_pull_requests()
    return PullRequestListResponse(items=items)


@app.post("/pull-requests", response_model=PullRequestSummary, status_code=status.HTTP_201_CREATED)
async def ingest_pull_request(payload: PullRequestIngestRequest, _: None = Depends(require_token)):
    record = payload.to_record()
    summary = storage.upsert_scan_result(record)
    enqueue_scan(summary.id)
    return summary


@app.get("/pull-requests/{pr_id:path}", response_model=PullRequestDetail)
def get_pull_request(pr_id: str):
    record = storage.load_pull_request_record(pr_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PR not found")
    return record


@app.post("/pull-requests/{pr_id:path}/rerun", status_code=status.HTTP_202_ACCEPTED)
async def rerun_pull_request(pr_id: str, _: None = Depends(require_token)):
    updated = storage.mark_scan_pending(pr_id)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PR not found")
    enqueue_scan(pr_id)
    return {"status": "queued"}


@app.post("/pull-requests/rerun-all", status_code=status.HTTP_202_ACCEPTED)
async def rerun_all_pull_requests(_: None = Depends(require_token)):
    ids = storage.list_scan_ids()
    if not ids:
        return {"status": "queued", "count": 0}
    for pr_id in ids:
        storage.mark_scan_pending(pr_id)
        enqueue_scan(pr_id)
    return {"status": "queued", "count": len(ids)}


@app.post("/agent-runs", response_model=AgentRunRecord, status_code=status.HTTP_202_ACCEPTED)
def ingest_agent_run(payload: AgentRunIngestRequest, _: None = Depends(require_token)):
    record = payload.to_record()
    storage.save_scan_result(record)
    return record


@app.get("/agent-results/{pr_id:path}", response_model=AgentRunRecord)
def get_agent_results(pr_id: str):
    record = storage.load_scan_result(pr_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Results not found")
    return record


@app.get("/debug/pull-requests", response_class=HTMLResponse)
def debug_pull_requests():
    prs = storage.load_pull_requests()
    rows = []
    for pr in prs:
        rows.append(
            f"<tr>"
            f"<td>{html.escape(pr.id)}</td>"
            f"<td>{html.escape(pr.repository)}</td>"
            f"<td>{pr.number}</td>"
            f"<td>{html.escape(pr.status)}</td>"
            f"<td>{pr.files_changed}</td>"
            f"<td>{pr.violations}</td>"
            f"<td>{pr.lines_added}</td>"
            f"<td>{pr.lines_removed}</td>"
            f"<td>{html.escape(pr.last_run.isoformat() if pr.last_run else '—')}</td>"
            f"</tr>"
        )
    rows_html = "\n".join(rows) or "<tr><td colspan='9'>No pull requests ingested yet.</td></tr>"
    html_body = f"""
    <html>
      <head>
        <title>Guardians PR Debug</title>
        <style>
          body {{ font-family: Arial, sans-serif; padding: 2rem; background: #0b1120; color: #e2e8f0; }}
          table {{ border-collapse: collapse; width: 100%; }}
          th, td {{ border: 1px solid #1e293b; padding: 0.5rem; text-align: left; }}
          th {{ background: #1e293b; }}
          tr:nth-child(even) {{ background: #111827; }}
          a {{ color: #38bdf8; }}
        </style>
      </head>
      <body>
        <h1>Pull Request Ingest Debug</h1>
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Repository</th>
              <th>#</th>
              <th>Status</th>
              <th>Files Changed</th>
              <th>Violations</th>
              <th>Lines Added</th>
              <th>Lines Removed</th>
              <th>Last Run</th>
            </tr>
          </thead>
          <tbody>
            {rows_html}
          </tbody>
        </table>
        <p>Data source: /pull-requests</p>
      </body>
    </html>
    """
    return HTMLResponse(content=html_body)

@app.post("/github/webhook", status_code=status.HTTP_202_ACCEPTED)
async def github_webhook(
    request: Request,
    x_github_event: str = Header(alias="X-GitHub-Event"),
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
):
    raw_body = await request.body()
    verify_github_signature(raw_body, x_hub_signature_256)

    if x_github_event != "pull_request":
        return {"status": "ignored", "reason": "unsupported event"}

    payload = await request.json()
    action = payload.get("action")
    if action not in {"opened", "reopened", "synchronize"}:
        return {"status": "ignored", "reason": f"action {action}"}

    pr = payload.get("pull_request") or {}
    repo = payload.get("repository") or {}
    changed_files = fetch_pull_request_files(repo.get("full_name", ""), pr.get("number"))

    pr_payload = PullRequestIngestRequest(
        repository=repo.get("full_name", ""),
        number=pr.get("number"),
        title=pr.get("title", ""),
        author=pr.get("user", {}).get("login", ""),
        base_branch=pr.get("base", {}).get("ref", ""),
        head_branch=pr.get("head", {}).get("ref", ""),
        head_sha=pr.get("head", {}).get("sha"),
        files_changed=pr.get("changed_files", 0),
        lines_added=pr.get("additions", 0),
        lines_removed=pr.get("deletions", 0),
        changed_files=changed_files,
    )
    summary = storage.upsert_scan_result(pr_payload.to_record())
    enqueue_scan(summary.id)
    return {"status": "accepted"}


# ===================== RAG Document Ingestion =====================

@app.post("/documents", status_code=status.HTTP_201_CREATED)
async def ingest_documents(
    files: List[UploadFile] = File(...),
    _: None = Depends(require_token),
):
    """Upload compliance/policy documents. This triggers the full pipeline:

    1. Chunk each document (recursive text splitting)
    2. Embed chunks and store in FAISS vector index
    3. Extract compliance tasks from each chunk via LLM
    4. Store tasks linked to their source chunks

    Each task knows exactly which document paragraph it came from.
    At validation time, that source chunk is injected as grounding context.
    """
    from .rag import chunk_document
    from .rag.task_extractor import extract_tasks_from_chunks

    retriever = get_retriever()
    all_extracted_tasks = []
    results = []

    for upload in files:
        content = (await upload.read()).decode("utf-8", errors="ignore")
        doc_id = upload.filename or f"doc-{len(results)}"

        # Persist raw document to DB
        storage.save_document(doc_id, upload.filename or "unknown", content)

        # Step 1 & 2: Chunk, embed, store in FAISS
        chunk_count = retriever.ingest_document(
            text=content,
            doc_id=doc_id,
            metadata={"filename": upload.filename},
        )

        # Step 3: Extract tasks from each chunk via LLM
        chunks = chunk_document(text=content, doc_id=doc_id)
        chunk_dicts = [
            {"text": c.text, "doc_id": c.doc_id, "chunk_index": c.chunk_index}
            for c in chunks
        ]

        try:
            extracted = extract_tasks_from_chunks(chunk_dicts)
            all_extracted_tasks.extend(extracted)
            logger.info("Extracted %d tasks from %s", len(extracted), doc_id)
        except Exception as exc:
            logger.error("Task extraction failed for %s: %s", doc_id, exc)

        results.append({
            "doc_id": doc_id,
            "chunks": chunk_count,
            "tasks_extracted": len([t for t in all_extracted_tasks if t.get("source_chunk", {}).get("doc_id") == doc_id]),
        })

    # Persist the vector index to disk
    retriever.save(DATA_DIR / "vector_index")

    # Step 4: Save extracted tasks (with source chunk links) as a task set
    if all_extracted_tasks:
        from .schemas import Task
        tasks = []
        for t in all_extracted_tasks:
            try:
                tasks.append(Task(
                    id=t.get("id", "unknown"),
                    title=t.get("title", "Untitled"),
                    description=t.get("description", ""),
                    category=t.get("category", "General"),
                    severity=t.get("severity", "warning"),
                    checkType=t.get("checkType", "Pattern Detection"),
                    fileTypes=t.get("fileTypes", ["*.py", "*.js"]),
                    exampleViolation=t.get("exampleViolation", ""),
                    suggestedFix=t.get("suggestedFix", ""),
                    docReference=t.get("docReference", ""),
                ))
            except Exception:
                continue
        if tasks:
            # Store tasks with source_chunk metadata preserved
            storage.save_task_set(tasks)
            # Also store the raw tasks with source chunks for validation
            storage.save_task_set_raw(all_extracted_tasks)
            logger.info("Saved %d tasks from document ingestion", len(tasks))

    return {
        "documents": results,
        "total_chunks": sum(r["chunks"] for r in results),
        "total_tasks_extracted": len(all_extracted_tasks),
    }


@app.get("/documents")
def list_documents():
    """List all ingested compliance documents."""
    docs = storage.load_documents()
    return {"documents": docs}


@app.get("/rag/status")
def rag_status():
    """Check the status of the RAG vector index."""
    retriever = get_retriever()
    return {
        "document_count": retriever.document_count,
        "chunk_count": retriever.chunk_count,
        "vector_count": retriever.vector_store.size,
    }


class RAGQueryRequest(BaseModel):
    query: str
    top_k: int = 5


@app.post("/rag/query")
def rag_query(body: RAGQueryRequest):
    """Debug endpoint: query the RAG index directly."""
    retriever = get_retriever()
    results = retriever.query(body.query, top_k=body.top_k)
    return {"results": results}
