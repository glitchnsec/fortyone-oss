"""
Message pipeline — orchestrates the state machine for every inbound message.

State flow:  RECEIVED → ACK → THINK → ACT → CONFIRM → LEARN

Channel-agnostic: the pipeline depends only on the Channel ABC.
SMS, Slack, or any future channel plugs in without touching this file.

MessagePipeline  : fast path (runs inside FastAPI background task)
ResponseListener : listens for worker results and delivers the final reply
"""
import asyncio
import json
import logging
from enum import Enum
from typing import TYPE_CHECKING

from app.core.ack import get_smart_ack
from app.core.greeter import first_greeting
from app.core.intent import IntentType, classify_intent, intent_label
from app.memory.store import MemoryStore
from app.queue.client import QueueClient

if TYPE_CHECKING:
    from app.channels.base import Channel

logger = logging.getLogger(__name__)


class MessageState(str, Enum):
    RECEIVED = "received"
    ACK      = "ack"
    THINK    = "think"
    ACT      = "act"
    CONFIRM  = "confirm"
    LEARN    = "learn"
    DONE     = "done"


# Intent → context tier mapping (D-05 / PERS-08)
# GREETING is handled before context assembly (early return after ACK)
_FULL_CONTEXT_INTENTS = frozenset({
    IntentType.SCHEDULE,
    IntentType.GENERAL,
    IntentType.WEB_SEARCH,
    IntentType.NEEDS_MANAGER,  # Manager gets full context for tool-calling decisions
})
_STANDARD_CONTEXT_INTENTS = frozenset({
    IntentType.REMINDER,
    IntentType.RECALL,
    IntentType.COMPLETE,
    IntentType.PREFERENCE,
    IntentType.STATUS,
    IntentType.FOLLOWUP,
})

# Intents that should never trigger persona clarification — they use "shared" context
# RECALL: "What do you know about me?" should not ask work vs personal
# IDENTITY: "what's your name?" — always answered directly, no clarification needed
_SKIP_CLARIFICATION_INTENTS = frozenset({IntentType.RECALL, IntentType.IDENTITY})

# Clarifying question text (per D-08 — sent once when LLM is uncertain about work vs personal)
_CLARIFYING_QUESTION = (
    "Just to make sure I use the right account — is this for work or personal?"
)


