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

# Cache of persona → connected service names (populated by pipeline)
# e.g. {"personal": ["Stan Store", "Notion"], "work": ["Google", "Notion"]}
_persona_tools: dict[str, list[str]] = {}
_persona_tools_user: str = ""  # user_id for cache invalidation


async def refresh_persona_tools(user_id: str, personas: list["Persona"]) -> None:
    """Fetch connected services per persona for richer detection context.

    Called once per inbound message in the pipeline. Cached per user_id —
    only re-fetches when the user changes.
    """
    global _persona_tools, _persona_tools_user
    if _persona_tools_user == user_id and _persona_tools:
        return  # already cached for this user

    import httpx
    from app.config import get_settings

    try:
        settings = get_settings()
        url = f"{settings.connections_service_url}/connections/{user_id}"
        headers = {"X-Service-Token": settings.service_auth_token} if settings.service_auth_token else {}
        async with httpx.AsyncClient(timeout=3.0, headers=headers) as client:
            resp = await client.get(url)
            resp.raise_for_status()

        connections = resp.json().get("connections", [])
        tools_by_persona: dict[str, list[str]] = {}
        persona_id_to_name = {str(p.id): p.name.lower() for p in personas}

        for conn in connections:
            if conn.get("status") != "connected":
                continue
            pid = conn.get("persona_id", "")
            pname = persona_id_to_name.get(pid, "shared")
            display = conn.get("display_name") or conn.get("provider", "").capitalize()
            if display and display not in tools_by_persona.get(pname, []):
                tools_by_persona.setdefault(pname, []).append(display)

        _persona_tools = tools_by_persona
        _persona_tools_user = user_id
    except Exception as exc:
        logger.debug("refresh_persona_tools failed: %s", exc)

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
Classify the user's message into the correct persona context.
Consider:
1. The message content and intent
2. Which persona has the relevant connected services (listed below)
3. Recent conversation context
If the user mentions a specific service (e.g. "Stan Store", "Notion"), route to the persona that has it connected.
If genuinely unclear, return "shared".
Return JSON only: {"persona": "<persona_name>", "confidence": 0.0-1.0}"""


async def detect_persona(
    body: str,
    user_personas: list["Persona"],
    recent_messages: list[dict],
    last_persona: Optional[str] = None,
    user_context: Optional[dict] = None,
) -> tuple[str, float, bool]:
    """
    Returns (persona_name, confidence, needs_clarification).

    persona_name: one of the user's persona .name values, or "shared".
    confidence: 0.0-1.0 (1.0 from rule fast-path, 0.5 from fallback).
    needs_clarification: True when LLM confidence < CLARIFICATION_THRESHOLD
        and the message is not a trivial follow-up. The pipeline (Plan 05)
        should send a clarifying question and return early — never guess wrong.

    user_context: optional dict with user profile info (name, timezone, etc.)
        from the pipeline's context assembly. Gives the LLM more signal.

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

        # Build rich persona descriptions including connected tools/services
        persona_lines = []
        for p in user_personas:
            desc = p.description or "no description"
            line = f"{p.name}: {desc}"
            # Include connected services if available (fetched by pipeline)
            tools = _persona_tools.get(p.name.lower(), [])
            if tools:
                line += f" | Connected services: {', '.join(tools)}"
            persona_lines.append(line)
        persona_descriptions = "\n".join(persona_lines)

        history = [
            {
                "role": "user" if m.get("direction") == "inbound" else "assistant",
                "content": m.get("body", ""),
            }
            for m in recent_messages[-4:]
        ]
        # Add user context if available (name, profile, etc.)
        user_info = ""
        if user_context:
            name = user_context.get("name")
            if name:
                user_info += f"\nUser's name: {name}"
            tz = user_context.get("timezone")
            if tz:
                user_info += f"\nTimezone: {tz}"

        result = await llm_messages_json(
            messages=[
                {
                    "role": "system",
                    "content": _PERSONA_SYSTEM + f"\n\nUser's personas:\n{persona_descriptions}{user_info}",
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
