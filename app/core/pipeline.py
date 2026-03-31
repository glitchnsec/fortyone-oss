"""
Message pipeline — orchestrates the state machine for every inbound SMS.

State flow:  RECEIVED → ACK → THINK → ACT → CONFIRM → LEARN

New users are intercepted by OnboardingHandler (name → timezone) before
the normal pipeline opens up.

MessagePipeline  : fast path (runs inside FastAPI background task)
ResponseListener : listens for worker results and sends the final SMS
"""
import json
import logging
from enum import Enum

from app.core.ack import get_smart_ack
from app.core.intent import IntentType, classify_intent, intent_label
from app.core.onboarding import OnboardingHandler, is_complete
from app.memory.store import MemoryStore
from app.queue.client import QueueClient
from app.sms.client import SMSClient

logger = logging.getLogger(__name__)


class MessageState(str, Enum):
    RECEIVED = "received"
    ACK = "ack"
    THINK = "think"
    ACT = "act"
    CONFIRM = "confirm"
    LEARN = "learn"
    DONE = "done"


class MessagePipeline:
    def __init__(
        self,
        sms: SMSClient,
        queue: QueueClient,
        store: MemoryStore,
    ) -> None:
        self.sms = sms
        self.queue = queue
        self.store = store

    async def handle(self, phone: str, body: str) -> None:
        """
        Entry point.  Runs in a FastAPI BackgroundTask so the HTTP 200 to
        Twilio has already been returned before this executes.
        """
        # ── RECEIVED ─────────────────────────────────────────────────────────
        user = self.store.get_or_create_user(phone)

        self.store.store_message(
            user_id=user.id,
            direction="inbound",
            body=body,
            state=MessageState.RECEIVED.value,
        )

        # ── ONBOARDING GATE ───────────────────────────────────────────────────
        # New users go through name → timezone collection before the normal
        # pipeline opens up.  OnboardingHandler returns the reply text, or
        # None when it just marked onboarding complete (pass-through).
        if not is_complete(self.store, user.id):
            logger.info("ONBOARDING phone=%s body=%r", phone, body[:60])
            handler = OnboardingHandler(self.store)
            reply = await handler.handle(user.id, phone, body)

            if reply:
                await self.sms.send(phone, reply)
                self.store.store_message(
                    user_id=user.id,
                    direction="outbound",
                    body=reply,
                    state=MessageState.CONFIRM.value,
                )
                return
            # reply is None only when the final step just completed and the
            # user's original message should now be processed normally — fall
            # through to the main pipeline below.

        # ── Normal pipeline ───────────────────────────────────────────────────
        intent = classify_intent(body)

        logger.info(
            "RECEIVED from=%s intent=%s body=%r",
            phone,
            intent_label(intent.type),
            body[:60],
        )

        self.store.store_message(
            user_id=user.id,
            direction="inbound",
            body=body,
            intent=intent.type.value,
            state=MessageState.THINK.value,
        )

        # ── ACK ──────────────────────────────────────────────────────────────
        # Try LLM-generated ACK within 450ms; falls back to static pool.
        user_name = user.name  # may be None for users who skipped onboarding
        ack_text = await get_smart_ack(intent.type, body, user_name=user_name)
        await self.sms.send(phone, ack_text)

        self.store.store_message(
            user_id=user.id,
            direction="outbound",
            body=ack_text,
            state=MessageState.ACK.value,
        )

        # Standalone greetings after onboarding: ACK is the full response
        if intent.type == IntentType.GREETING:
            return

        # ── THINK ─────────────────────────────────────────────────────────────
        context = self.store.get_context(user.id)

        # ── ACT ───────────────────────────────────────────────────────────────
        job_id = await self.queue.push_job({
            "phone": phone,
            "body": body,
            "intent": intent.type.value,
            "context": context,
            "user_id": user.id,
        })

        logger.info(
            "QUEUED job_id=%s intent=%s phone=%s",
            job_id, intent.type.value, phone,
        )


# ─── Response listener ────────────────────────────────────────────────────────

class ResponseListener:
    """
    Runs as a long-lived asyncio task inside the FastAPI process.

    Subscribes to the Redis pub/sub channel where workers publish job IDs.
    For each completed job it:
      1. Reads the result payload from Redis
      2. Sends the final SMS via Twilio
      3. Stores the outbound message
      4. Runs the LEARN step (infer + persist patterns)
    """

    def __init__(self, sms: SMSClient) -> None:
        self.sms = sms

    async def start(self, redis) -> None:
        from app.config import get_settings
        settings = get_settings()

        pubsub = redis.pubsub()
        await pubsub.subscribe(settings.response_channel)
        logger.info("ResponseListener subscribed to channel=%s", settings.response_channel)

        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            job_id: str = message["data"]
            try:
                await self._deliver(redis, job_id)
            except Exception as exc:
                logger.error("ResponseListener error job_id=%s: %s", job_id, exc, exc_info=True)

    async def _deliver(self, redis, job_id: str) -> None:
        raw = await redis.get(f"result:{job_id}")
        if not raw:
            logger.warning("No result stored for job_id=%s", job_id)
            return

        result: dict = json.loads(raw)
        phone: str = result.get("phone", "")
        response_text: str = result.get("response", "")

        if not phone or not response_text:
            logger.error("Malformed result for job_id=%s: %s", job_id, result)
            return

        # ── CONFIRM ───────────────────────────────────────────────────────────
        await self.sms.send(phone, response_text)
        logger.info("CONFIRM job_id=%s phone=%s", job_id, phone)

        # Store outbound message + run LEARN
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            store = MemoryStore(db)
            user = store.get_or_create_user(phone)

            store.store_message(
                user_id=user.id,
                direction="outbound",
                body=response_text,
                state=MessageState.CONFIRM.value,
                job_id=job_id,
            )

            # ── LEARN ─────────────────────────────────────────────────────────
            learn_signals: dict = result.get("learn", {})
            if learn_signals:
                await self._learn(store, user.id, learn_signals)
                logger.info("LEARN user_id=%s signals=%s", user.id, learn_signals)
        finally:
            db.close()

    @staticmethod
    async def _learn(store: MemoryStore, user_id: str, signals: dict) -> None:
        """
        Persist inferred facts and update behavioral counters.
        Intentionally simple — no LLM in the learn step.
        """
        signal_type = signals.get("type")

        if signal_type == "reminder_created":
            _increment_counter(store, user_id, "reminder_count")

        elif signal_type == "preference_stored":
            key = signals.get("key")
            value = signals.get("value")
            if key and value:
                store.store_memory(user_id, "long_term", key, value)

        elif signal_type == "scheduling_request":
            _increment_counter(store, user_id, "scheduling_requests")

        # Infer morning/afternoon preference from reminder due times
        due_at_str = signals.get("due_at")
        if due_at_str:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(due_at_str)
                label = "morning" if dt.hour < 12 else ("afternoon" if dt.hour < 17 else "evening")
                store.store_memory(user_id, "behavioral", "preferred_time_of_day", label, confidence=0.6)
            except Exception:
                pass


def _increment_counter(store: MemoryStore, user_id: str, key: str) -> None:
    memories = store.get_memories(user_id, "behavioral")
    existing = next((m for m in memories if m.key == key), None)
    if existing:
        try:
            store.store_memory(user_id, "behavioral", key, str(int(existing.value) + 1))
        except ValueError:
            store.store_memory(user_id, "behavioral", key, "1")
    else:
        store.store_memory(user_id, "behavioral", key, "1")
