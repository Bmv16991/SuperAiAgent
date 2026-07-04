"""
core/skill_router.py — Semantic Skill Router

Finds the best skill for a task using semantic similarity.
Supports 50+ languages via paraphrase-multilingual-MiniLM-L12-v2.
"""
import os
import time
from pathlib import Path
from loguru import logger
from config.settings import SKILLS_DIR, ST_CACHE_PATH, EMBED_MODEL

ROUTE_THRESHOLD = 0.55  # minimum score to route to a skill
META_BOOST      = 0.06  # boost meta-skills when score is close

_encoder     = None
_skill_cache = {}
_cache_time  = 0.0


def _get_encoder():
    global _encoder
    if _encoder is None:
        os.environ["TOKENIZERS_PARALLELISM"] = "false"
        os.environ["HF_HOME"] = ST_CACHE_PATH
        from sentence_transformers import SentenceTransformer
        _encoder = SentenceTransformer(EMBED_MODEL, device="cpu")
    return _encoder


def _extract_purpose(skill_md: str) -> str:
    lines, in_purpose = [], False
    for line in skill_md.splitlines():
        if line.startswith("description:"):
            lines.append(line.replace("description:", "").strip().strip('"'))
        elif line.strip() == "## Purpose":
            in_purpose = True
        elif in_purpose:
            if line.startswith("##"):
                break
            lines.append(line)
    return " ".join(lines)[:400] or skill_md[:300]


def _rebuild_cache():
    global _skill_cache, _cache_time
    import numpy as np

    encoder   = _get_encoder()
    new_cache = {}

    if not SKILLS_DIR.exists():
        return new_cache

    for skill_md_path in SKILLS_DIR.rglob("SKILL.md"):
        d     = skill_md_path.parent
        mtime = skill_md_path.stat().st_mtime

        # Build name: include subdirectory if nested
        rel = d.relative_to(SKILLS_DIR)
        parts = rel.parts
        name = "/".join(parts) if len(parts) > 1 else d.name

        if name in _skill_cache and _skill_cache[name]["mtime"] == mtime:
            new_cache[name] = _skill_cache[name]
            continue

        content = skill_md_path.read_text(encoding="utf-8")
        purpose = _extract_purpose(content)
        vec     = encoder.encode(purpose, normalize_embeddings=True)
        new_cache[name] = {"purpose": purpose, "vec": vec, "mtime": mtime}

    _skill_cache = new_cache
    _cache_time  = time.time()
    return new_cache


def route(task: str, top_k: int = 3) -> list[dict]:
    """Return top-k skills ranked by semantic similarity to the task."""
    try:
        import numpy as np

        if not _skill_cache or time.time() - _cache_time > 300:
            _rebuild_cache()

        if not _skill_cache:
            return []

        encoder  = _get_encoder()
        task_vec = encoder.encode(task[:500], normalize_embeddings=True)

        results = []
        for name, info in _skill_cache.items():
            score = float(task_vec @ info["vec"])
            if "meta/" in name or name.startswith("meta/"):
                score += META_BOOST
            if score >= ROUTE_THRESHOLD:
                results.append({
                    "name":    name,
                    "score":   round(score, 4),
                    "purpose": info["purpose"][:200],
                })

        try:
            from core.skill_leaderboard import rerank
            results = rerank(results, task)
            sort_key = "final_score"
        except Exception:
            sort_key = "score"

        return sorted(results, key=lambda x: x[sort_key], reverse=True)[:top_k]

    except Exception as e:
        logger.warning(f"[Router] Semantic routing failed: {e}")
        return []


def best_skill(task: str, threshold: float = ROUTE_THRESHOLD) -> str | None:
    """Return the best skill name, or None if no good match."""
    results = route(task, top_k=1)
    if results and results[0]["score"] >= threshold:
        return results[0]["name"]
    return None
