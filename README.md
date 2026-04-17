# FortyOne

A personal operating system disguised as an AI assistant.

FortyOne lets users interact with their own named assistant over SMS, Slack, and the web. The assistant can remember context, manage tasks and goals, use connected services, and proactively help with work and personal life. The product is multi-tenant by design: one operator can run an instance for many users while each user's data, tools, memories, and credentials remain isolated.

## Core Idea

Users do not need to learn a new app to use an assistant. They text the assistant they already named, for example: "Jarvis, your FortyOne operator." FortyOne handles the infrastructure, routing, memory, tools, and proactive follow-up behind that simple interface.

**Read the full story:** [Your Chief of Staff, One Text Away — Building FortyOne](https://medium.com/@glitchnsec/your-chief-of-staff-one-text-away-building-fortyone-c7b71c40de74)

## Features

- SMS-first assistant via Twilio, with Slack DM support and a web dashboard for setup and management.
- Fast ACK plus async worker architecture using Redis Streams and Redis Pub/Sub.
- Manager/subagent tool-calling loop with built-in tools, custom agents, and MCP-backed connections.
- Persona-aware operation for work, personal, and shared contexts.
- Google Gmail and Calendar integrations through the internal connections service.
- Slack workspace connection support separate from Slack DM delivery.
- Proactive scheduler for briefings, recaps, goal coaching, nudges, cooldowns, quiet hours, and content suppression.
- Context engineering with tiered retrieval, conversation history, memories, profile traits, active tasks, and persona scope.
- Multi-tenant isolation across routes, queue payloads, database queries, Redis keys, OAuth tokens, and tool execution.
- Admin dashboard for users, analytics, health, and operational visibility.

## Architecture

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
        │  • OAuth callback proxy          │
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
        │  • 16 tools across 7 subagents   │
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

The connections service is intentionally internal-only. Public OAuth callbacks land on the main API at `/oauth/callback/google` and `/oauth/callback/slack`; the API forwards them to the connections service with `X-Service-Token`.

## Quick Start

Prerequisites:

- Docker and Docker Compose
- Python 3.11+
- Node.js 20+ if developing the dashboard outside Docker

Create environment files:

```bash
cp .env.example .env
cp connections/.env.example connections/.env
```

Generate required secrets:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"  # JWT_SECRET
python -c "import secrets; print(secrets.token_urlsafe(32))"  # SERVICE_AUTH_TOKEN
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # ENCRYPTION_KEY
```

Start the stack:

```bash
docker compose up --build
```

Open the dashboard:

```text
http://localhost:8000
```

For real SMS, configure Twilio, set `MOCK_SMS=false`, and expose the API through a stable HTTPS URL so Twilio can reach `/sms/inbound`.

## Production Security Checklist

Before exposing an instance to real users:

- Replace `JWT_SECRET`; never use `change-me-in-production`.
- Set `ENCRYPTION_KEY`; OAuth tokens and sensitive values depend on it.
- Set the same strong `SERVICE_AUTH_TOKEN` for the API, worker, scheduler, and connections service.
- Keep the connections service internal; do not publish port `8001` to the internet.
- Set `MOCK_SMS=false` only after Twilio credentials and webhook validation are configured.
- Set `BASE_URL` to the exact public HTTPS origin used by Twilio and Slack webhooks.
- Configure OAuth redirects to the API callback paths, not the internal connections service.
- Set `MCP_ALLOWLIST` in production; an empty allowlist permits arbitrary MCP server URLs and creates SSRF risk.
- Use a strong PostgreSQL password and do not reuse the sample `operator:operator` local database credentials.
- Keep `ENVIRONMENT=production` unless intentionally enabling development-only debug routes.
- Rotate any exposed OpenRouter, Twilio, Slack, Google, or Brave credentials immediately.

## Production Environment Variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | Yes | PostgreSQL connection string for app data. Use pgvector for semantic memory. |
| `REDIS_URL` | Yes | Redis queue, pub/sub, proactive plans, cooldowns, and idempotency. |
| `JWT_SECRET` | Yes | Signs API access and refresh tokens. Must be unique per deployment. |
| `ENCRYPTION_KEY` | Yes | Fernet key for OAuth tokens and sensitive data at rest. Do not rotate without migration. |
| `SERVICE_AUTH_TOKEN` | Yes | Shared secret for API-to-connections internal calls. |
| `OPENROUTER_API_KEY` | Yes | LLM provider access through OpenRouter. |
| `TWILIO_ACCOUNT_SID` | For SMS | Twilio account identifier. |
| `TWILIO_AUTH_TOKEN` | For SMS | Twilio API secret and webhook validation secret. |
| `TWILIO_PHONE_NUMBER` | For SMS | Outbound assistant number. |
| `MOCK_SMS` | Recommended | Use `true` for local logs-only SMS; use `false` for real delivery. |
| `BASE_URL` | Production | Public HTTPS origin for webhooks and callback generation. |
| `DASHBOARD_URL` | OAuth | Browser landing URL after OAuth connection flows. |
| `CONNECTIONS_SERVICE_URL` | Yes | Internal URL, usually `http://connections:8001` in Docker Compose. |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Google | Gmail and Calendar OAuth. |
| `SLACK_BOT_TOKEN` / `SLACK_SIGNING_SECRET` | Slack DM | Slack channel delivery and event verification. |
| `SLACK_CLIENT_ID` / `SLACK_CLIENT_SECRET` | Slack connection | Slack workspace OAuth inside `connections/.env`. |
| `MCP_ALLOWLIST` | Production | Comma-separated trusted MCP server URL prefixes. Empty means allow all. |
| `ENVIRONMENT` | Recommended | Defaults to `production`; set `development` only for debug routes. |

## Setup Guides

| Guide | Purpose |
| --- | --- |
| [Docker Setup](docs/setup-docker.md) | Local and production Docker Compose behavior. |
| [Portainer Setup](docs/setup-portainer.md) | Deploying and operating through Portainer. |
| [Webhook Setup](docs/setup-webhooks.md) | Public webhook and OAuth callback URLs. |
| [Database Setup](docs/setup-database.md) | PostgreSQL, pgvector, migrations, and backups. |
| [Encryption Setup](docs/setup-encryption.md) | Fernet key generation and token encryption requirements. |
| [Twilio Setup](docs/setup-twilio.md) | SMS registration, inbound webhooks, and outbound delivery. |
| [Slack Setup](docs/setup-slack.md) | Slack DM channel and Slack workspace connection configuration. |
| [Google OAuth Setup](docs/setup-google-oauth.md) | Gmail and Calendar OAuth setup. |
| [OpenRouter Setup](docs/setup-openrouter.md) | LLM API keys and model configuration. |

## Development

Run backend tests:

```bash
python -m pytest tests -q
python -m pytest connections/tests -q
```

Run dashboard checks:

```bash
cd dashboard
npm run build
npm run lint
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution expectations, project structure, and security-sensitive review guidance.

## License

FortyOne is licensed under the GNU Affero General Public License v3.0 only (`AGPL-3.0-only`). See [LICENSE](LICENSE).

If you modify FortyOne and run it as a network service, the AGPL requires that you provide the corresponding source code to users who interact with that service over the network.

## Trademark

"FortyOne" is the project name for this open source release. The AGPL license grants rights to the code; it does not grant trademark rights or imply endorsement by the project maintainers.
