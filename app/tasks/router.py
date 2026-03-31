"""
Routes a queued job to the correct task handler based on intent.
"""
import logging

from app.core.intent import IntentType

logger = logging.getLogger(__name__)


async def route_job(payload: dict) -> dict:
    intent_str: str = payload.get("intent", "general")

    try:
        intent = IntentType(intent_str)
    except ValueError:
        intent = IntentType.GENERAL

    logger.debug("Routing intent=%s job_id=%s", intent, payload.get("job_id"))

    if intent == IntentType.REMINDER:
        from app.tasks.reminder import handle_reminder
        return await handle_reminder(payload)

    if intent == IntentType.SCHEDULE:
        from app.tasks.scheduling import handle_scheduling
        return await handle_scheduling(payload)

    if intent in (IntentType.RECALL, IntentType.STATUS):
        from app.tasks.recall import handle_recall
        return await handle_recall(payload)

    if intent == IntentType.PREFERENCE:
        from app.tasks.reminder import handle_preference
        return await handle_preference(payload)

    if intent == IntentType.COMPLETE:
        from app.tasks.recall import handle_complete
        return await handle_complete(payload)

    from app.tasks.recall import handle_general
    return await handle_general(payload)
