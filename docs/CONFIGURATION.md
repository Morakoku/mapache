# Mapache CRM — Configuration Guide

**Complete reference for all environment variables.**

---

## Quick Start

```bash
cp backend/.env.example .env

# Generate required keys:
python -c "from cryptography.fernet import Fernet; print('ENCRYPTION_KEY=' + Fernet.generate_key().decode())"
python -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(48))"
```

Paste both keys into `.env` and you're ready for local development.

---

## Configuration Sections

### App

| Variable | Default | Required | Description |
|---|---|---|---|
| `APP_NAME` | `CRM Prospección` | No | Display name; used in Swagger docs |
| `ENVIRONMENT` | `local` | No | `local`, `test`, `staging`, or `production`. Triggers validation gates when `production` |
| `DEBUG` | `false` | No | Enables debug mode. **Must be `false` in production** |
| `API_V1_PREFIX` | `/api/v1` | No | Prefix for all API routes |
| `PUBLIC_BASE_URL` | `http://localhost:8000` | Yes | Public URL for tracking pixels, OAuth callbacks, webhooks. Must be `https://` in production |
| `CORS_ORIGINS` | `http://localhost:3000` | No | Comma-separated allowed origins for CORS |
| `FRONTEND_BASE_URL` | — | No | Where to redirect after OAuth callback. If empty, shows a self-contained HTML page |

### Database

| Variable | Default | Required | Description |
|---|---|---|---|
| `DATABASE_URL` | — | **Yes** | PostgreSQL connection string. Examples:<br>Local: `postgresql://crm:password@localhost:5435/crm`<br>Supabase pooler: `postgresql://postgres.[ref]:[password]@pooler.supabase.com:6543/postgres` |
| `DB_ECHO` | `false` | No | Log all SQL statements (debug only) |
| `DB_POOL_SIZE` | `10` | No | Connection pool size |
| `DB_MAX_OVERFLOW` | `20` | No | Max overflow connections |
| `DB_POOL_PRE_PING` | `true` | No | Verify connections before use (prevents stale pool connections) |

### Security

| Variable | Default | Required | Description |
|---|---|---|---|
| `ENCRYPTION_KEY` | — | **Yes** | Fernet key (32 bytes, URL-safe base64). **Losing this key = losing all encrypted credentials** |
| `SECRET_KEY` | — | **Yes** | HMAC signing key for sessions/tokens (48+ random bytes) |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `10080` (7 days) | No | JWT session lifetime |

### Service-to-Service Auth (L1)

| Variable | Default | Required | Description |
|---|---|---|---|
| `SERVICE_AUTH_ENABLED` | `false` | No | Enables HMAC bearer token verification. **Must be `true` in production** |
| `SERVICE_TOKEN_KEY` | `<placeholder>` | No | HMAC signing key for service identities. Replace with real secret in production |
| `SERVICE_TOKEN_TTL_SECONDS` | `300` | No | Token time-to-live (seconds) |
| `SERVICE_TRUSTED_CLIENT_IDS` | `hermes` | No | Comma-separated list of allowed service client IDs |

### Service Authorization (L2 — Scopes)

| Variable | Default | Required | Description |
|---|---|---|---|
| `HERMES_SCOPES` | `hermes.dispatch,hermes.jobs.read` | No | Scopes granted to the `hermes` identity. **Never expand** — only `dispatch` and `jobs.read` are permitted |
| `TENANT_ISOLATION_ENFORCED` | `false` | No | Enforces tenant isolation checks. **Must be `true` in production** |

### Localization (Colombia Defaults)

| Variable | Default | Description |
|---|---|---|
| `DEFAULT_COUNTRY_CODE` | `CO` | ISO country code |
| `DEFAULT_PHONE_REGION` | `CO` | Region for phone normalization (E.164) |
| `DEFAULT_CURRENCY` | `COP` | Default currency |
| `DEFAULT_LOCALE` | `es-CO` | Locale |
| `DEFAULT_TIMEZONE` | `America/Bogota` | Timezone for send windows, counters, warm-up |

### OAuth — Email Providers

| Variable | Description |
|---|---|
| `GOOGLE_CLIENT_ID` | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret |
| `MICROSOFT_CLIENT_ID` | Azure app registration client ID |
| `MICROSOFT_CLIENT_SECRET` | Azure app registration client secret |

> Without these, the UI only offers SMTP. To enable Gmail:
> 1. Google Cloud Console → APIs & Services → Credentials → OAuth 2.0 Client ID (Web)
> 2. Redirect URI: `http://localhost:8000/auth/google/callback`
> 3. Scopes: `gmail.send`, `gmail.readonly`, `openid`, `email`
>
> To enable Outlook:
> 1. Azure Portal → Microsoft Entra ID → App Registrations
> 2. Redirect URI: `http://localhost:8000/auth/microsoft/callback`
> 3. Permissions: `Mail.Send`, `Mail.Read`, `User.Read`, `offline_access`

