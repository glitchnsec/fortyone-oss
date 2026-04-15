# Encryption Key Setup

FortyOne encrypts sensitive data at rest using [Fernet symmetric encryption](https://cryptography.io/en/latest/fernet/) (from the `cryptography` Python library). This guide covers generating and configuring the required secrets.

## What Gets Encrypted

- OAuth access and refresh tokens (connections service)
- User PII stored in the connections database
- Any sensitive credential data from connected services

## Step 1: Generate a Fernet Encryption Key

Run this command to generate a key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

This produces a URL-safe base64-encoded 32-byte key, e.g.:
```
dGhpcyBpcyBhIHNhbXBsZSBrZXkgZm9yIGRvY3M=
```

## Step 2: Generate a Service Auth Token

The `SERVICE_AUTH_TOKEN` is a shared secret used for API-to-connections service authentication. Generate it with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

## Step 3: Configure Environment Variables

Add both values to your `.env` file:

```bash
# Fernet key for encrypting sensitive data at rest
ENCRYPTION_KEY=your-generated-fernet-key

# Shared secret for API <-> connections service auth
SERVICE_AUTH_TOKEN=your-generated-token
```

Both the main API and the connections service need the same `ENCRYPTION_KEY` and `SERVICE_AUTH_TOKEN`. In Docker Compose, the environment variables are shared via `env_file: .env` and the `environment:` section in each service.

## Step 4: Generate a JWT Secret

While setting up secrets, also generate a unique JWT secret:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Set it in `.env`:

```bash
JWT_SECRET=your-generated-jwt-secret
```

> **Important:** The default `JWT_SECRET` is `change-me-in-production`. Always replace it before deploying.

## Environment Variable Reference

| Variable | Where | Description |
|----------|-------|-------------|
| `ENCRYPTION_KEY` | `.env` | Fernet key for encrypting OAuth tokens and PII |
| `SERVICE_AUTH_TOKEN` | `.env` | Shared secret for inter-service authentication |
| `JWT_SECRET` | `.env` | Secret for signing JWT access and refresh tokens |

## Security Warnings

**Do NOT lose the encryption key.** If the `ENCRYPTION_KEY` is lost, all encrypted data (OAuth tokens, credentials) becomes permanently unrecoverable. Users would need to re-authorize all their connections.

**Do NOT reuse keys across environments.** Use different `ENCRYPTION_KEY`, `SERVICE_AUTH_TOKEN`, and `JWT_SECRET` values for development, staging, and production.

**Do NOT commit secrets to version control.** The `.env` file is in `.gitignore`. Never add real keys to `.env.example` or any tracked file.

**Back up the encryption key securely.** Store it in a password manager, secrets vault (e.g. AWS Secrets Manager, HashiCorp Vault), or encrypted backup — separate from the database backup.

**Key rotation requires a migration.** If you need to rotate the `ENCRYPTION_KEY`, you must decrypt all existing data with the old key and re-encrypt with the new key. There is no built-in rotation script yet.

## Quick Reference: All Secrets to Generate

For a fresh production deployment, generate all three secrets:

```bash
# 1. Encryption key (Fernet)
python -c "from cryptography.fernet import Fernet; print('ENCRYPTION_KEY=' + Fernet.generate_key().decode())"

# 2. Service auth token
python -c "import secrets; print('SERVICE_AUTH_TOKEN=' + secrets.token_urlsafe(32))"

# 3. JWT secret
python -c "import secrets; print('JWT_SECRET=' + secrets.token_urlsafe(64))"
```

Copy the output directly into your `.env` file or Portainer environment variables.
