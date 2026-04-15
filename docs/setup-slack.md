# Slack Integration Setup

FortyOne uses **two separate Slack apps** for different purposes. This guide covers both.

## Overview

| App | Purpose | Token Type | Where Configured |
|-----|---------|------------|------------------|
| **DM Bot App** | Users message FortyOne via Slack DMs (messaging channel) | Bot token (`xoxb-`) | Main `.env` |
| **Connection OAuth App** | FortyOne reads workspace data as a tool (connection) | User token (`xoxp-`) | `connections/.env` |

**Why two apps?** The DM bot uses bot-level permissions to receive and send direct messages. The connection app uses user-level OAuth to read workspace channels and threads on behalf of a specific user. These are fundamentally different permission models.

---

## Part 1: DM Bot App (Messaging Channel)

This app lets users interact with FortyOne by sending it Slack DMs.

### Step 1: Create the Bot App

1. Go to [api.slack.com/apps](https://api.slack.com/apps) and click **Create New App**
2. Choose **From scratch**
3. Name it (e.g. "FortyOne") and select your workspace
4. Click **Create App**

### Step 2: Configure Bot Token Scopes

1. In the left sidebar, go to **OAuth & Permissions**
2. Under **Bot Token Scopes**, add:
   - `chat:write` — send messages
   - `im:history` — read DM history
   - `im:read` — view DM channels
   - `im:write` — open DM channels

### Step 3: Enable Event Subscriptions

1. In the left sidebar, go to **Event Subscriptions**
2. Toggle **Enable Events** to ON
3. Set the **Request URL** to:
   - Development: `https://your-ngrok-url.ngrok-free.app/slack/events`
   - Production: `https://your-domain.com/slack/events`
4. Under **Subscribe to bot events**, add:
   - `message.im` — triggers when a user sends a DM to the bot
5. Click **Save Changes**

> **Note:** Slack will verify the Request URL immediately. Your FortyOne server must be running and publicly accessible (see [setup-webhooks.md](setup-webhooks.md)).

### Step 4: Install to Workspace

1. Go to **OAuth & Permissions**
2. Click **Install to Workspace** and authorize
3. Copy the **Bot User OAuth Token** (starts with `xoxb-`)

### Step 5: Get the Signing Secret

1. Go to **Basic Information**
2. Under **App Credentials**, copy the **Signing Secret**

### Step 6: Configure Environment Variables

Add to your main `.env` file:

```bash
SLACK_BOT_TOKEN=xoxb-your-bot-token
SLACK_SIGNING_SECRET=your-signing-secret
```

### DM Bot Environment Variable Reference

| Variable | Where | Description |
|----------|-------|-------------|
| `SLACK_BOT_TOKEN` | `.env` | Bot User OAuth Token (`xoxb-...`) from OAuth & Permissions page |
| `SLACK_SIGNING_SECRET` | `.env` | Signing Secret from Basic Information > App Credentials |

---

## Part 2: Connection OAuth App (Workspace Data Access)

This app lets FortyOne read a user's Slack workspace channels and threads as a **connection** — a data source the assistant can query.

### Step 1: Create a Separate App

1. Go to [api.slack.com/apps](https://api.slack.com/apps) and click **Create New App**
2. Choose **From scratch**
3. Name it differently (e.g. "FortyOne Workspace Reader")
4. Select your workspace and click **Create App**

> **Important:** This must be a separate app from the DM bot. They serve different roles and need different OAuth scopes.

### Step 2: Configure User Token Scopes

1. Go to **OAuth & Permissions**
2. Under **User Token Scopes** (not Bot Token Scopes), add:
   - `channels:read` — list public channels
   - `channels:history` — read public channel messages
   - `groups:read` — list private channels
   - `groups:history` — read private channel messages
   - `users:read` — resolve user names
   - `team:read` — get workspace info

### Step 3: Configure Redirect URI

1. Under **OAuth & Permissions > Redirect URLs**, add:
   - Development: `http://localhost:8000/oauth/callback/slack`
   - Production: `https://your-domain.com/oauth/callback/slack`
2. Click **Save URLs**

> **Note:** The redirect goes through the main API (port 8000), which proxies to the connections service. Do NOT point it at port 8001 directly.

### Step 4: Enable Token Rotation

1. Go to **OAuth & Permissions**
2. Under the **Token Rotation** section, enable token rotation
3. This ensures refresh tokens work correctly for long-lived connections

### Step 5: Get Client Credentials

1. Go to **Basic Information**
2. Under **App Credentials**, copy:
   - **Client ID**
   - **Client Secret**

### Step 6: Configure Environment Variables

Add to `connections/.env` (or the shared `.env` if using docker-compose):

```bash
SLACK_CLIENT_ID=your-client-id
SLACK_CLIENT_SECRET=your-client-secret
SLACK_REDIRECT_URI=http://localhost:8000/oauth/callback/slack
```

### Connection OAuth Environment Variable Reference

| Variable | Where | Description |
|----------|-------|-------------|
| `SLACK_CLIENT_ID` | `connections/.env` | Client ID from the Connection OAuth app |
| `SLACK_CLIENT_SECRET` | `connections/.env` | Client Secret from the Connection OAuth app |
| `SLACK_REDIRECT_URI` | `connections/.env` | Public API proxy redirect URI for Slack OAuth (default: `http://localhost:8000/oauth/callback/slack`) |

---

## Verification

### DM Bot

1. Start the stack: `docker compose up`
2. Expose the API publicly (see [setup-webhooks.md](setup-webhooks.md))
3. In Slack, find the bot in your DMs and send a message
4. You should receive a response from FortyOne

### Connection OAuth

1. Log in to the FortyOne dashboard at `http://localhost:8000`
2. Navigate to **Connections** and click **Add Slack**
3. Authorize the workspace reader app
4. The connection should appear as active in the dashboard

## Troubleshooting

- **"url_verification" failing:** Your server must respond to Slack's URL verification challenge. Make sure the FortyOne API is running and publicly accessible at the Request URL.
- **DMs not triggering:** Confirm `message.im` is subscribed under Event Subscriptions, and the bot is installed to the workspace.
- **"invalid_redirect_uri" on connection OAuth:** The redirect URI must exactly match what is configured in the Slack app's Redirect URLs section.
- **Slack channel not appearing in FortyOne:** If `SLACK_BOT_TOKEN` or `SLACK_SIGNING_SECRET` are blank, the Slack route is not registered at startup.
