# OpenRouter (LLM) Setup

FortyOne uses [OpenRouter](https://openrouter.ai) as a model-agnostic LLM gateway. OpenRouter provides a single API compatible with the OpenAI SDK, routing requests to any supported model (OpenAI, Anthropic, Google, Meta, etc.).

## Prerequisites

- An OpenRouter account ([openrouter.ai](https://openrouter.ai))

## Step 1: Create an Account

1. Go to [openrouter.ai](https://openrouter.ai) and sign up
2. Add credits to your account (most useful models require a paid balance)

## Step 2: Generate an API Key

1. Go to [openrouter.ai/keys](https://openrouter.ai/keys)
2. Click **Create Key**
3. Name it (e.g. "FortyOne")
4. Copy the key (starts with `sk-or-`)

## Step 3: Configure Environment Variables

Add to your `.env` file:

```bash
# Required
OPENROUTER_API_KEY=sk-or-your-api-key-here

# Optional: model selection (defaults shown)
LLM_MODEL_FAST=openai/gpt-4o-mini
LLM_MODEL_CAPABLE=anthropic/claude-3.5-haiku

# Optional: shown on openrouter.ai/activity for usage tracking
OPENROUTER_SITE_URL=https://yourapp.com
OPENROUTER_SITE_NAME=FortyOne
```

## Model Configuration

FortyOne uses two model slots:

| Slot | Default | Used For |
|------|---------|----------|
| `LLM_MODEL_FAST` | `openai/gpt-4o-mini` | Structured extraction, acknowledgments, intent classification |
| `LLM_MODEL_CAPABLE` | `anthropic/claude-3.5-haiku` | Free-form responses, scheduling, general conversation |

You can swap any [OpenRouter-compatible model ID](https://openrouter.ai/models). Examples:

```bash
# Google models
LLM_MODEL_FAST=google/gemini-flash-1.5
LLM_MODEL_CAPABLE=google/gemini-pro-1.5

# Anthropic models
LLM_MODEL_FAST=anthropic/claude-3.5-haiku
LLM_MODEL_CAPABLE=anthropic/claude-3.5-sonnet

# Meta models (may require paid balance)
LLM_MODEL_FAST=meta-llama/llama-3.1-8b-instruct
LLM_MODEL_CAPABLE=meta-llama/llama-3.1-70b-instruct
```

> **Warning:** Free-tier models (those with `:free` suffix) may return 404 errors intermittently. Paid models are recommended for reliable operation.

## Environment Variable Reference

| Variable | Where | Description |
|----------|-------|-------------|
| `OPENROUTER_API_KEY` | `.env` | API key from openrouter.ai/keys (starts with `sk-or-`) |
| `LLM_MODEL_FAST` | `.env` | Model for fast tasks (default: `openai/gpt-4o-mini`) |
| `LLM_MODEL_CAPABLE` | `.env` | Model for complex tasks (default: `anthropic/claude-3.5-haiku`) |
| `OPENROUTER_SITE_URL` | `.env` | Optional: your app URL shown on OpenRouter activity page |
| `OPENROUTER_SITE_NAME` | `.env` | Optional: your app name shown on OpenRouter activity page |

## Mock Mode (Graceful Degradation)

If `OPENROUTER_API_KEY` is missing or too short (under 20 characters), FortyOne operates in **mock mode**:

- All LLM calls return static fallback responses
- The assistant still functions but gives generic replies
- No API calls are made to OpenRouter

This means you can run FortyOne locally without an API key for development and testing. LLM-dependent features (smart acknowledgments, intelligent task routing, free-form conversation) will use hardcoded fallbacks.

Additionally, every LLM call has a timeout with automatic fallback:
- Acknowledgments: 0.9s timeout
- Greetings: 3s timeout
- Task handlers: 10s timeout

If the LLM does not respond within the timeout, a static fallback is used automatically.

## Verification

1. Set `OPENROUTER_API_KEY` in your `.env`
2. Start the stack: `docker compose up`
3. Send a message to FortyOne (via SMS, Slack, or the dashboard)
4. Check the worker logs for LLM call entries:
   ```
   [INFO] app.tasks._llm: LLM model=openai/gpt-4o-mini latency_ms=1234 tokens=150
   ```

## Troubleshooting

- **LLM calls returning fallbacks:** Check that `OPENROUTER_API_KEY` is set and longer than 20 characters. Look for `[WARNING]` entries in the worker logs.
- **404 errors from a model:** The model ID may be incorrect or unavailable. Check [openrouter.ai/models](https://openrouter.ai/models) for valid IDs.
- **High latency:** Some models are slower than others. `gpt-4o-mini` and `claude-3.5-haiku` are optimized for speed.
- **Usage tracking:** Visit [openrouter.ai/activity](https://openrouter.ai/activity) to monitor API calls and costs.
