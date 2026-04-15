# Webhook Exposure

FortyOne receives inbound messages via webhooks from Twilio (SMS) and Slack (Events API). These webhooks require a publicly accessible HTTPS URL. This guide covers how to expose your server for both development and production.

## Webhook Endpoints

| Endpoint | Method | Source | Purpose |
|----------|--------|--------|---------|
| `/sms/inbound` | POST | Twilio | Inbound SMS messages |
| `/slack/events` | POST | Slack | Slack DM events |
| `/connections/callback` | GET | Google/Slack OAuth | OAuth redirect after authorization |

---

## Local Development: ngrok

[ngrok](https://ngrok.com) creates a temporary public HTTPS URL that tunnels to your local server.

### Setup

1. Install ngrok: [ngrok.com/download](https://ngrok.com/download)
2. Sign up for a free account and authenticate:
   ```bash
   ngrok config add-authtoken your-auth-token
   ```
3. Start the tunnel:
   ```bash
   ngrok http 8000
   ```
4. Copy the HTTPS URL (e.g. `https://abcd-1234.ngrok-free.app`)

### Configure FortyOne

Set `BASE_URL` in your `.env` to the ngrok URL:

```bash
BASE_URL=https://abcd-1234.ngrok-free.app
```

### Configure External Services

- **Twilio:** Set the webhook URL to `https://abcd-1234.ngrok-free.app/sms/inbound` (see [setup-twilio.md](setup-twilio.md))
- **Slack:** Set the Events Request URL to `https://abcd-1234.ngrok-free.app/slack/events` (see [setup-slack.md](setup-slack.md))

> **Note:** The ngrok URL changes every time you restart ngrok (unless you have a paid plan with reserved domains). Update Twilio and Slack webhook URLs each time.

---

## Production: Cloudflare Tunnels

[Cloudflare Tunnels](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) provide a stable, secure way to expose your server without opening firewall ports.

### Prerequisites

- A Cloudflare account with a domain
- `cloudflared` installed on your server ([install guide](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/))

### Setup

1. Authenticate cloudflared:
   ```bash
   cloudflared tunnel login
   ```

2. Create a tunnel:
   ```bash
   cloudflared tunnel create fortyone
   ```

3. Configure the tunnel (create `~/.cloudflared/config.yml`):
   ```yaml
   tunnel: fortyone
   credentials-file: /root/.cloudflared/<tunnel-id>.json

   ingress:
     - hostname: fortyone.your-domain.com
       service: http://localhost:8000
     - service: http_status:404
   ```

4. Add DNS record:
   ```bash
   cloudflared tunnel route dns fortyone fortyone.your-domain.com
   ```

5. Start the tunnel:
   ```bash
   cloudflared tunnel run fortyone
   ```

### Configure FortyOne

```bash
BASE_URL=https://fortyone.your-domain.com
```

### Configure External Services

- **Twilio:** `https://fortyone.your-domain.com/sms/inbound`
- **Slack:** `https://fortyone.your-domain.com/slack/events`
- **Google OAuth redirect:** `https://fortyone.your-domain.com/connections/callback`
- **Slack OAuth redirect:** `https://fortyone.your-domain.com/connections/callback`

---

## Important: BASE_URL Must Match Exactly

Twilio signs webhook requests using the URL it sends to. FortyOne validates this signature using the `BASE_URL` environment variable.

**If `BASE_URL` does not exactly match the webhook URL configured in Twilio, all inbound SMS requests will fail with a 403 error.**

Common mismatches:
- Trailing slash: `https://example.com/` vs `https://example.com`
- HTTP vs HTTPS: `http://` vs `https://`
- Different subdomain: `www.example.com` vs `example.com`

## OAuth Callback URLs

Google and Slack OAuth redirect URIs must match what is configured in their respective developer consoles:

| Service | Development | Production |
|---------|-------------|------------|
| Google OAuth | `http://localhost:8000/connections/callback` | `https://your-domain.com/connections/callback` |
| Slack Connection OAuth | `http://localhost:8000/connections/callback` | `https://your-domain.com/connections/callback` |

> **Note:** These callbacks go through the API server (port 8000), which proxies to the connections service. Never point OAuth callbacks directly at the connections service.

## Troubleshooting

- **403 on Twilio webhooks:** `BASE_URL` does not match the Twilio webhook URL exactly.
- **Slack "url_verification" fails:** The server must be running and accessible at the Events Request URL before Slack will accept it.
- **OAuth redirect fails:** The redirect URI in the developer console must exactly match the one FortyOne sends. Check the URL in the browser address bar during the OAuth flow.
- **ngrok URL expired:** Free ngrok URLs change on restart. Update webhook URLs in Twilio and Slack after restarting ngrok.
