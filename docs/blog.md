# Your Chief of Staff, One Text Away — Building FortyOne

*A CS degree shouldn't be required to turn on your AI assistant. Here's the architecture, the hard decisions, and ten production lessons from building FortyOne: a multi-tenant, SMS-first personal operating system where each user names their own operator.*

---

## The Access Gap

Every ambitious person runs their life across 15 apps, 3 calendars, and a notes app they never check. They manage follow-ups in their head, context-switch 25 times an hour, and spend more time managing their tools than doing actual work.

Meanwhile, the people who move fastest often have one thing in common: a chief of staff, or an EA, who knows their world and just handles things.

AI promised to change that — to give everyone access to that leverage. And technically, the pieces exist. OpenClaw and similar platforms are genuinely powerful. But there's a gap between "technically capable" and "actually used." I've walked through AI assistant setup with at least ten people. Every time, we hit the same wall: API keys, Git, Docker, decisions about VPS vs Mac Mini vs containers. Most people never made it past step three.

That's not a user problem. It's an infrastructure problem. And the people who *have* figured it out — the power users running OpenClaw or similar setups — had to earn it. They shouldn't have to.

A CS degree shouldn't be required to turn on your AI assistant.

That's the problem FortyOne is built to solve. This post is about how it works.

---

## What FortyOne Is

FortyOne is a multi-tenant, SMS-first personal operating system for user-named AI operators. A user might name their operator Jarvis, or Friday, or whatever fits. Jarvis is the assistant they text. FortyOne is the operating layer underneath: memory, personas, tools, scheduling, credentials, queues, and the dashboard.

Users interact through SMS, Slack DM, or the web dashboard. Their operator can set reminders, track goals, search the web, manage email and calendar, remember preferences, manage connections, and proactively reach out when there's something useful to say.

The design constraints were tight from the start:

**SMS first.** The most universal text interface on earth. No app install, no new interface to learn. Once registered, users interact entirely by text — texting is the universal command line for humans. This was the most important decision — it removed most end-user interface friction by using a channel everyone already knows.

**Multi-tenant from day one.** One deployment serves many users. A technical operator runs the infrastructure for friends, family, a team, or a community. Non-technical users just get a number to text. Complexity lives with the deployer, not the end user.

**Extensible.** Built-in tools are just the base layer. Users can add custom agents, webhooks, prompt agents, and MCP servers. The assistant surface area can expand without rewriting the core pipeline.

**User-centered, not tool-centered.** The goal isn't to connect 47 productivity apps. It's to have an assistant that understands you well enough to act on your behalf. Proactive and reactive. Work *and* personal. Stays relevant across life changes — job, career, relationships, geography.

Three modes of operation:
- **Reactive** — You text, your operator handles it. Reminders, scheduling, email, calendar, search. Natural language, no commands to memorize.
- **Proactive** — Your operator reaches out when it matters. Morning briefings, goal coaching, smart check-ins, feature discovery. Content-delta suppression means it stays quiet when there's nothing new.
- **Accumulative** — It gets better every day. Learns preferences, patterns, context. Week 1 it knows your timezone. Month 6 it knows your life.

---

## System Architecture

```
┌─────────────────────────────────────────────────────┐
│   Users (SMS via Twilio · Slack DM · Web Dashboard) │
└──────────────────────┬──────────────────────────────┘
                       ▼
        ┌──────────────────────────────────┐
        │         FastAPI API Server       │
        │                                  │
        │  • Webhook ingestion (SMS/Slack) │
        │  • JWT auth + registration       │
        │  • Rule-based intent classifier  │
        │  • Context assembly + job queue  │
        │  • ACK race pattern              │
        │  • REST API for dashboard/admin  │
        │  • ResponseListener (pub/sub)    │
        └──────────┬───────────┬───────────┘
                   │           ▲
           Redis Streams   Redis Pub/Sub
           (durable queue) (result delivery)
                   │           │
                   ▼           │
        ┌──────────────────────────────────┐
        │          Worker Process          │
        │                                  │
        │  • Manager / subagent pattern    │
        │  • 15 tools across 7 subagents   │
        │  • LLM calls via OpenRouter      │
        │  • Tool-calling loop (max 3)     │
        │  • Passive learning → memory     │
        │  • Goal vs reminder recognition  │
        └──────────┬───────────────────────┘
                   ▼
        ┌──────────────────────────────────┐
        │     PostgreSQL + pgvector        │
        │  Users · Memories · Tasks        │
        │  Goals · Personas · Connections  │
        │  ProactivePreferences · Logs     │
        └──────────────────────────────────┘

        ┌────────────────┐  ┌─────────────────┐
        │   Scheduler    │  │  Connections    │
        │  1-3 proactive │  │  OAuth providers│
        │  msgs/day      │  │  Gmail/Cal/Slack│
        │  Weighted pool │  │  MCP gateway    │
        │  Delta suppress│  │  Fernet encrypt │
        └────────────────┘  └─────────────────┘
```

