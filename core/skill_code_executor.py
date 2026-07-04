"""
core/skill_code_executor.py — Real Code Execution for Skills

Extracts execute() from ## Code section in SKILL.md → runs it directly.
If code fails → LLM fixes it → retry (MAX 2x) → fallback to LLM execution.

Skills change from "tell LLM what to do" → "actually do it with Python"
"""
import re
import sys
import io
import traceback
import threading
from pathlib import Path
from loguru import logger

TIMEOUT_SEC = 30
MAX_RETRIES = 2


def extract_code(skill_md: str) -> str | None:
    """Extract Python code from ## Code section. Returns None if no execute() function."""
    m = re.search(r"## Code\s*\n```python\s*\n(.*?)```", skill_md, re.DOTALL)
    if not m:
        return None
    code = m.group(1).strip()
    if "def execute(" not in code:
        return None
    return code


def _run_with_timeout(fn, timeout: int):
    result_box = [None]
    error_box  = [None]

    def target():
        try:
            result_box[0] = fn()
        except Exception:
            error_box[0] = traceback.format_exc()

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout)

    if t.is_alive():
        return None, f"Timeout after {timeout}s"
    return result_box[0], error_box[0]


def _execute_code(code: str, task: str, context: str = "") -> dict:
    namespace: dict  = {}
    old_stdout       = sys.stdout
    sys.stdout       = captured = io.StringIO()

    try:
        exec(compile(code, "<skill-code>", "exec"), namespace)

        if "execute" not in namespace:
            return {"result": "", "success": False,
                    "error": "No execute() function found in ## Code section"}

        raw        = namespace["execute"](task, context)
        stdout_out = captured.getvalue()

        if isinstance(raw, dict):
            raw.setdefault("result", str(raw))
            raw.setdefault("success", True)
            return raw

        text = str(raw) if raw is not None else stdout_out
        return {"result": text, "success": bool(text.strip()), "data": raw}

    except Exception:
        tb = traceback.format_exc()
        return {"result": "", "success": False, "error": tb}
    finally:
        sys.stdout = old_stdout


def _fix_code(code: str, error: str, task: str) -> str:
    from core.llm import call_llm
    fixed = call_llm(
        """You are a Python debugger. Fix the code below so it works correctly.
REQUIREMENT: The code MUST define `def execute(task: str, context: str = "") -> str:` that returns a string result.
Return ONLY the corrected Python code — no explanation, no markdown fences.""",
        f"ERROR:\n{error[:600]}\n\nTASK CONTEXT: {task[:200]}\n\nBROKEN CODE:\n{code}",
        max_tokens=1200,
        use_pro=True,
    )
    fixed = re.sub(r"^```python\s*\n?", "", fixed.strip())
    fixed = re.sub(r"\n?```$", "", fixed)
    return fixed.strip()


def _update_skill_code(skill_path: Path, fixed_code: str):
    try:
        md      = skill_path.read_text(encoding="utf-8")
        new_block = f"## Code\n```python\n{fixed_code}\n```"
        updated = re.sub(r"## Code\s*\n```python\s*\n.*?```", new_block, md, flags=re.DOTALL)
        skill_path.write_text(updated, encoding="utf-8")
        logger.info(f"[CodeExec] Skill code updated: {skill_path.parent.name}")
    except Exception as e:
        logger.warning(f"[CodeExec] Could not update skill code: {e}")


def run(code: str, task: str, context: str = "", skill_path: Path = None) -> dict:
    """
    Main entry: run code → fix & retry on error → return result dict.
    Returns: {result: str, success: bool, ran_code: bool}
    """
    current_code = code

    for attempt in range(MAX_RETRIES + 1):
        result, timeout_err = _run_with_timeout(
            lambda c=current_code: _execute_code(c, task, context),
            TIMEOUT_SEC,
        )

        if timeout_err:
            logger.warning(f"[CodeExec] {timeout_err}")
            break

        if result and result.get("success"):
            logger.info(f"[CodeExec] OK (attempt {attempt + 1})")
            result["ran_code"] = True
            if attempt > 0 and skill_path:
                _update_skill_code(skill_path, current_code)
            return result

        err = result.get("error", "Unknown") if result else "None returned"
        logger.warning(f"[CodeExec] Attempt {attempt + 1} failed: {err[:100]}")

        if attempt < MAX_RETRIES:
            current_code = _fix_code(current_code, err, task)

    return {"result": "", "success": False, "ran_code": False,
            "error": "All code execution attempts failed"}
