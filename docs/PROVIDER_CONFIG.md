# Mapache CRM — Serverless Provider Configuration Guide

> **Target:** Vercel serverless deployment (no Playwright, no persistent workers)
> **Market:** Colombia (COP, +57, `America/Bogota`, es-CO)
> **Date:** 2026-09-07

---

## 1. Google Maps Scraping Provider

### Comparison Matrix

| Provider | Pricing (Lowest Plan) | Free Tier | Speed (avg) | Success Rate | Serverless-Friendly | Legal Risk | Best For |
|---|---|---|---|---|---|---|---|
| **SerpAPI** | $75/mo (5,000 searches) | 250 searches/mo | 0.23s ⚡ | 100% | ✅ REST API only | ⚠️ Google DMCA lawsuit (hearing May 2026) | Speed-critical, structured data |
| **Apify** | $49/mo (Starter) | $5 credits (~1,400 results) | 16.9s | 100% | ✅ REST API only | Lower (marketplace model) | Custom workflows, Actor chaining |
| **ScraperAPI** | $49/mo (4,000 searches) | 5,000 credits | 2-3s | 99.99% | ✅ REST API only | Lower | High-volume, simple integration |
| **Scrapingdog** | $40/mo | 1,000 credits | 1.83s | ~98% | ✅ REST API only | Lower | Budget Maps-only |
| **Scrap.io** | $35/mo | None | Fast | ~98% | ✅ REST API only | Lower | Geographic filters |

### Recommendation: **SerpAPI** (primary) + **ScraperAPI** (fallback)

**Why SerpAPI:**
- Fastest response time (0.23s) — critical for Vercel's 10s function timeout
- 100% success rate on Google Maps benchmarks
- Structured `local_results` JSON with place details, reviews, photos
- Simple REST API — no async polling, no Actor management
- 250 free searches/month for development

**Caveat:** Google filed a DMCA lawsuit in December 2025. SerpAPI moved to dismiss (Feb 2026). Hearing scheduled May 2026. If SerpAPI becomes unavailable, switch to ScraperAPI by changing one env var — both share the same `DiscoveryProvider` protocol in Mapache.

**Why not Apify for serverless:** Apify Actors run async (submit job → poll → fetch results). Median completion time is 16.9s. Vercel's Pro plan has a 60s timeout, but the Free/Starter plans cap at 10s. Apify works for background jobs (via webhooks), but adds complexity.

### SerpAPI Pricing Detail

| Plan | Monthly Cost | Searches | Cost/Search |
|---|---|---|---|
| Starter | $25 | 1,000 | $0.025 |
| Developer | $75 | 5,000 | $0.015 |
| Production | $150 | 15,000 | $0.010 |
| Big Data | $275 | 30,000 | $0.009 |

Pay-as-you-go: $0.00916/request on Developer plan.

---

## 2. Transactional Email Provider

### Comparison Matrix

| Provider | Free Tier | 10K emails | 50K emails | 100K emails | SMTP | API | Colombia Deliverability | Ease of Use |
|---|---|---|---|---|---|---|---|---|
| **AWS SES** | 3K/mo (1st yr) | $1.00 | $5.00 | $10.00 | ✅ | ✅ (SDK) | Good (us-east-1 / sa-east-1) | ⚠️ Complex |
| **Mailgun** | 100/day (~3K/mo) | $15 | $35 | $75 | ✅ | ✅ (REST) | High | ✅ Easy |
| **SendGrid** | 100/day (60-day trial) | $19.95 | $35 | $60 | ✅ | ✅ (REST) | High | ✅ Easy |
| **Brevo** | 300/day (~9K/mo) | $15 | $35 | $69 | ✅ | ✅ (REST) | Good | ✅ Easy |
| **Postmark** | 100 trial | $15 | ~$68 | ~$133 | ✅ | ✅ (REST) | Very High | ✅ Easy |

### Recommendation: **Mailgun** (primary) + **AWS SES** (cost-optimized fallback)

