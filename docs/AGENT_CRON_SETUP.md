# Mapache CRM — Agent & Cron Setup Guide

**Configuring service agents (Hermes), background workers, and cron-like scheduling.**

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                        Mapache CRM (FastAPI)                      │
│                                                                    │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌──────────┐ │
│  │  Scheduler  │  │ Job Queue   │  │  Workers    │  │  Hermes  │ │
│  │  (in-proc)  │  │ (in-proc/   │  │ (discovery, │  │ Contract │ │
│  │             │──▶│  ARQ+Redis) │──▶│  enrichment,│  │ (LOOP-16)│ │
│  │  FOLLOWUP_  │  │             │  │  scoring,   │  │          │ │
│  │  TICK every │  │  JobType    │  │  send, etc) │  │  L1: HMAC│ │
│  │  15 min     │  │  enum       │  │             │  │  L2:Scope│ │
│  │  INBOX_SYNC │  │             │  │             │  │          │ │
│  │  every 10min│  └─────────────┘  └─────────────┘  └──────────┘ │
│  └─────────────┘                                                    │
└──────────────────────────────────────────────────────────────────┘
```

---

## 1. Job System

### 1.1 Job Types

| JobType | Worker | Triggered By |
|---|---|---|
| `DISCOVERY` | `run_discovery` | `POST /searches/{id}/run`, `POST /services/{id}/prospect-plan/run` |
| `ENRICHMENT` | `run_enrichment` | `POST /companies/{id}/enrich`, `POST /companies/bulk-enrich` |
| `SCORING` | `run_scoring` | `POST /leads/score`, automatic after enrichment |
| `SEND_BATCH` | `run_send_batch` | `POST /emails/send` |
| `INBOX_SYNC` | `run_inbox_sync` | `POST /settings/email-accounts/{id}/resync`, scheduler |
| `FOLLOWUP_TICK` | `run_followup_tick` | `POST /follow-ups/run`, scheduler every 15 min |
| `SCRAPER_HEALTH` | `run_provider_health` | `POST /jobs/provider-health`, daily cron |

### 1.2 Job Lifecycle

```
QUEUED → RUNNING → COMPLETED
                  → FAILED (up to max_attempts retries)
                  → CANCELLED
