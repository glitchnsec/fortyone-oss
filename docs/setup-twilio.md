# Twilio SMS Setup

FortyOne uses Twilio for SMS-based messaging — the primary channel for interacting with the assistant.

## Prerequisites

- A Twilio account ([twilio.com](https://www.twilio.com))
- The FortyOne stack running (see [setup-docker.md](setup-docker.md))

## Step 1: Create a Twilio Account

1. Sign up at [twilio.com/try-twilio](https://www.twilio.com/try-twilio)
2. Verify your email and phone number
3. After signing in, you land on the **Console Dashboard**

## Step 2: Get Account Credentials

1. On the Console Dashboard, find:
   - **Account SID** (starts with `AC`)
   - **Auth Token** (click to reveal)
2. Copy both values

## Step 3: Get a Phone Number

1. Go to **Phone Numbers > Manage > Buy a number** (or use the trial number provided)
2. Choose a number with SMS capability
3. Copy the phone number in E.164 format (e.g. `+15551234567`)

> **Trial accounts:** You can only send SMS to verified phone numbers. Add recipients under **Phone Numbers > Manage > Verified Caller IDs**.

## Step 4: Configure the Webhook URL

1. Go to **Phone Numbers > Manage > Active Numbers**
2. Click your phone number
3. Under **Messaging > A message comes in**, set:
   - **Webhook URL:** `https://your-domain.com/sms/inbound`
   - **HTTP Method:** `POST`
4. Click **Save**

> **For local development:** Use ngrok or a Cloudflare Tunnel to expose your local server. See [setup-webhooks.md](setup-webhooks.md). Example: `https://abcd-1234.ngrok-free.app/sms/inbound`

> **Important:** Twilio signs requests using the webhook URL. The `BASE_URL` env var must exactly match the URL configured in Twilio, or signature validation will fail with a 403 error.

## Step 5: Set Up Twilio Verify (for OTP Registration)

FortyOne uses Twilio Verify to send OTP codes during web registration (phone verification step).

1. Go to **Verify > Services** in the Twilio Console
2. Click **Create new** (or use an existing service)
3. Name it (e.g. "FortyOne OTP")
4. Copy the **Service SID** (starts with `VA`)

> **Dev mode:** If `TWILIO_VERIFY_SERVICE_SID` is left blank, the OTP step accepts any 6-digit code without sending a real SMS. This is useful for local development.

## Step 6: Configure Environment Variables

Add to your `.env` file:

```bash
# Core Twilio credentials
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_auth_token_here
TWILIO_PHONE_NUMBER=+15551234567

# OTP verification (leave blank for dev mode)
TWILIO_VERIFY_SERVICE_SID=VAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Set to false in production to send real SMS
MOCK_SMS=false

# Must match the webhook URL configured in Twilio exactly
BASE_URL=https://your-domain.com
```

## Environment Variable Reference

| Variable | Where | Description |
|----------|-------|-------------|
| `TWILIO_ACCOUNT_SID` | `.env` | Account SID from the Twilio Console dashboard |
| `TWILIO_AUTH_TOKEN` | `.env` | Auth Token from the Twilio Console dashboard |
| `TWILIO_PHONE_NUMBER` | `.env` | Your Twilio phone number in E.164 format |
| `TWILIO_VERIFY_SERVICE_SID` | `.env` | Verify Service SID for OTP (leave blank for dev mode) |
| `MOCK_SMS` | `.env` | Set `true` to log SMS instead of sending (default: `true`) |
| `BASE_URL` | `.env` | Public URL for webhook signature validation |

## Mock Mode (Development)

For local development without Twilio credentials:

- Set `MOCK_SMS=true` (or leave `TWILIO_ACCOUNT_SID` blank)
- All outbound SMS messages are printed to the application logs instead of being sent
- Inbound webhooks still work if you POST to `/sms/inbound` manually

Mock mode is enabled automatically when `TWILIO_ACCOUNT_SID` is empty.

## Verification

1. Configure the webhook URL in Twilio to point at your server
2. Send an SMS to your Twilio number
3. You should see the message in FortyOne's logs and receive a response

## Troubleshooting

- **403 on inbound SMS:** `BASE_URL` does not match the Twilio webhook URL. They must be identical.
- **SMS not arriving:** Check that `MOCK_SMS` is `false` and `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` are correct.
- **OTP not sending:** Verify `TWILIO_VERIFY_SERVICE_SID` is set. Check Twilio Verify logs in the Console for errors.
- **Trial account limitations:** Trial accounts can only send to verified numbers and include a "Sent from your Twilio trial account" prefix.