**Why Mailgun for Colombia:**
- Immediate approval (no sandbox warm-up required for new accounts)
- 100 emails/day free — enough for development and initial testing
- Built-in email validation API (reduces bounces)
- 99.99% uptime SLA
- SMTP + REST API — works with Mapache's `aiosmtplib` out of the box
- Sinch-owned (EU-based), good deliverability to Gmail/Outlook/Yahoo in Latin America
- No AWS account required — simpler for a solo developer

**Why AWS SES as fallback:**
- Unbeatable pricing: $0.10 per 1,000 emails (100× cheaper than Mailgun at scale)
- New pricing plans (Essentials/Pro/Enterprise) bundle deliverability tools
- Direct integration if Mapache ever moves to AWS
- Requires technical expertise to set up (SPF, DKIM, DMARC, bounce handling, reputation monitoring)

**Cost crossover:** At ~50K emails/month, Mailgun costs $35 vs SES $5. The savings justify SES at scale, but Mailgun's free tier and instant approval make it the better starting point.

### Mailgun Pricing Detail

| Plan | Monthly Cost | Emails | Cost/1K | Features |
|---|---|---|---|---|
| Free | $0 | 100/day | — | Basic API, no dedicated IP |
| Basic | $15 | 10,000 | $1.50 | Email validation, analytics |
| Foundation | $35 | 50,000 | $0.70 | Dedicated IP add-on ($59/mo) |
| Scale | $90 | 100,000 | $0.90 | Priority support |

---

## 3. AI Provider (Personalization & Classification)

### Recommendation: **Anthropic Claude API**

**Why Anthropic:**
- Already integrated in Mapache (`ANTHROPIC_API_KEY`)
- Haiku 4.5 ($1/$5 per MTok) is cost-effective for email classification
- Sonnet 4.6 ($3/$15) for balanced quality/speed on personalization drafts
- Prompt caching reduces cost when reusing context (email templates + prospect data)
- Batch API: 50% discount for non-urgent workloads (overnight personalization)
- 1M context window on all models — fits full prospect dossiers

### Claude API Pricing (per million tokens)

| Model | Input | Output | Cached Input | Best For |
|---|---|---|---|---|
| Haiku 4.5 | $1.00 | $5.00 | $0.10 | Email classification, intent detection, routing |
| Sonnet 4.6 | $3.00 | $15.00 | $0.30 | Email drafting, personalization, summarization |
| Opus 4.8 | $5.00 | $25.00 | $0.50 | Complex reasoning, high-stakes drafts |
| Fable 5.1 | $10.00 | $50.00 | $1.00 | Long-horizon agents, creative writing |

### Cost Estimation (Mapache use case)

| Use Case | Model | Avg Tokens/Call | Calls/Month | Monthly Cost |
|---|---|---|---|---|
| Email classification (real vs auto-reply vs bounce) | Haiku 4.5 | 500 in / 50 out | 1,000 | ~$0.53 |
| Email personalization draft | Sonnet 4.6 | 2,000 in / 500 out | 500 | ~$6.75 |
| Lead scoring explanation | Haiku 4.5 | 1,000 in / 200 out | 500 | ~$1.00 |
| **Total estimated** | | | | **~$8-10/month** |

With prompt caching (5-min cache): ~40-60% reduction on repeated template context.

---

## 4. Configuration Templates

### 4.1 Environment Variables (`.env.example`)

