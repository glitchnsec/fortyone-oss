"""
Routes a queued job to the correct task handler based on intent.

Handles 401 HTTPException from the connections service: translates it into a
user-readable "needs reauthorization" message (CONN-07) so the assistant
always sends an actionable reply rather than silently failing.
"""
import logging

from fastapi import HTTPException as FastAPIHTTPException

from app.core.intent import IntentType

logger = logging.getLogger(__name__)


async def route_job(payload: dict) -> dict:
    # Handle confirmed pending actions (from confirmation flow in pipeline)
    confirmed_action = payload.get("confirmed_action")
    if confirmed_action:
        import json
        from app.tasks.manager import _execute_tool
        tool_name = confirmed_action["type"]
        tool_args = json.dumps(confirmed_action["params"])
        tool_result = await _execute_tool(tool_name, tool_args, payload)

        # Log the confirmed action
        from app.database import AsyncSessionLocal
        from app.memory.store import MemoryStore
        async with AsyncSessionLocal() as db:
            store = MemoryStore(db)
            await store.log_action(
                user_id=payload.get("user_id", ""),
                action_type=tool_name,
                description=f"Executed confirmed action: {tool_name}",
                outcome="success" if "error" not in tool_result else "failed",
                trigger="user_request",
            )

        response = tool_result.get("result", tool_result.get("results", json.dumps(tool_result)))
        if isinstance(response, dict):
            response = json.dumps(response)
        return {
            "job_id": payload.get("job_id", ""),
            "phone": payload.get("phone", ""),
            "address": payload.get("address", payload.get("phone", "")),
            "channel": payload.get("channel", "sms"),
            "user_id": payload.get("user_id", ""),
            "response": f"Done! {str(response)[:300]}",
        }

    intent_str: str = payload.get("intent", "general")

    try:
        intent = IntentType(intent_str)
    except ValueError:
        intent = IntentType.GENERAL

    logger.debug("Routing intent=%s job_id=%s", intent, payload.get("job_id"))

    # ── Resolve handler ───────────────────────────────────────────────────────
    if intent == IntentType.REMINDER:
        from app.tasks.reminder import handle_reminder
        handler = handle_reminder

    elif intent == IntentType.SCHEDULE:
        from app.tasks.scheduling import handle_scheduling
        handler = handle_scheduling

    elif intent in (IntentType.RECALL, IntentType.STATUS):
        from app.tasks.recall import handle_recall
        handler = handle_recall

    elif intent == IntentType.PREFERENCE:
        from app.tasks.reminder import handle_preference
        handler = handle_preference

    elif intent == IntentType.COMPLETE:
        from app.tasks.recall import handle_complete
        handler = handle_complete

    elif intent == IntentType.WEB_SEARCH:
        from app.tasks.web_search import handle_web_search
        handler = handle_web_search

    elif intent == IntentType.NEEDS_MANAGER:
        # Manager dispatch handles its own return format — bypass normal handler flow
        from app.tasks.manager import manager_dispatch
        try:
            result = await manager_dispatch(payload)
        except FastAPIHTTPException as exc:
            if exc.status_code == 401:
                job_id = payload.get("job_id", "")
                phone = payload.get("phone", "")
                logger.warning(
                    "401 from connections service — needs reauth job_id=%s phone=%s", job_id, phone
                )
                return {
                    "job_id": job_id,
                    "phone": phone,
                    "address": payload.get("address", phone),
                    "channel": payload.get("channel", "sms"),
                    "user_id": payload.get("user_id", ""),
                    "response": (
                        "Your Google connection needs reauthorization. "
                        "Visit your dashboard connections page to reconnect."
                    ),
                }
            raise
        return result

    else:
        from app.tasks.recall import handle_general
        handler = handle_general

    # ── Dispatch with 401 reauth interception (CONN-07) ───────────────────────
    try:
        result = await handler(payload)
    except FastAPIHTTPException as exc:
        if exc.status_code == 401:
            job_id = payload.get("job_id", "")
            phone = payload.get("phone", "")
            logger.warning(
                "401 from connections service — needs reauth job_id=%s phone=%s", job_id, phone
            )
            return {
                "job_id": job_id,
                "phone": phone,
                "address": payload.get("address", phone),
                "channel": payload.get("channel", "sms"),
                "user_id": payload.get("user_id", ""),
                "response": (
                    "Your Google connection needs reauthorization. "
                    "Visit your dashboard connections page to reconnect."
                ),
            }
        raise

    return result
