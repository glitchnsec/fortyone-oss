# Docker Deployment

FortyOne runs as a multi-service stack via Docker Compose. This guide covers both local development and production deployment.

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) (20.10+)
- [Docker Compose](https://docs.docker.com/compose/install/) (v2+)

## Services Overview

| Service | Image | Port | Description |
|---------|-------|------|-------------|
| `api` | Custom (Dockerfile) | 8000 | FastAPI server — webhooks, dashboard, REST API |
| `worker` | Custom (Dockerfile) | — | Background job processor (LLM calls, task execution) |
| `connections` | Custom (connections/Dockerfile) | — (internal 8001) | OAuth token management and connection service |
| `scheduler` | Custom (Dockerfile) | — | Proactive messaging scheduler |
| `postgres` | `ankane/pgvector:latest` | 5432 | PostgreSQL with pgvector extension |
| `redis` | `redis:7-alpine` | 6379 | Job queue (Redis Streams) and pub/sub |

> **Note:** The `connections` service is NOT exposed to the host. It is only accessible via the internal Docker network. This is intentional — all requests to connections go through the API service, which adds `SERVICE_AUTH_TOKEN` authentication.

## Quick Start (Local Development)

```bash
# 1. Clone the repository
git clone https://github.com/your-org/fortyone.git
cd fortyone

# 2. Copy and configure environment variables
cp .env.example .env
# Edit .env — at minimum set OPENROUTER_API_KEY and JWT_SECRET

# 3. Build and start
docker compose up --build

# 4. Run database migrations
docker compose exec api alembic upgrade head

# 5. Access the dashboard
open http://localhost:8000
```

## Environment Setup

Before starting, configure your `.env` file. See [.env.example](../.env.example) for all variables.

**Minimum required for development:**

```bash
OPENROUTER_API_KEY=sk-or-...          # LLM features (or leave blank for mock mode)
JWT_SECRET=change-me-in-production     # Auth tokens
MOCK_SMS=true                          # Skip Twilio for local dev
```

**Required for production:**

```bash
OPENROUTER_API_KEY=sk-or-...
JWT_SECRET=<generate-a-strong-secret>
ENCRYPTION_KEY=<fernet-key>            # See docs/setup-encryption.md
SERVICE_AUTH_TOKEN=<shared-secret>     # See docs/setup-encryption.md
MOCK_SMS=false
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_PHONE_NUMBER=+1...
BASE_URL=https://your-domain.com
```

## Docker Compose Overrides

The default `docker-compose.yml` is configured for development:

- **Source code mounts:** `./app:/app/app` and `./scripts:/app/scripts` enable hot-reload
- **`--reload` flag:** Uvicorn watches for file changes
- **Ports exposed:** PostgreSQL (5432) and Redis (6379) accessible from host

### Production Adjustments

For production, create a `docker-compose.prod.yml` override or modify the compose file:

1. **Remove source code volume mounts** — use the baked-in image code instead
2. **Remove `--reload`** from the api command
3. **Remove host port mappings** for postgres and redis (keep them internal)
4. **Set `restart: unless-stopped`** on all services (already set on connections and scheduler)
5. **Use named volumes only** for data persistence

Example production api command:
```yaml
command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
```

## Building the Dashboard

The web dashboard (React SPA) is baked into the API Docker image during build. If you modify dashboard code:

```bash
# Rebuild the dashboard
cd dashboard && npm run build && cd ..

# Rebuild the API image
docker compose build api
```

The `Dockerfile` copies `dashboard/dist/` into the image. The API serves these static files.

## Service Communication

```
[External]  -->  api (8000)  -->  redis (6379)  -->  worker
                    |                                   |
                    +--------> connections (8001)       +-> postgres (5432)
                    |                                   |
                    +--------> postgres (5432)          +-> redis (6379)
                    |
                scheduler --> redis --> worker
```

All inter-service communication uses Docker's internal DNS (service names as hostnames):
- `redis://redis:6379`
- `postgresql+asyncpg://operator:operator@postgres:5432/assistant`
- `http://connections:8001`

## Common Commands

```bash
# Start all services
docker compose up --build

# Start in background
docker compose up -d --build

# View logs for a specific service
docker compose logs -f api
docker compose logs -f worker

# Run database migrations
docker compose exec api alembic upgrade head

# Create a new migration
docker compose exec api alembic revision --autogenerate -m "description"

# Stop all services
docker compose down

# Stop and remove volumes (WARNING: deletes all data)
docker compose down -v

# Rebuild a single service
docker compose build api
docker compose up -d api
```

## Health Checks

The following health checks are configured in `docker-compose.yml`:

| Service | Check | Interval |
|---------|-------|----------|
| `postgres` | `pg_isready -U operator -d assistant` | 5s |
| `redis` | `redis-cli ping` | 5s |

Services that depend on postgres and redis will wait for these health checks to pass before starting.

## Troubleshooting

- **"connection refused" to postgres/redis:** Wait for health checks. Run `docker compose ps` to see service status.
- **Migrations fail:** Make sure postgres is healthy first: `docker compose exec postgres pg_isready`
- **Dashboard not loading:** Ensure `dashboard/dist/` was built before `docker compose build api`. The dist folder is copied during Docker build.
- **Hot-reload not working:** Verify source code volume mounts are in the compose file (development only).
- **connections service unreachable:** It intentionally has no host port. The API proxies requests to it internally. Check `SERVICE_AUTH_TOKEN` matches between services.