```bash
# ============================================================
# MAPACHE CRM — Serverless Configuration (Vercel)
# ============================================================

# ── Database ────────────────────────────────────────────────
DATABASE_URL=postgresql+asyncpg://user:password@host:5432/mapache

# ── Scraping Provider ───────────────────────────────────────
# Choose ONE: serpapi | scraperapi | apify
SCRAPING_PROVIDER=serpapi

# SerpAPI (https://serpapi.com/dashboard)
SERPAPI_KEY=your_serpapi_key_here

# ScraperAPI (https://www.scraperapi.com/dashboard)
SCRAPERAPI_KEY=your_scraperapi_key_here

# Apify (https://console.apify.com/account#/integrations)
APIFY_TOKEN=your_apify_token_here
APIFY_GOOGLE_MAPS_ACTOR=compass/crawler-google-places

# ── Email Provider ──────────────────────────────────────────
# Choose ONE: mailgun | sendgrid | aws_ses | smtp
EMAIL_PROVIDER=mailgun

# Mailgun (https://app.mailgun.com/app/account/security/api_keys)
MAILGUN_API_KEY=your_mailgun_api_key_here
MAILGUN_DOMAIN=mg.yourdomain.com
MAILGUN_REGION=us  # us or eu

# SendGrid (https://app.sendgrid.com/settings/api_keys)
SENDGRID_API_KEY=your_sendgrid_api_key_here

# AWS SES (https://console.aws.amazon.com/ses/)
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=your_aws_access_key
AWS_SECRET_ACCESS_KEY=your_aws_secret_key
SES_CONFIGURATION_SET=mapache-tracking

# Generic SMTP (fallback)
SMTP_HOST=smtp.mailgun.org
SMTP_PORT=587
SMTP_USERNAME=postmaster@mg.yourdomain.com
SMTP_PASSWORD=your_smtp_password
SMTP_USE_TLS=true

# ── AI Provider ─────────────────────────────────────────────
# Anthropic (https://console.anthropic.com/settings/keys)
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxx

# Optional: Override default model per task
AI_MODEL_CLASSIFICATION=claude-haiku-4-5-20251001
AI_MODEL_PERSONALIZATION=claude-sonnet-4-6-20260514

# ── Application Settings ────────────────────────────────────
ENCRYPTION_KEY=  # Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
SECRET_KEY=      # Generate: python -c "import secrets; print(secrets.token_urlsafe(48))"
DEFAULT_COUNTRY=COP
DEFAULT_TIMEZONE=America/Bogota
DEFAULT_LANGUAGE=es-CO
DEFAULT_PHONE_PREFIX=57
```

### 4.2 Vercel Environment Variables (CLI Setup)

```bash
# Install Vercel CLI if needed
npm i -g vercel

# Link project
vercel link

# Set all environment variables
vercel env add DATABASE_URL preview
vercel env add DATABASE_URL production

vercel env add SCRAPING_PROVIDER production
vercel env add SERPAPI_KEY production
vercel env add EMAIL_PROVIDER production
vercel env add MAILGUN_API_KEY production
vercel env add MAILGUN_DOMAIN production
vercel env add ANTHROPIC_API_KEY production
vercel env add ENCRYPTION_KEY production
vercel env add SECRET_KEY production

# Verify
vercel env ls
```

### 4.3 API Key Setup Instructions

#### SerpAPI Key Setup

