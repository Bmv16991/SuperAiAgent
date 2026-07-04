"""
core/hermes_memory.py — Tiered Memory System

Tiers:
  episodic   (hermes_episodes)   — task traces, raw results
  semantic   (hermes_facts)      — distilled knowledge / rules
  procedural (hermes_mutations)  — skill mutations + fitness deltas

Retrieval: weighted_score = 0.5·cosine + 0.3·recency_decay + 0.2·importance
  recency_decay = exp(-Δt / tau_days)   default tau=30 days

Source patterns: Fundamental-Ava (Park et al.), R2R hybrid retrieval, MemoryOS tier promotion
"""
from __future__ import annotations

import hashlib
import math
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from loguru import logger
from config.settings import QDRANT_URL, ST_CACHE_PATH, EMBED_MODEL, TIER_COLLECTIONS

VECTOR_SIZE = 384

Tier = Literal["episodic", "semantic", "procedural"]

_client  = None
_encoder = None
_lock    = threading.Lock()


def _get_client():
    global _client
    if _client is None:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams
        _client = QdrantClient(url=QDRANT_URL)
        existing = {c.name for c in _client.get_collections().collections}
        for col in TIER_COLLECTIONS.values():
            if col not in existing:
                _client.create_collection(
                    collection_name=col,
                    vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
                )
                logger.info(f"Created memory tier: {col}")
    return _client


def _get_encoder():
    global _encoder
    if _encoder is None:
        os.environ["TOKENIZERS_PARALLELISM"] = "false"
        os.environ["HF_HOME"] = ST_CACHE_PATH
        from sentence_transformers import SentenceTransformer
        _encoder = SentenceTransformer(EMBED_MODEL, device="cpu")
    return _encoder


def _embed(text: str) -> list[float]:
    with _lock:
        vec = _get_encoder().encode(text[:500], normalize_embeddings=True, show_progress_bar=False)
    return vec.tolist()


def _content_id(content: str) -> str:
    h = hashlib.md5(content.encode()).hexdigest()
    return str(uuid.UUID(h))


def store(
    tier: Tier,
    content: str,
    metadata: dict,
    importance: float = 0.5,
    embed_text: str = "",
) -> str:
    """Store a memory in the specified tier. Returns point_id."""
    col      = TIER_COLLECTIONS[tier]
    point_id = _content_id(content)
    now      = datetime.now(timezone.utc)
    payload  = {
        "content":    content,
        "tier":       tier,
        "timestamp":  now.isoformat(),
        "importance": max(0.0, min(1.0, importance)),
        **metadata,
    }
    vector = _embed(embed_text or content)
    client = _get_client()
    from qdrant_client.models import PointStruct
    client.upsert(
        collection_name=col,
        points=[PointStruct(id=point_id, vector=vector, payload=payload)],
    )
    return point_id


def retrieve(
    tier: Tier,
    query: str,
    k: int = 5,
    weights: Optional[dict] = None,
    tau_days: float = 30.0,
) -> list[dict]:
    """Hybrid retrieval: weighted_score = w_cos·cosine + w_rec·recency + w_imp·importance"""
    w         = weights or {"cos": 0.5, "recency": 0.3, "importance": 0.2}
    col       = TIER_COLLECTIONS[tier]
    query_vec = _embed(query)
    client    = _get_client()

    results = client.query_points(
        collection_name=col,
        query=query_vec,
        limit=k * 3,
        with_payload=True,
    ).points

    now    = datetime.now(timezone.utc)
    scored = []
    for r in results:
        cos = r.score
        ts  = r.payload.get("timestamp", now.isoformat())
        try:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            delta_days = (now - dt).total_seconds() / 86400
        except Exception:
            delta_days = 0.0
        recency    = math.exp(-delta_days / tau_days)
        importance = float(r.payload.get("importance", 0.5))
        hybrid     = w["cos"] * cos + w["recency"] * recency + w["importance"] * importance
        scored.append({
            "score":      round(hybrid, 4),
            "cos":        round(cos, 4),
            "recency":    round(recency, 4),
            "importance": importance,
            "content":    r.payload.get("content", ""),
            "meta":       {k: v for k, v in r.payload.items() if k not in ("content",)},
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:k]


def novelty_check(proposed_text: str, tier: Tier = "procedural", threshold: float = 0.92) -> dict:
    """
    Novelty Gate: embed proposed mutation, compare against recent procedural memories.
    Reject if cosine > threshold — prevents redundant skill upgrades.
    """
    results = retrieve(tier, proposed_text, k=3, weights={"cos": 1.0, "recency": 0.0, "importance": 0.0})
    if not results:
        return {"novel": True, "reason": "no prior mutations", "nearest_id": None, "nearest_score": 0.0}
    top = results[0]
    if top["cos"] >= threshold:
        return {
            "novel": False,
            "reason": f"too similar to existing mutation (cosine={top['cos']:.3f} >= {threshold})",
            "nearest_id": top["meta"].get("mutation_id", "?"),
            "nearest_score": top["cos"],
        }
    return {"novel": True, "reason": f"sufficiently novel (cosine={top['cos']:.3f} < {threshold})",
            "nearest_id": None, "nearest_score": top["cos"]}


def stats() -> dict:
    """Return point counts per tier."""
    client = _get_client()
    out    = {}
    for tier, col in TIER_COLLECTIONS.items():
        try:
            out[tier] = client.get_collection(col).points_count or 0
        except Exception:
            out[tier] = 0
    return out
