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
import time
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
        logger.info("TZ_RESOLVE  source=static  raw=%r  iana=%s", raw[:40], result)
        return result

    from app.config import get_settings
    if not get_settings().has_llm:
        logger.info("TZ_RESOLVE  source=none  raw=%r  result=null", raw[:40])
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
        logger.info("TZ_RESOLVE  source=llm  raw=%r  iana=%s", raw[:40], iana)
        return iana
    logger.info("TZ_RESOLVE  source=llm  raw=%r  result=null", raw[:40])
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
        logger.info("NAME_EXTRACT  source=llm  raw=%r  name=%r", raw[:40], name)
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
        t0 = time.monotonic()

        logger.info(
            "ONBOARDING_STEP  user=%s  step=%s  body=%r",
            user_id[:8], step, body[:60],
        )

        if step == "new":
            reply = await self._step1_ask_name(user_id)
        elif step == "awaiting_name":
            reply = await self._step2_save_name_ask_tz(user_id, body)
        elif step == "awaiting_tz":
            reply = await self._step3_save_tz_ask_assistant_name(user_id, body)
        elif step == "awaiting_assistant_name":
            reply = await self._step4_save_assistant_name_and_intro(user_id, body)
        else:
            return None

        latency_ms = int((time.monotonic() - t0) * 1000)
        new_step = get_step(self.store, user_id)
        logger.info(
            "ONBOARDING_REPLY  user=%s  step=%s→%s  latency_ms=%d  reply=%r",
            user_id[:8], step, new_step, latency_ms,
            (reply or "")[:100],
        )
        return reply

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
            f"Got it — {tz_label} locked in.\n\n"
            f"Last thing, {name}: what would you like to call me? "
            "Aria, Jay, Max, Nova — anything works. "
            "Reply 'skip' if you don't mind."
        )

    async def _step4_save_assistant_name_and_intro(self, user_id: str, body: str) -> str:
        memories = self.store.get_memories(user_id, "long_term")
        mem = {m.key: m.value for m in memories}
        user_name = mem.get("name", "there")
        tz_label = _TZ_LABELS.get(mem.get("timezone", ""), mem.get("timezone", "your timezone"))

        classification = await _classify_name_reply(body, user_name)
        intent = classification.get("intent")   # "name" | "skip" | "confused"
        assistant_name = classification.get("name")

        if intent == "confused":
            # User likely didn't see the previous question — re-send it with context
            return (
                f"Hey {user_name}! Quick setup question I still need: "
                "what would you like to call me?\n"
                "Give me a name — Aria, Jay, Max, Nova — or just say 'skip'."
            )

        if intent == "skip" or not assistant_name:
            assistant_name = _DEFAULT_ASSISTANT_NAME

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

async def _classify_name_reply(body: str, user_name: str) -> dict:
    """
    Use the LLM to classify whether the user's reply to "what should I call you?"
    is an actual name, a skip, or confusion (they likely didn't see the question).

    Returns: {"intent": "name"|"skip"|"confused", "name": str|None}

    5-second hard timeout — falls back to rule-based classifier on any
    slowness so the user never waits more than ~5s for a classification.
    """
    from app.config import get_settings
    if not get_settings().has_llm:
        result = _classify_name_rules(body)
        logger.info("NAME_CLASSIFY  source=rules  body=%r  result=%s", body[:40], result)
        return result

    from app.tasks._llm import llm_json
    data = await llm_json(
        prompt=(
            f'The user {user_name} was asked: "What would you like to call your AI assistant?"\n'
            f'They replied: "{body}"\n\n'
            'Classify the reply and return JSON:\n'
            '{\n'
            '  "intent": "name" | "skip" | "confused",\n'
            '  "name": "ProperCasedName or null"\n'
            '}\n\n'
            '"name"     — they gave an actual name (e.g. Aria, Jay, "call yourself Max")\n'
            '"skip"     — they want a default (e.g. skip, whatever, doesn\'t matter, surprise me)\n'
            '"confused" — looks like a greeting, reaction, or off-topic reply that suggests\n'
            '             they did not see the question (e.g. hey, heyy, ok, lol, cool, hi)\n\n'
            'For "name", extract just the name in proper case. For anything else set name to null.'
        ),
        mock_payload=_classify_name_rules(body),
        timeout_s=5.0,   # never wait more than 5s for a classification
    )

    if data.get("intent") in ("name", "skip", "confused"):
        logger.info("NAME_CLASSIFY  source=llm  body=%r  result=%s", body[:40], data)
        return data

    # LLM returned something unexpected — fall back to rules
    fallback = _classify_name_rules(body)
    logger.warning("NAME_CLASSIFY  source=rules_fallback  body=%r  result=%s", body[:40], fallback)
    return fallback


def _classify_name_rules(body: str) -> dict:
    """Rule-based fallback for name classification (no LLM)."""
    clean = re.sub(r"(.)\1{2,}", r"\1\1", body.strip().lower().rstrip("!?.,"))

    greetings = {"hey", "hi", "hello", "sup", "yo", "heyy", "hii", "howdy"}
    reactions = {"ok", "okay", "k", "sure", "yeah", "yep", "lol", "haha",
                 "nice", "great", "cool", "awesome", "wow", "omg", "idk",
                 "hmm", "ugh", "hm", "got it", "alright", "fine"}
    skips = {"skip", "no", "nope", "none", "whatever", "default", "anything",
             "doesn't matter", "dont care", "don't care", "up to you",
             "your choice", "surprise me", "no preference"}

    if clean in greetings or clean in reactions:
        return {"intent": "confused", "name": None}
    if clean in skips or not clean:
        return {"intent": "skip", "name": None}

    # Strip lead-ins
    for prefix in ("call yourself ", "name yourself ", "you are ",
                   "your name is ", "call you ", "i'll call you "):
        if clean.startswith(prefix):
            body = body[len(prefix):]
            break

    name = body.strip().split()[0].strip(".,!?").capitalize() if body.strip() else None
    return {"intent": "name" if name else "skip", "name": name}




_STATIC_INTRO_TEMPLATE = (
    "Hey {user_name}! I'm {assistant} — I'll handle your reminders, "
    "scheduling, and follow-ups so nothing slips through the cracks. "
    "I remember what you tell me and get more useful over time.\n\n"
    "What's the most important thing on your plate right now?"
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

    has_name = assistant_name != _DEFAULT_ASSISTANT_NAME
    system = (
        "You are a personal SMS assistant being introduced to a new user for the first time. "
        "Write a warm, confident, 2-sentence introduction followed by one specific open question. "
        "Stay under 320 characters total (SMS-friendly). "
        "Do NOT use bullet lists. Sound like a real person — warm and direct, not a product pitch. "
        "The final sentence MUST be a question that invites the user to share something "
        "about themselves or their life — NOT 'What can I help you with?' or 'Let's get started'. "
        "Example ending: 'What's the most important thing on your plate right now?'"
    )

    asst_label = assistant_name if has_name else "(no name chosen — refer to yourself as just 'I')"
    user_msg = (
        f"Assistant name: {asst_label}\n"
        f"User's name: {user_name}\n"
        f"User's timezone: {tz_label}\n\n"
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