**Stack:** Python 3.11 · FastAPI · SQLAlchemy (async) · PostgreSQL + pgvector · Redis Streams · React/Vite/shadcn · OpenRouter (model-agnostic)

### The Two-Process Split

The most important architectural decision: the API server and the worker run as separate processes, connected by Redis Streams.

When a message arrives — SMS, Slack DM, doesn't matter — the API server identifies the user, classifies intent using rule-based regex (no LLM, sub-millisecond), assembles the right context tier, and queues a job via `XADD`. Then it races two outcomes:

```python
# app/core/pipeline.py — the race pattern
race_timeout = get_settings().race_timeout_s  # default 8s

# Start ACK generation concurrently (so it's ready if we need it)
ack_task = asyncio.create_task(get_smart_ack(...))

# Wait for worker result within timeout
result = await self.queue.wait_for_result(job_id, timeout_s=race_timeout)

if result is not None:
    # Worker responded fast → send single message (no ACK)
    ack_task.cancel()
    await self.channel.send(address, result["response"])
else:
    # Timeout → send ACK now, ResponseListener delivers result later
    ack_text = await ack_task
    await self.channel.send(address, ack_text)
```

If the worker finishes within the race window, the user receives one final response — no ACK, no double message. If the worker takes longer, the user gets a short acknowledgment first, and the final response is delivered later by the `ResponseListener` via Redis pub/sub.

This matters in a text interface. A user should never wonder whether their message disappeared. But sending an ACK too aggressively is noisy — the race pattern avoids double messages when the worker is fast. We settled on an 8s default after testing showed that aggressive ACKs produced confusing overlapping responses.

The queue uses Redis Streams with consumer groups — `XADD` to enqueue, `XREADGROUP` to dequeue, `XACK` to acknowledge — giving durable delivery with at-least-once guarantees:

```python
# Producer (app/queue/client.py)
await self._redis.xadd(self.settings.queue_name, payload)

# Consumer (app/queue/worker.py)
msgs = await self._redis.xreadgroup(group, consumer, {self.settings.queue_name: ">"})
# ... process ...
await self._redis.xack(self.settings.queue_name, group, msg_id)
```

### Channel-Agnostic Pipeline

SMS and Slack share the exact same processing pipeline. The abstraction is a single abstract base class:

```python
# app/channels/base.py
class Channel(ABC):
    name: str
    error_reply: str = "Something went wrong on my end — try again in a moment."

    @abstractmethod
    async def send(self, to: str, body: str) -> bool:
        """Deliver body to the user identified by to.
        Returns True on success, False on failure."""
        ...
```

To add a new channel, you subclass `Channel`, implement `send()`, and register it in `main.py`. The webhook ingestion, queue, worker, memory assembly, and tool execution are identical regardless of where the message came from.

This was tested the hard way — see Lesson 1 below.

### Manager / Subagent Pattern

The worker uses a two-layer architecture. A "manager" LLM call receives the user's message and assembled context and decides which tools to invoke. Subagents are never directly visible to the user; they execute specific tool calls and return results to the manager, which synthesizes a final response.

Current built-in tool surface — 15 tools across 7 subagents, defined declaratively in YAML:

| Subagent | Tools | What it does |
|----------|-------|-------------|
| **search_agent** | `web_search` | Brave Search API for current information |
| **email_agent** | `read_emails`, `send_email` | Gmail inbox read + compose |
| **calendar_agent** | `list_events`, `create_event` | Google Calendar read + create |
| **slack_agent** | `slack_read_channels`, `slack_get_workspace`, `slack_read_threads` | Slack workspace data |
| **task_agent** | `create_reminder`, `list_tasks` | Reminders with timezone-aware scheduling |
| **profile_agent** | `upsert_profile`, `update_user_field` | TELOS profile + user settings |
| **goal_agent** | `create_goal`, `update_goal`, `list_goals` | Goal tracking with coaching |

Plus one built-in system tool (`update_setting`) for proactive message and quiet-hours configuration — 16 tool schemas before user custom agents and MCP-discovered tools are added. Adding a new built-in tool means adding an entry to `config/subagents.yaml` with a handler path — no changes to the dispatch loop. Medium/high-risk tools require explicit confirmation before execution.

The manager is hard-capped at 3 tool-calling rounds to prevent infinite loops:

```python
# app/tasks/manager.py
MAX_TOOL_ROUNDS = 3  # Hard limit
for round_num in range(MAX_TOOL_ROUNDS):
    # ... LLM call, tool execution, result collection ...
```

This pattern means the system prompt stays coherent and the user-facing LLM is never doing raw tool execution. It also isolates failures — a subagent error doesn't collapse the whole response.

### Proactive Scheduler

The scheduler isn't a cron job that fires at a fixed time. It's a weighted random pool of message categories, each with configurable weights, cooldown windows, day-of-week filters, and quiet hours:

```python
# app/core/proactive_pool.py
@dataclass
class ProactiveCategory:
    name: str
    weight: float = 1.0
    cooldown_hours: int = 24
    days_of_week: list[int] | None = None  # 0=Mon..6=Sun
    requires: list[str] | None = None      # e.g. ["goals", "calendar"]
```

