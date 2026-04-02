"""
Message pipeline — orchestrates the state machine for every inbound message.

State flow:  RECEIVED → ACK → THINK → ACT → CONFIRM → LEARN

Channel-agnostic: the pipeline depends only on the Channel ABC.
SMS, Slack, or any future channel plugs in without touching this file.

MessagePipeline  : fast path (runs inside FastAPI background task)
ResponseListener : listens for worker results and delivers the final reply
"""
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
})
_STANDARD_CONTEXT_INTENTS = frozenset({
    IntentType.REMINDER,
    IntentType.RECALL,
    IntentType.COMPLETE,
    IntentType.PREFERENCE,
    IntentType.STATUS,
    IntentType.FOLLOWUP,
})

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

        # ── ACK ──────────────────────────────────────────────────────────────
        # First message ever → warm intro that also acknowledges their request.
        # All subsequent messages → fast smart ACK (LLM or static fallback).
        is_first = await self.store.message_count(user.id) == 1
        if is_first:
            ack_text = await first_greeting(self.channel.name, body)
            logger.info("FIRST_MESSAGE  channel=%s  address=%s", self.channel.name, address)
        else:
            ack_text = await get_smart_ack(
                intent.type,
                body,
                user_name=user.name,
                recent_messages=recent_messages,   # D-12: context-aware ACK
            )
        await self.channel.send(address, ack_text)

        await self.store.store_message(
            user_id=user.id,
            direction="outbound",
            body=ack_text,
            state=MessageState.ACK.value,
            channel=self.channel.name,
            persona_tag=last_persona,  # tag ACK with current persona so next get_context_minimal can read it
        )

        # Standalone greetings: ACK is the full response
        if intent.type == IntentType.GREETING:
            return

        # ── PERSONA DETECTION ────────────────────────────────────────────────
        # Runs once per message; result cached in job payload.
        # detect_persona returns (persona_name, confidence, needs_clarification).
        # Does NOT block on embedding — uses rule fast-path first.
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
        # When the LLM is uncertain about work vs personal context, ask once
        # rather than guess wrong. The user's next message resolves the context.
        if needs_clarification:
            logger.info(
                "CLARIFICATION_NEEDED  channel=%s  address=%s  body=%r",
                self.channel.name, address, body[:60],
            )
            await self.channel.send(address, _CLARIFYING_QUESTION)
            await self.store.store_message(
                user_id=user.id,
                direction="outbound",
                body=_CLARIFYING_QUESTION,
                state=MessageState.ACK.value,
                channel=self.channel.name,
                persona_tag=None,  # no persona set — waiting for user to clarify
            )
            return  # do NOT push a job; wait for the user's clarifying reply

        # ── THINK / ACT — tiered context assembly ────────────────────────────
        # Select context depth based on intent complexity (PERS-08 / D-05)
        if intent.type in _FULL_CONTEXT_INTENTS:
            context = await self.store.get_context_full(
                user.id,
                channel=self.channel.name,
                query=body,
                persona_tag=persona_name if persona_name != "shared" else None,
            )
        else:
            # Standard tier for REMINDER, RECALL, COMPLETE, PREFERENCE, STATUS, FOLLOWUP
            context = await self.store.get_context_standard(
                user.id,
                channel=self.channel.name,
                query=body,
                persona_tag=persona_name if persona_name != "shared" else None,
            )

        job_id = await self.queue.push_job({
            "channel":  self.channel.name,   # used by ResponseListener to route reply
            "address":  address,
            "phone":    address,             # backward-compat alias for worker tasks
            "body":     body,
            "intent":   intent.type.value,
            "context":  context,
            "user_id":  user.id,
            "persona":  persona_name,        # NEW — task handlers use this for connection selection
        })

        logger.info(
            "QUEUED  job_id=%s  channel=%s  intent=%s  persona=%s",
            job_id, self.channel.name, intent.type.value, persona_name,
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
