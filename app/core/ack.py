"""
ACK message generator.

get_smart_ack() tries to produce a personalised, LLM-written acknowledgment
within NIM_ACK_TIMEOUT_S seconds.  If the model doesn't respond in time (or
no API key is configured), it falls back instantly to the static pool so the
user is never kept waiting past 500ms.

Token budget: max_tokens=25 keeps the inference fast and forces brevity.
"""
import asyncio
import logging
import random
import time
from typing import Optional

from app.core.intent import IntentType

logger = logging.getLogger(__name__)


# ─── Static fallback pool ────────────────────────────────────────────────────

_ACK_POOL: dict[IntentType, list[str]] = {
    IntentType.REMINDER: [
        "On it — setting that up now.",
        "Got it. Give me a sec.",
        "Sure, I'll take care of that.",
    ],
    IntentType.SCHEDULE: [
        "Let me check your preferences and find a good time.",
        "On it — looking at your calendar now.",
        "Working on finding the best slot for you.",
    ],
    IntentType.RECALL: [
        "Let me pull that up for you...",
        "One sec — checking what I have.",
        "On it.",
    ],
    IntentType.PREFERENCE: [
        "Got it — I'll remember that.",
        "Noted. I'll keep that in mind.",
        "Good to know. Storing that.",
    ],
    IntentType.COMPLETE: [
        "Got it — marking that done.",
        "On it.",
    ],
    IntentType.STATUS: [
        "Let me check on that for you.",
        "One moment — looking into it.",
    ],
    IntentType.GENERAL: [
        "Got it — working on it.",
        "On it. Give me a moment.",
        "Sure, let me handle that.",
    ],
}

_GREETING_POOL = [
    "Hey! What can I help you with today?",
    "Hi there! I'm here — what do you need?",
    "Hello! Ready to help. What's on your mind?",
]


def get_ack(intent_type: IntentType) -> str:
    """Synchronous static ACK — instant, always available."""
    if intent_type == IntentType.GREETING:
        return random.choice(_GREETING_POOL)
    pool = _ACK_POOL.get(intent_type, _ACK_POOL[IntentType.GENERAL])
    return random.choice(pool)


# ─── Smart ACK (LLM with timeout) ────────────────────────────────────────────

_ACK_SYSTEM = (
    "You are a personal SMS assistant. "
    "Reply with ONE short acknowledgment sentence (max 8 words). "
    "Be natural and warm — not robotic. "
    "Do NOT answer the question yet, just acknowledge you're on it. "
    "No punctuation at the very end."
)


async def _llm_ack(body: str, user_name: Optional[str]) -> str:
    """Raw LLM call — no timeout, no fallback. Callers handle that."""
    from app.config import get_settings
    from app.tasks._llm import _client

    settings = get_settings()
    name_hint = f" The user's name is {user_name}." if user_name else ""
    user_prompt = f'User just texted: "{body}"{name_hint}'

    resp = await _client(settings).chat.completions.create(
        model=settings.llm_model_fast,
        messages=[
            {"role": "system", "content": _ACK_SYSTEM},
            {"role": "user",   "content": user_prompt},
        ],
        temperature=0.8,
        max_tokens=25,      # tiny budget = fast + forces brevity
    )
    text = resp.choices[0].message.content.strip().strip('"').strip("'")
    return text if text else None


async def get_smart_ack(
    intent_type: IntentType,
    body: str,
    user_name: Optional[str] = None,
    timeout_s: float = 0.90,    # leaves 100ms headroom inside the 1s SLA
) -> str:
    """
    Try an LLM-generated ACK within `timeout_s` seconds.
    Falls back to the static pool the moment the deadline is exceeded.

    Logs whether the LLM path was used and its latency, so you can monitor
    how often the fallback fires in production.
    """
    from app.config import get_settings
    settings = get_settings()
    if not settings.has_llm:
        return get_ack(intent_type)

    static_fallback = get_ack(intent_type)
    t0 = time.monotonic()

    try:
        result = await asyncio.wait_for(_llm_ack(body, user_name), timeout=timeout_s)
        elapsed_ms = (time.monotonic() - t0) * 1000

        if result:
            logger.info("smart_ack=llm elapsed_ms=%.0f text=%r", elapsed_ms, result)
            return result

        # Empty response — fall back
        logger.debug("smart_ack=empty, using static fallback")
        return static_fallback

    except asyncio.TimeoutError:
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info("smart_ack=timeout elapsed_ms=%.0f, using static fallback", elapsed_ms)
        return static_fallback

    except Exception as exc:
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.warning("smart_ack=error elapsed_ms=%.0f err=%s, using static fallback", elapsed_ms, exc)
        return static_fallback
