"""
config/settings.py — All configurable paths and constants
Override any setting via environment variable.
"""
import os
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent

# ── Base directories ───────────────────────────────────────────────────────────
DATA_DIR   = Path(os.environ.get("SUPERAI_DATA_DIR",   str(_REPO_ROOT / "data")))
SKILLS_DIR = Path(os.environ.get("SUPERAI_SKILLS_DIR", str(_REPO_ROOT / "skills")))

# ── SentenceTransformers cache ─────────────────────────────────────────────────
ST_CACHE_PATH = os.environ.get("ST_CACHE_PATH", str(DATA_DIR / "st_cache"))

# ── Embedding model ────────────────────────────────────────────────────────────
# paraphrase-multilingual-MiniLM-L12-v2 supports 50+ languages (384-dim)
EMBED_MODEL = os.environ.get("EMBED_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")

# ── Qdrant ─────────────────────────────────────────────────────────────────────
QDRANT_URL  = os.environ.get("QDRANT_URL",  "http://localhost:6333")
QDRANT_PATH = os.environ.get("QDRANT_PATH", str(DATA_DIR / "qdrant"))

# ── Persistent data files ──────────────────────────────────────────────────────
EPISODIC_MEMORY_FILE = DATA_DIR / "episodic_memory.json"
DELIBERATION_LOG     = DATA_DIR / "deliberation_log.json"
KNOWLEDGE_GAPS_FILE  = DATA_DIR / "knowledge_gaps.json"
LEADERBOARD_FILE     = DATA_DIR / "skill_leaderboard.json"
EVOLUTION_LOG        = DATA_DIR / "evolution_log.jsonl"
SKILL_VECTORS_PATH   = DATA_DIR / "skill_vectors.npz"

# ── Knowledge base collections (Qdrant) ───────────────────────────────────────
COLLECTIONS = {
    "repos":     "gh_repos",
    "knowledge": "gh_knowledge",
    "patterns":  "gh_patterns",
    "synthesis": "gh_synthesis",
}

# ── Tiered memory collections (Qdrant) ────────────────────────────────────────
TIER_COLLECTIONS = {
    "episodic":   "hermes_episodes",
    "semantic":   "hermes_facts",
    "procedural": "hermes_mutations",
}
