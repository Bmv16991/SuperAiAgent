"""
core/orchestrator.py — SuperAgent Brain

Pipeline:
  1. EpisodicMemory.recall()   → past context
  2. WorkingMemory             → session context
  3. DeliberationEngine        → System 2 path selection
  4. SkillRouter.route()       → semantic skill matching
  5. SkillExecutor.run()       → execute skill (code or LLM)
  6. EpisodicMemory.store()    → learn from result
  7. Evolution.log()           → track performance
"""
import re
from loguru import logger
from core.llm import call_llm
from core.skill_executor import SkillExecutor, SkillResult
from core.skill_factory  import SkillFactory
import core.evolution        as evolution
import core.skill_router     as skill_router
import core.episodic_memory  as memory
import core.skill_leaderboard as leaderboard
from core.working_memory      import get_session
from core.knowledge_gap       import handle_failure
from core.deliberation_engine import deliberate, record_outcome, PATH_DIRECT, PATH_RECALL, PATH_SEARCH, PATH_DECOMPOSE


class Orchestrator:

    def __init__(self):
        self.executor = SkillExecutor()
        self.factory  = SkillFactory()

    def execute(self, task: str, context: str = "") -> str:
        logger.info(f"Task: {task[:80]}")

        # 1. Working memory + Episodic memory
        session  = get_session()
        wm_ctx   = session.context_str()
        episodes = memory.recall(task, top_k=3)
        past_ctx = memory.format_context(episodes)

        extra_ctx = "\n\n".join(filter(None, [wm_ctx, past_ctx, context]))
        if extra_ctx:
            context = extra_ctx

        # 2. Deliberation — System 2 thinking
        skills = self.executor.list_skills()
        delib  = deliberate(task, context, available_skills=skills)

        logger.info(
            f"[Orchestrator] Path: {delib.chosen_path} "
            f"(conf={delib.confidence:.0%}) | {delib.reasoning[:60]}"
        )

        # 3. Execute chosen path
        if delib.chosen_path == PATH_DECOMPOSE:
            from core.task_decomposer import execute_plan
            decomposed = execute_plan(task, context)
            if decomposed:
                record_outcome(delib.task_hash, actual_success=True)
                memory.store(task, "decomposed-plan", decomposed, success=True, context=context[:200])
                return self._format_decomposed(task, decomposed)

        if delib.chosen_path == PATH_SEARCH:
            try:
                import core.kb_learner as kb
                kb_results = kb.search(task, top_k=5)
                if kb_results:
                    kb_ctx  = "\n".join(f"- {r['content'][:200]}" for r in kb_results)
                    context = (context + f"\n\nKB SEARCH RESULTS:\n{kb_ctx}").strip()
                    logger.info(f"[Orchestrator] PATH_SEARCH: injected {len(kb_results)} KB results")
            except Exception as e:
                logger.warning(f"[Orchestrator] KB search failed: {e}")

        if delib.chosen_path == PATH_RECALL:
            extra = memory.recall(task, top_k=5)
            if extra:
                extra_ctx = memory.format_context(extra)
                context   = (context + "\n\n" + extra_ctx).strip()

        # 4. Semantic skill routing
        skill_name = self._route_skill(task)
        cls        = f"{delib.chosen_path}→{skill_name or 'NEW'}"

        # 5. No skill found → create new (unless meta-task)
        if not skill_name:
            if self._is_meta_task(task):
                logger.info(f"[Orchestrator] Meta-task detected — LLM direct")
                raw = call_llm(
                    "You are an AI assistant. Answer the task concisely and helpfully.",
                    f"Task: {task}\n\nContext:\n{context[:800]}" if context else f"Task: {task}",
                    max_tokens=1000,
                )
                result = SkillResult(task=task, skill="llm-direct", result=raw, raw=raw,
                                     success=bool(raw.strip()), confidence="MEDIUM", next_step="")
                memory.store(task, "llm-direct", raw, True, context[:200])
                return self._format(result, "META→LLM-DIRECT", "llm-direct")

            logger.info(f"[Orchestrator] No skill matched — Skill Factory creating")
            skill_name = self.factory.create(task)
            cls        = "NEW→CREATED"

        # 6. Execute
        result = self.executor.run(skill_name, task, context)

        # 7. Record outcomes
        record_outcome(delib.task_hash, actual_success=result.success)
        session.push_result(task, result.result, skill_name)
        memory.store(task, skill_name, result.result, result.success, context[:200])
        leaderboard.record(skill_name, task, result.success, result.confidence)

        if not result.success:
            handle_failure(task, skill_name, result.result)

        evolution.log(
            skill=skill_name, task=task, success=result.success,
            confidence=result.confidence, result_summary=result.result[:250],
        )

        return self._format(result, cls, skill_name)

    def _is_meta_task(self, task: str) -> bool:
        """Detect introspection/meta tasks — language-agnostic via semantic similarity."""
        t = task.lower()

        SYSTEM_KEYWORDS = [
            "self-reflection", "evolution_log", "skill stats",
            "kb targeted search", "kb search: find",
            "urgent: skill", "failure rate",
        ]
        if any(kw in t for kw in SYSTEM_KEYWORDS):
            return True

        META_PROTOTYPES = [
            "list all available skills in the system",
            "what skills do you have",
            "show me skills about a topic",
            "search for skills in the agent",
            "what can this AI agent do",
            "show capabilities and skills",
            "find skills related to machine learning",
            "which skills are available",
            "tell me what you can do",
        ]
        try:
            from core.skill_router import _get_encoder
            import numpy as np
            enc        = _get_encoder()
            task_vec   = enc.encode(task[:300], normalize_embeddings=True, show_progress_bar=False)
            proto_vecs = enc.encode(META_PROTOTYPES, normalize_embeddings=True, show_progress_bar=False)
            if float((proto_vecs @ task_vec).max()) > 0.58:
                return True
        except Exception:
            pass

        return False

    def _route_skill(self, task: str) -> str | None:
        # 1. Semantic
        routed = skill_router.best_skill(task)
        if routed:
            logger.info(f"[Router] Semantic match: {routed}")
            return routed

        # 2. Keyword table
        task_lower = task.lower()
        for entry in _ROUTING_TABLE:
            for kw in entry["keywords"]:
                if kw in task_lower:
                    name = entry["skill"]
                    if self.executor.find_skill_path(name):
                        logger.info(f"[Router] Keyword match: {name}")
                        return name

        # 3. LLM classify
        return self._llm_classify(task)

    def _llm_classify(self, task: str) -> str | None:
        skills = self.executor.list_skills()
        if not skills:
            return None
        raw = call_llm(
            f"""Map this task to the best available skill.
Available skills: {", ".join(skills)}

Reply ONLY:
SKILL: <exact-skill-name or "none">
REASON: <one sentence>""",
            f"Task: {task}",
            max_tokens=60,
        )
        m = re.search(r"SKILL:\s*(\S+)", raw)
        if not m:
            return None
        name = m.group(1).strip()
        if name in ("none", "new", "") or not self.executor.find_skill_path(name):
            return None
        logger.info(f"[Router] LLM match: {name}")
        return name

    @staticmethod
    def _format(result: SkillResult, cls: str, skill_name: str) -> str:
        sep  = "=" * 52
        line = "-" * 52
        ok   = "YES" if result.success else "NO"
        return (
            f"\n{sep}\n"
            f"  SUPERAI RESULT\n"
            f"{sep}\n"
            f"  Task       : {result.task[:70]}\n"
            f"  Class      : {cls}\n"
            f"  Skill Used : {skill_name}\n"
            f"  Confidence : {result.confidence}\n"
            f"  Success    : {ok}\n"
            f"{line}\n"
            f"{result.result}\n"
            f"{line}\n"
            f"  Next Step  : {result.next_step}\n"
            f"{sep}\n"
        )

    @staticmethod
    def _format_decomposed(task: str, result: str) -> str:
        sep  = "=" * 52
        line = "-" * 52
        return (
            f"\n{sep}\n"
            f"  SUPERAI RESULT (Multi-Step Plan)\n"
            f"{sep}\n"
            f"  Task       : {task[:70]}\n"
            f"  Class      : DECOMPOSED\n"
            f"{line}\n"
            f"{result}\n"
            f"{sep}\n"
        )


# Keyword routing fallback table
_ROUTING_TABLE = [
    {"skill": "code-reviewer", "keywords": ["review code", "code review", "audit code", "find bugs"]},
    {"skill": "rate-limiter",  "keywords": ["rate limit", "rate limiting", "throttle api"]},
]
