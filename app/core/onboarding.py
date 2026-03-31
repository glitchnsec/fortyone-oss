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
            return await self._step3_save_tz_ask_assistant_name(user_id, body)
        if step == "awaiting_assistant_name":
            return await self._step4_save_assistant_name_and_intro(user_id, body)

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

    async def _step3_save_tz_ask_assistant_name(self, user_id: str, body: str) -> str:
        iana_tz = await _resolve_timezone(body)
        memories = self.store.get_memories(user_id, "long_term")
        name = next((m.value for m in memories if m.key == "name"), "there")

        if not iana_tz:
            return (
                "Hmm, I couldn't figure out that timezone.\n"
                "Try something like: EST, Pacific, London, or America/Chicago"
            )

        tz_label = _TZ_LABELS.get(iana_tz, iana_tz)
        self.store.store_memory(user_id, "long_term", "timezone", iana_tz)
        from app.memory.models import User
        user = self.store.db.query(User).filter(User.id == user_id).first()
        if user:
            user.timezone = iana_tz
            self.store.db.commit()

        _set_step(self.store, user_id, "awaiting_assistant_name")
        return (
            f"Got it — {tz_label} it is.\n\n"
            f"One last thing, {name}: what would you like to call me?\n"
            "Give me a name — Aria, Jay, Max, Nova... anything you like.\n"
            "(Or reply 'skip' to keep it simple.)"
        )

    async def _step4_save_assistant_name_and_intro(self, user_id: str, body: str) -> str:
        memories = self.store.get_memories(user_id, "long_term")
        mem = {m.key: m.value for m in memories}
        user_name = mem.get("name", "there")
        tz_label = _TZ_LABELS.get(mem.get("timezone", ""), mem.get("timezone", "your timezone"))

        # Extract assistant name — treat "skip" as no preference
        assistant_name = _extract_assistant_name(body)
        self.store.store_memory(user_id, "long_term", "assistant_name", assistant_name)

        _set_step(self.store, user_id, "complete")

        return await _generate_intro(user_name, assistant_name, tz_label)


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


# ─── Assistant name + intro ───────────────────────────────────────────────────

_DEFAULT_ASSISTANT_NAME = "your assistant"

_SKIP_WORDS = {"skip", "no", "nope", "none", "idc", "whatever", "default",
               "anything", "doesn't matter", "dont care", "don't care"}


def _extract_assistant_name(raw: str) -> str:
    """
    Pull a name for the assistant out of the user's reply.
    Falls back to the default when the user skips or is indifferent.
    """
    clean = raw.strip().lower().rstrip("!.,")
    if clean in _SKIP_WORDS or not clean:
        return _DEFAULT_ASSISTANT_NAME

    # Strip lead-ins: "call yourself Jay", "name yourself Aria", "you are Max"
    for prefix in ("call yourself ", "name yourself ", "you are ", "your name is ",
                   "call you ", "i'll call you ", "let's call you "):
        if clean.startswith(prefix):
            raw = raw[len(prefix):]
            break

    words = raw.strip().split()
    if words:
        return words[0].strip(".,!?").capitalize()
    return _DEFAULT_ASSISTANT_NAME


_STATIC_INTRO_TEMPLATE = (
    "I'm {assistant}, your personal assistant. "
    "Here's what I can do for you, {user_name}:\n\n"
    "  • Set reminders — \"Remind me to call John tomorrow at 3pm\"\n"
    "  • Manage tasks — \"What do I have today?\"\n"
    "  • Help with scheduling — \"When should I meet with the team?\"\n"
    "  • Remember things — \"I prefer morning meetings\"\n\n"
    "I remember everything you tell me and get smarter over time. "
    "What's the first thing on your mind?"
)


async def _generate_intro(user_name: str, assistant_name: str, tz_label: str) -> str:
    """
    Generate a warm, personalised introduction message.
    Uses the LLM when available; falls back to a static template.
    """
    from app.config import get_settings
    if not get_settings().has_llm:
        name_part = assistant_name if assistant_name != _DEFAULT_ASSISTANT_NAME else "your assistant"
        return _STATIC_INTRO_TEMPLATE.format(assistant=name_part, user_name=user_name)

    from app.tasks._llm import llm_text

    system = (
        "You are a personal SMS assistant being introduced to a new user for the first time. "
        "Write a warm, confident, and concise introduction. "
        "You must stay under 320 characters total (SMS-friendly). "
        "Do NOT use bullet lists. Be conversational — like a real person, not a product page. "
        "End with an open invitation, not a question."
    )

    user_msg = (
        f"My name is {assistant_name if assistant_name != _DEFAULT_ASSISTANT_NAME else 'not set yet'}.\n"
        f"The user's name is {user_name}.\n"
        f"Their timezone is {tz_label}.\n\n"
        "Write the intro message now."
    )

    static_fallback = _STATIC_INTRO_TEMPLATE.format(
        assistant=assistant_name if assistant_name != _DEFAULT_ASSISTANT_NAME else "I",
        user_name=user_name,
    )

    return await llm_text(
        system=system,
        messages=[{"role": "user", "content": user_msg}],
        mock_text=static_fallback,
    )
