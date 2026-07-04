"""
core/llm.py - LLM gateway with multi-provider fallback

Tier strategy:
  flash → DeepSeek V4-Flash direct ($0.0028 cache hit) → Cloudflare → Groq
  plus  → Qwen3.7-Plus (OpenRouter) → DeepSeek → Cloudflare → Groq
  max   → Qwen3.7-Max (OpenRouter) → Qwen-Plus → DeepSeek → Cloudflare
  opus  → Claude Opus (OpenRouter) → Qwen-Max → fallbacks

Set API keys in .env:
  DEEPSEEK_API_KEY, OPENROUTER_API_KEY, GROQ_API_KEY, CF_API_TOKEN
"""
import os
import httpx
import time
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

DEEPSEEK_BASE   = "https://api.deepseek.com/v1/chat/completions"
MODEL_DS_FLASH  = "deepseek-v4-flash"

OPENROUTER_BASE = "https://openrouter.ai/api/v1/chat/completions"
MODEL_QWEN_PLUS = "qwen/qwen3.7-plus"
MODEL_QWEN_MAX  = "qwen/qwen3.7-max"
MODEL_OPUS      = "anthropic/claude-opus-4.7"


def call_llm(
    system: str,
    user: str,
    max_tokens: int = 2000,
    temperature: float = 0.3,
    tier: str = "flash",
    use_pro: bool = False,
) -> str:
    """
    Call LLM with automatic fallback chain.

    tier:
      "flash"  — cheap/fast  (DeepSeek Flash → Cloudflare → Groq)
      "plus"   — mid-tier    (Qwen3.7-Plus → DeepSeek → Cloudflare)
      "max"    — high-quality (Qwen3.7-Max → Qwen-Plus → DeepSeek)
      "opus"   — best        (Claude Opus → Qwen-Max → fallbacks)
    """
    if use_pro and tier == "flash":
        tier = "max"

    deepseek_key   = os.getenv("DEEPSEEK_API_KEY", "")
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "")
    cf_token       = os.getenv("CF_API_TOKEN", "")
    cf_account     = os.getenv("CF_ACCOUNT_ID", "")
    groq_key       = os.getenv("GROQ_API_KEY", "")

    messages = [
        {"role": "system", "content": system},
        {"role": "user",   "content": user},
    ]

    def _try_deepseek_direct() -> str | None:
        if not deepseek_key:
            return None
        try:
            r = httpx.post(
                DEEPSEEK_BASE,
                headers={"Authorization": f"Bearer {deepseek_key}", "Content-Type": "application/json"},
                json={"model": MODEL_DS_FLASH, "messages": messages,
                      "max_tokens": max_tokens, "temperature": temperature},
                timeout=45,
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except Exception as e:
            logger.warning(f"DeepSeek error: {e}")
            return None

    def _try_openrouter(model: str, label: str = "") -> str | None:
        if not openrouter_key:
            return None
        try:
            r = httpx.post(
                OPENROUTER_BASE,
                headers={
                    "Authorization": f"Bearer {openrouter_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://superai-agent.local",
                    "X-Title": "SuperAiAgent",
                },
                json={"model": model, "messages": messages,
                      "max_tokens": max_tokens, "temperature": temperature},
                timeout=60,
            )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            logger.debug(f"[LLM] {label or model} via OpenRouter (tier={tier})")
            return content
        except Exception as e:
            logger.warning(f"OpenRouter ({label or model}) error: {e}")
            return None

    def _try_cloudflare() -> str | None:
        if not cf_token or not cf_account:
            return None
        try:
            cf_model = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
            r = httpx.post(
                f"https://api.cloudflare.com/client/v4/accounts/{cf_account}/ai/run/{cf_model}",
                headers={"Authorization": f"Bearer {cf_token}", "Content-Type": "application/json"},
                json={"messages": messages, "max_tokens": min(max_tokens, 2048)},
                timeout=60,
            )
            r.raise_for_status()
            data = r.json()
            if data.get("success"):
                return data["result"]["response"]
        except Exception as e:
            logger.warning(f"Cloudflare error: {e}")
        return None

    def _try_groq() -> str | None:
        if not groq_key:
            return None
        payload = {
            "model": os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        for attempt in range(3):
            try:
                r = httpx.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
                    json=payload, timeout=60,
                )
                if r.status_code == 429:
                    wait = 20 * (attempt + 1)
                    logger.warning(f"Groq rate limit — waiting {wait}s")
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"]
            except Exception as e:
                logger.warning(f"Groq error: {e}")
        return None

    if tier == "opus":
        chain = [
            lambda: _try_openrouter(MODEL_OPUS,      "Opus"),
            lambda: _try_openrouter(MODEL_QWEN_MAX,  "Qwen-Max"),
            lambda: _try_openrouter(MODEL_QWEN_PLUS, "Qwen-Plus"),
            _try_deepseek_direct,
            _try_cloudflare,
            _try_groq,
        ]
    elif tier == "max":
        chain = [
            lambda: _try_openrouter(MODEL_QWEN_MAX,  "Qwen-Max"),
            lambda: _try_openrouter(MODEL_QWEN_PLUS, "Qwen-Plus"),
            _try_deepseek_direct,
            _try_cloudflare,
            _try_groq,
        ]
    elif tier == "plus":
        chain = [
            lambda: _try_openrouter(MODEL_QWEN_PLUS, "Qwen-Plus"),
            _try_deepseek_direct,
            _try_cloudflare,
            _try_groq,
        ]
    else:  # flash
        chain = [
            _try_deepseek_direct,
            _try_cloudflare,
            _try_groq,
        ]

    for fn in chain:
        result = fn()
        if result and result.strip():
            return result

    raise RuntimeError(f"All LLM providers failed (tier={tier})")
