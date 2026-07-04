"""
core/knowledge_gap.py — Knowledge Gap Detection

When a task fails → detect what knowledge is missing
→ search KB automatically → fill the gap → upgrade/create skill

Flow:
  task fails → detect_gap() → search KB → upgrade skill with new knowledge
"""
import json
from datetime import datetime
from loguru import logger
from core.llm import call_llm
from config.settings import KNOWLEDGE_GAPS_FILE


def _load_gaps() -> list[dict]:
    if KNOWLEDGE_GAPS_FILE.exists():
        try:
            return json.loads(KNOWLEDGE_GAPS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _save_gaps(gaps: list[dict]):
    KNOWLEDGE_GAPS_FILE.parent.mkdir(parents=True, exist_ok=True)
    KNOWLEDGE_GAPS_FILE.write_text(json.dumps(gaps[-100:], ensure_ascii=False, indent=2))


def detect_gap(task: str, failed_result: str) -> str | None:
    """Analyze why the task failed and what knowledge is missing."""
    raw = call_llm(
        """Analyze why an AI agent failed this task.
Identify if it's a KNOWLEDGE GAP (missing domain knowledge) or OTHER issue.

Reply EXACTLY:
IS_GAP: YES|NO
GAP_TOPIC: <what knowledge is missing, one phrase, or "none">
SEARCH_QUERY: <best search query to find this knowledge in a GitHub repo database>""",
        f"Task: {task}\nFailed Output: {failed_result[:400]}",
        max_tokens=100,
    )

    if "IS_GAP: NO" in raw.upper():
        return None

    import re
    m = re.search(r"GAP_TOPIC:\s*(.+)", raw)
    return m.group(1).strip() if m else None


def fill_gap(task: str, skill_name: str, gap_topic: str) -> bool:
    """Search KB for missing knowledge and upgrade the skill."""
    import core.kb_learner as kb
    from core.skill_factory import SkillFactory

    logger.info(f"[Gap] Searching KB for: '{gap_topic}'")
    results = kb.search(gap_topic, top_k=5)

    if not results:
        logger.info(f"[Gap] No KB results for: {gap_topic}")
        _log_unfilled_gap(task, gap_topic)
        return False

    knowledge = "\n\n".join(
        f"Source: {r['meta'].get('repo', '?')}\n{r['content']}"
        for r in results
    )

    logger.info(f"[Gap] Found {len(results)} KB entries → upgrading skill '{skill_name}'")
    reason = (
        f"Knowledge gap detected for task: {task}\n"
        f"Gap topic: {gap_topic}\n\n"
        f"Knowledge from KB:\n{knowledge[:1500]}"
    )

    try:
        SkillFactory().upgrade(skill_name, reason)
        logger.success(f"[Gap] Filled gap in '{skill_name}' with KB knowledge")
        return True
    except Exception as e:
        logger.error(f"[Gap] Upgrade failed: {e}")
        _log_unfilled_gap(task, gap_topic)
        return False


def _log_unfilled_gap(task: str, gap_topic: str):
    gaps = _load_gaps()
    gaps.append({
        "ts":        datetime.now().isoformat(),
        "task":      task[:200],
        "gap_topic": gap_topic,
        "filled":    False,
    })
    _save_gaps(gaps)
    logger.info(f"[Gap] Logged unfilled gap: '{gap_topic}'")


def handle_failure(task: str, skill_name: str, failed_result: str) -> bool:
    """Entry point: called by Orchestrator when a task fails."""
    gap = detect_gap(task, failed_result)
    if not gap or gap.lower() in ("none", "n/a", ""):
        logger.debug(f"[Gap] No knowledge gap detected")
        return False
    logger.info(f"[Gap] Knowledge gap: '{gap}' → filling...")
    return fill_gap(task, skill_name, gap)


def get_unfilled_gaps(limit: int = 10) -> list[dict]:
    gaps = _load_gaps()
    return [g for g in gaps if not g.get("filled")][-limit:]
