"""
First-message greeter.

New users get one warm, natural intro on their very first message.
No steps, no forms — the greeting is just the ACK for message #1.

After that, profile info is collected passively through conversation:
  • handle_general extracts name/timezone/email/assistant-name from any
    message where the user mentions them, and stores them as learn signals.
  • Task handlers ask inline (JIT) when they need a missing field.
"""
import logging

logger = logging.getLogger(__name__)

_MOCK = (
    "Hey! I'm your personal assistant — I can set reminders, help with "
    "scheduling, and keep track of things for you. What's on your mind?"
)


async def first_greeting(
    channel_name: str,
    body: str,
    assistant_name: str | None = None,
    personality_notes: str | None = None,
) -> str:
    """
    Generate a casual intro that also acknowledges the user's first message.
    Replaces the smart ACK for message #1 only.
    """
    from app.core.identity import identity_preamble
    from app.tasks._llm import llm_text

    identity = identity_preamble(
        assistant_name=assistant_name,
        personality_notes=personality_notes,
        channel_hint=channel_name,
    )

    return await llm_text(
        system=(
            f"{identity} "
            "This is the user's very first message to you. "
            "Reply in 2-3 sentences: quickly introduce yourself (you help with "
            "reminders, tasks, scheduling), then naturally address what they said. "
            "Casual tone, no bullet lists, no forms, no asking for personal details."
        ),
        messages=[{"role": "user", "content": body}],
        mock_text=_MOCK,
        timeout_s=3.0,
    )