```

### 1.3 Job Queue Backends

| Backend | Variable | Use Case |
|---|---|---|
| `InProcessQueue` | `JOB_QUEUE_BACKEND=inprocess` | Local dev, single instance MVP |
| `ArqQueue` | `JOB_QUEUE_BACKEND=arq` | Production with Redis (multiple workers) |

---

## 2. Scheduler (Cron-like Behavior)

The built-in scheduler uses `asyncio.sleep` loops to enqueue periodic jobs. It's deliberately simple for single-instance deployment.

### 2.1 Configuration

| Variable | Default | Description |
|---|---|---|
| `SCHEDULER_ENABLED` | `true` | Master switch. **Disable on secondary instances** to avoid duplicate ticks |
| `FOLLOWUP_TICK_MINUTES` | `15` | How often to check for due follow-up emails |
| `INBOX_SYNC_MINUTES` | `10` | How often to poll IMAP inboxes |

### 2.2 What the Scheduler Does

On startup, the app creates two `PeriodicTask` entries:

```python
PeriodicTask(
    JobType.FOLLOWUP_TICK,
    every_seconds=900,  # 15 min
    payload={},
),
PeriodicTask(
    JobType.INBOX_SYNC,
    every_seconds=600,  # 10 min
    payload={},
    initial_delay=60,   # Staggered start
),
```

Each tick:
1. Creates a `Job` row in the database
2. Enqueues it via `JobQueue.enqueue()`
3. The corresponding worker picks it up

### 2.3 Limitation

> **No catch-up:** If the process is down at tick time, that tick is lost. Overdue follow-ups execute on next tick — they don't accumulate.

---

## 3. Workers

### 3.1 Discovery Worker

**Trigger:** `POST /searches/{id}/run`  
**Payload:**
```json
{
  "search_id": "uuid",
  "provider": "google_maps_scraper | google_places_api | instagram_serp | linkedin_serp",
  "scraper_concurrency": 2,
  "scraper_delay_min": 1200,
  "scraper_delay_max": 3500,
  "scraper_headless": true
}
```

**Behavior:**
1. Marks job as RUNNING
2. Fetches provider from registry
3. Streams results (incremental persistence every 5 companies)
4. Deduplicates against existing companies
5. On completion, chains `ENRICHMENT` → `SCORING` jobs if `search.auto_enrich` / `search.auto_score` are enabled
6. On failure: marks as FAILED, preserves partial results

### 3.2 Enrichment Worker

**Trigger:** `POST /companies/{id}/enrich`, `POST /companies/bulk-enrich`, or chained from Discovery  
**Payload:**
```json
{
  "company_ids": ["uuid1", "uuid2", ...]
}
```

**Behavior:**
1. For each company with a website:
   - Checks `robots.txt` (skips if disallowed)
   - Crawls homepage + `/contacto`, `/about`, `/equipo` (max 3 pages)
   - Extracts emails, phones, social links, JSON-LD schema.org data
   - Runs email verifier (syntax + MX + role detection)
   - Detects opportunity signals (no website, no responsive, no SSL, etc.)
2. Creates `company_sources`, `company_socials`, `company_signals` rows
3. Chains `SCORING` job

### 3.3 Scoring Worker

**Trigger:** `POST /leads/score`, or chained from Enrichment  
**Payload:**
```json
{
  "lead_ids": ["uuid1", ...]  // empty = all leads
}
```

**Score formula (6 dimensions):**
```
score = round(
    0.30 * fit +           // Industry match, city, size, rating
    0.25 * opportunity,    // Signals matching service opportunity_signals
    0.20 * contactability, // Email verified, phone, LinkedIn
    0.10 * data_quality,   // % of key fields present
    0.10 * intent,         // Normalized engagement_score
    0.05 * timing          // Recency decay
)
```

### 3.4 Send Worker

**Trigger:** `POST /emails/send`  
**Payload:**
```json
{
  "account_id": "uuid",
  "drafts": [
    {
      "lead_id": "uuid",
      "subject": "...",
      "body_text": "...",
      "body_html": "...",
      "template_id": "uuid | null",
      "was_edited": true
    }
  ]
}
```

**Guardrails applied per email (in order):**
1. Global automations paused?
2. Email/domain in `suppression_list`?
3. Contact `do_not_contact`?
4. Email verified as `INVALID`/`BOUNCED`?
5. Daily/hourly send limit reached?
6. Outside send window or weekend?
7. Duplicate (same template to same lead)?
8. Account not `ACTIVE`?

**Sending flow:**
1. Create conversation + message rows
2. Insert `email_message` (QUEUED)
3. Rewrite links → `email_links`
4. Inject tracking pixel
5. Inject footer with unsubscribe URL
6. Add `List-Unsubscribe` headers (RFC 8058)
7. Call provider (Gmail/Graph/SMTP)
8. Update counters, record events, advance stage

### 3.5 Inbox Worker

**Trigger:** `POST /settings/email-accounts/{id}/resync`, scheduler, or webhook  
**Payload:**
```json
{
  "account_id": "uuid"
}
```

**Sync mechanisms:**

| Provider | Mechanism | Latency |
|---|---|---|
| Gmail | `users.watch` → Pub/Sub push + `history.list` | Seconds |
| Microsoft | Graph `subscriptions` → webhook + `delta` query | Seconds |
| SMTP/IMAP | Poll `SEARCH UNSEEN` from saved UID | ≤ 5 min |

**Per message:**
1. Parse DSN/bounce if applicable
2. Thread resolution (provider_thread_id → In-Reply-To → subject+from)
3. Detect auto-responses (don't count as reply)
4. Classify intent (AI or rule-based)
5. Apply sequence rules (stop on reply, etc.)

### 3.6 Follow-up Worker

**Trigger:** `POST /follow-ups/run`, scheduler every 15 min  
**Payload:** `{}` (processes all due follow-ups)

**Stop rules (in order):**
1. Lead replied and `sequence.stop_on_reply`
2. `reply_intent = NEGATIVE`
3. `reply_intent` in (`MEETING_REQUEST`, `PRICING`)
4. Lead status != `OPEN`
5. `sequence_paused`
6. Email in suppression list
7. Contact email `BOUNCED`

**Send window:** Outside window → reschedule, don't skip.

---

## 4. Hermes Agent Contract (LOOP-16)

### 4.1 Purpose

Hermes is the **only external service** allowed to enqueue jobs via the API. All other consumers use the frontend directly.

### 4.2 Authentication (L1)

**Token format:**
```
Authorization: Bearer <client_id>.<timestamp>.<nonce>.<signature>
```

**Signature generation (Python example):**
```python
import hmac, hashlib, time, secrets

