"""
Manager orchestrator — the core of the proactive agent architecture.

The manager receives messages that passed through the NEEDS_MANAGER intent
classification. It uses LLM tool-calling to decide both what the intent is
and how to handle it in one pass.

Per D-05: The pipeline is a "manager" that responds directly to simple queries
and delegates to subagents (tools) for complex tasks. Subagents return results
to the manager; manager formats with user personality and sends.

Per D-12: After generating a response, the manager optionally extracts
profile-relevant facts from the conversation and calls upsert_profile_entry
to passively enrich the user's TELOS profile.

Per Common Pitfall 2: Hard limit of 3 tool-calling rounds to prevent infinite loops.
"""
import asyncio
import json
import logging
from typing import Any

from app.config import get_settings
from app.core.tools import get_tool_schemas, get_tool_risk

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 3  # Hard limit per Pitfall 2


async def manager_dispatch(payload: dict) -> dict:
    """
    Process a NEEDS_MANAGER job using LLM tool-calling.

    Args:
        payload: Job payload with keys: job_id, phone, address, body, context,
                 user_id, persona, channel

    Returns:
        Standard result dict: {job_id, phone, response, channel, address, learn}
    """
    job_id = payload.get("job_id", "")
    phone = payload.get("phone", "")
    address = payload.get("address", phone)
    channel = payload.get("channel", "sms")
    body = payload.get("body", "")
    context = payload.get("context", {})
    user_id = payload.get("user_id", "")
    persona = payload.get("persona", "shared")

    # Build system prompt with personality and context
    system_prompt = _build_system_prompt(payload)

    # Build conversation messages from context
    messages = [{"role": "system", "content": system_prompt}]

    # Add recent conversation history from context
    recent = context.get("recent_messages", [])
    for msg in recent[-6:]:  # Last 6 messages for context window
        role = "user" if msg.get("direction") == "inbound" else "assistant"
        messages.append({"role": role, "content": msg.get("body", "")})

    # Current user message
    messages.append({"role": "user", "content": body})

    # Get available tool schemas
    tools = get_tool_schemas()

    # Manager dispatch loop with tool calling
    from app.tasks._llm import llm_tools

    response_text = None
    learn_signals = {}

    for round_num in range(MAX_TOOL_ROUNDS):
        result = await llm_tools(
            messages=messages,
            tools=tools,
            mock_text=f"I understand you said: '{body[:50]}'. Let me help with that.",
            timeout_s=15.0,
        )

        tool_calls = result.get("tool_calls")
        content = result.get("content")

        if not tool_calls:
            # No tool calls — LLM produced a direct response
            response_text = content or f"I heard you. Let me think about '{body[:30]}'..."
            break

        # Execute tool calls and feed results back
        # Append assistant message with tool calls
        messages.append({
            "role": "assistant",
            "content": content,
            "tool_calls": tool_calls,
        })

        for tc in tool_calls:
            tool_name = tc["function"]["name"]
            tool_args_raw = tc["function"]["arguments"]
            tool_call_id = tc["id"]

            risk = get_tool_risk(tool_name)

            # D-04: Confirmation for medium/high risk tools
            if risk in ("medium", "high"):
                from app.database import AsyncSessionLocal
                from app.memory.store import MemoryStore
                async with AsyncSessionLocal() as db:
                    store = MemoryStore(db)
                    pending = await store.create_pending_action(
                        user_id=user_id,
                        action_type=tool_name,
                        action_params=json.loads(tool_args_raw) if isinstance(tool_args_raw, str) else tool_args_raw,
                        risk_level=risk,
                    )
                    await store.log_action(
                        user_id=user_id,
                        action_type=f"confirmation_requested:{tool_name}",
                        description=f"Awaiting user confirmation for {tool_name}",
                        outcome="pending",
                        trigger="user_request",
                    )

                description = _format_action_description(tool_name, tool_args_raw)
                response_text = f"I'd like to {description}. Should I go ahead? (Reply YES or NO)"

                return {
                    "job_id": job_id,
                    "phone": phone,
                    "address": address,
                    "channel": channel,
                    "response": response_text,
                    "learn": {"pending_action_id": pending.id},
                }

            tool_result = await _execute_tool(tool_name, tool_args_raw, payload)

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": json.dumps(tool_result),
            })

            # Log the action for AGENT-06
            learn_signals["action_log"] = {
                "action_type": tool_name,
                "outcome": "success" if "error" not in tool_result else "failed",
                "trigger": "user_request",
            }

            logger.info(
                "TOOL_CALL  round=%d  tool=%s  risk=%s  job_id=%s",
                round_num + 1, tool_name, risk, job_id,
            )
    else:
        # Exhausted MAX_TOOL_ROUNDS — force a response
        logger.warning(
            "TOOL_LIMIT_REACHED  rounds=%d  job_id=%s — forcing response",
            MAX_TOOL_ROUNDS, job_id,
        )
        # One final call without tools to get a summary
        result = await llm_tools(
            messages=messages,
            tools=[],  # No tools — force text response
            mock_text="I've been working on your request. Here's what I found so far.",
            timeout_s=10.0,
        )
        response_text = result.get("content") or "I've been working on your request but need a bit more time."

    # D-12: Passive profile learning — extract profile-relevant facts from conversation
    # Runs asynchronously after response to avoid adding latency
    if user_id and body:
        asyncio.create_task(_passive_profile_learn(user_id, body, persona))

    return {
        "job_id": job_id,
        "phone": phone,
        "address": address,
        "channel": channel,
        "response": response_text,
        "learn": learn_signals,
    }


