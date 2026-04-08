# Operator — Personal Operating System

A SaaS personal operating system disguised as an AI assistant. Users text (SMS/Slack) or use the web dashboard to interact with their always-on assistant, which handles tasks, remembers context across interactions, connects to external services, and proactively manages both work and personal life.

**Core Value:** Text your assistant, it just handles things — and gets better at it over time.

## Architecture

```
SMS (Twilio)  /  Slack  /  Web Dashboard
         │            │           │
         ▼            ▼           ▼
┌──────────────────────────────────────────┐
│   FastAPI API Server                     │
│   • /sms/inbound — Twilio webhook        │
│   • /slack/events — Slack webhook         │
│   • /api/v1/* — Dashboard REST API        │
│   • /admin/* — Admin dashboard API        │
│   • /auth/* — JWT auth + registration     │
│   • Intent classification + ACK < 500ms   │
│   • Pushes jobs → Redis Streams           │
│   • ResponseListener (pub/sub delivery)   │
└──────────────────────────────────────────┘
         │                    ▲
    Redis Streams         Redis Pub/Sub
         │                    │
         ▼                    │
┌──────────────────────────────────────────┐
│   Worker Process                         │
│   • Manager/subagent tool-calling loop    │
│   • Built-in tools (12) + custom agents   │
│   • Memory context assembly               │
│   • LLM calls via OpenRouter              │
│   • Publishes results                     │
└──────────────────────────────────────────┘
         │
    PostgreSQL (Users, Memories, Tasks, Messages, Goals, Personas, CustomAgents)

┌──────────────────────────────────────────┐
│   Connections Service (Docker)           │
│   • OAuth flow management (Google)        │
│   • Token encryption (Fernet)             │
│   • Gmail + Calendar tool execution       │
│   • Capability manifest per provider      │
│   • Per-persona connection scoping        │
└──────────────────────────────────────────┘

┌──────────────────────────────────────────┐
│   Scheduler Process                      │
│   • Proactive engagement pool             │
│   • Per-user category selection            │
│   • Time-windowed job scheduling           │
│   • Quiet hours enforcement               │
└──────────────────────────────────────────┘

┌──────────────────────────────────────────┐
│   React Dashboard (Vite + shadcn/ui)     │
│   • Registration + onboarding             │
│   • Conversations (filterable by channel) │
│   • Connections (per-persona management)  │
│   • Personas (Work/Personal profiles)     │
│   • Goals + Tasks                         │
│   • Capabilities (built-in + custom)      │
│   • Proactive settings                    │
│   • Admin dashboard (users, analytics)    │
│   • Served from FastAPI static mount      │
└──────────────────────────────────────────┘
```

## Features

### Multi-Channel Messaging
- **SMS** via Twilio (primary channel)
- **Slack** via Bot DM (with account linking)
- Channel-agnostic pipeline — same assistant across all channels
- Proactive messages delivered to user's preferred channel

### Persona System
- Work and Personal persona profiles
- Per-persona connections (separate Google accounts)
- Cross-context awareness ("busy week at work → reschedule gym")
- Automatic persona detection per message

### Proactive Agent
- Morning briefings, day check-ins, evening recaps, goal coaching
- Configurable categories with time windows
- Per-user rate limiting and quiet hours
- Preferred channel selection (SMS or Slack)

### Connections & Tools
- Google (Gmail + Calendar) via OAuth
- Web search (Brave Search API)
- 12 built-in tools across 6 subagents
- Custom agents: webhook, prompt, and YAML/script types
- Per-persona connection scoping
- Dynamic capability manifest

### Admin Dashboard
- User management (view, suspend, delete, impersonate)
- Platform analytics (adoption + usage metrics)
- System health monitoring (Redis, DB, worker)

### Security
- JWT authentication with refresh token rotation
- Twilio signature validation
- Slack signing secret verification
- OAuth token encryption at rest (Fernet)
- Role-based access control (admin/user)
- Multi-tenant user isolation

---

## Quick Start

### Prerequisites
- Docker + Docker Compose
- Python 3.11+

### 1. Configure environment
```bash
cp .env.example .env
# Edit .env — set at minimum:
#   OPENROUTER_API_KEY (for LLM)
#   TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER (for SMS)
#   BRAVE_SEARCH_API_KEY (for web search)
```

### 2. Start all services
```bash
docker compose up --build
```

This starts: API server (port 8000), worker, scheduler, Redis, PostgreSQL, and connections service.

### 3. Access the dashboard
Open http://localhost:8000 and register an account.

### 4. (Optional) Create an admin user
```bash
docker compose exec api python scripts/create_admin.py your@email.com
```

