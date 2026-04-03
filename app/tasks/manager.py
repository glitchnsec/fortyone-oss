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
from datetime import datetime, timezone
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
    tool_failure_counts: dict[str, int] = {}  # Track per-tool failure count
    failed_tools: dict[str, str] = {}  # tool_name -> user-facing reason

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
        # Note: content must be a string (not None) for some providers via OpenRouter
        assistant_msg = {
            "role": "assistant",
            "tool_calls": tool_calls,
        }
        if content:
            assistant_msg["content"] = content
        messages.append(assistant_msg)

        should_break_early = False

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
                        action_params=json.loads(tool_args_raw) if isinstance(
                            tool_args_raw, str) else tool_args_raw,
                        risk_level=risk,
                    )
                    await store.log_action(
                        user_id=user_id,
                        action_type=f"confirmation_requested:{tool_name}",
                        description=f"Awaiting user confirmation for {tool_name}",
                        outcome="pending",
                        trigger="user_request",
                    )

                description = _format_action_description(
                    tool_name, tool_args_raw)
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

            # Track tool failures — distinguish retryable (bad input) from capability (tool down)
            is_failed = "error" in tool_result
            error_type = tool_result.get("error", "") if is_failed else ""
            is_retryable = error_type in (
                "date_parse_failed", "invalid_input", "validation_error")

            if is_failed and is_retryable:
                # RETRYABLE: The tool works but the LLM sent bad input.
                # Feed the error back with specifics so the LLM can fix and retry.
                tool_failure_counts[tool_name] = tool_failure_counts.get(
                    tool_name, 0) + 1
                logger.info(
                    "TOOL_RETRYABLE  tool=%s  error=%s  attempt=%d  job_id=%s",
                    tool_name, error_type, tool_failure_counts[tool_name], job_id,
                )
                now_utc = datetime.now(timezone.utc).isoformat()
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": json.dumps({
                        "error": error_type,
                        "message": tool_result.get("user_message", tool_result.get("result", "")),
                        "hint": (
                            f"The date you provided was invalid or in the past. "
                            f"The current UTC time is {now_utc}. "
                            f"Please retry with a correct future date in ISO 8601 format."
                        ),
                    }),
                })
                # Do NOT remove the tool — let the LLM retry with corrected input

            elif is_failed:
                # CAPABILITY FAILURE: The tool itself is unavailable.
                tool_failure_counts[tool_name] = tool_failure_counts.get(
                    tool_name, 0) + 1
                user_reason = tool_result.get(
                    "user_message", tool_result["error"])
                failed_tools[tool_name] = user_reason

                logger.warning(
                    "TOOL_FAILED  tool=%s  job_id=%s  reason=%s",
                    tool_name, job_id, user_reason,
                )

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": json.dumps({
                        "error": True,
                        "message": f"The {tool_name} tool is currently unavailable.",
                    }),
                })

                # Remove the failed tool so it can't be retried
                tools = [t for t in tools if t["function"]
                         ["name"] != tool_name]

                # Remove deflection tools when a primary tool fails
                deflection_tools = {"create_reminder", "list_tasks"}
                if tool_name not in deflection_tools:
                    tools = [t for t in tools if t["function"]
                             ["name"] not in deflection_tools]
                    logger.info(
                        "DEFLECTION_BLOCKED  removed=%s  remaining=%s  job_id=%s",
                        deflection_tools, [t["function"]["name"]
                                           for t in tools], job_id,
                    )
            else:
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
                "TOOL_CALL  round=%d  tool=%s  risk=%s  status=%s  job_id=%s",
                round_num + 1, tool_name, risk,
                "degraded" if "error" in tool_result else "ok",
                job_id,
            )

        if should_break_early:
            break
    else:
        # Exhausted MAX_TOOL_ROUNDS without early break
        logger.warning(
            "TOOL_LIMIT_REACHED  rounds=%d  job_id=%s — forcing response",
            MAX_TOOL_ROUNDS, job_id,
        )

    # If the loop ended without a response (LLM kept calling tools until limit),
    # force a final text-only response
    if response_text is None:
        result = await llm_tools(
            messages=messages,
            tools=[],  # No tools — force text response
            mock_text="I've been working on your request. Here's what I found so far.",
            timeout_s=10.0,
        )
        response_text = result.get(
            "content") or "I've been working on your request but need a bit more time."

    # TRANSPARENCY GUARD: If any tools failed during this dispatch,
    # ensure the response acknowledges the limitation. The LLM often
    # produces responses that gloss over failures — prepend a clear note.
    if failed_tools and response_text:
        failure_summaries = [_tool_failure_user_message(
            name) for name in failed_tools]
        failure_note = " ".join(failure_summaries)
        # Only prepend if the response doesn't already mention the limitation
        limitation_keywords = ["can't search", "unable to search",
                               "not available", "couldn't search", "unavailable"]
        if not any(kw in response_text.lower() for kw in limitation_keywords):
            response_text = f"Heads up: {failure_note}\n\n{response_text}"

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
                logger.debug(
                    "upsert_profile_entry not yet available — skipping passive learning")
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
        "You are a VERY capable and wise personal assistant manager. Your the best at understanding the user's needs and you help them achieve their fullest potential. "
        "You can respond directly to simple questions "
        "or use tools and subagents to help with tasks like searching the web, managing email, checking "
        "calendar events, and handling reminders. "
        "You understand the tools you're equiped with very well.\n\n"
        "Guidelines:\n"
        "- It is important that you understand the ultimate intent of the user before deciding whether to act or delegate.\n"
        "- For simple questions or conversation, respond directly without delegating.\n"
        "- For tasks requiring external data (weather, email, calendar), use the appropriate tool.\n"
        "- If you're unable to find the write tool. Let the user know, no shame in that.\n"
        "- Keep responses concise and helpful — you're texting, not writing an essay.\n"
        "- If you use a tool, summarize the results naturally for the user.\n"
        "- Never expose raw tool output or JSON to the user.\n"
        "- If a tool fails or is unavailable, tell the user honestly — e.g., 'I can't search the web right now.'\n"
        # "- When the user says 'remind me to...' or 'set a reminder for...', ALWAYS use the create_reminder tool. "
        # "Setting reminders IS your job — you are a personal assistant that manages reminders and tasks.\n"
        # "- However, do NOT create a reminder as a substitute when a different tool fails. "
        # "For example, if web_search fails while the user asked you to find something, "
        # "don't create a reminder for the user to search manually — tell them the search is unavailable instead.\n"
        "- Do not mention technical details like API keys or configuration to the user."
    )

    # Add persona context
    if persona and persona != "shared":
        parts.append(f"\nCurrent persona context: {persona}")

    # Add user memories/preferences from context
    # memories is a dict {key: value} from MemoryStore.get_context_standard/full
    memories = context.get("memories", {})
    if memories:
        items = list(memories.items())[:10] if isinstance(
            memories, dict) else []
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
        args = json.loads(tool_args_raw) if isinstance(
            tool_args_raw, str) else tool_args_raw
    except json.JSONDecodeError:
        return f"perform {tool_name}"

    if tool_name == "send_email":
        return f"send an email to {args.get('to', 'someone')} about \"{args.get('subject', 'something')}\""
    elif tool_name == "create_event":
        return f"create a calendar event \"{args.get('summary', 'event')}\" at {args.get('start_time', 'the scheduled time')}"
    else:
        return f"perform {tool_name} with {json.dumps(args)[:100]}"


