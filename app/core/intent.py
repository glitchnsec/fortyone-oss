"""
Rule-based intent classifier.

Deliberately kept fast and dependency-free — no LLM call in the hot path.
Patterns are checked in priority order; first match wins.
"""
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class IntentType(str, Enum):
    REMINDER = "reminder"
    SCHEDULE = "schedule"
    RECALL = "recall"
    PREFERENCE = "preference"
    COMPLETE = "complete"   # "done", "mark X complete"
    GREETING = "greeting"
    STATUS = "status"
    WEB_SEARCH = "web_search"
    IDENTITY = "identity"
    GENERAL = "general"
    FOLLOWUP = "followup"


# Intents that can be answered quickly without an LLM worker job
FAST_PATH_INTENTS: set[IntentType] = {
    IntentType.GREETING,
    IntentType.IDENTITY,
}


@dataclass
class Intent:
    type: IntentType
    confidence: float
    requires_worker: bool   # True → push to async queue
    raw_text: str

    @property
    def is_fast_path(self) -> bool:
        return not self.requires_worker


# (intent, pattern, confidence)
_RULES: list[tuple[IntentType, str, float]] = [
    # Identity questions — "what's your name?", "who are you?", "what are you?"
    (IntentType.IDENTITY, r"\b(what'?s?\s+your\s+name|who\s+are\s+you|what\s+are\s+you|what\s+should\s+i\s+call\s+you|tell\s+me\s+(about\s+)?yourself)\b", 0.95),

    # Greetings — standalone greeting only (not "hey, remind me…")
    (IntentType.GREETING, r"^(hi|hello|hey(\s+there)?|good\s+(morning|afternoon|evening)|sup|what'?s\s+up)\s*[!?.]?\s*$", 0.95),

    # Preference storage
    (IntentType.PREFERENCE, r"\b(i\s+(prefer|like|always|usually|hate|love|don'?t\s+like)|my\s+preference\s+is|i'?m\s+a\s+morning|i\s+work\s+best)\b", 0.90),

    # Task completion
    (IntentType.COMPLETE, r"\b(done|finished|complete[d]?|mark\s+.+\s+(as\s+)?(done|complete[d]?))\b", 0.88),

    # Recall / list / meta-recall ("what do you know about me?")
    (IntentType.RECALL, r"\b(what\s+(reminders?|tasks?|do\s+i\s+have|did\s+i|are\s+my)|show\s+(me\s+)?(my\s+)?(reminders?|tasks?)|list\s+(my\s+)?(reminders?|tasks?)|do\s+i\s+have\s+any|check\s+my|what\s+(do\s+you|you)\s+(know|remember)\s*(about)?|what\s+have\s+you\s+(learned|stored|saved|remembered)|what\s+(info(rmation)?|data)\s+(do\s+you\s+have|have\s+you)|tell\s+me\s+what\s+you\s+(know|remember)|who\s+am\s+i)\b", 0.88),

    # Scheduling
    (IntentType.SCHEDULE, r"\b(schedule|book\s+(a\s+)?(meeting|call|appointment)|find\s+(a\s+)?time|when\s+should\s+(we|i)|set\s+up\s+a\s+(meeting|call)|calendar|availability)\b", 0.85),

    # Reminder
    (IntentType.REMINDER, r"\b(remind\s+me|set\s+a\s+reminder|don'?t\s+let\s+me\s+forget|follow[\s-]?up|alert\s+me|ping\s+me)\b", 0.90),

    # Status check
    (IntentType.STATUS, r"\b(status|update|how'?s\s+it\s+going|did\s+you|have\s+you|any\s+updates?)\b", 0.75),

    # Web search
    (IntentType.WEB_SEARCH, r"\b(search(\s+for)?|look\s+up|google|find\s+out|what\s+is\s+the\s+(weather|news|price|score)|weather\s+in|news\s+(about|on)|latest\s+(news|info)|who\s+is|what\s+is|how\s+(do|does|did|to)|tell\s+me\s+about)\b", 0.72),
]


def classify_intent(text: str) -> Intent:
    lower = text.lower().strip()

    for intent_type, pattern, confidence in _RULES:
        if re.search(pattern, lower, re.IGNORECASE):
            return Intent(
                type=intent_type,
                confidence=confidence,
                requires_worker=intent_type not in FAST_PATH_INTENTS,
                raw_text=text,
            )

    # FOLLOWUP: short message (<15 words), no rule fired — likely a clarification of prior intent.
    # Handles multi-turn continuations: "I meant Friday" / "actually 3pm" / "no, work email"
    # Runs AFTER the rules loop so explicit intents (REMINDER, RECALL, etc.) always take priority.
    words = text.split()
    if len(words) < 15:
        return Intent(
            type=IntentType.FOLLOWUP,
            confidence=0.70,
            requires_worker=True,
            raw_text=text,
        )

    return Intent(
        type=IntentType.GENERAL,
        confidence=0.50,
        requires_worker=True,
        raw_text=text,
    )


def intent_label(intent_type: IntentType) -> str:
    """Human-readable label for logging."""
    return {
        IntentType.REMINDER: "Reminder",
        IntentType.SCHEDULE: "Schedule",
        IntentType.RECALL: "Recall",
        IntentType.PREFERENCE: "Preference",
        IntentType.COMPLETE: "Complete task",
        IntentType.GREETING: "Greeting",
        IntentType.STATUS: "Status check",
        IntentType.WEB_SEARCH: "Web search",
        IntentType.IDENTITY: "Identity",
        IntentType.GENERAL: "General",
        IntentType.FOLLOWUP: "Follow-up",
    }.get(intent_type, intent_type.value)
