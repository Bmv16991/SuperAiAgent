"""
core/skill_factory.py — Voyager-style Skill Factory

Flow:
  1. Semantic search existing skills for similar content
  2a. similarity >= 0.85 → upgrade existing skill (absorb new knowledge)
  2b. similarity 0.55-0.84 → create new skill WITH related skills as context
  2c. similarity < 0.55 → create brand new skill from scratch
"""
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from loguru import logger
from core.llm import call_llm
from config.settings import SKILLS_DIR, ST_CACHE_PATH, EMBED_MODEL

UPGRADE_THRESHOLD = 0.85
CONTEXT_THRESHOLD = 0.55
MAX_NAME_LEN      = 255

_encoder = None


def _trim_name(name: str) -> str:
    if len(name) <= MAX_NAME_LEN:
        return name
    trimmed   = name[:MAX_NAME_LEN]
    last_dash = trimmed.rfind("-")
    return trimmed[:last_dash] if last_dash > 10 else trimmed


def _get_encoder():
    global _encoder
    if _encoder is None:
        os.environ["TOKENIZERS_PARALLELISM"] = "false"
        os.environ["HF_HOME"] = ST_CACHE_PATH
        from sentence_transformers import SentenceTransformer
        _encoder = SentenceTransformer(EMBED_MODEL, device="cpu")
    return _encoder


