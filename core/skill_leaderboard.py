"""
core/skill_leaderboard.py — Skill Performance Leaderboard

Track performance of each skill per domain.
Router uses leaderboard score combined with semantic score to pick the best skill.

Score = 0.65 * semantic_similarity + 0.35 * leaderboard_score
"""
import json
from loguru import logger
from config.settings import LEADERBOARD_FILE

DOMAIN_KEYWORDS = {
    "trading":  ["forex", "trade", "market", "price", "xauusd", "signal", "strategy", "backtest"],
    "research": ["search", "find", "pattern", "kb", "knowledge", "retrieve", "lookup"],
    "coding":   ["code", "function", "class", "bug", "implement", "refactor", "python", "debug"],
    "planning": ["plan", "decompose", "step", "sequence", "task", "schedule", "organize"],
    "memory":   ["remember", "recall", "store", "context", "history", "previous"],
    "analysis": ["analyze", "evaluate", "assess", "review", "audit", "compare"],
    "general":  [],
}


def _detect_domain(task: str) -> str:
    task_lower = task.lower()
    for domain, keywords in DOMAIN_KEYWORDS.items():
        if domain == "general":
            continue
        if any(kw in task_lower for kw in keywords):
            return domain
    return "general"


def _load() -> dict:
    if LEADERBOARD_FILE.exists():
        try:
            return json.loads(LEADERBOARD_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save(data: dict):
    LEADERBOARD_FILE.parent.mkdir(parents=True, exist_ok=True)
    LEADERBOARD_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def record(skill: str, task: str, success: bool, confidence: str):
    data   = _load()
    domain = _detect_domain(task)
    if skill not in data:
        data[skill] = {}
    if domain not in data[skill]:
        data[skill][domain] = {"success": 0, "fail": 0, "high": 0, "medium": 0, "low": 0}
    entry = data[skill][domain]
    if success:
        entry["success"] += 1
    else:
        entry["fail"] += 1
    entry[confidence.lower()] = entry.get(confidence.lower(), 0) + 1
    _save(data)


def score(skill: str, task: str) -> float:
    data   = _load()
    domain = _detect_domain(task)
    if skill not in data:
        return 0.5
    for d in [domain, "general"]:
        if d in data[skill]:
            entry = data[skill][d]
            total = entry["success"] + entry["fail"]
            if total >= 3:
                base_score = entry["success"] / total
                confidence_bonus = entry.get("high", 0) / max(total, 1) * 0.1
                return min(base_score + confidence_bonus, 1.0)
    return 0.5


def rerank(candidates: list[dict], task: str) -> list[dict]:
    for c in candidates:
        semantic        = c["score"]
        lb_score        = score(c["name"], task)
        c["lb_score"]   = round(lb_score, 4)
        c["final_score"] = round(0.65 * semantic + 0.35 * lb_score, 4)
    return sorted(candidates, key=lambda x: x["final_score"], reverse=True)


def top_skills(domain: str = "general", limit: int = 10) -> list[dict]:
    data    = _load()
    results = []
    for skill, domains in data.items():
        for d, entry in domains.items():
            if d != domain:
                continue
            total = entry["success"] + entry["fail"]
            if total < 3:
                continue
            results.append({
                "skill":        skill,
                "domain":       domain,
                "success_rate": round(entry["success"] / total, 2),
                "total":        total,
            })
    return sorted(results, key=lambda x: x["success_rate"], reverse=True)[:limit]
