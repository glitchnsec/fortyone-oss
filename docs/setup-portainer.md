# Portainer Stack Deployment

This guide covers deploying FortyOne as a Docker stack using [Portainer CE](https://www.portainer.io/). Portainer provides a web UI for managing Docker containers, making it easy to deploy, monitor, and update FortyOne without CLI access.

## Prerequisites

- A server with Docker installed
- Portainer CE running ([installation guide](https://docs.portainer.io/start/install-ce))
- The FortyOne repository cloned or accessible via Git

## Step 1: Create the Stack

1. Log in to Portainer at `https://your-server:9443`
2. Select your Docker environment
3. Go to **Stacks** in the left sidebar
4. Click **Add stack**

### Option A: From Git Repository (Recommended)

1. Select **Repository** as the build method
2. Enter the Git repository URL
3. Set the **Compose path** to `docker-compose.yml`
4. Portainer will pull the compose file directly from the repo

### Option B: Upload or Paste

1. Select **Upload** or **Web editor**
2. Paste the contents of `docker-compose.yml` from the repository

## Step 2: Configure Environment Variables

In the **Environment variables** section of the stack creation form, add all required variables. Reference [.env.example](../.env.example) for the complete list.

### Production Environment Variable Checklist

These variables **must** be set for a production deployment:

| Variable | How to Generate | Notes |
|----------|----------------|-------|
| `OPENROUTER_API_KEY` | Get from [openrouter.ai/keys](https://openrouter.ai/keys) | Required for LLM features |
| `JWT_SECRET` | `python -c "import secrets; print(secrets.token_urlsafe(64))"` | Must be unique per deployment |
| `ENCRYPTION_KEY` | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` | See [setup-encryption.md](setup-encryption.md) |
| `SERVICE_AUTH_TOKEN` | `python -c "import secrets; print(secrets.token_urlsafe(32))"` | Must match across all services |
| `DATABASE_URL` | `postgresql+asyncpg://operator:operator@postgres:5432/assistant` | Internal Docker network |
| `REDIS_URL` | `redis://redis:6379` | Internal Docker network |
| `BASE_URL` | `https://your-domain.com` | Public URL (for Twilio signature validation) |
| `MOCK_SMS` | `false` | Set to `false` for real SMS delivery |
| `TWILIO_ACCOUNT_SID` | From Twilio Console | See [setup-twilio.md](setup-twilio.md) |
| `TWILIO_AUTH_TOKEN` | From Twilio Console | See [setup-twilio.md](setup-twilio.md) |
| `TWILIO_PHONE_NUMBER` | From Twilio Console | E.164 format |

### Optional Variables

| Variable | Default | Notes |
|----------|---------|-------|
| `ENVIRONMENT` | `production` | Set to `development` only for debug routes |
| `LLM_MODEL_FAST` | `openai/gpt-4o-mini` | See [setup-openrouter.md](setup-openrouter.md) |
| `LLM_MODEL_CAPABLE` | `anthropic/claude-3.5-haiku` | See [setup-openrouter.md](setup-openrouter.md) |
| `GOOGLE_CLIENT_ID` | — | See [setup-google-oauth.md](setup-google-oauth.md) |
| `SLACK_BOT_TOKEN` | — | See [setup-slack.md](setup-slack.md) |
| `BRAVE_API_KEY` | — | For web search capability |

## Step 3: Deploy

1. Click **Deploy the stack**
2. Portainer will pull images, build containers, and start all services
3. Wait for all containers to show as **Running** in the stack view

## Networking

Portainer creates a default network for the stack. All services communicate using Docker service names as hostnames:

- `postgres` — PostgreSQL database
- `redis` — Redis server
- `connections` — Connections service (internal only, no published ports)
- `api` — Main API server (port 8000)

The `connections` service intentionally has no published ports. It is only accessible within the Docker network. The API service proxies requests to it with `SERVICE_AUTH_TOKEN` authentication.

## Running Migrations

After the first deployment (or after updates that include schema changes):

1. Go to the `api` container in Portainer
2. Click **Console** and select `/bin/bash`
3. Run: `alembic upgrade head`

Or use Portainer's **Exec** feature:
```
alembic upgrade head
```

## Updating the Stack

### For Git-linked stacks:
1. Go to your stack in Portainer
2. Click **Pull and redeploy**
3. Portainer detects new commits and rebuilds

### For manually uploaded stacks:
1. Go to your stack
2. Click **Editor**, update the compose file if needed
3. Click **Update the stack**
4. Check **Re-pull image and redeploy** to ensure latest images

## Monitoring

### Container Logs
1. Click on any container in the stack
2. Select **Logs** to view real-time log output
3. Use the search and filter controls to find specific entries

### Container Stats
1. Click on any container
2. Select **Stats** for CPU, memory, network, and I/O metrics

### Health Checks
Health checks are configured in `docker-compose.yml` for:
- **PostgreSQL:** `pg_isready -U operator -d assistant` (every 5s)
- **Redis:** `redis-cli ping` (every 5s)

Containers depending on these services wait for health checks to pass before starting.

## Pairing with Cloudflare Tunnels

For production deployments, pair FortyOne with a [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) to expose the API securely:

1. Create a Cloudflare Tunnel pointing to `http://api:8000` (or `http://localhost:8000` if the tunnel runs on the host)
2. Set `BASE_URL` to the tunnel's public URL (e.g. `https://fortyone.your-domain.com`)
3. Configure Twilio and Slack webhooks to use this URL

See [setup-webhooks.md](setup-webhooks.md) for detailed webhook configuration.

## Troubleshooting

- **Stack fails to deploy:** Check that all required environment variables are set. Look at container logs for specific errors.
- **Database connection errors:** Ensure `DATABASE_URL` uses `postgres` as the hostname (Docker service name), not `localhost`.
- **Services restarting:** Check container logs for crash reasons. Common causes: missing env vars, database not ready.
- **Cannot reach the API:** Verify port 8000 is published on the `api` service and not blocked by a firewall.
- **connections service errors:** Verify `SERVICE_AUTH_TOKEN` is the same value in all services.