For each user, `plan_day()` creates a daily plan keyed in Redis (`proactive:plan:{user_id}:{date}`) and selects 1–3 categories based on eligibility, cooldowns, available user state, and recent sends. Content delta suppression gates prevent stale messages: if there's nothing meaningfully new to say in a category, the message is suppressed entirely rather than sent anyway.

This took three iteration cycles to get right. See Lessons 2 and 3.

---

## Multi-Tenancy: Isolation at Every Boundary

This is the section I find most interesting to explain, because isolation in a multi-tenant system isn't a feature you add — it's a constraint you enforce at every boundary, separately and defensively.

User isolation isn't a feature — it's a constraint enforced at every layer. The `user_id` flows from channel identification through pipeline, queue, worker, LLM context, tool execution, and response delivery. At no point does the system operate without knowing exactly whose data it's touching.

### Database Layer

The core repository layer is `MemoryStore`. It's constructed with a DB session, and every method — `get_memories()`, `get_active_tasks()`, `get_goals()`, `get_context_standard()` — receives `user_id` explicitly as a parameter and filters queries by it. Tenant-owned data queries are always scoped by `user_id`. There is no ambient global user — the current user is passed through function calls and job payloads at every layer.

### Message Pipeline

When an SMS arrives, the phone number identifies the user. When a Slack DM arrives, the Slack user ID identifies them. The pipeline looks up the user record and pins the entire request to that `user_id`. Every downstream operation — intent classification, memory assembly, job queuing, response delivery — carries this `user_id`. There is no shared "current user" state; it's passed explicitly through every function call.

### Worker Isolation

Each job payload in Redis Streams is self-contained enough to produce a response. The API process assembles the primary context packet *before* queuing — user info, memories, profile traits, conversation history, personas:

```python
# app/core/pipeline.py — context is pre-assembled and serialized into the job
job_id = await self.queue.push_job({
    "channel":  self.channel.name,         # "sms" | "slack"
    "address":  address,                    # channel-specific delivery target
    "phone":    user.phone,                 # canonical phone for identity
    "body":     body,                       # user's message text
    "intent":   intent.type.value,          # classified intent
    "context":  context,                    # nested dict from get_context_*()
    "user_id":  user.id,                    # isolation key
    "persona":  persona_name,               # "work" | "personal" | "shared"
    "persona_id": persona_id,               # UUID for connection lookup
    "persona_confidence": persona_confidence,
})
```

The worker also performs DB reads for operational state — active task sessions, custom agent schemas, MCP tool capabilities, action logs, and passive learning writes. Those operations also carry `user_id`. Two users texting at the same millisecond get two completely independent jobs with independent context packets and independent LLM calls.

### Redis Namespace

Every user-scoped Redis key includes `user_id`:
```
rate:proactive:{user_id}:day:2026-04-09
proactive:plan:{user_id}:2026-04-09
proactive:cooldown:{user_id}:morning
handler_lock:{user_id}:{category}
```

User A's rate limit never affects User B's quota. Idempotency keys are scoped per user per job — a duplicate delivery to User A's channel cannot trigger User B's handler.

### OAuth Token Isolation

Connections are stored per user, per provider, per persona, encrypted with Fernet symmetric encryption:

```python
# connections/app/crypto.py
from cryptography.fernet import Fernet

def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()

def decrypt(value: str) -> str:
    return _fernet().decrypt(value.encode()).decode()
```

When User A connects Gmail under their Work persona, that token is stored with `user_id=A, persona_id=work`. User B connecting the same Google account gets a completely separate token entry. Tool execution receives `user_id` and optional `persona_id`, then resolves credentials for that scope — User A's Work email cannot be read by User B's request under the intended internal service boundary.

### Persona System: Isolation Within a User

Even within a single user, personas provide context boundaries. Work and Personal profiles have separate connections, separate memory contexts, and separate behavioral patterns. The manager receives the detected persona (inferred from message content, no commands required) and filters tool access accordingly.

The persona controls:
- Which memories are retrieved (`persona_tag` plus shared memories)
- Which connections and credentials are available
- Which MCP tools are exposed
- Which cross-persona hints are injected ("I can do that through your Work persona")
- Which tone and context notes shape the response

Shared memory enables cross-context awareness when it's relevant — "Busy week at work? Maybe push that gym session to Saturday." — but this is a deliberate opt-in pattern, not a default information leak.

---

## The Connections Service: A Proof of Concept for Agent Vaults

Most AI assistant platforms share a dirty secret: your OAuth tokens, API keys, and third-party credentials live in the same process that runs the LLM. The agent that can hallucinate tool execution is the same process that holds your Gmail refresh token. If something goes wrong — a prompt injection, a hallucinated API call, a dependency compromise — the blast radius includes every secret the agent can reach.

FortyOne takes a different approach. The connections service is a standalone FastAPI microservice that owns all third-party credentials. The main agent platform never sees a raw token. It's a security-first separation layer — and a proof of concept for what I think the future of agent credential management looks like.

### Architecture: Agent and Vault Are Separate Processes

