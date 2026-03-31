"""
Guided onboarding handler.

Intercepts every message for a new user until registration is complete.
Uses the Memory store for state — no schema migration required.

States (stored as memory key "onboarding_step"):
  absent          → user is brand-new, start step 1
  "awaiting_name" → waiting for the user's name
  "awaiting_tz"   → waiting for timezone
  "complete"      → onboarding done, normal pipeline takes over
"""
import logging
import re
from typing import Optional

from app.memory.store import MemoryStore

logger = logging.getLogger(__name__)

# ─── Timezone resolution ──────────────────────────────────────────────────────

# Maps common abbreviations + city names → IANA timezone strings
_TZ_MAP: dict[str, str] = {
    # US abbreviations
    "est": "America/New_York",
    "eastern": "America/New_York",
    "new york": "America/New_York",
    "nyc": "America/New_York",
    "et": "America/New_York",
    "cst": "America/Chicago",
    "central": "America/Chicago",
    "chicago": "America/Chicago",
    "ct": "America/Chicago",
    "mst": "America/Denver",
    "mountain": "America/Denver",
    "denver": "America/Denver",
    "mt": "America/Denver",
    "pst": "America/Los_Angeles",
    "pacific": "America/Los_Angeles",
    "los angeles": "America/Los_Angeles",
    "la": "America/Los_Angeles",
    "pt": "America/Los_Angeles",
    "akst": "America/Anchorage",
    "alaska": "America/Anchorage",
    "hst": "Pacific/Honolulu",
    "hawaii": "Pacific/Honolulu",
    # Other common zones
    "gmt": "UTC",
    "utc": "UTC",
    "london": "Europe/London",
    "bst": "Europe/London",
    "paris": "Europe/Paris",
    "berlin": "Europe/Berlin",
    "cet": "Europe/Paris",
    "dubai": "Asia/Dubai",
    "ist": "Asia/Kolkata",
    "india": "Asia/Kolkata",
    "mumbai": "Asia/Kolkata",
    "singapore": "Asia/Singapore",
    "sgt": "Asia/Singapore",
    "tokyo": "Asia/Tokyo",
    "jst": "Asia/Tokyo",
    "sydney": "Australia/Sydney",
    "aest": "Australia/Sydney",
    "auckland": "Pacific/Auckland",
    "nzst": "Pacific/Auckland",
}

_TZ_LABELS: dict[str, str] = {
    "America/New_York": "Eastern (ET)",
    "America/Chicago": "Central (CT)",
    "America/Denver": "Mountain (MT)",
    "America/Los_Angeles": "Pacific (PT)",
    "America/Anchorage": "Alaska (AKT)",
    "Pacific/Honolulu": "Hawaii (HST)",
    "UTC": "UTC / GMT",
    "Europe/London": "London (GMT/BST)",
    "Europe/Paris": "Paris (CET)",
    "Asia/Kolkata": "India (IST)",
    "Asia/Singapore": "Singapore (SGT)",
    "Asia/Tokyo": "Tokyo (JST)",
    "Australia/Sydney": "Sydney (AEST)",
    "Pacific/Auckland": "Auckland (NZST)",
}


def _normalize(text: str) -> str:
    """Strip punctuation, emoji, and extra whitespace. Lowercase."""
    import re
    # Remove anything that isn't a letter, digit, slash, or space
    clean = re.sub(r"[^\w\s/]", "", text, flags=re.UNICODE)
    # Collapse whitespace
    return re.sub(r"\s+", " ", clean).strip().lower()


def _resolve_timezone_static(raw: str) -> Optional[str]:
    """
    Fast, synchronous map lookup — no API call.
    Handles punctuation noise, word scanning, and multi-word city names.
    """
    clean = _normalize(raw)

    # Direct IANA / Olson passthrough (e.g. "America/New_York", "US/Eastern")
    if "/" in clean:
        return re.sub(r"[^\w/]", "", raw.strip())

    # Full-string match
    result = _TZ_MAP.get(clean)
    if result:
        return result

    # Word-by-word scan — "I'm in EST!", "it's Pacific time", etc.
    for word in clean.split():
        result = _TZ_MAP.get(word)
        if result:
            return result

    # Multi-word phrase scan — "New York", "Los Angeles"
    for key in _TZ_MAP:
        if " " in key and key in clean:
            return _TZ_MAP[key]

    return None


async def _resolve_timezone(raw: str) -> Optional[str]:
    """
    Resolve timezone from freeform input.

    Strategy:
      1. Static map (instant) — handles abbreviations, city names, IANA strings
      2. LLM extraction — handles anything the map misses:
           "east coast", "wherever London is", "same as NYC", etc.

    Returns an IANA timezone string, or None if both methods fail.
    """
    result = _resolve_timezone_static(raw)
    if result:
        return result

    from app.config import get_settings
    if not get_settings().has_llm:
        return None

    from app.tasks._llm import llm_json
    data = await llm_json(
        prompt=(
            f'Extract the IANA timezone from this message.\n\n'
            f'Message: "{raw}"\n\n'
            'Return JSON: {"iana": "America/New_York"}\n'
            'Use IANA format only (e.g. America/New_York, Europe/London, Asia/Tokyo).\n'
            'If you cannot determine a timezone, return {"iana": null}.'
        ),
        mock_payload={"iana": None},
    )

    iana = data.get("iana")
    if iana and isinstance(iana, str) and "/" in iana:
        return iana
    return None