def _tool_failure_user_message(tool_name: str) -> str:
    """Map tool names to user-friendly failure explanations. No technical details."""
    messages = {
        "web_search": "I can't search the web right now — that feature isn't available at the moment.",
        "send_email": "I can't send emails right now — the email connection needs to be set up.",
        "read_emails": "I can't read your emails right now — the email connection needs to be set up.",
        "list_events": "I can't check your calendar right now — the calendar connection needs to be set up.",
        "create_event": "I can't create calendar events right now — the calendar connection needs to be set up.",
        "create_reminder": "I wasn't able to create that reminder.",
        "list_tasks": "I wasn't able to check your tasks.",
    }
    return messages.get(tool_name, f"the {tool_name} tool isn't available right now.")


async def _execute_tool(tool_name: str, tool_args_raw: str, payload: dict) -> dict:
    """
    Execute a tool by name with the given arguments.

    For now, tools that call the connections service use httpx.
    Local tools (web_search, reminder, recall) are called directly.
    """
    try:
        tool_args = json.loads(tool_args_raw) if isinstance(
            tool_args_raw, str) else tool_args_raw
    except json.JSONDecodeError:
        return {"error": f"Invalid tool arguments: {tool_args_raw[:100]}"}

    user_id = payload.get("user_id", "")

    try:
        if tool_name == "web_search":
            from app.tasks.web_search import handle_web_search
            search_payload = {
                **payload, "body": tool_args.get("query", payload.get("body", ""))}
            result = await handle_web_search(search_payload)
            tool_result = {"results": result.get(
                "response", "No results found")}
            if result.get("degraded"):
                tool_result["error"] = result.get(
                    "user_reason", "Web search is not available")
                tool_result["degraded"] = True
                if result.get("admin_reason"):
                    logger.warning(
                        "TOOL_DEGRADED  tool=web_search  admin_reason=%s",
                        result["admin_reason"],
                    )
            return tool_result

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
            # Pass the ORIGINAL user message as body so the relative time
            # parser can find "in 5 mins", "in 2 hours" etc. The LLM's
            # title and due_at are passed separately via tool_args.
            original_body = payload.get("body", "")
            reminder_body = f"remind me to {tool_args.get('title', '')}"
            if tool_args.get("due_at"):
                reminder_body += f" at {tool_args['due_at']}"
            reminder_payload = {
                **payload,
                "body": reminder_body,
                "_original_body": original_body,  # for relative time fallback
            }
            result = await handle_reminder(reminder_payload)
            tool_result = {"result": result.get("response", "Reminder set")}
            # Propagate errors so the manager loop knows the tool partially failed
            if result.get("error"):
                tool_result["error"] = result["error"]
                tool_result["user_message"] = result.get("response", "")
            return tool_result

        elif tool_name == "list_tasks":
            from app.tasks.recall import handle_recall
            result = await handle_recall(payload)
            return {"result": result.get("response", "No tasks found")}

        else:
            return {"error": f"Unknown tool: {tool_name}"}

    except Exception as exc:
        logger.error("Tool execution failed tool=%s error=%s",
                     tool_name, exc, exc_info=True)
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