```
┌───────────────────────┐         HTTP (internal)         ┌────────────────────────┐
│    Main Agent App     │ ──────────────────────────────▶ │  Connections Service   │
│                       │                                 │  (Agent Vault)         │
│  • LLM calls          │   POST /tools/gmail/read_emails │                        │
│  • Tool orchestration │   {"user_id", "persona_id"}     │  • OAuth flows         │
│  • Memory + context   │                                 │  • Token storage       │
│  • No raw credentials │ ◀────────────────────────────── │  • Fernet encryption   │
│                       │   {"emails": [...]}             │  • Token refresh       │
└───────────────────────┘                                 │  • MCP gateway         │
                                                          │  • Credential lifecycle│
                                                          └────────────────────────┘
```

The main app knows *which* tools are available (via capability discovery), but credential resolution, token refresh, and API calls to connected-tool services (Gmail, Calendar, Slack workspace tools, MCP servers) all happen inside the connections service. The main app sends a request like "read emails for user X under persona Y" and gets back structured data. It never decrypts a token, never holds a refresh secret, never makes a direct call to Google's API or a Slack workspace endpoint.

(Slack *message delivery* is a separate concern — the Slack channel credential for sending DMs is still owned by the main app, same as the Twilio credential for SMS. Channel credentials and tool credentials are different trust boundaries.)

### What This Gives Users

The dashboard's connections page is a **single pane of glass** for every credential shared with the platform:

- See exactly which services are connected and to which persona
- See the granted OAuth scopes (what the operator can actually do)
- Revoke any connection with one click — cascading delete wipes tokens immediately
- Reconnect when tokens expire (`needs_reauth` status surfaces in the dashboard)
- No secrets buried in `.env` files — OAuth tokens are acquired through browser-based flows and stored encrypted, never hand-managed

Users connect services through standard OAuth: click "Connect Gmail" in the dashboard, authorize in Google's consent screen, and the callback stores encrypted tokens scoped to the right persona. Rotation is automatic — the connections service refreshes expired tokens at execution time:

```python
# connections/app/tools/gmail.py — transparent token refresh
async def _get_fresh_token(token, conn, db):
    if token.expires_at and token.expires_at > datetime.now(UTC):
        return decrypt(token.access_token_enc)  # still valid

    # Refresh transparently
    resp = await client.post("https://oauth2.googleapis.com/token", data={
        "grant_type": "refresh_token",
        "refresh_token": decrypt(token.refresh_token_enc),
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
    })
    # ... update encrypted tokens in DB ...
    return new_access_token
```

### MCP as a Gateway Pattern

The connections service also acts as an MCP gateway. Users can connect MCP-compatible tool servers — Notion, Linear, custom internal tools — through OAuth or API key. The service discovers available tools via JSON-RPC (`/tools/list`), stores the schemas, and proxies execution requests (`/tools/call`). The main agent platform sees MCP tools exactly like built-in tools — same schema format, same dispatch path. The connection service handles auth, session management, and response validation.

```python
# connections/app/routes/mcp.py — tool discovery
async def _discover_tools(mcp_url, access_token):
    resp = await client.post(f"{mcp_url}/tools/list", json={
        "jsonrpc": "2.0", "method": "tools/list"
    }, headers={"Authorization": f"Bearer {access_token}"})
    # Validates: max 20 tools, max 64-char names, no reserved names
    return validated_tools
```

### The Future: User-Owned Agent Vaults

The current implementation is a PoC, but the architecture points toward something more interesting. Today, the connections service is deployed alongside the agent platform. The future is that the connections service becomes a **standalone agent vault owned by the user**.

The model inverts: instead of the platform holding your credentials, *you* hold your credentials in your own vault. An agent platform — FortyOne, or any other — gets a single authorization to connect to your vault. From there, it can request access to specific services on a need-to-access basis. You see every access request. You control the scope. You can rotate credentials or revoke platform access at any time without touching the individual services.

This is the difference between "I gave my agent my Gmail password" and "my agent has a revocable, scoped, auditable credential to read my inbox through a vault I control."

The separation also creates natural extension points for security layering:
- **Tool output validation** against prompt injection attacks — the vault can inspect what comes back from third-party APIs before it reaches the LLM
- **Rate limiting and anomaly detection** at the credential layer — the vault can flag unusual access patterns
- **Audit logging** — every credential use is traceable through a single chokepoint
- **Scope narrowing** — the vault can enforce tighter scopes than what the OAuth grant allows, per platform

None of that requires changes to the agent platform. The vault is the control plane; the agent is just a consumer.

---

## Context Engineering

If multi-tenancy is the security story of FortyOne, context engineering is the intelligence story. Every LLM call is only as good as what you put in front of it — and in a system that needs to respond quickly *and* know your life deeply, you can't afford to assemble the same context window for every request.

"Context engineering" has become a bit of a buzzword, but the concept here is concrete: treat context as a first-class resource with budgets, tiers, scoping rules, and graceful degradation, the same way you'd treat database queries or memory allocation.

### Tiered Context Assembly

