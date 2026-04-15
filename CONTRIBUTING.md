# Contributing to FortyOne

FortyOne is an AGPL-3.0 licensed personal operating system disguised as an AI assistant. Contributions are welcome when they preserve the project's core constraints: multi-tenant isolation, SMS-first usability, secure credential handling, and a clean operator experience.

## Development Setup

Prerequisites:

- Docker and Docker Compose
- Python 3.11
- Node.js 20+
- A PostgreSQL-compatible database with pgvector for production-like testing
- Redis

Start from the example environment:

```bash
cp .env.example .env
cp connections/.env.example connections/.env
```

Set at least these values before using real integrations:

```bash
JWT_SECRET=...
ENCRYPTION_KEY=...
SERVICE_AUTH_TOKEN=...
OPENROUTER_API_KEY=...
```

For local development:

```bash
docker compose up --build
```

The API serves the dashboard at `http://localhost:8000`. The connections service is internal to the compose network and should not be exposed directly in production.

## Project Structure

```text
app/                 Main FastAPI app, API routes, pipeline, worker logic, memory, tools, channels
connections/         Internal connections service for OAuth, provider tools, MCP gateway support
dashboard/           React/Vite dashboard
docs/                Public setup guides and launch docs
config/              Subagent and tool configuration
alembic/             Main database migrations
connections/alembic/ Connections service migrations
tests/               Main app tests
connections/tests/   Connections service tests
```

## Code Style

- Keep tenant boundaries explicit. Pass `user_id` and persona scope through call chains instead of relying on global state.
- Treat the connections service as an internal service. API-to-connections calls must use `X-Service-Token`.
- Do not expose OAuth tokens, service tokens, API keys, phone numbers, or user content in logs.
- Keep channel code behind the channel abstraction. SMS, Slack, and future channels should share the same pipeline where possible.
- Prefer small, focused modules over broad framework abstractions.
- Document new production configuration in `.env.example` and the relevant `docs/setup-*.md` guide.

## Testing

Run focused tests while developing:

```bash
python -m pytest tests/test_service_auth.py tests/test_oauth_callback_proxy.py -q
python -m pytest connections/tests -q
```

Run dashboard checks from `dashboard/`:

```bash
npm run build
npm run lint
```

For changes that touch OAuth, webhook validation, Redis queueing, or multi-tenant data access, add tests that verify the boundary directly. A mocked success return is not enough if the risk is data landing in the wrong table or being exposed through the wrong route.

## Pull Requests

A good PR includes:

- A concise description of the behavior change
- Tests or a clear explanation of why tests were not practical
- Any required migration or environment variable changes
- Updates to setup docs for new integrations or production requirements
- Security notes for changes involving auth, OAuth, encryption, webhooks, Redis, or tenant isolation

Before opening a PR, check for accidental secrets:

```bash
git diff --cached
rg -n "sk-|xoxb-|AC[0-9a-fA-F]{32}|BEGIN PRIVATE KEY|SERVICE_AUTH_TOKEN|JWT_SECRET|ENCRYPTION_KEY" .
```

## License

By contributing, you agree that your contribution will be licensed under the GNU Affero General Public License v3.0 only (`AGPL-3.0-only`), matching the repository license.
