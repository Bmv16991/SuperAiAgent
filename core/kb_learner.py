"""
core/kb_learner.py — Knowledge Base Search

Queries Qdrant KB collections for semantic search.
Connects to server mode (QDRANT_URL) first, falls back to local path.

Collections:
  gh_repos      - repo metadata
  gh_patterns   - extracted AI patterns/techniques
  gh_synthesis  - final synthesis insights
  gh_knowledge  - raw chunked file content
"""
import os
from pathlib import Path
from loguru import logger
from config.settings import QDRANT_URL as _QDRANT_URL, QDRANT_PATH, ST_CACHE_PATH, EMBED_MODEL, COLLECTIONS

QDRANT_URL    = _QDRANT_URL
QDRANT_LOCAL  = Path(QDRANT_PATH)

COL_PATTERNS  = COLLECTIONS["patterns"]
COL_SYNTHESIS = COLLECTIONS["synthesis"]
COL_REPOS     = COLLECTIONS["repos"]
COL_KNOWLEDGE = COLLECTIONS["knowledge"]

_client  = None
_encoder = None
_ready   = None

_LOCKED = object()


def _connect():
    from qdrant_client import QdrantClient
    try:
        import httpx
        httpx.get(f"{QDRANT_URL}/collections", timeout=2).raise_for_status()
        client = QdrantClient(url=QDRANT_URL)
        cols   = {c.name for c in client.get_collections().collections}
        if COL_PATTERNS in cols or COL_SYNTHESIS in cols or COL_KNOWLEDGE in cols:
            logger.info(f"KB: server mode ({QDRANT_URL})")
            return client
    except Exception:
        pass

    if QDRANT_LOCAL.exists():
        import warnings
        warnings.filterwarnings("ignore", message="Local mode is not recommended")
        try:
            client = QdrantClient(path=str(QDRANT_LOCAL))
            logger.info(f"KB: local mode ({QDRANT_LOCAL})")
            return client
        except Exception as e:
            if "already accessed" in str(e):
                return _LOCKED
            raise

    return None


def _get_encoder():
    global _encoder
    if _encoder is None:
        os.environ["TOKENIZERS_PARALLELISM"] = "false"
        os.environ["HF_HOME"] = ST_CACHE_PATH
        from sentence_transformers import SentenceTransformer
        _encoder = SentenceTransformer(EMBED_MODEL, device="cpu")
        logger.info(f"KB: encoder loaded ({EMBED_MODEL})")
    return _encoder


def _init() -> bool:
    global _client, _ready
    if _ready is True:
        return True
    if _ready is False:
        return False

    try:
        from qdrant_client import QdrantClient
        from sentence_transformers import SentenceTransformer
    except ImportError as e:
        logger.debug(f"Missing dependency: {e}")
        _ready = False
        return False

    client = _connect()
    if client is _LOCKED:
        return False
    if client is None:
        _ready = False
        return False

    cols = {c.name for c in client.get_collections().collections}
    if not (cols & {COL_PATTERNS, COL_SYNTHESIS, COL_KNOWLEDGE}):
        logger.warning(f"KB collections not found. Available: {cols}")
        _ready = False
        return False

    _get_encoder()
    _client = client
    _ready  = True
    return True


def search(query: str, collection: str = None, top_k: int = 8) -> list[dict]:
    """Semantic search in KB. Returns [] if unavailable."""
    if collection is None:
        collection = COL_PATTERNS
    if not _init():
        return []
    try:
        vec      = _get_encoder().encode(query[:1000], normalize_embeddings=True).tolist()
        response = _client.query_points(collection_name=collection, query=vec, limit=top_k, with_payload=True)
        points   = response.points if hasattr(response, "points") else response
        return [
            {
                "score":   round(r.score, 4),
                "content": _build_content(r.payload),
                "meta":    {k: v for k, v in r.payload.items() if k != "content"},
            }
            for r in points
        ]
    except Exception as e:
        logger.warning(f"KB search failed: {e}")
        return []


def search_knowledge(query: str, top_k: int = 6) -> list[dict]:
    """Search both gh_patterns AND gh_synthesis — merged and deduplicated."""
    if not _init():
        return []
    patterns  = search(query, COL_PATTERNS,  top_k)
    synthesis = search(query, COL_SYNTHESIS, top_k // 2)
    seen, merged = set(), []
    for r in patterns + synthesis:
        key = r["content"][:80]
        if key not in seen:
            seen.add(key)
            merged.append(r)
    return sorted(merged, key=lambda x: x["score"], reverse=True)[:top_k]


def _build_content(payload: dict) -> str:
    if "content" in payload:
        return payload["content"][:800]
    if "title" in payload:
        parts = []
        for field in ("title", "category", "description", "implementation"):
            if payload.get(field):
                parts.append(f"{field.capitalize()}: {payload[field][:300]}")
        return "\n".join(parts)[:800]
    if "full_name" in payload:
        return f"{payload.get('full_name', '')} — {payload.get('description', '')}"
    return " | ".join(f"{k}: {str(v)[:100]}" for k, v in payload.items())[:800]


def is_available() -> bool:
    return _init()