async def _passive_profile_learn(user_id: str, message_text: str, persona: str = "shared") -> None:
    """
    D-12: Passively extract profile-relevant facts from user messages.

    Uses the fast LLM model to check if the message contains self-knowledge
    (preferences, goals, challenges, biographical facts). If found, upserts
    into the UserProfile table via store.upsert_profile_entry.

    This runs as a fire-and-forget task — failures are logged but never
    block the response.
    """
    try:
        from app.tasks._llm import llm_json

        extraction = await llm_json(
            prompt=(
                "Analyze this user message for personal facts worth remembering. "
                "Extract ONLY clearly stated facts about the user (preferences, goals, "
                "challenges, biographical details, work info). Do NOT extract requests or questions.\n\n"
                f"Message: \"{message_text[:500]}\"\n\n"
                "Return JSON: {\"facts\": [{\"section\": \"<TELOS section>\", "
                "\"label\": \"<short label>\", \"content\": \"<the fact>\"}]} "
                "where section is one of: preferences, mission, goals, challenges, "
                "wisdom, ideas, predictions, history, narratives, problems.\n"
                "If no personal facts found, return {\"facts\": []}."
            ),
            mock_payload={"facts": []},
            timeout_s=3.0,
        )

        facts = extraction.get("facts", [])
        if not facts:
            return

        from app.database import AsyncSessionLocal
        from app.memory.store import MemoryStore

        async with AsyncSessionLocal() as db:
            store = MemoryStore(db)

            # Check if upsert_profile_entry is available (added in plan 04-04)
            if not hasattr(store, "upsert_profile_entry"):
                logger.debug("upsert_profile_entry not yet available — skipping passive learning")
                return

            for fact in facts[:3]:  # Cap at 3 facts per message to avoid noise
                section = fact.get("section", "preferences")
                label = fact.get("label", "")
                content = fact.get("content", "")
                if label and content:
                    await store.upsert_profile_entry(
                        user_id=user_id,
                        section=section,
                        label=label,
                        content=content,
                        persona_id=None,  # Passive learning is persona-agnostic
                    )
                    logger.info(
                        "PASSIVE_LEARN  user=%s  section=%s  label=%s",
                        user_id[:8], section, label,
                    )

    except Exception as exc:
        # Never fail the response due to passive learning
        logger.warning("Passive profile learning failed: %s", exc)


def _build_system_prompt(payload: dict) -> str:
    """Build the manager's system prompt with personality and context."""
    context = payload.get("context", {})
    persona = payload.get("persona", "shared")

    # Start with identity preamble if available
    parts = []
    try:
        from app.core.identity import identity_preamble
        preamble = identity_preamble(
            assistant_name=context.get("assistant_name"),
            personality_notes=context.get("personality_notes"),
        )
        if preamble:
            parts.append(preamble)
    except ImportError:
        pass

    parts.append(
        "You are a personal assistant manager. You can respond directly to simple questions "
        "or use tools to help with tasks like searching the web, managing email, checking "
        "calendar events, and handling reminders.\n\n"
        "Guidelines:\n"
        "- For simple questions or conversation, respond directly without tools.\n"
        "- For tasks requiring external data (weather, email, calendar), use the appropriate tool.\n"
        "- Keep responses concise and helpful — you're texting, not writing an essay.\n"
        "- If you use a tool, summarize the results naturally for the user.\n"
        "- Never expose raw tool output or JSON to the user."
    )

    # Add persona context
    if persona and persona != "shared":
        parts.append(f"\nCurrent persona context: {persona}")

    # Add user memories/preferences from context
    # memories is a dict {key: value} from MemoryStore.get_context_standard/full
    memories = context.get("memories", {})
    if memories:
        items = list(memories.items())[:10] if isinstance(memories, dict) else []
        if items:
            mem_text = "\n".join(f"- {k}: {v}" for k, v in items)
            parts.append(f"\nWhat you know about this user:\n{mem_text}")

    # D-13: Include profile traits if available in context (injected by tiered context in plan 04-03)
    profile_traits = context.get("profile_traits", [])
    if profile_traits:
        trait_text = "\n".join(
            f"- [{t.get('section', '')}] {t.get('label', '')}: {t.get('content', '')}"
            for t in profile_traits[:10]
        )
        parts.append(f"\nUser profile:\n{trait_text}")

    # Also check for full profile entries (full context tier)
    profile_entries = context.get("profile_entries", [])
    if profile_entries and not profile_traits:
        entry_text = "\n".join(
            f"- [{e.get('section', '')}] {e.get('label', '')}: {e.get('content', '')}"
            for e in profile_entries[:15]
        )
        parts.append(f"\nUser profile (TELOS):\n{entry_text}")

    return "\n\n".join(parts)