### 5. (Optional) Connect Slack
- Create a Slack app with `message.im` event subscription
- Set Request URL to `https://<your-url>/slack/events`
- Enable Messages Tab in App Home settings
- Set `SLACK_BOT_TOKEN` and `SLACK_SIGNING_SECRET` in `.env`

---

## Project Structure

```
app/
├── main.py              # FastAPI app + lifespan
├── config.py            # Settings (pydantic-settings)
├── database.py          # SQLAlchemy async engine + session
├── crypto.py            # Fernet encryption for sensitive data
├── middleware/
│   └── auth.py          # JWT auth, get_current_user, require_admin
├── routes/
│   ├── sms.py           # Twilio webhook + SMS registration flow
│   ├── slack.py         # Slack events + onboarding + account linking
│   ├── auth.py          # Register, login, refresh, Slack link codes
│   ├── dashboard.py     # User API (me, conversations, connections proxy, proactive settings)
│   ├── admin.py         # Admin API (users, analytics, health)
│   ├── capabilities.py  # Capabilities + custom agents CRUD
│   └── personas.py      # Persona CRUD
├── core/
│   ├── pipeline.py      # Message state machine + ResponseListener
│   ├── intent.py        # Intent classification (LLM + regex fast-path)
│   ├── persona.py       # Persona detection per message
│   ├── ack.py           # Context-aware acknowledgment
│   ├── greeter.py       # First-message greeting
│   ├── identity.py      # Assistant identity preamble
│   ├── tools.py         # Tool registry (subagents.yaml + custom agents)
│   └── proactive_pool.py # Proactive engagement scheduling
├── memory/
│   ├── models.py        # SQLAlchemy models (User, Memory, Task, Message, Persona, Goal, CustomAgent, etc.)
│   └── store.py         # Memory CRUD + tiered context assembly
├── queue/
│   ├── client.py        # Redis Streams producer
│   └── worker.py        # Worker loop + job dispatch
├── tasks/
│   ├── manager.py       # Manager/subagent tool-calling loop
│   ├── router.py        # Intent → handler routing
│   ├── reminder.py      # Reminders + preferences
│   ├── scheduling.py    # Scheduling suggestions
│   ├── recall.py        # Memory recall + general handler
│   ├── web_search.py    # Brave Search integration
│   └── _llm.py          # LLM wrapper (OpenRouter, graceful fallback)
└── channels/
    ├── base.py          # Abstract channel interface
    ├── sms.py           # Twilio SMS channel
    └── slack.py         # Slack Web API channel

connections/             # Dockerized connections service
├── app/
│   ├── main.py          # FastAPI + startup migrations
│   ├── models.py        # Connection, OAuthToken, OAuthState
│   ├── providers/       # Google OAuth provider + base interface
│   ├── routes/          # OAuth flow, connections list, tool execution
│   └── tools/           # Gmail + Calendar tool handlers

dashboard/               # React SPA (Vite + shadcn/ui)
├── src/
│   ├── routes/          # TanStack Router pages
│   ├── components/      # UI components (layout, admin, ui)
│   └── lib/             # Auth context, API client

scripts/
├── run_worker.py        # Worker process entry point
├── run_scheduler.py     # Scheduler process entry point
├── create_admin.py      # Promote user to admin role

config/
└── subagents.yaml       # Built-in tool definitions (6 subagents, 12 tools)

tests/                   # pytest + pytest-asyncio
```

---

## Environment Variables

See `.env.example` for the full list with descriptions. Key variables:

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENROUTER_API_KEY` | Yes | LLM API access via OpenRouter |
| `DATABASE_URL` | No | PostgreSQL URL (default in docker-compose) |
| `REDIS_URL` | No | Redis URL (default in docker-compose) |
| `TWILIO_ACCOUNT_SID` | For SMS | Twilio account credentials |
| `TWILIO_AUTH_TOKEN` | For SMS | Twilio auth token |
| `TWILIO_PHONE_NUMBER` | For SMS | Your Twilio phone number |
| `SLACK_BOT_TOKEN` | For Slack | Slack bot OAuth token |
| `SLACK_SIGNING_SECRET` | For Slack | Slack request signing secret |
| `BRAVE_SEARCH_API_KEY` | For search | Brave Search API key |
| `JWT_SECRET` | Yes | Secret for JWT token signing |
| `FERNET_KEY` | Yes | Encryption key for OAuth tokens |
| `BASE_URL` | For SMS | Public URL for Twilio signature validation |

---

## Running Tests

```bash
pip install -r requirements.txt
python -m pytest tests/ -v
```

---

## License

Proprietary. All rights reserved.