### AI (Optional)

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Enables AI features. Without it, templates render normally and reply classification falls back to rule engine |
| `AI_MAX_OUTPUT_TOKENS` | `8000` | Max tokens per AI response |
| `AI_TIMEOUT_SECONDS` | `90` | AI request timeout |

### Scheduler

| Variable | Default | Description |
|---|---|---|
| `SCHEDULER_ENABLED` | `true` | Enables periodic background jobs (follow-up ticks, inbox sync). **Disable on secondary API instances** |
| `FOLLOWUP_TICK_MINUTES` | `15` | How often to check for due follow-ups |
| `INBOX_SYNC_MINUTES` | `10` | How often to poll IMAP inboxes (irrelevant if webhooks active) |

### Jobs

| Variable | Default | Description |
|---|---|---|
| `JOB_QUEUE_BACKEND` | `inprocess` | `inprocess` (asyncio, MVP) or `arq` (Redis, production) |
| `JOB_MAX_ATTEMPTS` | `3` | Max retries for failed jobs |
| `JOB_WORKER_CONCURRENCY` | `2` | Parallel worker tasks |

### Logging

| Variable | Default | Description |
|---|---|---|
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOG_JSON` | `false` | `true` for production (JSON to stdout), `false` for local (human-readable) |

### Security Headers (LOOP-13)

| Variable | Default | Description |
|---|---|---|
| `SECURITY_HEADERS_ENABLED` | `true` | Adds CSP, nosniff, X-Frame-Options, Referrer-Policy |
| `HSTS_ENABLED` | `false` | HSTS header. **Only enable with real TLS in front** |
| `AUDIT_ENABLED` | `true` | Logs all mutating operations to `audit_log` table |
| `IDEMPOTENCY_ENABLED` | `true` | Requires `Idempotency-Key` header on mutating endpoints |
| `RATE_LIMIT_ENABLED` | `true` | Rate limiting by category + IP |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Rate limit window |
| `DESTRUCTIVE_CONFIRM_REQUIRED` | `true` | DELETE and job cancellation require `?confirm=true` |

---

## Production Validation Gate

When `ENVIRONMENT=production`, the following are **enforced** at startup:

| Requirement | Variable |
|---|---|
| Debug off | `DEBUG=false` |
| HTTPS public URL | `PUBLIC_BASE_URL` starts with `https://` |
| Service auth enabled | `SERVICE_AUTH_ENABLED=true` |
| Real service token | `SERVICE_TOKEN_KEY` set and not `<placeholder>` |
| Tenant isolation | `TENANT_ISOLATION_ENFORCED=true` |

Attempting to start in production with incomplete config raises `ValueError` immediately.

---

## Database URL Formats

### Local (Docker)

```
postgresql://crm:password@localhost:5435/crm
```

### Supabase — Transaction Pooler (Recommended for Vercel)

```
postgresql://postgres.[project-ref]:[password]@pooler.supabase.com:6543/postgres
```

### Supabase — Session Pooler

```
postgresql://postgres.[project-ref]:[password]@pooler.supabase.com:5432/postgres
```

### Supabase — Direct (Migrations, Admin)

```
postgresql://postgres:[password]@db.[project-ref].supabase.com:5432/postgres
```

---

## Feature Flags Summary

| Feature | How to Enable | How to Disable |
|---|---|---|
| AI | Set `ANTHROPIC_API_KEY` | Remove key or set `AI_ENABLED=false` in AppSettings |
| Gmail OAuth | Set `GOOGLE_CLIENT_ID` + `GOOGLE_CLIENT_SECRET` | Remove env vars |
| Outlook OAuth | Set `MICROSOFT_CLIENT_ID` + `MICROSOFT_CLIENT_SECRET` | Remove env vars |
| Scheduler | `SCHEDULER_ENABLED=true` | `SCHEDULER_ENABLED=false` |
| Service Auth | `SERVICE_AUTH_ENABLED=true` + `SERVICE_TOKEN_KEY` | `SERVICE_AUTH_ENABLED=false` |
| Audit Logging | `AUDIT_ENABLED=true` | `AUDIT_ENABLED=false` |
| Rate Limiting | `RATE_LIMIT_ENABLED=true` | `RATE_LIMIT_ENABLED=false` |
| Idempotency | `IDEMPOTENCY_ENABLED=true` | `IDEMPOTENCY_ENABLED=false` |

---

## Environment Variable Priority

1. **Shell environment** (highest priority)
2. **`.env` file** (loaded by `pydantic-settings`)
3. **Defaults** in `config.py` (lowest priority)

Variables are read once and cached via `@lru_cache`. To override in tests:

```python
from app.core.config import get_settings
get_settings.cache_clear()
```
