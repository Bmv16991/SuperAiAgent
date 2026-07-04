"""
core/deliberation_engine.py — System 2 Deliberation Engine

"Think before acting" — instead of executing immediately,
generate N paths → predict outcome → choose the best one.

Flow:
  task arrives
    → generate 2-3 paths (DIRECT / RECALL / SEARCH / DECOMPOSE)
    → predict outcome of each path using memory + past predictions
    → choose the best path
    → execute
    → record: "predicted X, got Y" → calibrate for next time

The longer it runs → better predictions → better decisions = AI "experience"
"""
import json
import re
import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from loguru import logger
from core.llm import call_llm
from config.settings import DELIBERATION_LOG as _DELIB_LOG

DELIB_LOG = _DELIB_LOG
MAX_LOG   = 200

PATH_DIRECT    = "DIRECT"
PATH_RECALL    = "RECALL"
PATH_SEARCH    = "SEARCH"
PATH_DECOMPOSE = "DECOMPOSE"


@dataclass
class PathOption:
    path_type:         str
    description:       str
    predicted_outcome: str
    success_prob:      float
    speed:             str
    risks:             str


@dataclass
class DeliberationResult:
    task:              str
    chosen_path:       str
    reasoning:         str
    predicted_outcome: str
    confidence:        float
    alternatives:      list[PathOption] = field(default_factory=list)
    ts:                str = field(default_factory=lambda: datetime.now().isoformat())
    task_hash:         str = ""

    def __post_init__(self):
        self.task_hash = hashlib.md5(self.task[:200].encode()).hexdigest()[:8]


