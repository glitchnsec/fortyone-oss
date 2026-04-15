# Google OAuth Setup

FortyOne uses Google OAuth to connect users' Gmail and Calendar accounts as **connections** (data sources the assistant can read/act on).

## Prerequisites

- A Google Cloud Console account ([console.cloud.google.com](https://console.cloud.google.com))
- The FortyOne stack running (see [setup-docker.md](setup-docker.md))

## Step 1: Create a Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Click the project selector (top bar) and select **New Project**
3. Name it (e.g. "FortyOne") and click **Create**

## Step 2: Enable Required APIs

1. Navigate to **APIs & Services > Library**
2. Search for and enable each of these:
   - **Gmail API**
   - **Google Calendar API**

## Step 3: Configure the OAuth Consent Screen

1. Go to **APIs & Services > OAuth consent screen**
2. Select **External** user type and click **Create**
3. Fill in:
   - App name: `FortyOne` (or your preferred name)
   - User support email: your email
   - Developer contact information: your email
4. Click **Save and Continue**
5. On the **Scopes** page, click **Add or Remove Scopes** and add:
   - `https://www.googleapis.com/auth/gmail.readonly`
   - `https://www.googleapis.com/auth/gmail.send`
   - `https://www.googleapis.com/auth/calendar.readonly`
   - `https://www.googleapis.com/auth/calendar.events`
6. Click **Save and Continue**
7. On the **Test users** page, add your Google email address (required while in test mode)
8. Click **Save and Continue**

> **Note:** While in "Testing" status, only listed test users can authorize. To allow any Google account, submit for verification (requires a privacy policy URL and domain ownership).

## Step 4: Create OAuth 2.0 Credentials

1. Go to **APIs & Services > Credentials**
2. Click **Create Credentials > OAuth client ID**
3. Select **Web application** as the application type
4. Name it (e.g. "FortyOne Web")
5. Under **Authorized redirect URIs**, add:
   - Development: `http://localhost:8000/oauth/callback/google`
   - Production: `https://your-domain.com/oauth/callback/google`
6. Click **Create**
7. Copy the **Client ID** and **Client Secret**

> **Important:** The redirect URI goes through the main API (port 8000), which proxies to the connections service. Do NOT point it directly at the connections service (port 8001).

## Step 5: Configure Environment Variables

Add these to your `.env` file (main app):

```bash
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
```

The connections service reads these from its own `.env` or from the shared `.env` file via docker-compose. It also uses:

```bash
# In connections/.env (or inherited from docker-compose)
GOOGLE_REDIRECT_URI=http://localhost:8000/oauth/callback/google
```

> **Note:** Configure Google to redirect to the public API proxy route. The API forwards the callback to the internal connections service with `X-Service-Token`.

## Environment Variable Reference

| Variable | Where | Description |
|----------|-------|-------------|
| `GOOGLE_CLIENT_ID` | `.env` + `connections/.env` | OAuth 2.0 Client ID from Google Cloud Console |
| `GOOGLE_CLIENT_SECRET` | `.env` + `connections/.env` | OAuth 2.0 Client Secret |
| `GOOGLE_REDIRECT_URI` | `connections/.env` | Public API proxy redirect URI for Google OAuth (default: `http://localhost:8000/oauth/callback/google`) |

## Verification

1. Start the stack: `docker compose up`
2. Log in to the dashboard at `http://localhost:8000`
3. Navigate to **Connections** and click **Add Google**
4. You should be redirected to Google's consent screen
5. After authorizing, you should be redirected back to the dashboard with the connection active

## Troubleshooting

- **"redirect_uri_mismatch" error:** The redirect URI in Google Console must exactly match what FortyOne sends. Check that `GOOGLE_CLIENT_ID` is set and the redirect URI includes the correct domain and path.
- **"Access blocked: app has not completed verification":** Add your email as a test user on the OAuth consent screen.
- **Scopes not showing in consent screen:** Make sure the Gmail API and Calendar API are enabled in the API Library.
