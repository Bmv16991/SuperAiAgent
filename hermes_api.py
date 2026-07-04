"""
hermes_api.py — SuperAiAgent REST API Server

Endpoints:
  POST /run          — execute task (synchronous)
  POST /run_async    — execute task (async, returns job_id)
  GET  /result/{id}  — poll async job status
  POST /search       — find skills by semantic similarity
  POST /kb_query     — query knowledge base (Qdrant)
  POST /memory       — store/retrieve tiered memory
  GET  /stats        — system status
  GET  /health       — health check (no auth)

Auth: X-API-Key header (set HERMES_API_KEY in .env)

Run:
  python hermes_api.py
  uvicorn hermes_api:app --host 0.0.0.0 --port 8765
"""
import sys
import os
import asyncio
import uuid
from pathlib import Path
from typing import Optional

ROOT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(ROOT_DIR))
os.chdir(ROOT_DIR)

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from loguru import logger
import uvicorn

from config.settings import QDRANT_URL, ST_CACHE_PATH, EMBED_MODEL, SKILL_VECTORS_PATH

API_KEY = os.environ.get("HERMES_API_KEY", "superai-local-dev-key")
PORT    = int(os.environ.get("HERMES_API_PORT", 8765))

app = FastAPI(
    title="SuperAiAgent API",
    description="Semantic skill routing + self-evolution + tiered memory",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_orchestrator = None
_qdrant_client = None
_encoder       = None
_skill_cache: dict = {}
_jobs: dict = {}  # job_id → {"status": "running"|"done"|"error", "result": str}


def _get_orchestrator():
    global _orchestrator
    if _orchestrator is None:
        from core.orchestrator import Orchestrator
        _orchestrator = Orchestrator()
    return _orchestrator


def _get_encoder():
    global _encoder
    if _encoder is None:
        os.environ["TOKENIZERS_PARALLELISM"] = "false"
        os.environ["HF_HOME"] = ST_CACHE_PATH
        from sentence_transformers import SentenceTransformer
        _encoder = SentenceTransformer(EMBED_MODEL, device="cpu")
    return _encoder


def _get_kb_client():
    global _qdrant_client
    if _qdrant_client is None:
        from qdrant_client import QdrantClient
        _qdrant_client = QdrantClient(url=QDRANT_URL)
    return _qdrant_client, _get_encoder()


def _build_skill_cache():
    import numpy as np
    if "vecs" in _skill_cache:
        return _skill_cache
    if SKILL_VECTORS_PATH.exists():
        data = np.load(str(SKILL_VECTORS_PATH), allow_pickle=True)
        _skill_cache["names"] = data["names"].tolist()
        _skill_cache["descs"] = data["descs"].tolist()
        _skill_cache["vecs"]  = data["vecs"]
    return _skill_cache


def verify_key(x_api_key: str = Header(...)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return x_api_key


# ── Request Models ────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    task:    str
    context: str = ""

class SearchRequest(BaseModel):
    query: str
    top_k: int = 5

class KbQueryRequest(BaseModel):
    query: str
    top_k: int = 5

class MemoryRequest(BaseModel):
    action:     str
    tier:       str = "episodic"
    content:    Optional[str] = None
    query:      Optional[str] = None
    importance: float = 0.5

class AsyncRunRequest(BaseModel):
    task:    str
    context: str = ""


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "superai-agent"}


@app.get("/stats", dependencies=[Depends(verify_key)])
async def stats():
    loop = asyncio.get_event_loop()

    def _stats():
        from config.settings import SKILLS_DIR
        total = sum(1 for _ in SKILLS_DIR.rglob("SKILL.md")) if SKILLS_DIR.exists() else 0
        result = {"skills": {"total": total}}
        try:
            from qdrant_client import QdrantClient
            c    = QdrantClient(url=QDRANT_URL)
            cols = {col.name: c.get_collection(col.name).points_count for col in c.get_collections().collections}
            result["qdrant"] = cols
        except Exception as e:
            result["qdrant"] = {"error": str(e)}
        try:
            from core.hermes_memory import stats as mem_stats
            result["memory"] = mem_stats()
        except Exception as e:
            result["memory"] = {"error": str(e)}
        return result

    return await loop.run_in_executor(None, _stats)


@app.post("/run", dependencies=[Depends(verify_key)])
async def run(req: RunRequest):
    loop = asyncio.get_event_loop()

    def _run():
        orch   = _get_orchestrator()
        result = orch.execute(req.task, req.context)
        if hasattr(result, "result"):
            return {"success": getattr(result, "success", True), "result": str(result.result)}
        return {"success": True, "result": str(result)}

    return await loop.run_in_executor(None, _run)


def _execute_job(job_id: str, task: str, context: str):
    try:
        orch   = _get_orchestrator()
        result = orch.execute(task, context)
        text   = str(result.result) if hasattr(result, "result") else str(result)
        _jobs[job_id] = {"status": "done", "result": text}
        logger.info(f"Job {job_id[:8]} done")
    except Exception as e:
        _jobs[job_id] = {"status": "error", "result": str(e)}
        logger.error(f"Job {job_id[:8]} error: {e}")


@app.post("/run_async", dependencies=[Depends(verify_key)])
async def run_async(req: AsyncRunRequest):
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "running", "result": ""}
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _execute_job, job_id, req.task, req.context)
    logger.info(f"Job {job_id[:8]} started: {req.task[:60]}")
    return {"job_id": job_id, "status": "running"}