Not every request gets the same context. The pipeline classifies intent first — using rule-based regex, no LLM required — and selects one of three tiers. In practice, the current classifier only fast-paths greetings and identity questions; everything substantive routes to `NEEDS_MANAGER`, which gets full context:

```python
# app/core/intent.py — only two hot-path patterns
_RULES = [
    (IntentType.IDENTITY, r"\b(what'?s?\s+your\s+name|who\s+are\s+you|...)\b", 0.95),
    (IntentType.GREETING, r"^(hi|hello|hey|...)\s*[!?.]?\s*$", 0.95),
]

def classify_intent(text: str) -> Intent:
    for intent_type, pattern, confidence in _RULES:
        if re.search(pattern, lower, re.IGNORECASE):
            return Intent(type=intent_type, ...)
    # Everything else → manager LLM dispatch in worker
    return Intent(type=IntentType.NEEDS_MANAGER, ...)
```

The three context tiers — separate functions in `app/memory/store.py`:

| Tier | Used for | What's included |
|------|---------|-----------------|
| **Minimal** | ACKs, greeting, persona inheritance | User info, last 5 channel-scoped messages, last persona tag |
| **Standard** | Normal worker flows | Last 20 messages + relevant memories (2K token budget) + key TELOS profile traits |
| **Full** | Manager/general/schedule/cross-context | Standard + all personas + full active task list + full TELOS profile |

```python
# app/memory/store.py
async def get_context_minimal(self, user_id, channel="sms"):
    """Last 5 messages + last_persona. No embedding, no memory search."""

async def get_context_standard(self, user_id, channel="sms", query="", persona_tag=None):
    """Last 20 messages + relevant memories (2K token budget).
    Includes semantic retrieval when embedding is available."""

async def get_context_full(self, user_id, channel="sms", query="", persona_tag=None):
    """Standard context + all personas + full active tasks + full TELOS profile."""
```

The practical implication: a greeting doesn't need your goals and active tasks. A scheduling request does. Loading the full context for every message would add DB overhead to every single interaction. Over thousands of daily messages, that compounds fast.

### Dynamic System Prompt Construction

The system prompt isn't static — it's assembled fresh for every worker call in `_build_system_prompt()`. The sections are composed conditionally based on what's available in the payload:

1. **Identity preamble** — The assistant's name and personality notes from user config. ("You are Jarvis, a personal assistant. Personality: witty and concise.") Each user can name and configure their operator's personality.
2. **User info** — Name, current local time in their timezone with UTC conversion. Sounds trivial. Timezone bugs in production are not trivial (see Lesson 3 and Lesson 8).
3. **Core capabilities** — Tool usage guidelines, goal-vs-reminder disambiguation rules, settings update enforcement, response length constraints. SMS gets a hard 1200-character target enforced here — the channel constraint becomes a prompt constraint.
4. **Scheduled context** — Different instructions depending on whether this is a scheduled execution, goal coaching session, or check-in (conditional per source type).
5. **Persona context** — Which persona is active (work/personal/shared), plus *cross-persona tool hints*: when the active persona doesn't have a capability, the system prompt tells the LLM what tools exist on the other persona. This lets it say "I can do that through your Work persona" instead of "I can't do that."
6. **Channel guidance** — SMS-specific constraints like character limits (conditional on channel).
7. **Memories/preferences** — Up to 10 key-value pairs from the memory store, persona-scoped.
8. **Profile traits** — Sections from the TELOS framework. Standard tier gets 3 high-signal sections (preferences, mission, problems); full tier gets everything.
9. **Active task session envelope** — For multi-turn workflows, this carries the original intent, gathered context, tools already used, and the next expected step. The LLM can continue without re-explanation across message turns.

Most sections are conditional — a first-time user with no profile, no memories, and no active session gets a much shorter prompt than a power user mid-workflow.

### Memory: Three Types, Two Retrieval Strategies

The memory model supports three types:

```python
# app/memory/models.py
class Memory(Base):
    """
    memory_type values:
      - short_term   : current conversation context (capped at N entries)
      - long_term    : explicit facts (name, preferences)
      - behavioral   : inferred patterns (e.g. prefers_mornings=true)
    """
```

Conversation history lives primarily in the `messages` table; longer-lived facts live in `memories` and `user_profiles`.

Retrieval uses two strategies depending on availability:

- **Semantic search via pgvector** — Memory values are embedded async on write using `text-embedding-3-small` (1536 dims). At retrieval time, cosine similarity surfaces the most contextually relevant memories for the current request, not just the most recent ones.
- **Recency fallback** — When embeddings aren't available (cold start, write lag), newest memories first. Graceful degradation over silent failure.

All memories are persona-scoped. Work context sees `work|shared` memories; personal context sees `personal|shared`:

```python
# app/memory/store.py — persona filtering
if persona_tag and persona_tag != "shared":
    stmt = stmt.where(
        (Memory.persona_tag == persona_tag) |
        (Memory.persona_tag == "shared") |
        (Memory.persona_tag.is_(None))
    )
```

The persona boundary isn't just for tools and connections — it applies to what the LLM even knows about when it's operating in a given context.