1. Go to [serpapi.com/dashboard](https://serpapi.com/dashboard)
2. Sign up with email or GitHub
3. Copy your **Private API Key** from the dashboard
4. Paste into `SERPAPI_KEY` in Vercel env vars
5. Verify with: `curl "https://serpapi.com/search?engine=google_maps&q=restaurantes+en+Medellín&api_key=$SERPAPI_KEY"`

#### Mailgun Key Setup

1. Go to [app.mailgun.com](https://app.mailgun.com)
2. Sign up (use US region for best latency to Colombia)
3. Navigate to **Settings → API Keys → Private API Key**
4. Copy the key
5. Verify your domain (Settings → Domains → Add Domain):
   - Add DNS records (TXT for SPF, CNAME for DKIM, MX)
   - Click "Verify" after DNS propagates (~5 min with Cloudflare)
6. Paste API key into `MAILGUN_API_KEY` and domain into `MAILGUN_DOMAIN`

#### AWS SES Setup (if using as fallback)

1. Go to [AWS Console → SES](https://console.aws.amazon.com/ses/)
2. Create a **Configuration Set** (for tracking bounces/complaints)
3. Verify your domain (SES → Domains → Verify a New Domain)
4. Create **SMTP Credentials** (SES → SMTP Settings → Create SMTP Credentials)
5. Move out of sandbox (request production access — requires use case description)
6. Set env vars: `AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`

#### Anthropic API Key Setup

1. Go to [console.anthropic.com](https://console.anthropic.com/)
2. Create account (requires phone verification)
3. Navigate to **Settings → API Keys → Create Key**
4. Copy the key (starts with `sk-ant-`)
5. Paste into `ANTHROPIC_API_KEY` in Vercel env vars
6. Optional: Set spending limits in **Settings → Billing → Spending Limits**

---

## 5. Architecture for Vercel Serverless

### Why API-based scraping (no Playwright)?

| Constraint | Vercel Serverless | Playwright |
|---|---|---|
| Max execution time | 10s (Hobby) / 60s (Pro) | Needs 15-30s for Maps |
| Binary dependencies | Limited | Requires Chromium (~300MB) |
| Cold start | Fast | Very slow with browser |
| Concurrent executions | 1,000 | Limited by memory |
| File system | Read-only (except `/tmp`) | Needs temp for browser profile |

**Conclusion:** Playwright doesn't work on Vercel serverless. API-based providers (SerpAPI, ScraperAPI, Apify) are the only option.

### Worker Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ Vercel Serverless Functions                                  │
├─────────────────────────────────────────────────────────────┤
│  POST /searches/{id}/run                                    │
│    → 202 {job_id}                                           │
│    → enqueue to background job                              │
│                                                              │
│  Background Job (separate function, max 60s):               │
│    1. SerpAPI: search "restaurantes en Medellín"            │
│    2. Parse local_results JSON                              │
│    3. Enrich: crawler sitios web (1 req/s, respeta robots)  │
│    4. Verify: MX lookup for emails                          │
│    5. Score: 6-dimension algorithm                          │
│    6. AI: personalize first email (Sonnet 4.6)              │
│    7. Mailgun API: send with tracking pixel                 │
│    8. Store results in PostgreSQL                           │
│                                                              │
│  Webhooks:                                                   │
│    GET /jobs/{id}/status  → progress 0-100%                 │
│    POST /mailgun/webhook → bounce/click/open events         │
└─────────────────────────────────────────────────────────────┘
```

### Timeout Budget (60s Vercel Pro)

| Step | Estimated Time | Provider |
|---|---|---|
| SerpAPI search (100 results) | 1-2s | SerpAPI |
| Parse & deduplicate | 0.5s | Local |
| Web crawl (10 sites, 1 req/s) | 10s | httpx async |
| MX verification (50 emails) | 3s | aiodns |
| Score calculation | 0.5s | Local |
| AI personalization (50 emails) | 15-20s | Anthropic batch |
| Mailgun send (50 emails) | 5s | Mailgun API |
| **Total** | **~35-40s** | |

If crawling is needed, run it as a separate job with webhook callback to stay within timeout.

---

## 6. Cost Summary (Monthly Estimate)

### Startup (0-500 emails/month, development)

| Service | Cost | Notes |
|---|---|---|
| SerpAPI | $0 | 250 free searches/month |
| Mailgun | $0 | 100 emails/day free |
| Anthropic | ~$2-5 | Haiku for classification, Sonnet for drafts |
| Vercel | $0 | Hobby plan |
| PostgreSQL | $0 | Supabase free tier (500MB) |
| **Total** | **$2-5/month** | |

### Growth (5,000 emails/month, 1,000 prospect searches)

| Service | Cost | Notes |
|---|---|---|
| SerpAPI | $75 | Developer plan (5,000 searches) |
| Mailgun | $35 | Foundation plan (50,000 emails) |
| Anthropic | ~$10-15 | Mix of Haiku + Sonnet, with caching |
| Vercel | $20 | Pro plan (60s functions) |
| PostgreSQL | $0-15 | Supabase Pro or Railway |
| **Total** | **$140-160/month** | |

### Scale (50,000 emails/month, 10,000 searches)

| Service | Cost | Notes |
|---|---|---|
| SerpAPI | $150 | Production plan (15,000 searches) |
| Mailgun | $90 | Scale plan (100,000 emails) |
| Anthropic | ~$50-80 | Batch API discounts, prompt caching |
| Vercel | $20 | Pro plan |
| PostgreSQL | $15-30 | Railway / Supabase Pro |
| **Total** | **$325-370/month** | |

### Cost-Optimized Scale (swap Mailgun → AWS SES, SerpAPI → ScraperAPI)

| Service | Cost | Savings |
|---|---|---|
| ScraperAPI | $49 | -$101 |
| AWS SES | $5 | -$85 |
| Anthropic | ~$50-80 | — |
| Vercel | $20 | — |
| PostgreSQL | $15-30 | — |
| **Total** | **$139-184/month** | **~$190/month saved** |

---

## 7. Migration Path

### Phase 1: Development (now)
- SerpAPI free tier (250 searches/month)
- Mailgun free tier (100 emails/day)
- Anthropic pay-as-you-go (start with Haiku)
- Vercel Hobby + Supabase free

### Phase 2: First customers (100 emails/week)
- SerpAPI Developer ($75/mo) when free tier exhausted
- Mailgun Foundation ($35/mo) when volume exceeds free tier
- Vercel Pro ($20/mo) for 60s function timeout

### Phase 3: Growth (1,000 emails/week)
- Evaluate AWS SES if cost becomes significant
- Consider ScraperAPI if SerpAPI legal risk materializes
- Implement batch API for overnight AI personalization (50% discount)

### Phase 4: Scale (10,000+ emails/week)
- Migrate to AWS SES for email (10× cheaper at scale)
- Evaluate self-hosted SerpAPI alternative (e.g., custom ScraperAPI + proxy rotation)
- Consider dedicated IP on Mailgun/SES ($25-59/mo) for reputation isolation

---

## 8. Risk Mitigation

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| SerpAPI loses Google lawsuit | Medium (hearing May 2026) | High — primary scraper unavailable | ScraperAPI configured as fallback (`SCRAPING_PROVIDER=scraperapi`) |
| Mailgun account suspension | Low | High — email delivery stops | AWS SES ready as backup provider |
| Vercel function timeout | Medium (at scale) | Medium — jobs fail | Background jobs with webhook callbacks; consider Railway for long-running workers |
| Anthropic rate limits | Low | Medium — AI personalization queued | Batch API for non-urgent jobs; fallback to rule-based templates |
| Email deliverability issues | Medium | High — emails go to spam | SPF/DKIM/DMARC mandatory; warm-up curve; dedicated IP at scale |
| Google Maps DOM changes | Medium | Medium — scraper breaks | API-based providers handle this; Mapache's `DiscoveryProvider` protocol allows hot-swap |

---

## 9. Quick-Start Checklist

- [ ] Create SerpAPI account → copy API key
- [ ] Create Mailgun account → verify domain → copy API key
- [ ] Create Anthropic account → create API key → set spending limit
- [ ] Set all env vars in Vercel (`vercel env add ...`)
- [ ] Generate `ENCRYPTION_KEY` and `SECRET_KEY`
- [ ] Configure DNS: SPF, DKIM, DMARC for sending domain
- [ ] Test: `curl -X POST https://your-app.vercel.app/api/v1/searches/1/run`
- [ ] Monitor: SerpAPI dashboard, Mailgun logs, Anthropic usage page
- [ ] Set billing alerts: Vercel ($50), SerpAPI ($75), Anthropic ($20)

---

*Generated for Mapache CRM — Veyra Soluciones. Configure once, swap providers by changing one env var.*