def _format_action_description(tool_name: str, tool_args_raw: str) -> str:
    """Format a human-readable description of a tool call for confirmation."""
    try:
        args = json.loads(tool_args_raw) if isinstance(tool_args_raw, str) else tool_args_raw
    except json.JSONDecodeError:
        return f"perform {tool_name}"

    if tool_name == "send_email":
        return f"send an email to {args.get('to', 'someone')} about \"{args.get('subject', 'something')}\""
    elif tool_name == "create_event":
        return f"create a calendar event \"{args.get('summary', 'event')}\" at {args.get('start_time', 'the scheduled time')}"
    else:
        return f"perform {tool_name} with {json.dumps(args)[:100]}"


async def _execute_tool(tool_name: str, tool_args_raw: str, payload: dict) -> dict:
    """
    Execute a tool by name with the given arguments.

    For now, tools that call the connections service use httpx.
    Local tools (web_search, reminder, recall) are called directly.
    """
    try:
        tool_args = json.loads(tool_args_raw) if isinstance(tool_args_raw, str) else tool_args_raw
    except json.JSONDecodeError:
        return {"error": f"Invalid tool arguments: {tool_args_raw[:100]}"}

    user_id = payload.get("user_id", "")

    try:
        if tool_name == "web_search":
            from app.tasks.web_search import handle_web_search
            search_payload = {**payload, "body": tool_args.get("query", payload.get("body", ""))}
            result = await handle_web_search(search_payload)
            return {"results": result.get("response", "No results found")}

        elif tool_name == "read_emails":
            return await _call_connections_tool(
                "gmail", "read_emails", user_id, tool_args
            )

        elif tool_name == "send_email":
            return await _call_connections_tool(
                "gmail", "send_email", user_id, tool_args
            )

        elif tool_name == "list_events":
            return await _call_connections_tool(
                "calendar", "list_events", user_id, tool_args
            )

        elif tool_name == "create_event":
            return await _call_connections_tool(
                "calendar", "create_event", user_id, tool_args
            )

        elif tool_name == "create_reminder":
            from app.tasks.reminder import handle_reminder
            reminder_payload = {
                **payload,
                "body": f"remind me to {tool_args.get('title', '')}",
            }
            if tool_args.get("due_at"):
                reminder_payload["body"] += f" at {tool_args['due_at']}"
            result = await handle_reminder(reminder_payload)
            return {"result": result.get("response", "Reminder set")}

        elif tool_name == "list_tasks":
            from app.tasks.recall import handle_recall
            result = await handle_recall(payload)
            return {"result": result.get("response", "No tasks found")}

        else:
            return {"error": f"Unknown tool: {tool_name}"}

    except Exception as exc:
        logger.error("Tool execution failed tool=%s error=%s", tool_name, exc, exc_info=True)
        return {"error": f"Tool {tool_name} failed: {str(exc)[:200]}"}


async def _call_connections_tool(
    service: str, action: str, user_id: str, params: dict
) -> dict:
    """Call the connections service via HTTP (per research: maintain service isolation)."""
    import httpx
    settings = get_settings()

    try:
        async with httpx.AsyncClient(
            base_url=settings.connections_service_url, timeout=10.0
        ) as client:
            resp = await client.post(
                f"/tools/{service}/{action}",
                json={"user_id": user_id, **params},
            )
            if resp.status_code == 401:
                return {
                    "error": "needs_reauth",
                    "message": "Your connection needs reauthorization. Visit your dashboard to reconnect.",
                }
            resp.raise_for_status()
            return resp.json()
    except httpx.ConnectError:
        return {"error": "Connection service unavailable. I'll try again later."}
    except Exception as exc:
        return {"error": f"Service error: {str(exc)[:200]}"}
