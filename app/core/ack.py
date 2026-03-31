"""
ACK message generator.

These fire < 500ms after receiving an SMS — before any heavy processing.
They set the tone: helpful, concise, human.
"""
import random

from app.core.intent import IntentType

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

_GREETING_RESPONSES = [
    "Hey! What can I help you with today?",
    "Hi there! I'm here — what do you need?",
    "Hello! Ready to help. What's on your mind?",
]


def get_ack(intent_type: IntentType) -> str:
    if intent_type == IntentType.GREETING:
        return random.choice(_GREETING_RESPONSES)
    pool = _ACK_POOL.get(intent_type, _ACK_POOL[IntentType.GENERAL])
    return random.choice(pool)
