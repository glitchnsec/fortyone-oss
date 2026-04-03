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
    NEEDS_MANAGER = "needs_manager"


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


# Regex fast-path: only GREETING and IDENTITY (zero latency, high accuracy)
# All other intents route to NEEDS_MANAGER for LLM-powered classification (AGENT-07)
_RULES: list[tuple[IntentType, str, float]] = [
    (IntentType.IDENTITY, r"\b(what'?s?\s+your\s+name|who\s+are\s+you|what\s+are\s+you|what\s+should\s+i\s+call\s+you|tell\s+me\s+(about\s+)?yourself)\b", 0.95),
    (IntentType.GREETING, r"^(hi|hello|hey(\s+there)?|good\s+(morning|afternoon|evening)|sup|what'?s\s+up)\s*[!?.]?\s*$", 0.95),
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

    # Everything else: route to manager LLM dispatch in worker (AGENT-07)
    return Intent(
        type=IntentType.NEEDS_MANAGER,
        confidence=1.0,
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
        IntentType.NEEDS_MANAGER: "Manager dispatch",
    }.get(intent_type, intent_type.value)
