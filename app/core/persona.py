"""
Persona detection — classifies inbound messages as work / personal context.

Detection strategy (per D-08):
  1. Rule fast-path: check for strong work/personal signals (no LLM cost)
  2. If last_persona is set and no clear signal: inherit last persona
  3. LLM disambiguation for genuinely ambiguous messages
  4. Fallback to "shared" on any error or no personas defined

Returns (persona_name, confidence, needs_clarification).

needs_clarification=True when:
  - LLM confidence < 0.6 AND message is not a short follow-up
  - The pipeline sends a single clarifying question and returns early
    (Plan 05). This prevents the assistant from guessing wrong twice (D-08).

detect_persona() is called once per inbound message in the pipeline.
The result is cached in the job payload — not re-detected in task handlers.
"""
import logging
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from app.memory.models import Persona

logger = logging.getLogger(__name__)

# Confidence threshold below which the assistant should ask for clarification
CLARIFICATION_THRESHOLD = 0.4

# Work signals — strong indicators that the message is work context
_WORK_SIGNALS = [
    "meeting", "standup", "sprint", "client", "deadline", "project",
    "manager", "colleague", "office", "work email", "slack", "jira",
    "quarterly", "budget", "proposal", "presentation", "team",
]

# Personal signals — strong indicators that the message is personal context
_PERSONAL_SIGNALS = [
    "gym", "doctor", "dentist", "kids", "family", "dinner", "weekend",
    "vacation", "holiday", "personal", "friend", "partner", "spouse",
    "birthday", "anniversary", "home", "grocery", "pickup",
]

_PERSONA_SYSTEM = """\
Classify the user's message as 'work' or 'personal' context.
Work: job, meetings, colleagues, professional tasks, work email, projects.
Personal: family, health, hobbies, social plans, home tasks, personal appointments.
If genuinely unclear, return "shared".
Return JSON only: {"persona": "work"|"personal"|"shared", "confidence": 0.0-1.0}"""


async def detect_persona(
    body: str,
    user_personas: list["Persona"],
    recent_messages: list[dict],
    last_persona: Optional[str] = None,
) -> tuple[str, float, bool]:
    """
    Returns (persona_name, confidence, needs_clarification).

    persona_name: one of the user's persona .name values, or "shared".
    confidence: 0.0-1.0 (1.0 from rule fast-path, 0.5 from fallback).
    needs_clarification: True when LLM confidence < CLARIFICATION_THRESHOLD
        and the message is not a trivial follow-up. The pipeline (Plan 05)
        should send a clarifying question and return early — never guess wrong.

    Never raises — falls back to ("shared", 0.5, False) on any error.
    """
    if not user_personas:
        return ("shared", 0.5, False)

    persona_names = {p.name.lower() for p in user_personas}
    lower_body = body.lower()

    # Rule fast-path: check strong signals
    work_hit = any(sig in lower_body for sig in _WORK_SIGNALS)
    personal_hit = any(sig in lower_body for sig in _PERSONAL_SIGNALS)

    if work_hit and not personal_hit:
        work_name = next((p.name for p in user_personas if p.name.lower() == "work"), "work")
        if work_name.lower() in persona_names:
            logger.debug("PERSONA_DETECT rule=work body=%r", body[:40])
            return (work_name, 0.90, False)

    if personal_hit and not work_hit:
        personal_name = next(
            (p.name for p in user_personas if p.name.lower() == "personal"), "personal"
        )
        if personal_name.lower() in persona_names:
            logger.debug("PERSONA_DETECT rule=personal body=%r", body[:40])
            return (personal_name, 0.90, False)

    # Inherit last persona for short/ambiguous follow-ups (per D-08 — never wrong twice)
    if last_persona and last_persona in persona_names and len(body.split()) < 10:
        logger.debug(
            "PERSONA_DETECT inherit last_persona=%r body=%r", last_persona, body[:40]
        )
        return (last_persona, 0.75, False)

    # LLM disambiguation for genuinely ambiguous messages
    try:
        from app.tasks._llm import llm_messages_json
        persona_descriptions = "\n".join(
            f"{p.name}: {p.description or 'no description'}" for p in user_personas
        )
        history = [
            {
                "role": "user" if m.get("direction") == "inbound" else "assistant",
                "content": m.get("body", ""),
            }
            for m in recent_messages[-4:]
        ]
        result = await llm_messages_json(
            messages=[
                {
                    "role": "system",
                    "content": _PERSONA_SYSTEM + f"\n\nUser's personas:\n{persona_descriptions}",
                },
                *history,
                {"role": "user", "content": body},
            ],
            mock_payload={"persona": "shared", "confidence": 0.5},
            timeout_s=2.0,
        )
        persona_detected = result.get("persona", "shared")
        confidence = float(result.get("confidence", 0.5))

        # Map LLM response to actual persona name
        matched = next(
            (p.name for p in user_personas if p.name.lower() == persona_detected.lower()),
            "shared",
        )

        # Signal clarification needed when LLM is uncertain and this isn't a short follow-up
        # (per D-08: ask once rather than guess wrong)
        needs_clarification = (
            confidence < CLARIFICATION_THRESHOLD
            and len(body.split()) >= 5  # short messages inherit last persona above
        )

        logger.info(
            "PERSONA_DETECT llm=%r confidence=%.2f needs_clarification=%s body=%r",
            matched, confidence, needs_clarification, body[:40],
        )
        return (matched, confidence, needs_clarification)

    except Exception as exc:
        logger.warning("PERSONA_DETECT error=%s — falling back to shared", exc)
        return ("shared", 0.5, False)