### Conversation History Reconstruction

Message history is stored per-message in the `messages` table with `metadata_json` preserving tool calls and their results. When building the context window, history is reconstructed into OpenAI-compatible format with a **10K token budget**:

```python
# app/tasks/manager.py
_HISTORY_TOKEN_BUDGET = 10000  # ~10k tokens for history window

kept, dropped = _apply_token_budget(history_msgs)
```

Walk from newest to oldest. Keep whole conversation turns — assistant/tool result pairs are never split. When the budget is exhausted, dropped turns are **summarized, not silently truncated**:

```python
def _summarize_dropped_turns(dropped: list[dict]) -> str:
    """Extract tool names and key result snippets from dropped messages."""
    tool_summaries = []
    for msg in dropped:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                tool_summaries.append(tc["function"]["name"])
        if msg.get("role") == "tool":
            content = msg.get("content", "")[:150] + "..."
            tool_summaries.append(f"result: {content}")
    return f"[Earlier in this conversation, these tools were used: {', '.join(tool_summaries[:10])}]"
```

The LLM gets the shape of what happened without the full token cost. Tool call/result pairs are reconstructed from metadata so the LLM sees the full tool-use flow — not just the assistant's message, but what was actually executed and returned. This matters for multi-turn tasks where the next step depends on understanding what the previous step did.

SMS and Slack history are channel-scoped. A Slack thread doesn't pollute the SMS window.

### The Queue Payload as Context Serialization Boundary

The Redis job payload is the architectural seam between the fast API process and the slow worker process. The API process assembles the primary context packet before queuing:

```json
{
  "body": "user message",
  "intent": "needs_manager",
  "context": {
    "user": { "name": "...", "timezone": "...", "assistant_name": "...", "personality_notes": "..." },
    "recent_messages": [{ "direction": "...", "body": "...", "at": "...", "intent": "...", "metadata": {} }],
    "memories": { "key": "value" },
    "active_tasks": [{ "id": "...", "title": "...", "due_at": "..." }],
    "profile_traits": [{ "section": "...", "label": "...", "content": "..." }],
    "personas": [{ "id": "...", "name": "...", "description": "...", "tone_notes": "..." }]
  },
  "persona": "shared",
  "persona_id": null,
  "persona_confidence": 0.5
}
```

The worker may still fetch operational data (task sessions, custom agents, MCP capabilities, action logs), but context assembly for the LLM prompt is centralized in the API process and auditable in one place.

### Passive Learning Loop

After every response, the pipeline's `LEARN` phase processes structured signals from the worker result:

- `reminder_created` → increment behavioral counter
- `preference_stored` → upsert memory
- `profile_update` → store to TELOS profile
- `due_at` → infer `preferred_time_of_day`
- `scheduling_request` → increment behavioral counter

This is how "Week 4 it knows your work schedule" happens. Not through explicit configuration — through passive extraction from normal conversation. The user never fills out a profile form. The system builds their profile by paying attention.

The full pipeline stage flow, end to end:

```
RECEIVED  → identify user, classify intent (regex, <1ms)
    ↓
THINK     → assemble context tier (minimal, standard, or full)
    ↓
QUEUE     → serialize to Redis Streams payload with all context
    ↓
    ├─────────────────────────────────────────────────┐
    │ API process (RACE)                              │ Worker process (ACT)
    │ Start ACK generation + wait for worker result   │ Dequeue job, build system prompt
    │ (8s default race timeout)                       │ LLM called with tools (max 3 rounds)
    │                                                 │ Publish result via Redis pub/sub
    ├─────────────────────────────────────────────────┘
    ↓
CONFIRM   → if worker won race: single response sent directly
          → if timeout: ACK sent, ResponseListener delivers result later
    ↓
LEARN     → extract facts, increment counters, update profile
```

The key principle across all of this: **every context source has a cap**. 10 memories. 10K history tokens. 3 high-signal TELOS sections for standard tier, full profile for complex intents. Graceful degradation at every LLM call with explicit timeouts. Context is a resource — budget it like one.

---

## Ten Production Lessons

These came from live user testing. Ten real bugs, ten real fixes, each one teaching something about building reliable LLM-integrated systems.

### 1. Blocking I/O kills async systems silently

The Twilio SDK is synchronous. During proactive briefing windows, `SMSChannel.send()` was blocking the FastAPI event loop for 200ms–2s per message. With 10+ briefings queued, new inbound messages — including first-time user onboarding — were starved for minutes. Users experienced delayed responses with no error in logs. Slack was already async; the inconsistency was the root cause.

**Fix:** Wrapped all Twilio calls in `asyncio.to_thread()`:

```python
# app/channels/sms.py
async def send(self, to: str, body: str) -> bool:
    # Offload blocking Twilio HTTP call to a thread so the event loop
    # stays free for other async tasks (inbound webhooks, pub/sub, etc.)
    for idx, part in enumerate(parts, start=1):
        msg = await asyncio.to_thread(self._send_sync, to, part)
```

Rule: every I/O call in an async codebase must be audited. The absence of errors does not mean the absence of blocking.