class MessagePipeline:
    def __init__(
        self,
        channel: "Channel",
        queue: QueueClient,
        store: MemoryStore,
    ) -> None:
        self.channel = channel
        self.queue = queue
        self.store = store

    async def handle(self, address: str, body: str) -> None:
        """
        Entry point.  Runs in a FastAPI BackgroundTask so the HTTP 200 to
        the channel webhook has already been returned before this executes.

        `address` is the channel-specific user identifier:
          SMS   → E.164 phone number, e.g. "+15551234567"
          Slack → Slack User ID,       e.g. "U01ABC123"
        """
        # ── RECEIVED ─────────────────────────────────────────────────────────
        user = await self.store.get_or_create_user(address)
        return await self._process(user, address, body)

    async def handle_with_user(self, user, body: str) -> None:
        """Entry point for Slack onboarding path — user already resolved."""
        address = user.slack_user_id or user.phone
        return await self._process(user, address, body)

    async def _process(self, user, address: str, body: str) -> None:
        """Shared processing logic for handle() and handle_with_user()."""

        # ── CLASSIFY ─────────────────────────────────────────────────────────
        intent = classify_intent(body)

        logger.info(
            "RECEIVED  channel=%s  address=%s  intent=%s  body=%r",
            self.channel.name, address, intent_label(intent.type), body[:60],
        )

        # Single write per inbound message — includes intent once classified
        await self.store.store_message(
            user_id=user.id,
            direction="inbound",
            body=body,
            intent=intent.type.value,
            state=MessageState.THINK.value,
            channel=self.channel.name,   # D-01, D-02: scope message to originating channel
        )

        # ── MINIMAL CONTEXT (for ACK — no embedding, fast) ───────────────────
        # get_context_minimal returns recent messages and last_persona for:
        #   1. Context-aware ACK (D-12)
        #   2. Persona inheritance across turns (D-08)
        minimal_ctx = await self.store.get_context_minimal(user.id, channel=self.channel.name)
        recent_messages = minimal_ctx.get("recent_messages", [])
        last_persona = minimal_ctx.get("last_persona")   # D-08: inherit across turns

        # ── FIRST MESSAGE — warm intro (fast path, no race) ───────────────────
        is_first = await self.store.message_count(user.id) == 1
        if is_first:
            ack_text = await first_greeting(
                self.channel.name, body,
                assistant_name=getattr(user, "assistant_name", None),
                personality_notes=getattr(user, "personality_notes", None),
            )
            logger.info("FIRST_MESSAGE  channel=%s  address=%s", self.channel.name, address)
            await self.channel.send(address, ack_text)
            await self.store.store_message(
                user_id=user.id, direction="outbound", body=ack_text,
                state=MessageState.ACK.value, channel=self.channel.name,
                persona_tag=last_persona,
            )
            # First greeting IS the response — no worker needed
            return

        # Identity questions: respond with configured name/personality, no worker
        if intent.type == IntentType.IDENTITY:
            assistant_name = getattr(user, "assistant_name", None)
            personality_notes = getattr(user, "personality_notes", None)
            if assistant_name:
                identity_text = f"I'm {assistant_name}, your personal assistant!"
                if personality_notes:
                    identity_text += f" {personality_notes}"
            else:
                identity_text = "I'm your personal assistant! You can give me a name in your settings."
            await self.channel.send(address, identity_text)
            await self.store.store_message(
                user_id=user.id, direction="outbound", body=identity_text,
                state=MessageState.DONE.value, channel=self.channel.name,
                persona_tag=last_persona,
            )
            return

        # Standalone greetings: respond immediately, no worker
        if intent.type == IntentType.GREETING:
            ack_text = await get_smart_ack(
                intent.type, body, user_name=user.name,
                recent_messages=recent_messages,
                assistant_name=getattr(user, "assistant_name", None),
                personality_notes=getattr(user, "personality_notes", None),
            )
            await self.channel.send(address, ack_text)
            await self.store.store_message(
                user_id=user.id, direction="outbound", body=ack_text,
                state=MessageState.DONE.value, channel=self.channel.name,
                persona_tag=last_persona,
            )
            return

        # ── CONFIRMATION RESOLUTION (D-04) ──────────────────────────────────
        # Check if user is responding to a pending action confirmation
        lower_body = body.lower().strip()
        if lower_body in ("yes", "no", "y", "n", "yeah", "nah", "nope", "yep", "go ahead", "cancel"):
            pending = await self.store.get_pending_action(user.id)
            if pending:
                is_confirmed = lower_body in ("yes", "y", "yeah", "yep", "go ahead")
                if is_confirmed:
                    status = "confirmed"
                    await self.store.resolve_pending_action(pending.id, status)
                    # Queue the tool execution
                    job_id = await self.queue.push_job({
                        "channel": self.channel.name,
                        "address": address,
                        "phone": address,
                        "body": f"Execute confirmed action: {pending.action_type}",
                        "intent": "needs_manager",
                        "context": await self.store.get_context_full(user.id, channel=self.channel.name, query=body),
                        "user_id": user.id,
                        "persona": "shared",
                        "confirmed_action": {
                            "type": pending.action_type,
                            "params": json.loads(pending.action_params_json),
                        },
                    })
                    ack_text = "Got it, I'm on it!"
                else:
                    status = "rejected"
                    await self.store.resolve_pending_action(pending.id, status)
                    ack_text = "No problem, I've cancelled that."

                await self.channel.send(address, ack_text)
                await self.store.store_message(
                    user_id=user.id, direction="outbound", body=ack_text,
                    state=MessageState.DONE.value, channel=self.channel.name,
                )
                logger.info("CONFIRMATION_%s  user=%s  action=%s", status.upper(), user.id[:8], pending.action_type)
                return

        # ── PERSONA DETECTION ────────────────────────────────────────────────
        user_personas = await self.store.get_personas(user.id)
        persona_name, persona_confidence, needs_clarification = ("shared", 0.5, False)
        if user_personas:
            try:
                from app.core.persona import detect_persona
                persona_name, persona_confidence, needs_clarification = await detect_persona(
                    body=body,
                    user_personas=user_personas,
                    recent_messages=recent_messages,
                    last_persona=last_persona,
                )
            except Exception as exc:
                logger.warning("PERSONA_DETECT failed=%s — using shared", exc)

        logger.info(
            "PERSONA  channel=%s  persona=%s  confidence=%.2f  needs_clarification=%s",
            self.channel.name, persona_name, persona_confidence, needs_clarification,
        )

        # ── CLARIFICATION BRANCH (per D-08) ──────────────────────────────────
        if needs_clarification and intent.type not in _SKIP_CLARIFICATION_INTENTS:
            logger.info(
                "CLARIFICATION_NEEDED  channel=%s  address=%s  body=%r",
                self.channel.name, address, body[:60],
            )
            await self.channel.send(address, _CLARIFYING_QUESTION)
            await self.store.store_message(
                user_id=user.id, direction="outbound", body=_CLARIFYING_QUESTION,
                state=MessageState.ACK.value, channel=self.channel.name,
                persona_tag=None,
            )
            return

        # ── RESOLVE persona_id (name → UUID for connections service) ─────────
        persona_id = None
        if persona_name != "shared" and user_personas:
            matched_persona = next(
                (p for p in user_personas if p.name == persona_name), None
            )
            if matched_persona:
                persona_id = matched_persona.id

        # ── THINK / ACT — tiered context assembly ────────────────────────────
        if intent.type in _FULL_CONTEXT_INTENTS:
            context = await self.store.get_context_full(
                user.id, channel=self.channel.name, query=body,
                persona_tag=persona_name if persona_name != "shared" else None,
            )
        else:
            context = await self.store.get_context_standard(
                user.id, channel=self.channel.name, query=body,
                persona_tag=persona_name if persona_name != "shared" else None,
            )

        # ── QUEUE JOB ────────────────────────────────────────────────────────
        job_id = await self.queue.push_job({
            "channel":  self.channel.name,
            "address":  address,
            "phone":    address,
            "body":     body,
            "intent":   intent.type.value,
            "context":  context,
            "user_id":  user.id,
            "persona":  persona_name,
            "persona_id": persona_id,  # UUID for connection lookup
        })

        logger.info(
            "QUEUED  job_id=%s  channel=%s  intent=%s  persona=%s",
            job_id, self.channel.name, intent.type.value, persona_name,
        )

        # ── RACE PATTERN ─────────────────────────────────────────────────────
        # Queue the job first, then race: wait for worker result vs ACK timeout.
        # If worker responds fast → send single message (no ACK).
        # If timeout → send ACK, let ResponseListener deliver result later.
        from app.config import get_settings
        race_timeout = get_settings().race_timeout_s

        # Start ACK generation concurrently (so it's ready if we need it)
        ack_task = asyncio.create_task(get_smart_ack(
            intent.type, body, user_name=user.name,
            recent_messages=recent_messages,
            assistant_name=getattr(user, "assistant_name", None),
            personality_notes=getattr(user, "personality_notes", None),
        ))

        # Wait for worker result within timeout
        result = await self.queue.wait_for_result(job_id, timeout_s=race_timeout)

        if result is not None:
            # ── RACE WON — worker responded fast, send single message ─────
            ack_task.cancel()
            claimed = await self.queue.claim_delivery(job_id)
            if not claimed:
                logger.info("RACE_LOST_CLAIM  job_id=%s — ResponseListener already delivered", job_id)
                return

            response_text = result.get("response", "")
            if response_text:
                await self.channel.send(address, response_text)
                await self.store.store_message(
                    user_id=user.id, direction="outbound", body=response_text,
                    state=MessageState.CONFIRM.value, channel=self.channel.name,
                    persona_tag=persona_name if persona_name != "shared" else None,
                    job_id=job_id,
                )
                logger.info(
                    "RACE_WON  job_id=%s  channel=%s  address=%s  (single message)",
                    job_id, self.channel.name, address,
                )
                # Run LEARN inline since ResponseListener won't handle this job
                learn_signals = result.get("learn", {})
                if learn_signals:
                    await ResponseListener._learn(self.store, user.id, learn_signals)
        else:
            # ── RACE TIMEOUT — send ACK, ResponseListener delivers later ──
            ack_text = await ack_task
            await self.channel.send(address, ack_text)
            await self.store.store_message(
                user_id=user.id, direction="outbound", body=ack_text,
                state=MessageState.ACK.value, channel=self.channel.name,
                persona_tag=last_persona,
            )
            logger.info(
                "RACE_TIMEOUT  job_id=%s  channel=%s  address=%s  (ACK sent, waiting for worker)",
                job_id, self.channel.name, address,
            )


