"""
core/evolution.py - Evolution log read/write
Shared by orchestrator, skills, and CLI.
"""
import json
from datetime import datetime
from pathlib import Path
from collections import Counter

from config.settings import SKILLS_DIR, EVOLUTION_LOG as _EVOLUTION_LOG

EVOLUTION_LOG = _EVOLUTION_LOG


def log(skill: str, task: str, success: bool,
        confidence: str = "MEDIUM", result_summary: str = ""):
    """Append one entry to evolution_log.jsonl."""
    entry = {
        "timestamp":      datetime.now().isoformat(),
        "skill":          skill,
        "task":           task[:200],
        "success":        success,
        "confidence":     confidence,
        "result_summary": result_summary[:300],
    }
    EVOLUTION_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(EVOLUTION_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_recent(n: int = 20) -> list:
    if not EVOLUTION_LOG.exists():
        return []
    lines = [l.strip() for l in EVOLUTION_LOG.read_text(encoding="utf-8").splitlines() if l.strip()]
    return [json.loads(l) for l in lines[-n:]]


def failure_rate(skill: str = None, n: int = 10) -> float:
    logs = read_recent(n)
    if skill:
        logs = [l for l in logs if l.get("skill") == skill]
    if not logs:
        return 0.0
    return sum(1 for l in logs if not l.get("success", True)) / len(logs)


def weakest_skill(n: int = 20) -> str:
    logs   = read_recent(n)
    failed = [l["skill"] for l in logs if not l.get("success", True)]
    if not failed:
        return ""
    return Counter(failed).most_common(1)[0][0]


def skill_stats(n: int = 50) -> dict:
    logs  = read_recent(n)
    stats = {}
    for l in logs:
        s = l.get("skill", "unknown")
        if s not in stats:
            stats[s] = {"success": 0, "fail": 0}
        if l.get("success", True):
            stats[s]["success"] += 1
        else:
            stats[s]["fail"] += 1
    return stats