### 2. "Dead metadata" is worse than no metadata

`ProactiveCategory.cooldown_hours` was defined on the dataclass but never enforced. `select_categories()` filtered by `days_of_week` and `requires` but completely ignored `cooldown_hours`. Result: same user got two morning briefings 28 minutes apart. All three defense layers — cooldown, idempotency, and spacing — had gaps that compounded invisibly.

**Fix:** Redis-backed cooldown keys set on send, handler-level `SET NX` locks, and a rule: if a field exists on a model, something must read it. Dead metadata is a maintenance trap.

### 3. Silent fallbacks mask wrong-path bugs

`context.get("timezone", "UTC")` looks defensive and harmless. But timezone lived at `context["user"]["timezone"]`, not `context["timezone"]`. The fallback silently returned "UTC" every time. This pattern was systematic across 4 files, 9 instances. Users got reminders in UTC, briefings at the wrong time, and the system asked "what timezone are you in?" despite already knowing.

**Fix:** Audited every context path. Rule: if a fallback exists, log when it activates. A silent default that fires in production is a silent bug in production.

### 4. Two stores for the same data means one store is always stale

The text-based settings handler wrote per-category enable/disable to `proactive_settings_json` (a JSON blob on `User`). The dashboard read from the `ProactivePreference` table. Both were "correct" in isolation. User disables morning briefings via text → dashboard still shows enabled → scheduler still sends them.

**Fix:** Settings handler now upserts `ProactivePreference` rows — same store as the dashboard. Contract tests verify which store is written to. Rule: one source of truth per domain, full stop.

### 5. LLMs hallucinate tool execution

User texted "stop sending proactive messages." The LLM responded "Got it, I've disabled proactive messages for you!" with `tool_calls=0`. It described the action without performing it. The confirmation message also leaked raw JSON: *I'd like to perform `update_setting` with `{"scope": "proactive"...}`*

**Fix:** System prompt now enforces tool execution:

```python
# app/tasks/manager.py — _build_system_prompt()
"CRITICAL: To change any user setting (proactive messages, quiet hours, profile, etc.), "
"you MUST call the update_setting tool. NEVER claim you have changed a setting without "
"actually calling the tool. If you cannot determine the right parameters, ask the user "
"for clarification instead of pretending the change was made."
```

This is a pattern I suspect most production LLM systems will encounter. The model is optimized to sound helpful, not to be reliably correct about what it executed. Treat tool execution as unverified until you confirm the call was made.

### 6. Tool descriptions drive selection more than system prompts

`create_reminder` said "use whenever the user wants something done" — so "I need to publish 3 LinkedIn posts by Friday" matched it. `create_goal` existed but its description was less assertive. The LLM set a reminder instead of creating a coachable goal.

**Fix:** Updated both tool descriptions with explicit boundary examples and added goal-vs-reminder disambiguation to both the system prompt *and* the tool descriptions in `config/subagents.yaml`:

```yaml
# config/subagents.yaml — create_reminder
description: >-
  IMPORTANT: Do NOT use this for multi-step goals, projects, or
  achievements that require effort over time — use create_goal instead.
  A reminder is a single nudge at a point in time ('call mom at 5pm').
  A goal is something the user works toward ('publish 3 posts by Friday').
```

Key insight: system prompts and tool descriptions need to *agree*. If they're in tension, the tool description wins more often than you'd expect.

### 7. Internal operations leak into user-facing messages

The evening recap fed the raw action log to the LLM. Internal entries like "scheduled check-in re-queued", "profile nudge sent", "feature_discovery dispatched" showed up in the user's recap. When there were no real user actions, the recap summarized system internals.

**Fix:** Module-level filter set gates all proactive handlers:

```python
# app/tasks/proactive.py
_INTERNAL_ACTION_TYPES = {
    "morning_briefing", "evening_recap", "weekly_digest",
    "profile_nudge", "goal_coaching", "smart_checkin",
    "insight_observation", "feature_discovery", "afternoon_followup",
    "proactive_send", "scheduler", "requeue",
}
```

If no user-facing actions remain after filtering, the message is suppressed entirely. Rule: never give the LLM a data source you haven't audited for what it might contain.

### 8. Registration is the cheapest place to collect critical data

Timezone bugs — wrong UTC offsets, "what timezone are you in?" conversations, reminders set in the past — all traced to the `User` model defaulting with no reliable way to correct it except via conversation. The fix was obvious in hindsight: collect name and timezone at registration, with browser auto-detection via `Intl.DateTimeFormat`.

**Fix:** Registration now collects name, timezone (auto-detected), and phone (with country selector):

```python
# app/routes/auth.py
class RegisterInput(BaseModel):
    email: EmailStr
    phone: str
    password: str
    name: str | None = None
    timezone: str | None = None  # auto-detected by dashboard via Intl.DateTimeFormat
```

This eliminated an entire class of bugs that had been producing confusing experiences in production.

### 9. Tests that mock everything catch nothing

