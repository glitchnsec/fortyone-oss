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
        "Working on that...",
        "On it, one sec...",
        "Looking into it...",
    ],
    IntentType.SCHEDULE: [
        "Checking your schedule...",
        "On it, one moment...",
        "Looking into that...",
    ],
    IntentType.RECALL: [
        "Pulling that up...",
        "One sec, checking...",
        "Looking into it...",
    ],
    IntentType.PREFERENCE: [
        "On it, one moment...",
        "Working on that...",
        "One sec...",
    ],
    IntentType.COMPLETE: [
        "On it, one sec...",
        "Working on that...",
    ],
    IntentType.STATUS: [
        "Checking on that...",
        "One moment, looking into it...",
    ],
    IntentType.GENERAL: [
        "Working on that...",
        "On it, one moment...",
        "Looking into it...",
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
    "Generate a SHORT (under 10 words) acknowledgment that the user's request is being processed. "
    "CRITICAL RULES: "
    "- NEVER answer the user's question. "
    "- NEVER provide information or suggestions. "
    "- ONLY indicate you are working on their request. "
    "- Reference what they asked about if possible. "
    "Examples: 'Checking your schedule...', 'Looking into that for you...', 'Pulling up your reminders...' "
    "Return ONLY the acknowledgment text, nothing else."
)


async def _llm_ack(
    body: str,
    user_name: Optional[str],
    recent_messages: Optional[list[dict]] = None,
    assistant_name: Optional[str] = None,
    personality_notes: Optional[str] = None,
) -> str:
    """Raw LLM call — no timeout, no fallback. Callers handle that."""
    from app.config import get_settings
    from app.core.identity import identity_preamble
    from app.tasks._llm import _client

    settings = get_settings()
    name_hint = f" The user's name is {user_name}." if user_name else ""
    identity = identity_preamble(assistant_name=assistant_name, personality_notes=personality_notes)

    # Build conversation history for context-aware ACK (D-12)
    messages = [{"role": "system", "content": identity + " " + _ACK_SYSTEM + name_hint}]
    if recent_messages:
        # Include last 3 exchanges for context without exceeding ACK latency budget
        for msg in recent_messages[-6:]:   # 3 pairs = 6 messages
            role = "user" if msg.get("direction") == "inbound" else "assistant"
            content = msg.get("body", "")
            if content:
                messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": f'User just sent: "{body}"{name_hint}'})

    resp = await _client(settings).chat.completions.create(
        model=settings.llm_model_fast,
        messages=messages,
        temperature=0.8,
        max_tokens=25,      # tiny budget = fast + forces brevity
    )
    text = resp.choices[0].message.content.strip().strip('"').strip("'")
    return text if text else None


async def get_smart_ack(
    intent_type: IntentType,
    body: str,
    user_name: Optional[str] = None,
    recent_messages: Optional[list[dict]] = None,   # NEW — per D-12: context-aware ACK
    assistant_name: Optional[str] = None,
    personality_notes: Optional[str] = None,
    timeout_s: float = 0.90,    # leaves 100ms headroom inside the 1s SLA
) -> str:
    """
    Try an LLM-generated ACK within `timeout_s` seconds.
    Falls back to the static pool the moment the deadline is exceeded.

    recent_messages: last N messages from get_context_minimal for context-aware ACK.
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
        result = await asyncio.wait_for(
            _llm_ack(body, user_name, recent_messages=recent_messages,
                     assistant_name=assistant_name, personality_notes=personality_notes),
            timeout=timeout_s,
        )
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
