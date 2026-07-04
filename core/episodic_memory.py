"""
core/episodic_memory.py — Episodic Memory

Stores history of agent executions (task + skill + result + success).
Searches for similar past episodes to use as context for new tasks.

Storage: data/episodic_memory.json + in-memory vector cache
"""
import json
import os
import time
from datetime import datetime
from loguru import logger
from config.settings import EPISODIC_MEMORY_FILE, ST_CACHE_PATH, EMBED_MODEL

MAX_EPISODES     = 500
RECALL_THRESHOLD = 0.55

_encoder  = None
_episodes = None
_vecs     = None


def _get_encoder():
    global _encoder
    if _encoder is None:
        os.environ["TOKENIZERS_PARALLELISM"] = "false"
        os.environ["HF_HOME"] = ST_CACHE_PATH
        from sentence_transformers import SentenceTransformer
        _encoder = SentenceTransformer(EMBED_MODEL, device="cpu")
    return _encoder


def _load() -> list[dict]:
    global _episodes
    if _episodes is not None:
        return _episodes
    if EPISODIC_MEMORY_FILE.exists():
        try:
            _episodes = json.loads(EPISODIC_MEMORY_FILE.read_text(encoding="utf-8"))
            return _episodes
        except Exception:
            pass
    _episodes = []
    return _episodes


def _save(episodes: list[dict]):
    EPISODIC_MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    EPISODIC_MEMORY_FILE.write_text(json.dumps(episodes, ensure_ascii=False, indent=2))


def _rebuild_vecs(episodes: list[dict]):
    global _vecs
    if not episodes:
        _vecs = None
        return
    encoder = _get_encoder()
    tasks   = [e["task"][:400] for e in episodes]
    _vecs   = encoder.encode(tasks, normalize_embeddings=True, show_progress_bar=False)


def store(task: str, skill: str, result: str, success: bool, context: str = ""):
    global _episodes, _vecs
    episodes = _load()
    episode  = {
        "ts":      datetime.now().isoformat(),
        "task":    task[:400],
        "skill":   skill,
        "result":  result[:600],
        "success": success,
        "context": context[:200],
    }
    episodes.append(episode)
    if len(episodes) > MAX_EPISODES:
        episodes = episodes[-MAX_EPISODES:]
    _episodes = episodes
    _vecs     = None
    _save(episodes)
    logger.debug(f"[Memory] Stored episode #{len(episodes)}: {task[:50]}")


def recall(task: str, top_k: int = 3) -> list[dict]:
    global _vecs
    try:
        import numpy as np
        episodes = _load()
        if not episodes:
            return []
        if _vecs is None:
            _rebuild_vecs(episodes)
        if _vecs is None or len(_vecs) == 0:
            return []

        encoder  = _get_encoder()
        task_vec = encoder.encode(task[:400], normalize_embeddings=True)
        sims     = (task_vec @ _vecs.T).tolist()

        results = []
        for i, score in enumerate(sims):
            if score >= RECALL_THRESHOLD:
                results.append({**episodes[i], "similarity": round(score, 4)})
        results.sort(key=lambda x: x["similarity"], reverse=True)
        return results[:top_k]
    except Exception as e:
        logger.warning(f"[Memory] Recall failed: {e}")
        return []


def format_context(episodes: list[dict]) -> str:
    if not episodes:
        return ""
    lines = ["PAST EXPERIENCE (similar tasks):"]
    for ep in episodes:
        status = "SUCCESS" if ep["success"] else "FAILED"
        lines.append(
            f"- [{ep['ts'][:10]}] {status} | Skill: {ep['skill']}\n"
            f"  Task: {ep['task'][:100]}\n"
            f"  Result: {ep['result'][:150]}"
        )
    return "\n".join(lines)


def stats() -> dict:
    episodes = _load()
    if not episodes:
        return {"total": 0}
    success_count = sum(1 for e in episodes if e["success"])
    return {
        "total":        len(episodes),
        "success":      success_count,
        "failed":       len(episodes) - success_count,
        "success_rate": round(success_count / len(episodes), 2),
        "oldest":       episodes[0]["ts"][:10],
        "newest":       episodes[-1]["ts"][:10],
    }