client_id = "hermes"
timestamp = str(int(time.time()))
nonce = secrets.token_hex(16)
message = f"{client_id}.{timestamp}.{nonce}".encode()
signature = hmac.new(SERVICE_TOKEN_KEY.encode(), message, hashlib.sha256).hexdigest()

token = f"{client_id}.{timestamp}.{nonce}.{signature}"
```

**Validation:**
- TTL: 300 seconds (`SERVICE_TOKEN_TTL_SECONDS`)
- Nonce tracked for anti-replay
- Client ID must be in `SERVICE_TRUSTED_CLIENT_IDS`

### 4.3 Authorization (L2 — Scopes)

Hermes is **hardcoded** to exactly two scopes:

| Scope | Permitted Endpoint |
|---|---|
| `hermes.dispatch` | `POST /api/v1/hermes/dispatch` |
| `hermes.jobs.read` | `GET /api/v1/hermes/jobs/{id}` |

**Any other scope is DENY.** This is enforced in `ServiceAuthMiddleware` and cannot be expanded via config.

### 4.4 Dispatch Endpoint

```bash
curl -X POST "https://api.mapache.app/api/v1/hermes/dispatch" \
  -H "Authorization: Bearer hermes.1717761600.abc123def456..." \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: 550e8400-e29b-41d4-a716-446655440000" \
  -d '{
    "job_type": "DISCOVERY",
    "payload": {
      "search_id": "uuid"
    }
  }'
```

**Allowed job types (whitelist):**
- `DISCOVERY`
- `ENRICHMENT`
- `FOLLOWUP_TICK`

**Blocked:** `SEND_BATCH` (founder's decision — no automated email sends).

**Response:**
```json
{
  "job_id": "uuid",
  "status": "QUEUED",
  "message": "Job encolado."
}
```

### 4.5 Job Status Endpoint

```bash
curl "https://api.mapache.app/api/v1/hermes/jobs/{job_id}" \
  -H "Authorization: Bearer hermes.1717761600.abc123def456..."
```

Only returns jobs owned by the `hermes` identity. Cross-identity access returns `403 JOB_NOT_OWNED`.

### 4.6 Idempotency

Required header: `Idempotency-Key` (8–128 chars).  
Reusing the same key → `409 Conflict` with the original `job_id`.

---

## 5. Production Cron Setup

For production deployments where the built-in scheduler isn't sufficient, use external cron:

### 5.1 Vercel Cron (cron.json)

Create `api/cron.py`:

```python
from fastapi import APIRouter, BackgroundTasks
from app.core.database import session_scope
from app.services.job_svc import JobService
from app.core.enums import JobType
from app.core.container import get_job_queue

router = APIRouter()

@router.get("/cron/followup-tick")
async def cron_followup_tick(background_tasks: BackgroundTasks):
    """Vercel cron triggers this every 15 minutes."""
    async with session_scope() as session:
        job = await JobService(session).create(JobType.FOLLOWUP_TICK, {})
        await session.commit()
        await get_job_queue().enqueue(JobType.FOLLOWUP_TICK, {}, job_id=job.id)
    return {"status": "ok", "job_id": str(job.id)}