async def _extract_name(raw: str) -> str:
    """
    Extract the user's name from a freeform reply.

    Strategy:
      1. LLM extraction — handles "My name is Alex", "It's Jordan", "call me J"
      2. Rule-based fallback — used when no API key is set
    """
    from app.config import get_settings
    if get_settings().has_llm:
        from app.tasks._llm import llm_json
        data = await llm_json(
            prompt=(
                f'Extract the name this person wants to be called.\n\n'
                f'Message: "{raw}"\n\n'
                'Return JSON: {"name": "Alex"}\n'
                'Just the name, properly capitalized. '
                'If genuinely unclear, return the full input title-cased.'
            ),
            mock_payload={"name": _extract_name_rules(raw)},
        )
        name = data.get("name", "").strip()
        if name:
            return name

    return _extract_name_rules(raw)


# ─── State helpers ────────────────────────────────────────────────────────────

_STEP_KEY = "onboarding_step"


def get_step(store: MemoryStore, user_id: str) -> str:
    memories = store.get_memories(user_id, "long_term")
    for m in memories:
        if m.key == _STEP_KEY:
            return m.value
    return "new"


def _set_step(store: MemoryStore, user_id: str, step: str) -> None:
    store.store_memory(user_id, "long_term", _STEP_KEY, step)


def is_complete(store: MemoryStore, user_id: str) -> bool:
    return get_step(store, user_id) == "complete"


# ─── Handler ──────────────────────────────────────────────────────────────────

class OnboardingHandler:
    """
    Drives the user through name → timezone collection.
    Returns the SMS text to send back, or None if onboarding just completed
    (in which case the normal pipeline should continue with the original message).
    """

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    async def handle(self, user_id: str, phone: str, body: str) -> Optional[str]:
        """
        Returns the reply string to send, or None once onboarding is marked complete.
        """
        step = get_step(self.store, user_id)

        if step == "new":
            return await self._step1_ask_name(user_id)

        if step == "awaiting_name":
            return await self._step2_save_name_ask_tz(user_id, body)

        if step == "awaiting_tz":
            return await self._step3_save_tz_finish(user_id, body)

        # Already complete — shouldn't normally reach here
        return None

    async def _step1_ask_name(self, user_id: str) -> str:
        _set_step(self.store, user_id, "awaiting_name")
        return (
            "Hey! I'm your personal assistant — I'll help you manage tasks, "
            "reminders, and scheduling.\n\nFirst, what's your name?"
        )

    async def _step2_save_name_ask_tz(self, user_id: str, body: str) -> str:
        name = await _extract_name(body)
        self.store.store_memory(user_id, "long_term", "name", name)
        self.store.update_user_name(user_id, name)
        _set_step(self.store, user_id, "awaiting_tz")

        return (
            f"Nice to meet you, {name}! 👋\n\n"
            "What timezone are you in?\n"
            "Anything works — 'EST', 'Pacific', 'London', 'east coast', etc."
        )

    async def _step3_save_tz_finish(self, user_id: str, body: str) -> str:
        iana_tz = await _resolve_timezone(body)
        name_memories = self.store.get_memories(user_id, "long_term")
        name = next((m.value for m in name_memories if m.key == "name"), "there")

        if iana_tz:
            tz_label = _TZ_LABELS.get(iana_tz, iana_tz)
            self.store.store_memory(user_id, "long_term", "timezone", iana_tz)
            db_session = self.store.db
            from app.memory.models import User
            user = db_session.query(User).filter(User.id == user_id).first()
            if user:
                user.timezone = iana_tz
                db_session.commit()

            _set_step(self.store, user_id, "complete")
            return (
                f"All set, {name}! I'll keep everything in {tz_label}.\n\n"
                "You can now:\n"
                "  • \"Remind me to call John tomorrow at 3pm\"\n"
                "  • \"What reminders do I have?\"\n"
                "  • \"When should I schedule a meeting?\"\n\n"
                "What do you need?"
            )
        else:
            return (
                f"Hmm, I couldn't figure out that timezone.\n"
                "Try something like: EST, Pacific, London, or America/Chicago"
            )


# ─── Name extraction (rule-based fallback) ───────────────────────────────────

def _extract_name_rules(raw: str) -> str:
    """
    Pure rule-based name extraction — no API call.
    Used as the fallback when no LLM key is set.
    """
    text = raw.strip()

    patterns = [r"(?:my name is|i'm|i am|it's|it is|call me)\s+(.+)"]
    for pat in patterns:
        m = re.match(pat, text, re.IGNORECASE)
        if m:
            return m.group(1).strip().title()

    words = text.split()
    if len(words) <= 3:
        return " ".join(w.capitalize() for w in words)

    for w in words:
        if w[0].isupper() and w.isalpha():
            return w
    return text.title()
