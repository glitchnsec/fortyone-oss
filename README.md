# SMS Personal Assistant

A production-grade SMS-based personal assistant that remembers context, captures tasks, and suggests actions — like a real executive assistant in your pocket.

## Architecture

```
Twilio SMS
    │
    ▼
┌─────────────────────────────────┐
│   FastAPI  (always-on)          │  ← returns 200 to Twilio in < 50ms
│   • receives /sms/inbound       │
│   • sends ACK via Twilio REST   │  ← ACK < 500ms
│   • classifies intent           │
│   • pushes job → Redis queue    │
│   • listens on Redis pub/sub    │  ← sends final response when worker done
└─────────────────────────────────┘
         │                  ▲
    Redis Queue         Redis Pub/Sub
         │                  │
         ▼                  │
┌─────────────────────────────────┐
│   Worker  (async process)       │
│   • pulls job from queue        │
│   • retrieves memory/context    │
│   • calls OpenAI                │
│   • stores task/memory in DB    │
│   • publishes result            │
└─────────────────────────────────┘
         │
    SQLite / PostgreSQL
    (Users, Memories, Tasks, Messages)
```

### Message State Machine

```
RECEIVED → ACK → THINK → ACT → CONFIRM → LEARN
```

### Latency targets
| Stage | Target |
|-------|--------|
| ACK (acknowledgment SMS) | < 500ms |
| Full response | 1–3s |

---

## Quick Start (Local)

### Prerequisites
- Python 3.11+
- Docker (for Redis) — or install Redis locally

### 1. Install dependencies
```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment
```bash
cp .env.example .env
# Edit .env — at minimum set NVIDIA_API_KEY for real LLM responses.
# Get a free key at https://build.nvidia.com/ (click any model → "Get API Key").
# MOCK_SMS=true means SMS is just printed to logs (no Twilio needed locally).
```

### 3. Start Redis
```bash
docker run -d -p 6379:6379 redis:7-alpine
# or: docker compose up redis -d
```

### 4. Start the API
```bash
uvicorn app.main:app --reload --port 8000
```

### 5. Start the worker (separate terminal)
```bash
python scripts/run_worker.py
```

### 6. (Optional) Seed demo data
```bash
python scripts/seed_demo.py
```

### 7. Test it
```bash
# Simulate an inbound SMS
curl -X POST http://localhost:8000/sms/inbound \
  -d "From=%2B15551234567&Body=Remind+me+to+call+John+tomorrow+at+3pm"

# List all users and their memories
curl http://localhost:8000/debug/users

# Check health
curl http://localhost:8000/health
```

---

## Using with Twilio (Real SMS)

1. Get a Twilio number at https://twilio.com
2. Fill in `.env`: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`
3. Set `MOCK_SMS=false`
4. Expose your local API with ngrok:
   ```bash
   ngrok http 8000
   ```
5. Set your Twilio number's webhook to:
   `https://<ngrok-url>/sms/inbound` (HTTP POST)

---

## Example Flows

### Reminder
```
User:      "Remind me to call John tomorrow at 3pm"
Assistant: "On it — setting that up now."         ← ACK (< 500ms)
Assistant: "Got it! I'll remind you to call John tomorrow at 3:00 PM."
```

### Scheduling
```
User:      "When should I schedule a team sync this week?"
Assistant: "Let me check your preferences and find a good time."
Assistant: "You usually prefer mornings — how about Tuesday or Thursday at 9am?"
```

### Memory Recall
```
User:      "What reminders do I have?"
Assistant: "Let me pull that up for you..."
Assistant: "Here's what I have for you:\n1. Call John — due Thu Apr 3 at 3:00 PM"
```

### Preference Storage
```
User:      "I prefer morning meetings before 10am"
Assistant: "Got it — I'll remember that."
```

---

## Project Structure

```
app/
├── main.py              # FastAPI app + lifespan (startup/shutdown)
├── config.py            # Settings (pydantic-settings)
├── database.py          # SQLAlchemy engine + session
├── routes/
│   └── sms.py           # POST /sms/inbound webhook
├── core/
│   ├── intent.py        # Rule-based intent classifier
│   ├── ack.py           # ACK message generator
│   └── pipeline.py      # Message state machine + response listener
├── memory/
│   ├── models.py        # SQLAlchemy models (User, Memory, Task, Message)
│   └── store.py         # Memory CRUD operations
├── queue/
│   ├── client.py        # Redis queue producer
│   └── worker.py        # Worker loop (run as separate process)
├── tasks/
│   ├── router.py        # Routes jobs to correct handler
│   ├── reminder.py      # Reminder + preference handlers
│   ├── scheduling.py    # Scheduling suggestion handler
│   └── recall.py        # Memory recall + general handler
└── sms/
    └── client.py        # Twilio wrapper (with mock mode)

scripts/
├── run_worker.py        # Entry point for worker process
└── seed_demo.py         # Seed demo user + memories
```

---

## Production Notes

- Swap SQLite for PostgreSQL (`DATABASE_URL=postgresql://...`)
- Deploy API on Railway/Render/Fly.io (always-on)
- Deploy workers on AWS Lambda (triggered by SQS) or keep as always-on process
- Add Twilio signature validation in `routes/sms.py`
- Set `MOCK_SMS=false` and fill in Twilio credentials
- For on-prem / private NIM deployments set `NIM_BASE_URL` to your endpoint
- Swap `NIM_MODEL_FAST` / `NIM_MODEL_CAPABLE` for any NIM-hosted model