@router.get("/cron/scraper-health")
async def cron_scraper_health(background_tasks: BackgroundTasks):
    """Daily canary check."""
    async with session_scope() as session:
        job = await JobService(session).create(JobType.SCRAPER_HEALTH, {"provider": "google_maps_scraper"})
        await session.commit()
        await get_job_queue().enqueue(JobType.SCRAPER_HEALTH, {}, job_id=job.id)
    return {"status": "ok", "job_id": str(job.id)}
```

### 5.2 Systemd Timer (Self-hosted)

```ini
# /etc/systemd/system/mapache-followup.timer
[Unit]
Description=Mapache follow-up tick

[Timer]
OnCalendar=*:0/15
Persistent=true

[Install]
WantedBy=timers.target
```

```ini
# /etc/systemd/system/mapache-followup.service
[Unit]
Description=Mapache follow-up tick

[Service]
Type=oneshot
ExecStart=/usr/bin/curl -s https://api.mapache.app/cron/followup-tick
User=www-data
```

### 5.3 Recommended Cron Schedule

| Task | Frequency | Endpoint |
|---|---|---|
| Follow-up tick | Every 15 min | `POST /api/v1/follow-ups/run` |
| Inbox sync (IMAP only) | Every 10 min | `POST /api/v1/settings/email-accounts/{id}/resync` |
| Scraper health check | Daily at 08:00 | `POST /api/v1/jobs/provider-health` |
| Warm-up counter reset | Daily at 00:00 | (internal — happens automatically) |

---

## 6. Monitoring

### 6.1 Job Dashboard

```
GET /api/v1/jobs?status=RUNNING    # Active jobs
GET /api/v1/jobs?status=FAILED     # Failed jobs
GET /api/v1/jobs/{id}              # Progress details
```

### 6.2 Structured Logging

All events use `structlog` with `snake_case` keys:

```json
{
  "event": "job_started",
  "job_id": "uuid",
  "job_type": "DISCOVERY",
  "timestamp": "2026-09-07T10:30:00Z"
}
```

### 6.3 Key Events to Monitor

| Event | Meaning |
|---|---|
| `job_started` | Worker picked up a job |
| `job_completed` | Success |
| `job_failed` | Error (check `error_message`) |
| `scheduler_tick_failed` | Scheduler couldn't enqueue |
| `hermes_dispatch` | External agent enqueued a job |
| `provider_blocked` | Scraper provider returned challenge |
| `unsubscribed` | Contact opted out |
| `email_bounced` | Hard/soft bounce detected |

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Jobs stuck in `QUEUED` | Scheduler disabled or worker crashed | Check `SCHEDULER_ENABLED`, restart app |
| Duplicate ticks | Multiple instances with scheduler on | `SCHEDULER_ENABLED=false` on secondary instances |
| Hermes dispatch rejected | Invalid HMAC or expired token | Regenerate token, check clock skew |
| `JOB_TYPE_BLOCKED` | Hermes trying to send batch | Use UI for email sends; Hermes cannot |
| Scraper returning 0 results | Google changed DOM | Check `/settings/scraper-health`, update selectors |
| Follow-ups not sending | Outside send window | Check `send_window_start`/`end` in settings |
| Inbox not syncing | Watch expired | `token_refresh_worker` auto-renews; check logs |

---

## 8. Scaling Beyond MVP

### 8.1 When to Migrate to ARQ + Redis

| Symptom | Threshold |
|---|---|
| Jobs lost on restart | Any |
| Need multiple workers | > 1 API instance |
| Concurrent searches | > 1 search at a time |
| Volume | > 100 emails/day |

### 8.2 Migration Steps

```bash
# 1. Install Redis (e.g., Upstash, Redis Cloud)
# 2. Update config
JOB_QUEUE_BACKEND=arq

# 3. Implement ArqQueue (Protocol already exists)
# 4. Workers remain unchanged
```

The `JobQueue` Protocol abstraction means swapping backends requires changing **one line** in `app/core/container.py`.