The original text settings tests mocked the DB and verified function calls but never checked that data landed in the right table. The per-category disable bug shipped because tests confirmed `execute_setting_update` returned `{"result": "disabled"}` without verifying a `ProactivePreference` row was created.

**Fix:** Added contract tests that verify data shapes between components. Integration tests that register → authenticate → call endpoint → verify DB state. A test that only verifies the function returned success is not testing the right thing.

### 10. Queue systems need failure visibility, not just happy-path ACKs

A real incident: the worker generated an LLM response but never published the Redis result. The job stayed pending for hours. The user received only the ACK — no error, no fallback, no indication anything went wrong. The response simply vanished.

**Fix:** Added multiple defense layers:
- Pre-publish logging so the failure point is identifiable
- Redis publish timeouts so a stuck publish doesn't hang the worker
- User-visible fallback responses on timeout or exception
- No `XACK` if result publication fails — the job stays pending for retry
- `ResponseListener` pub/sub reconnection and cleanup
- Focused tests for timeout, publish failure, no-XACK, and reconnect behavior

Rule: a queued job should end in one of three visible states: delivered, failed with fallback, or still pending for retry. Silent disappearance is the failure mode to design against.

---

## Self-Hosting Reality

FortyOne is designed so deployers handle the infrastructure complexity and end users just text a number. But "deployer-friendly" still means real configuration:

- **Twilio** for SMS: account SID, auth token, phone number, inbound webhook, and optional Verify service for registration OTPs
- **Redis 7+** for the durable job queue, pub/sub response delivery, idempotency, cooldowns, and proactive plans
- **PostgreSQL + pgvector** for users, messages, memories, tasks, goals, personas, profile data, and semantic memory search
- **OpenRouter API key** for LLM calls and embeddings
- **Google OAuth credentials** for Gmail and Calendar tools
- **Slack apps** for two separate roles: Slack DM delivery and Slack workspace connection tools
- **Strong deployment secrets** for `JWT_SECRET`, `ENCRYPTION_KEY`, and `SERVICE_AUTH_TOKEN`

The `docker-compose.yml` gets you running locally with six services: PostgreSQL, Redis, API, Worker, Connections, and Scheduler. Production deployments still need proper secrets management, a reverse proxy, TLS, backups, and monitoring. Phase 11 added the hardening needed for a safer OSS default:

- **AGPL-3.0-only license** in `LICENSE`, plus `CONTRIBUTING.md` and a release-focused `README.md`
- **Production default**: `ENVIRONMENT` now defaults to `production`, so `/debug/*` routes are not registered unless explicitly enabled with `ENVIRONMENT=development`
- **Internal connections service**: port `8001` uses Docker `expose`, not a published host port. Browser-facing OAuth callbacks hit the main API first, then the API proxies internally to the connections service
- **Service-to-service auth**: internal API-to-connections calls include `X-Service-Token`; all connections routes except health require the shared `SERVICE_AUTH_TOKEN`
- **Public OAuth callback paths**: Google and Slack connection OAuth use `/oauth/callback/google` and `/oauth/callback/slack`; MCP OAuth continues to use `/connections/callback`
- **MCP allowlist warning**: `MCP_ALLOWLIST` defaults to empty, which means allow-all. Production deployers should set explicit trusted MCP server URL prefixes to reduce SSRF risk
- **Setup guide set**: `docs/` now includes Docker, Portainer, webhooks, database, encryption, Twilio, Slack, Google OAuth, and OpenRouter guides

The "no CS degree" promise is for *end users*, not deployers. If you're reading this blog, you're probably the deployer.

---

## The Vision

Most AI tools are stateless. Every conversation starts from zero. FortyOne is the opposite:

- **Week 1:** It knows your name, timezone, and assistant preferences.
- **Week 4:** It knows your work patterns, reminders, recurring tasks, and goals.
- **Month 3:** It knows your preferences, challenges, and what motivates you.
- **Month 6:** It has enough context to feel less like a chatbot and more like an operating layer.

The longer you use it, the more irreplaceable it becomes. Not because of lock-in — because it has accumulated context that took months to build, and starting over means losing that.

This is the accumulation moat. And it's why user-centered design matters more than tool count. A system that gets better by knowing *you* is fundamentally different from a system that gets better by connecting more apps.

---

## What's Available Now

**Community OSS release (today):** For tinkerers, hackers, and technical operators who want to self-host for themselves or a small circle. The AGPL-3.0 codebase includes the full multi-tenant architecture, SMS + Slack channels, all 16 built-in tool schemas, proactive scheduler, persona system, custom agents, MCP support, setup guides, and web dashboard. Fork it, extend it, deploy it.

**Managed node (planned):** A hosted deployment that would make the same system available to people who don't want to run infrastructure. This is future work — not yet available.

The assistant that increases your leverage shouldn't require you to become an infrastructure engineer first.

---

**GitHub:** [link]
**Managed node waitlist:** [link]
**Architecture docs:** In the repository
**Multi-tenancy deep dive:** In the repository

*Questions on architecture, the LLM orchestration pattern, or credential isolation — open an issue or find me on X/HN/LinkedIn.*