def _load_log() -> list[dict]:
    if DELIB_LOG.exists():
        try:
            return json.loads(DELIB_LOG.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _save_log(log: list[dict]):
    DELIB_LOG.parent.mkdir(parents=True, exist_ok=True)
    DELIB_LOG.write_text(json.dumps(log[-MAX_LOG:], ensure_ascii=False, indent=2))


def _past_prediction_accuracy() -> float:
    log      = _load_log()
    verified = [e for e in log if "actual_success" in e]
    if len(verified) < 5:
        return 0.5
    correct = sum(
        1 for e in verified
        if (e["predicted_success"] > 0.6) == e["actual_success"]
    )
    return round(correct / len(verified), 2)


def _should_deliberate(task: str, context: str) -> bool:
    if len(task) < 40:
        return False
    try:
        import core.episodic_memory as memory
        episodes = memory.recall(task, top_k=1)
        if episodes and episodes[0]["success"] and episodes[0]["similarity"] > 0.85:
            logger.debug(f"[Deliberation] Skipping — similar successful past task found")
            return False
    except Exception:
        pass
    return True


def deliberate(task: str, context: str = "", available_skills: list[str] = None) -> DeliberationResult:
    """System 2 thinking: generate paths → predict → choose best."""
    if not _should_deliberate(task, context):
        return DeliberationResult(
            task=task,
            chosen_path=PATH_DIRECT,
            reasoning="Simple/familiar task — direct execution",
            predicted_outcome="High likelihood of success based on past experience",
            confidence=0.8,
        )

    past_accuracy = _past_prediction_accuracy()
    skills_str    = ", ".join((available_skills or [])[:15]) or "none loaded yet"

    episode_ctx = ""
    try:
        import core.episodic_memory as memory
        episodes = memory.recall(task, top_k=3)
        if episodes:
            parts = []
            for ep in episodes:
                status = "SUCCESS" if ep["success"] else "FAILED"
                parts.append(f"- [{status}] {ep['task'][:80]} → {ep['result'][:100]}")
            episode_ctx = "Past similar tasks:\n" + "\n".join(parts)
    except Exception:
        pass

    system = f"""You are a Deliberation Engine — System 2 thinking for an AI agent.
Your job: analyze a task and evaluate 2-3 distinct approaches BEFORE executing.

Current prediction accuracy: {past_accuracy:.0%}
Available skills: {skills_str}
{episode_ctx}

Reply in EXACT format:

PATH_COUNT: <2 or 3>

PATH_1_TYPE: DIRECT|RECALL|SEARCH|DECOMPOSE
PATH_1_DESC: <what this approach does>
PATH_1_OUTCOME: <predicted result>
PATH_1_SUCCESS_PROB: <0.0-1.0>
PATH_1_SPEED: fast|medium|slow
PATH_1_RISKS: <what could go wrong>

PATH_2_TYPE: ...
PATH_2_DESC: ...
PATH_2_OUTCOME: ...
PATH_2_SUCCESS_PROB: ...
PATH_2_SPEED: ...
PATH_2_RISKS: ...

CHOSEN_PATH: <1, 2, or 3>
REASONING: <why this path is best>
PREDICTED_OUTCOME: <specific prediction>
CONFIDENCE: <0.0-1.0>"""

    user = f"Task: {task}\nContext: {context[:300] if context else 'none'}"

    try:
        raw = call_llm(system, user, max_tokens=800, use_pro=True)
    except Exception as e:
        logger.warning(f"[Deliberation] LLM failed: {e} — using direct path")
        return DeliberationResult(
            task=task, chosen_path=PATH_DIRECT,
            reasoning=f"Deliberation failed: {e}",
            predicted_outcome="Unknown", confidence=0.5,
        )

    def field_val(name: str) -> str:
        m = re.search(rf"{name}:\s*(.*?)(?=\nPATH_|\nCHOSEN_|\nREASONING:|\nPREDICTED|\nCONFIDENCE:|$)",
                      raw, re.DOTALL)
        return m.group(1).strip() if m else ""

    def path_field(n: int, name: str) -> str:
        m = re.search(rf"PATH_{n}_{name}:\s*(.*?)(?=\nPATH_|\nCHOSEN_|$)", raw, re.DOTALL)
        return m.group(1).strip() if m else ""

    count_m = re.search(r"PATH_COUNT:\s*(\d)", raw)
    count   = int(count_m.group(1)) if count_m else 2

    alternatives = []
    for i in range(1, count + 1):
        try:
            prob = float(path_field(i, "SUCCESS_PROB"))
        except Exception:
            prob = 0.5
        alternatives.append(PathOption(
            path_type         = path_field(i, "TYPE").upper() or PATH_DIRECT,
            description       = path_field(i, "DESC"),
            predicted_outcome = path_field(i, "OUTCOME"),
            success_prob      = prob,
            speed             = path_field(i, "SPEED") or "medium",
            risks             = path_field(i, "RISKS"),
        ))

    chosen_m = re.search(r"CHOSEN_PATH:\s*(\d)", raw)
    chosen_i = int(chosen_m.group(1)) - 1 if chosen_m else 0
    chosen_i = max(0, min(chosen_i, len(alternatives) - 1))

    chosen_type = alternatives[chosen_i].path_type if alternatives else PATH_DIRECT

    try:
        confidence = float(re.search(r"CONFIDENCE:\s*([\d.]+)", raw).group(1))
    except Exception:
        confidence = 0.6

    result = DeliberationResult(
        task              = task,
        chosen_path       = chosen_type,
        reasoning         = field_val("REASONING"),
        predicted_outcome = field_val("PREDICTED_OUTCOME"),
        confidence        = confidence,
        alternatives      = alternatives,
    )

    logger.info(
        f"[Deliberation] Chose: {chosen_type} (conf={confidence:.0%}) | "
        f"Task: {task[:50]}"
    )

    _append_log({
        "ts":               result.ts,
        "task_hash":        result.task_hash,
        "task":             task[:200],
        "chosen_path":      chosen_type,
        "predicted_success": confidence,
        "reasoning":        result.reasoning[:200],
    })

    return result


def record_outcome(task_hash: str, actual_success: bool):
    """Record actual outcome after execution — used to calibrate prediction accuracy."""
    log = _load_log()
    for entry in reversed(log):
        if entry.get("task_hash") == task_hash and "actual_success" not in entry:
            entry["actual_success"] = actual_success
            predicted_high = entry.get("predicted_success", 0.5) > 0.6
            entry["prediction_correct"] = (predicted_high == actual_success)
            break
    _save_log(log)


def _append_log(entry: dict):
    log = _load_log()
    log.append(entry)
    _save_log(log)