@app.get("/result/{job_id}", dependencies=[Depends(verify_key)])
async def get_result(job_id: str):
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return _jobs[job_id]


@app.post("/search", dependencies=[Depends(verify_key)])
async def search(req: SearchRequest):
    loop = asyncio.get_event_loop()

    def _search():
        cache = _build_skill_cache()
        if not cache or "vecs" not in cache:
            return {"results": []}
        enc   = _get_encoder()
        q_vec = enc.encode(req.query[:500], normalize_embeddings=True, show_progress_bar=False)
        sims  = (q_vec @ cache["vecs"].T).tolist()
        paired = sorted(zip(sims, cache["names"], cache["descs"]), reverse=True)[:req.top_k]
        return {"results": [{"score": round(s, 4), "name": n, "description": d} for s, n, d in paired]}

    return await loop.run_in_executor(None, _search)


@app.post("/kb_query", dependencies=[Depends(verify_key)])
async def kb_query(req: KbQueryRequest):
    loop = asyncio.get_event_loop()

    def _query():
        top_k  = min(req.top_k, 10)
        client, encoder = _get_kb_client()
        q_vec  = encoder.encode(req.query[:500], normalize_embeddings=True, show_progress_bar=False).tolist()
        points = client.query_points(
            collection_name="gh_knowledge",
            query=q_vec,
            limit=top_k,
            with_payload=True,
        ).points
        return {"results": [
            {
                "score":   round(r.score, 4),
                "repo":    r.payload.get("repo_name", "?"),
                "file":    r.payload.get("file_path", "?"),
                "content": r.payload.get("content", r.payload.get("text", ""))[:400],
            }
            for r in points
        ]}

    return await loop.run_in_executor(None, _query)


@app.post("/memory", dependencies=[Depends(verify_key)])
async def memory(req: MemoryRequest):
    loop = asyncio.get_event_loop()

    def _mem():
        from core.hermes_memory import store, retrieve, stats as mem_stats
        if req.action == "stats":
            return mem_stats()
        elif req.action == "store":
            if not req.content:
                raise ValueError("content required for store")
            pid = store(req.tier, req.content, {"source": "api"}, importance=req.importance)
            return {"stored": True, "id": pid, "tier": req.tier}
        elif req.action == "retrieve":
            if not req.query:
                raise ValueError("query required for retrieve")
            results = retrieve(req.tier, req.query, k=5)
            return {"results": [{"score": r["score"], "content": r["content"][:300]} for r in results]}
        raise ValueError(f"Unknown action: {req.action}")

    return await loop.run_in_executor(None, _mem)


@app.on_event("startup")
async def startup():
    loop = asyncio.get_event_loop()

    def _warmup():
        logger.info("Warming up encoder...")
        _get_encoder()
        logger.info("Loading skill vectors...")
        _build_skill_cache()
        n = len(_skill_cache.get("names", []))
        logger.info(f"Skill cache ready: {n} skills")
        try:
            client, _ = _get_kb_client()
            client.get_collections()
            logger.info("Qdrant ready.")
        except Exception as e:
            logger.warning(f"Qdrant warmup failed: {e}")

    await loop.run_in_executor(None, _warmup)
    logger.info(f"SuperAiAgent API ready on port {PORT}")


if __name__ == "__main__":
    uvicorn.run("hermes_api:app", host="0.0.0.0", port=PORT, reload=False, log_level="info")
