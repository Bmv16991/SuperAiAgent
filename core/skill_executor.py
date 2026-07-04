"""
core/skill_executor.py
Read SKILL.md + task → execute skill (real Python code or LLM) → structured result
"""
import re
from dataclasses import dataclass, field
from pathlib import Path
from loguru import logger
from core.llm import call_llm
from config.settings import SKILLS_DIR


@dataclass
class SkillResult:
    skill:      str
    task:       str
    result:     str
    confidence: str
    next_step:  str
    success:    bool
    raw:        str = field(repr=False)

    def __str__(self) -> str:
        sep = "=" * 50
        return (
            f"\n{sep}\n"
            f"SKILL RESULT\n"
            f"{sep}\n"
            f"Skill      : {self.skill}\n"
            f"Confidence : {self.confidence}\n"
            f"Success    : {'YES' if self.success else 'NO'}\n"
            f"{'-' * 50}\n"
            f"{self.result}\n"
            f"{'-' * 50}\n"
            f"Next Step  : {self.next_step}\n"
            f"{sep}"
        )


class SkillExecutor:

    def find_skill_path(self, skill_name: str) -> Path | None:
        """Find SKILL.md — checks top-level and all subdirectories."""
        # Direct match
        direct = SKILLS_DIR / skill_name / "SKILL.md"
        if direct.exists():
            return direct
        # Search subdirectories (domain skills)
        for subdir in SKILLS_DIR.iterdir():
            if subdir.is_dir():
                candidate = subdir / skill_name / "SKILL.md"
                if candidate.exists():
                    return candidate
        return None

    def list_skills(self) -> list[str]:
        skills = set()
        if not SKILLS_DIR.exists():
            return []
        for item in SKILLS_DIR.rglob("SKILL.md"):
            skills.add(item.parent.name)
        return sorted(skills)

    def skill_summary(self) -> str:
        return ", ".join(self.list_skills()) or "none"

    def run(self, skill_name: str, task: str, context: str = "") -> SkillResult:
        path = self.find_skill_path(skill_name)
        if not path:
            return SkillResult(
                skill=skill_name, task=task,
                result=f"Skill '{skill_name}' not found. Available: {self.skill_summary()}",
                confidence="LOW", next_step=f"Create skill directory: skills/{skill_name}/SKILL.md",
                success=False, raw=""
            )

        skill_md = path.read_text(encoding="utf-8")
        logger.info(f"Executing skill: {skill_name} ({len(skill_md)} chars)")

        # Try real code execution first
        try:
            from core.skill_code_executor import extract_code, run as run_code
            code = extract_code(skill_md)
            if code:
                logger.info(f"[Executor] Found ## Code — running Python directly")
                code_result = run_code(code, task, context, skill_path=path)
                if code_result.get("ran_code") and code_result.get("success"):
                    logger.success(f"[Executor] Code execution succeeded: {skill_name}")
                    return SkillResult(
                        skill=skill_name, task=task,
                        result=code_result.get("result", ""),
                        confidence="HIGH",
                        next_step=code_result.get("next_step", "Code executed successfully"),
                        success=True,
                        raw=str(code_result),
                    )
                logger.info(f"[Executor] Code failed — falling back to LLM")
        except Exception as e:
            logger.warning(f"[Executor] Code execution skipped: {e}")

        # LLM fallback
        system = f"""You are an autonomous AI executing the skill: **{skill_name}**

Follow the steps defined in this SKILL.md EXACTLY:

{skill_md}

After completing the task, respond in EXACT format:
RESULT:
<your complete output>
CONFIDENCE: HIGH|MEDIUM|LOW
NEXT_STEP: <one specific actionable item>
SUCCESS: true|false"""

        user_msg = f"TASK: {task}"
        if context:
            user_msg += f"\n\nCONTEXT:\n{context}"

        try:
            raw = call_llm(system, user_msg, max_tokens=3000)
            return self._parse(raw, skill_name, task)
        except Exception as e:
            logger.error(f"Execution error [{skill_name}]: {e}")
            return SkillResult(
                skill=skill_name, task=task,
                result=f"LLM execution failed: {e}",
                confidence="LOW", next_step="Check API keys in .env",
                success=False, raw=str(e)
            )

    def _parse(self, raw: str, skill: str, task: str) -> SkillResult:
        result_m = re.search(r"RESULT:\s*(.*?)(?=\nCONFIDENCE:|$)", raw, re.DOTALL)
        conf_m   = re.search(r"CONFIDENCE:\s*(HIGH|MEDIUM|LOW)", raw)
        next_m   = re.search(r"NEXT_STEP:\s*(.*?)(?=\n[A-Z]|$)", raw)
        succ_m   = re.search(r"SUCCESS:\s*(true|false)", raw, re.IGNORECASE)

        return SkillResult(
            skill=skill,
            task=task,
            result=(result_m.group(1).strip() if result_m else raw),
            confidence=(conf_m.group(1) if conf_m else "MEDIUM"),
            next_step=(next_m.group(1).strip() if next_m else ""),
            success=(succ_m.group(1).lower() == "true" if succ_m else True),
            raw=raw,
        )
