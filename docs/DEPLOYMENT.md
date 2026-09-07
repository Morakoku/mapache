# Mapache CRM — Deployment Guide

**Target:** Vercel (serverless) + Supabase (PostgreSQL)  
**Last updated:** 2026-09-07

---

## Architecture Overview

```
┌─────────────┐     ┌─────────────┐     ┌─────────────────┐
│   Vercel    │────▶│  Supabase   │────▶│  Supabase Auth  │
│  (FastAPI)  │     │ (Postgres)  │     │  (future auth)  │
└─────────────┘     └─────────────┘     └─────────────────┘
       │
       ▼
┌─────────────┐     ┌─────────────┐
│   Mailhog   │     │   Redis     │
│   (dev)     │     │  (future)   │
└─────────────┘     └─────────────┘
```

---

## Prerequisites

| Tool | Version | Purpose |
|---|---|---|
| Python | 3.12+ (3.13 recommended) | Backend runtime |
| Node.js | 20+ | Frontend (Next.js 16) |
| Vercel CLI | latest | Deployment |
| Supabase account | — | PostgreSQL + Auth |
| Git | — | Version control |

---

## Step 1: Supabase Setup

### 1.1 Create a Supabase Project

1. Go to [supabase.com](https://supabase.com) → **New Project**
2. Set a strong database password (save it — you'll need it)
3. Choose a region close to your users (e.g., `us-east-1` or `sa-east-1`)
4. Wait for the project to provision (~2 minutes)

### 1.2 Get Connection Strings

Navigate to **Project Settings → Database → Connection Pooling**:

- **Transaction pooler** (recommended for Vercel serverless):
  - Host: `pooler.supabase.com`
  - Port: `6543`
  - Database: `postgres`
  - User: `postgres.[project-ref]`
  - Mode: Transaction

- **Session pooler** (alternative):
  - Port: `5432`

- **Direct connection** (for migrations):
  - Host: `db.[project-ref].supabase.com`
  - Port: `5432`

### 1.3 Enable Required Extensions

Run in **SQL Editor**:

```sql
-- Required for fuzzy matching and text search
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS citext;

-- For geospatial queries (optional, for radius search)
CREATE EXTENSION IF NOT EXISTS cube;
CREATE EXTENSION IF NOT EXISTS earthdistance;
```

### 1.4 Configure RLS (Row Level Security)

> **Note:** Mapache currently uses `owner_id` columns (nullable) for future multi-tenancy. For single-tenant MVP, RLS is optional but recommended for production.

```sql
-- Enable RLS on all tables
ALTER TABLE companies ENABLE ROW LEVEL SECURITY;
ALTER TABLE leads ENABLE ROW LEVEL SECURITY;
ALTER TABLE contacts ENABLE ROW LEVEL SECURITY;
-- ... repeat for all tables

-- Create a policy for single-tenant access
CREATE POLICY "Allow all for service_role"
  ON companies
  FOR ALL
  TO service_role
  USING (true);
```

---

## Step 2: Backend Deployment (Vercel)

### 2.1 Project Structure

Ensure your `vercel.json` is at the repo root:

```json
{
  "buildCommand": "pip install -r backend/requirements.vercel.txt",
  "outputDirectory": "backend",
  "devCommand": "cd backend && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000",
  "installCommand": "pip install -r backend/requirements.vercel.txt",
  "framework": "fastapi"
}
```

### 2.2 Create `requirements.vercel.txt`

In `backend/requirements.vercel.txt`:

```txt
# Core
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
pydantic>=2.0.0
pydantic-settings>=2.0.0

# Database
sqlalchemy[asyncio]>=2.0.0
asyncpg>=0.29.0
alembic>=1.13.0

# Security
cryptography>=42.0.0
python-jose[cryptography]>=3.3.0
passlib[argon2]>=1.7.4

# HTTP
httpx>=0.27.0

# Logging
structlog>=24.1.0

# Email
aiosmtplib>=3.0.0
google-api-python-client>=2.100.0
google-auth-oauthlib>=1.0.0
msal>=1.24.0

# Scraping
playwright>=1.40.0
selectolax>=0.3.0

# AI (optional)
anthropic>=0.30.0

# Utils
phonenumbers>=8.13.0
tldextract>=5.1.0
```

### 2.3 Deploy to Vercel

```bash
# Install Vercel CLI
npm i -g vercel

# Login
vercel login

# Deploy (first time — follow prompts)
vercel

# Set production environment
vercel --prod
```

### 2.4 Configure Environment Variables

In Vercel Dashboard → **Settings → Environment Variables**:

| Variable | Environment | Description |
|---|---|---|
| `ENVIRONMENT` | All | `production` |
| `DATABASE_URL` | All | Supabase connection string (see §3) |
| `ENCRYPTION_KEY` | All | Fernet key (generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`) |
| `SECRET_KEY` | All | Random secret (generate with `python -c "import secrets; print(secrets.token_urlsafe(48))"`) |
| `PUBLIC_BASE_URL` | All | `https://your-app.vercel.app` |
| `CORS_ORIGINS` | All | `https://your-frontend.vercel.app` |
| `SERVICE_AUTH_ENABLED` | Production | `true` |
| `SERVICE_TOKEN_KEY` | Production | HMAC signing key |
| `TENANT_ISOLATION_ENFORCED` | Production | `true` |
| `ANTHROPIC_API_KEY` | All | Optional — for AI features |
| `GOOGLE_CLIENT_ID` | All | Optional — for Gmail OAuth |
| `GOOGLE_CLIENT_SECRET` | All | Optional — for Gmail OAuth |
| `MICROSOFT_CLIENT_ID` | All | Optional — for Outlook OAuth |
| `MICROSOFT_CLIENT_SECRET` | All | Optional — for Outlook OAuth |

> **Important:** Never commit `.env` files. Use Vercel's environment variable UI or CLI:
> ```bash
> vercel env add ENCRYPTION_KEY production
> ```

### 2.5 Run Migrations

```bash
# Set local .env with Supabase direct connection first
cd backend

# Run migrations
alembic upgrade head

# Or use the Makefile target
make migrate
```

---

## Step 3: Frontend Deployment (Vercel)

### 3.1 Connect Frontend Repo

1. In Vercel Dashboard → **Add New Project**
2. Import your frontend Git repository
3. Set **Framework Preset** to `Next.js`
4. Set **Root Directory** to `frontend`

### 3.2 Configure Environment Variables

| Variable | Description |
|---|---|
| `NEXT_PUBLIC_API_URL` | Backend URL (e.g., `https://your-api.vercel.app`) |
| `NEXT_PUBLIC_API_V1_PREFIX` | `/api/v1` |

### 3.3 Deploy

Vercel auto-deploys on push to `main`. For manual deploy:

```bash
cd frontend
vercel --prod
```

---

## Step 4: Email Configuration

### 4.1 SPF, DKIM, DMARC (Required before sending)

Before sending any emails, configure DNS for your sending domain:

**SPF Record:**
```
Type: TXT
Name: @
Value: v=spf1 include:_spf.google.com ~all  (for Gmail)
```

**DKIM Record:**
- Google: Generate in Google Admin Console → Apps → Google Workspace → Gmail → Authenticate email
- Microsoft: Configure in Exchange Admin Center

**DMARC Record:**
```
Type: TXT
Name: _dmarc
Value: v=DMARC1; p=quarantine; rua=mailto:dmarc@yourdomain.com
```

### 4.2 Warm-up Schedule

Mapache enforces a warm-up curve (20 → 100 emails/day over 22 days). This is **not configurable** — it's a deliverability safeguard.

| Days | Daily Limit |
|---|---|
| 1–3 | 20 |
| 4–7 | 35 |
| 8–14 | 50 |
| 15–21 | 75 |
| 22+ | 100 |

---

## Step 5: Post-Deployment Verification

### 5.1 Health Checks

```bash
# Liveness
curl https://your-api.vercel.app/health

# Readiness (checks DB)
curl https://your-api.vercel.app/health/ready
```

### 5.2 Swagger Docs

Visit `https://your-api.vercel.app/docs` (disabled in production by default — set `docs_url` manually if needed).

### 5.3 Run a Test Search

```bash
curl -X POST "https://your-api.vercel.app/api/v1/searches" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Test Search",
    "business_type": "Restaurantes",
    "city": "Medellín",
    "target_count": 5,
    "source": "GOOGLE_PLACES_API"
  }'
```

---

## Step 6: Monitoring & Maintenance

### 6.1 Logs

- Vercel Dashboard → **Deployments** → Select deployment → **Logs**
- Structured JSON logging via `structlog` (set `LOG_JSON=true` in production)

### 6.2 Database Backups

Supabase provides daily automatic backups. For manual backups:

```bash
pg_dump "postgresql://user:pass@host:5432/crm" > backup_$(date +%Y%m%d).sql
```

### 6.3 Scraper Health

The system runs a daily canary check. Monitor via:

```
GET /api/v1/jobs/provider-health
```

---

## Troubleshooting

| Issue | Solution |
|---|---|
| `503 Service Unavailable` | Check Supabase connection; verify `DATABASE_URL` |
| `ENCRYPTION_KEY` errors | Key must be 32 bytes, URL-safe base64 |
| Migrations fail | Ensure `pg_trgm` and `citext` extensions are enabled |
| CORS errors | Verify `CORS_ORIGINS` includes your frontend URL |
| OAuth redirect fails | Ensure `PUBLIC_BASE_URL` uses `https://` |
| Jobs not processing | Check `SCHEDULER_ENABLED=true` and `JOB_QUEUE_BACKEND` |

---

## Cost Estimation (Monthly)

| Service | Tier | Cost |
|---|---|---|
| Vercel | Pro | $20 |
| Supabase | Pro | $25 |
| Anthropic API | ~100 emails/day | ~$27 |
| **Total** | | **~$72/month** |

---

## Security Checklist

- [ ] `ENCRYPTION_KEY` generated and stored securely
- [ ] `SECRET_KEY` generated and stored securely
- [ ] `SERVICE_AUTH_ENABLED=true` in production
- [ ] `SERVICE_TOKEN_KEY` generated and rotated
- [ ] `TENANT_ISOLATION_ENFORCED=true` in production
- [ ] `PUBLIC_BASE_URL` uses HTTPS
- [ ] CORS origins restricted to frontend domain
- [ ] SPF/DKIM/DMARC configured for sending domain
- [ ] Supabase RLS enabled (for multi-tenant)
- [ ] Database backups scheduled
- [ ] Logs monitored for suspicious activity