class SkillFactory:

    def create(self, task: str) -> str:
        """Voyager-style: semantic search first, then create or upgrade."""
        similar = self._find_similar_skills(task)

        if similar and similar[0]["score"] >= UPGRADE_THRESHOLD:
            top = similar[0]
            logger.info(f"[Voyager] '{top['name']}' score={top['score']:.2f} ≥ {UPGRADE_THRESHOLD} → upgrading")
            self.upgrade(top["name"], f"New task absorbed: {task}")
            return top["name"]

        skill_name = self._generate_name(task)
        skill_dir  = SKILLS_DIR / skill_name
        skill_path = skill_dir / "SKILL.md"

        if skill_path.exists():
            logger.info(f"Skill already exists: {skill_name}")
            return skill_name

        top_score = similar[0]["score"] if similar else 0.0
        logger.info(f"[Voyager] Creating '{skill_name}' (top similarity={top_score:.2f})")
        skill_md = self._generate_skill_md(task, skill_name, similar)

        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_path.write_text(skill_md, encoding="utf-8")
        logger.success(f"New skill ready: {skill_name}")
        return skill_name

    def upgrade(self, skill_name: str, reason: str) -> bool:
        """Full rewrite of SKILL.md absorbing new KB insights."""
        from core.skill_executor import SkillExecutor
        executor = SkillExecutor()
        path     = executor.find_skill_path(skill_name)
        if not path:
            logger.error(f"Cannot upgrade — skill not found: {skill_name}")
            return False

        # Novelty Gate: reject if too similar to recent accepted mutation
        try:
            from core.hermes_memory import novelty_check, store as mem_store
            proposal = f"{skill_name}: {reason}"
            gate = novelty_check(proposal, tier="procedural", threshold=0.92)
            if not gate["novel"]:
                logger.warning(f"[NoveltyGate] SKIP upgrade '{skill_name}' — {gate['reason']}")
                return False
        except Exception as e:
            logger.debug(f"[NoveltyGate] check skipped: {e}")

        current = path.read_text(encoding="utf-8")
        core_content = re.sub(
            r"\n---\n## KB Insight \[.*?\]\n[\s\S]*?(?=\n---|\Z)", "", current
        ).rstrip()

        system = """You are a Skill Upgrader (Voyager-style).
Rewrite the given SKILL.md by fully absorbing the provided KB insights.
Rules:
- Keep frontmatter (---) and ## structure intact
- Integrate insights naturally — add/refine/reorder steps as needed
- Return ONLY the complete new SKILL.md. No explanation."""

        new_md = call_llm(
            system,
            f"KB INSIGHTS:\n{reason}\n\nCURRENT SKILL:\n{core_content}",
            max_tokens=3000,
            tier="flash",
        )
        path.write_text(new_md, encoding="utf-8")

        try:
            from core.hermes_memory import store as mem_store
            mem_store(
                "procedural",
                f"{skill_name}: {reason}",
                {"skill": skill_name, "action": "upgrade",
                 "mutation_id": skill_name + "-" + datetime.now().strftime("%Y%m%d%H%M")},
                importance=0.6,
            )
        except Exception:
            pass

        logger.success(f"Upgraded: {skill_name}")
        return True

    def _find_similar_skills(self, query: str, top_k: int = 3) -> list:
        try:
            import numpy as np
            encoder = _get_encoder()
            skills  = []

            for skill_md_path in SKILLS_DIR.rglob("SKILL.md"):
                content = skill_md_path.read_text(encoding="utf-8")
                snippet = self._extract_purpose(content)
                skills.append({"name": skill_md_path.parent.name, "content": content, "snippet": snippet})

            if not skills:
                return []

            query_vec  = encoder.encode(query[:500], normalize_embeddings=True)
            skill_vecs = encoder.encode([s["snippet"] for s in skills], normalize_embeddings=True)
            sims = (query_vec @ skill_vecs.T).tolist()

            results = []
            for i, score in enumerate(sims):
                if score >= CONTEXT_THRESHOLD:
                    results.append({"name": skills[i]["name"], "content": skills[i]["content"][:600], "score": round(score, 4)})

            return sorted(results, key=lambda x: x["score"], reverse=True)[:top_k]

        except Exception as e:
            logger.warning(f"[Voyager] Semantic search failed: {e}")
            return []

    def _extract_purpose(self, skill_md: str) -> str:
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
        return " ".join(lines)[:400] or skill_md[:400]

    def _generate_name(self, task: str) -> str:
        m = re.match(r"Skill:\s*([^\n]+)", task)
        if m:
            candidate = m.group(1).strip().lower().replace(" ", "-").replace("_", "-")
            candidate = re.sub(r"[^a-z0-9-]", "", candidate)
            candidate = _trim_name(candidate)
            if len(candidate) >= 3:
                return candidate

        system = """Generate a short kebab-case skill name (3-4 words max).
Return ONLY the name. No quotes, no explanation.
Examples: market-analyzer, rate-limiter, code-reviewer, kb-searcher"""
        name = call_llm(system, f"Task: {task[:300]}", max_tokens=30)
        name = name.strip().lower().replace(" ", "-").replace("_", "-")
        name = re.sub(r"[^a-z0-9-]", "", name)
        name = _trim_name(name)
        if not name or len(name) < 3:
            import hashlib
            name = "skill-" + hashlib.md5(task[:200].encode()).hexdigest()[:8]
        return name

    def _generate_skill_md(self, task: str, skill_name: str, similar: list) -> str:
        today = datetime.now().strftime("%Y-%m-%d")

        if similar:
            parts = [f"[{s['name']} | similarity={s['score']:.2f}]\n{s['content']}" for s in similar]
            related_block = "RELATED EXISTING SKILLS:\n" + "\n---\n".join(parts)
        else:
            related_block = "No similar skills found — create from scratch."

        system = f"""You are a Skill Factory (Voyager-style).
Design a complete, working SKILL.md for a new autonomous skill.

{related_block}

The SKILL.md must follow this EXACT structure:
---
name: <skill-name>
version: 1.0.0
description: "<one-line description>"
created_by: skill-factory
created: {today}
---

# SKILL: <skill-name>

## Purpose
<clear explanation of what this skill does and when to use it>

## Step 1 — <step name>
<Detailed instructions>

## Step 2 — Output
RESULT:
<output format>
CONFIDENCE: HIGH|MEDIUM|LOW
NEXT_STEP: <one action>
SUCCESS: true|false

## Code
```python
def execute(task: str, context: str = "") -> dict:
    # Real Python implementation — no LLM needed if this succeeds
    # Returns: {{"result": str, "success": bool}}
    result_text = f"Executed: {{task[:60]}}"
    return {{"result": result_text, "success": True, "next_step": "Review output"}}
```

IMPORTANT: Write a REAL implementation in ## Code using Python standard libraries."""

        return call_llm(
            system,
            f"Skill name: {skill_name}\nTask: {task}\nDate: {today}",
            max_tokens=4000,
        )
