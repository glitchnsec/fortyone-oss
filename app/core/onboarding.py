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


def _resolve_timezone(raw: str) -> Optional[str]:
    """
    Try to resolve a user's freeform timezone input to an IANA string.
    Returns None if unrecognised.
    """
    clean = raw.strip().lower().rstrip(".")

    # Direct IANA lookup (e.g. "America/New_York")
    if "/" in clean:
        # Trust it — we can't enumerate all IANA zones; accept as-is
        return raw.strip()

    return _TZ_MAP.get(clean)


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
        name = _extract_name(body)
        self.store.store_memory(user_id, "long_term", "name", name)
        self.store.update_user_name(user_id, name)
        _set_step(self.store, user_id, "awaiting_tz")

        return (
            f"Nice to meet you, {name}! 👋\n\n"
            "What timezone are you in?\n"
            "Reply with an abbreviation or city — e.g.\n"
            "  EST  PST  CST  MST\n"
            "  London  Paris  Tokyo  Sydney"
        )

    async def _step3_save_tz_finish(self, user_id: str, body: str) -> str:
        iana_tz = _resolve_timezone(body)
        name_memories = self.store.get_memories(user_id, "long_term")
        name = next((m.value for m in name_memories if m.key == "name"), "there")

        if iana_tz:
            tz_label = _TZ_LABELS.get(iana_tz, iana_tz)
            self.store.store_memory(user_id, "long_term", "timezone", iana_tz)
            # Also update the User row
            from app.memory.models import User
            from app.database import SessionLocal
            db_session = self.store.db
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
            # Unrecognised timezone — ask again with more guidance
            return (
                f"Hmm, I didn't recognise \"{body.strip()}\".\n\n"
                "Try a common abbreviation:\n"
                "  EST  CST  MST  PST\n"
                "or a city like: London, Tokyo, Sydney\n"
                "or an IANA name like: America/Chicago"
            )


# ─── Name extraction ──────────────────────────────────────────────────────────

def _extract_name(raw: str) -> str:
    """
    Best-effort name extraction from a freeform reply.
    Strips common lead-ins like "My name is Alex" → "Alex".
    Falls back to title-casing the raw input.
    """
    import re
    text = raw.strip()

    # "My name is ..." / "I'm ..." / "It's ..." / "Call me ..."
    patterns = [
        r"(?:my name is|i'm|i am|it's|it is|call me)\s+(.+)",
    ]
    for pat in patterns:
        m = re.match(pat, text, re.IGNORECASE)
        if m:
            return m.group(1).strip().title()

    # Single or double word → assume it's just the name
    words = text.split()
    if len(words) <= 3:
        return " ".join(w.capitalize() for w in words)

    # Longer response — take the first capitalised word or fall back to title-case
    for w in words:
        if w[0].isupper() and w.isalpha():
            return w
    return text.title()
