"""
core/task_decomposer.py — Task Decomposer / Planner

Breaks complex tasks into ordered subtasks → executes each step sequentially.
Each step's output becomes context for the next step.

Flow:
  1. LLM decides if task needs decomposition
  2. If yes → build plan (ordered subtasks + skill hints)
  3. Execute each step with accumulated context
  4. Synthesize final result
"""
import re
from loguru import logger
from core.llm import call_llm
from core.skill_executor import SkillExecutor
from core.skill_factory import SkillFactory
import core.skill_router as router

MAX_STEPS = 6


def _should_decompose(task: str) -> bool:
    if len(task) < 60:
        return False
    raw = call_llm(
        "Does this task require multiple sequential steps to complete properly?\nReply ONLY: YES or NO",
        f"Task: {task}",
        max_tokens=5,
    )
    return "YES" in raw.upper()


def _build_plan(task: str, available_skills: list[str]) -> list[dict]:
    top_relevant = router.route(task, top_k=15)
    if top_relevant:
        skills_str = ", ".join(r["name"] for r in top_relevant)
    else:
        skills_str = ", ".join(available_skills[:20]) or "none"

    raw = call_llm(
        f"""You are a Task Planner for an AI agent system.
Break the given task into sequential subtasks (max {MAX_STEPS} steps).
Each step should be executable by ONE skill.

Available skills: {skills_str}

Reply in EXACT format:
STEPS: <count>
STEP_1_TASK: <specific subtask description>
STEP_1_SKILL: <best skill name from available list, or "new" if none fits>
STEP_2_TASK: ...
STEP_2_SKILL: ...
(repeat for all steps)""",
        f"Main task: {task}",
        max_tokens=600,
    )

    steps_m = re.search(r"STEPS:\s*(\d+)", raw)
    count   = min(int(steps_m.group(1)) if steps_m else 3, MAX_STEPS)

    plan = []
    for i in range(1, count + 1):
        t_m = re.search(rf"STEP_{i}_TASK:\s*(.*?)(?=\nSTEP_|\Z)", raw, re.DOTALL)
        s_m = re.search(rf"STEP_{i}_SKILL:\s*(\S+)", raw)
        if not t_m:
            break
        plan.append({
            "step":       i,
            "subtask":    t_m.group(1).strip(),
            "skill_hint": s_m.group(1).strip() if s_m else "new",
        })

    return plan


def execute_plan(task: str, context: str = "") -> str:
    """Full decomposition pipeline: task → plan → execute steps → combine results."""
    executor = SkillExecutor()
    factory  = SkillFactory()
    skills   = executor.list_skills()

    if not _should_decompose(task):
        logger.info(f"[Decomposer] Single-step task — skip")
        return None

    logger.info(f"[Decomposer] Building plan for: {task[:70]}")
    plan = _build_plan(task, skills)

    if not plan:
        return None

    logger.info(f"[Decomposer] Plan: {len(plan)} steps")

    step_results    = []
    running_context = context

    for step in plan:
        subtask    = step["subtask"]
        skill_hint = step["skill_hint"]
        skill_name = None

        if skill_hint and skill_hint != "new" and executor.find_skill_path(skill_hint):
            hint_results = [r for r in router.route(subtask, top_k=5) if r["name"] == skill_hint]
            if hint_results and hint_results[0]["score"] >= 0.45:
                skill_name = skill_hint

        if not skill_name:
            routed = router.best_skill(subtask)
            if routed:
                skill_name = routed

        if not skill_name:
            skill_name = factory.create(subtask)

        logger.info(f"[Decomposer] Step {step['step']}: skill='{skill_name}'")

        full_context = running_context
        if step_results:
            prev = step_results[-1]
            full_context += f"\n\nPREVIOUS STEP RESULT:\n{prev['result'][:400]}"

        result = executor.run(skill_name, subtask, full_context)
        step_results.append({
            "step":    step["step"],
            "subtask": subtask,
            "skill":   skill_name,
            "result":  result.result,
            "success": result.success,
        })

        logger.info(f"[Decomposer] Step {step['step']}: {'OK' if result.success else 'FAIL'}")

    steps_summary = "\n\n".join(
        f"Step {s['step']} [{s['skill']}]:\n{s['result'][:300]}"
        for s in step_results
    )

    return call_llm(
        "Synthesize the results from all execution steps into ONE coherent final answer.",
        f"Original task: {task}\n\nStep Results:\n{steps_summary}",
        max_tokens=1500,
    )