# ─── Response listener ────────────────────────────────────────────────────────

class ResponseListener:
    """
    Runs as a long-lived asyncio task inside the FastAPI process.

    Subscribes to the Redis pub/sub channel where workers publish completed
    job IDs.  For each job it:
      1. Reads the result payload from Redis
      2. Looks up the right Channel by name from the registry
      3. Delivers the final reply to the user
      4. Stores the outbound message + runs LEARN
    """

    def __init__(self, channels: dict[str, "Channel"]) -> None:
        """
        channels — mapping of channel name → Channel instance,
                   e.g. {"sms": SMSChannel(), "slack": SlackChannel()}
        """
        self.channels = channels

    async def start(self, redis) -> None:
        from app.config import get_settings
        settings = get_settings()

        pubsub = redis.pubsub()
        await pubsub.subscribe(settings.response_channel)
        logger.info(
            "ResponseListener subscribed  channel=%s  registered=%s",
            settings.response_channel, list(self.channels),
        )

        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            job_id: str = message["data"]
            try:
                await self._deliver(redis, job_id)
            except Exception as exc:
                logger.error(
                    "ResponseListener error  job_id=%s: %s",
                    job_id, exc, exc_info=True,
                )

    async def _deliver(self, redis, job_id: str) -> None:
        # ── DELIVERY CLAIM (race pattern) ─────────────────────────────────────
        # If the race path already delivered this job, skip to avoid duplicates.
        from app.queue.client import queue_client
        claimed = await queue_client.claim_delivery(job_id)
        if not claimed:
            logger.info("RACE_DELIVERED  job_id=%s — race path already sent, skipping", job_id)
            return

        raw = await redis.get(f"result:{job_id}")
        if not raw:
            logger.warning("No result stored for job_id=%s", job_id)
            return

        result: dict = json.loads(raw)

        # Support both new "address" and legacy "phone" fields
        address: str   = result.get("address") or result.get("phone", "")
        channel_name   = result.get("channel", "sms")   # default to sms for old jobs
        response_text  = result.get("response", "")

        if not address or not response_text:
            logger.error("Malformed result  job_id=%s: %s", job_id, result)
            return

        channel = self.channels.get(channel_name)
        if channel is None:
            logger.error(
                "Unknown channel %r for job_id=%s — dropping", channel_name, job_id
            )
            return

        # ── CONFIRM ───────────────────────────────────────────────────────────
        await channel.send(address, response_text)
        logger.info("CONFIRM  job_id=%s  channel=%s  address=%s", job_id, channel_name, address)

        # ── LEARN ─────────────────────────────────────────────────────────────
        from app.database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            store = MemoryStore(db)
            user = await store.get_or_create_user(address)

            await store.store_message(
                user_id=user.id,
                direction="outbound",
                body=response_text,
                state=MessageState.CONFIRM.value,
                job_id=job_id,
            )

            learn_signals: dict = result.get("learn", {})
            if learn_signals:
                await self._learn(store, user.id, learn_signals)
                logger.info("LEARN  user_id=%s  signals=%s", user.id, learn_signals)

    @staticmethod
    async def _learn(store: MemoryStore, user_id: str, signals: dict) -> None:
        signal_type = signals.get("type")

        if signal_type == "reminder_created":
            await _increment_counter(store, user_id, "reminder_count")

        elif signal_type == "preference_stored":
            key   = signals.get("key")
            value = signals.get("value")
            if key and value:
                await store.store_memory(user_id, "long_term", key, value)

        elif signal_type == "scheduling_request":
            await _increment_counter(store, user_id, "scheduling_requests")

        elif signal_type == "profile_update":
            # Passive profile fields extracted by handle_general from conversation
            fields: dict = signals.get("fields", {})
            for key, value in fields.items():
                if not (key and value):
                    continue
                await store.store_memory(user_id, "long_term", key, str(value))
                if key == "name":
                    await store.update_user_name(user_id, str(value))
                elif key == "timezone":
                    await store.update_user_timezone(user_id, str(value))
            if fields:
                logger.info("PROFILE_UPDATED  user=%s  fields=%s", user_id[:8], list(fields))

        due_at_str = signals.get("due_at")
        if due_at_str:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(due_at_str)
                label = "morning" if dt.hour < 12 else ("afternoon" if dt.hour < 17 else "evening")
                await store.store_memory(user_id, "behavioral", "preferred_time_of_day", label, confidence=0.6)
            except Exception:
                pass


async def _increment_counter(store: MemoryStore, user_id: str, key: str) -> None:
    memories = await store.get_memories(user_id, "behavioral")
    existing = next((m for m in memories if m.key == key), None)
    if existing:
        try:
            await store.store_memory(user_id, "behavioral", key, str(int(existing.value) + 1))
        except ValueError:
            await store.store_memory(user_id, "behavioral", key, "1")
    else:
        await store.store_memory(user_id, "behavioral", key, "1")
