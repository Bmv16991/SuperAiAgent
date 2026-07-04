"""
core/working_memory.py — Session Working Memory

Remembers context within a single session (short-term).
Complements EpisodicMemory which persists across sessions.

Use cases:
  - "Asked about X earlier → continue from X"
  - Agent completes step 1 → step 2 knows the result
  - Set a goal → subsequent tasks work toward it

Auto-clears after IDLE_TIMEOUT seconds of inactivity.
"""
import time
from datetime import datetime
from loguru import logger

IDLE_TIMEOUT = 7200  # 2 hours
MAX_ENTRIES  = 20


class WorkingMemory:

    def __init__(self):
        self._store: dict[str, dict] = {}
        self._last_active = time.time()
        self._context_stack: list[str] = []

    def set(self, key: str, value: str):
        self._touch()
        self._store[key] = {"value": str(value)[:500], "ts": datetime.now().isoformat()}
        logger.debug(f"[WM] set: {key} = {str(value)[:60]}")

    def get(self, key: str, default: str = "") -> str:
        self._touch()
        return self._store.get(key, {}).get("value", default)

    def push_result(self, task: str, result: str, skill: str):
        self._touch()
        entry = f"[{skill}] {task[:60]}: {result[:200]}"
        self._context_stack.append(entry)
        if len(self._context_stack) > MAX_ENTRIES:
            self._context_stack.pop(0)

    def clear(self):
        self._store.clear()
        self._context_stack.clear()
        logger.info("[WM] Cleared")

    def is_idle(self) -> bool:
        return time.time() - self._last_active > IDLE_TIMEOUT

    def auto_clear_if_idle(self):
        if self.is_idle() and (self._store or self._context_stack):
            logger.info("[WM] Auto-clearing (idle timeout)")
            self.clear()

    def context_str(self) -> str:
        parts = []
        if self._store:
            kv = "\n".join(f"  {k}: {v['value']}" for k, v in self._store.items())
            parts.append(f"SESSION MEMORY:\n{kv}")
        if self._context_stack:
            recent = self._context_stack[-5:]
            stack  = "\n".join(f"  {e}" for e in recent)
            parts.append(f"RECENT ACTIONS:\n{stack}")
        return "\n\n".join(parts)

    def has_context(self) -> bool:
        return bool(self._store or self._context_stack)

    def summary(self) -> dict:
        return {
            "entries":      len(self._store),
            "stack_depth":  len(self._context_stack),
            "idle_seconds": int(time.time() - self._last_active),
        }

    def _touch(self):
        self._last_active = time.time()


_session = WorkingMemory()


def get_session() -> WorkingMemory:
    _session.auto_clear_if_idle()
    return _session
