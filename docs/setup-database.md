# Database Setup (PostgreSQL + Redis)

FortyOne uses PostgreSQL (with pgvector) for persistent storage and Redis for the job queue and real-time pub/sub.

## PostgreSQL

### Why PostgreSQL + pgvector

FortyOne requires the [pgvector](https://github.com/pgvector/pgvector) extension for semantic memory — storing and querying embedding vectors. The Docker Compose file uses the `ankane/pgvector:latest` image, which bundles PostgreSQL with pgvector pre-installed.

### Default Credentials

| Setting | Value |
|---------|-------|
| User | `operator` |
| Password | `operator` |
| Database | `assistant` |
| Port | `5432` |

> **Production:** Change the default credentials. Update `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB` in docker-compose.yml and the `DATABASE_URL` environment variable to match.

> **Note:** The "operator" in the database URL is the PostgreSQL role name, not the product name.

### Connection String

```bash
# Docker Compose (services use Docker DNS)
DATABASE_URL=postgresql+asyncpg://operator:operator@postgres:5432/assistant

# Local development (PostgreSQL on host)
DATABASE_URL=postgresql+asyncpg://operator:operator@localhost:5432/assistant
```

The `+asyncpg` suffix tells SQLAlchemy to use the async PostgreSQL driver. FortyOne's database layer auto-translates common URL schemes:
- `postgresql://` becomes `postgresql+asyncpg://`
- `postgres://` becomes `postgresql+asyncpg://`
- `sqlite:///` becomes `sqlite+aiosqlite:///`

### Running Migrations

FortyOne uses [Alembic](https://alembic.sqlalchemy.org/) for database migrations. All schema changes go through Alembic — never use `Base.metadata.create_all()` in production.

```bash
# Via Docker Compose
docker compose exec api alembic upgrade head

# Locally (with venv)
venv/bin/alembic upgrade head
```

Migrations are idempotent — they check for existing tables and columns before creating them. Safe to run multiple times.

### Creating New Migrations

```bash
# Auto-generate from model changes
docker compose exec api alembic revision --autogenerate -m "add column description to tasks"

# Create an empty migration
docker compose exec api alembic revision -m "custom migration"
```

### Migration Best Practices

- Always check `has_table()` before `CREATE TABLE`
- Always check `get_columns()` before `ALTER TABLE ADD COLUMN`
- Use helper functions like `_table_exists()` and `_column_exists()` (see existing migrations for the pattern)
- This handles brownfield databases and prevents "already exists" errors on re-runs

---

## Redis

### Purpose

Redis serves two roles in FortyOne:

1. **Job Queue (Redis Streams):** The API enqueues jobs; the worker dequeues and processes them
2. **Pub/Sub (Response Delivery):** The worker publishes results; the ResponseListener in the API delivers them to the correct channel

### Connection String

```bash
# Docker Compose (services use Docker DNS)
REDIS_URL=redis://redis:6379

# Local development (Redis on host)
REDIS_URL=redis://localhost:6379
```

### Running Redis Locally

If not using Docker Compose:

```bash
# macOS
brew install redis
brew services start redis

# Linux
sudo apt install redis-server
sudo systemctl start redis
```

### Health Check

Redis has a health check in docker-compose.yml:
```yaml
healthcheck:
  test: ["CMD", "redis-cli", "ping"]
  interval: 5s
  timeout: 3s
  retries: 5
```

Services that depend on Redis wait for this health check to pass before starting.

---

## Data Persistence

Docker Compose uses named volumes for data persistence:

| Volume | Mounted At | Purpose |
|--------|-----------|---------|
| `postgres_data` | `/var/lib/postgresql/data` | PostgreSQL data files |
| `redis_data` | `/data` | Redis persistence (AOF/RDB) |

> **Warning:** Running `docker compose down -v` deletes these volumes and all data. Use `docker compose down` (without `-v`) to stop services while preserving data.

## Environment Variable Reference

| Variable | Where | Description |
|----------|-------|-------------|
| `DATABASE_URL` | `.env` | PostgreSQL connection string (include `+asyncpg` driver) |
| `REDIS_URL` | `.env` | Redis connection string |

## Troubleshooting

- **"connection refused" to postgres:** Wait for the health check to pass. Run `docker compose ps` to check status. Run `docker compose exec postgres pg_isready` to test manually.
- **"pgvector" extension not found:** Make sure you are using the `ankane/pgvector:latest` image, not plain `postgres`.
- **Alembic "already exists" errors:** Migrations should be idempotent. If you see this, the migration may be missing an existence check — add `_table_exists()` / `_column_exists()` guards.
- **Redis connection errors in worker:** The worker depends on Redis health check. If Redis is not healthy, the worker will not start.
- **Stale connections:** The SQLAlchemy engine uses `pool_pre_ping=True` and `pool_recycle=300` to handle connection drops automatically.
