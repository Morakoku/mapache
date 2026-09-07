# Mapache CRM — API Reference

**Version:** 0.1.0 (beta)  
**Base URL:** `http://localhost:8000` (local) or your production URL  
**API Prefix:** `/api/v1` (except `/tracking/*` and `/auth/*` which are public)

---

## Authentication

Most endpoints require service-to-service authentication via HMAC-signed bearer tokens:

```
Authorization: Bearer <client_id>.<timestamp>.<nonce>.<signature>
```

The signature is `HMAC-SHA256(SERVICE_TOKEN_KEY, client_id.timestamp.nonce)` with a 300-second TTL.

Public endpoints (`/health`, `/tracking/*`, `/auth/*`) require no authentication.

---

## Conventions

| Convention | Detail |
|---|---|
| **Pagination** | `?page=1&size=50` → `{ items, total, page, size, pages }` |
| **Sorting** | `?sort=-score,last_activity_at` (prefix `-` for descending) |
| **Errors** | `{ "error": { "code": "LEAD_NOT_FOUND", "message": "...", "details": {} } }` |
| **Async operations** | Return `202 Accepted` + `{ job_id }`, never block |
| **PATCH** | Partial updates only; no `PUT` endpoints |

---

## Health & Public Endpoints

### Healthchecks (no auth)

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Redirects to `/docs` (Swagger) |
| `GET` | `/health` | Liveness check (no DB) |
| `GET` | `/health/ready` | Readiness check (verifies Postgres, returns 503 if down) |

### Tracking (no auth — called by email clients)

| Method | Path | Description |
|---|---|---|
| `GET` | `/tracking/open/{token}.gif` | 1×1 pixel; records email open (bot-filtered) |
| `GET` | `/tracking/click/{token}` | Records link click, 302-redirects to original URL |
| `GET` | `/tracking/unsubscribe/{token}` | Confirmation page (HTML) |
| `POST` | `/tracking/unsubscribe/{token}` | Processes unsubscribe (RFC 8058 One-Click compatible) |

### OAuth (no auth)

| Method | Path | Description |
|---|---|---|
| `GET` | `/auth/{provider}/connect` | Redirects to Google/Microsoft consent screen |
| `GET` | `/auth/{provider}/callback` | OAuth callback; exchanges code for tokens |
| `POST` | `/auth/{provider}/disconnect/{account_id}` | Revokes token at provider and deletes account |
| `GET` | `/auth/accounts` | Lists connected email accounts |

---

## Services (Módulo 1)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/services` | List services (`?only_active=true`) |
| `GET` | `/api/v1/services/signals` | Catalog of opportunity signals |
| `POST` | `/api/v1/services` | Create a service |
| `GET` | `/api/v1/services/{id}` | Get service details |
| `PATCH` | `/api/v1/services/{id}` | Update service |
| `DELETE` | `/api/v1/services/{id}` | Delete service |
| `GET` | `/api/v1/services/{id}/prospect-plan/cities` | Suggest cities where companies exist |
| `POST` | `/api/v1/services/{id}/prospect-plan` | Build a prospecting plan (proposal only) |
| `POST` | `/api/v1/services/{id}/prospect-plan/run` | Create searches from plan and launch them → `202 { job_ids }` |
| `GET` | `/api/v1/services/{id}/top-prospects` | Top-scored leads for this service |

---

## Searches (Módulo 2)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/searches` | List searches |
| `POST` | `/api/v1/searches` | Create a search configuration |
| `GET` | `/api/v1/searches/{id}` | Get search details |
| `PATCH` | `/api/v1/searches/{id}` | Update search |
| `DELETE` | `/api/v1/searches/{id}` | Delete search |
| `POST` | `/api/v1/searches/{id}/run` | Launch search → `202 { job_id }`. Query: `?provider=google_maps_scraper\|google_places_api\|instagram_serp\|linkedin_serp` |
| `GET` | `/api/v1/searches/{id}/runs` | Execution history |

---

## Companies (Módulos 3, 4, 5)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/companies` | List with filters: `?q=&city=&category=&has_email=&has_website=&has_phone=&has_social=&platform=&signal=&min_rating=&page=&size=` |
| `GET` | `/api/v1/companies/facets` | Counts per category/city/signal for filter UI |
| `POST` | `/api/v1/companies` | Manual company creation |
| `GET` | `/api/v1/companies/{id}` | Full detail (includes sources, socials, signals, leads) |
| `GET` | `/api/v1/companies/{id}/dossier` | Complete dossier (cached web findings). Query: `?refresh_web=true` |
| `DELETE` | `/api/v1/companies/{id}` | Cascade delete (suppression mechanism per Ley 1581/2012) |
| `GET` | `/api/v1/companies/{id}/duplicates` | Possible merge candidates |
| `POST` | `/api/v1/companies/{id}/enrich` | Enrich single company → `202 { job_id }` |
| `POST` | `/api/v1/companies/bulk-enrich` | `{ company_ids: [] }` → `202 { job_id }` |

### CSV Import

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/csv/import` | Upload CSV (multipart) with columns: name, company_name, description, category, city, address, phone, email, website |

### Contact Queue

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/contact-queue/` | Leads ready for human contact (`?min_score=0&limit=50`) |
| `POST` | `/api/v1/contact-queue/{lead_id}/attempt` | Record a contact attempt |

---

## Contacts (Módulo 6)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/contacts` | List (`?company_id=&q=&has_email=&contactable=`) |
| `POST` | `/api/v1/contacts` | Create contact |
| `GET` | `/api/v1/contacts/{id}` | Get contact |
| `PATCH` | `/api/v1/contacts/{id}` | Update contact |
| `DELETE` | `/api/v1/contacts/{id}` | Delete contact |
| `POST` | `/api/v1/contacts/{id}/primary` | Set as primary contact for company |
| `POST` | `/api/v1/contacts/{id}/verify-email` | Re-verify email (syntax + MX) |

---

## Leads / Prospectos (Módulo 7)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/leads` | List with filters: `?stage_id=&service_id=&status=&min_score=&engagement=&has_email=&q=&followup_before=&page=&size=&sort=score,-last_activity_at` |
| `GET` | `/api/v1/leads/segments/summary` | Counts for all 5 segments |
| `GET` | `/api/v1/leads/segments/{segment}` | Leads by segment: `opened_no_reply`, `clicked_no_reply`, `sent_no_open`, `replied`, `bounced`. Query: `?min_days=2&service_id=&page=&size=` |
| `POST` | `/api/v1/leads` | Create lead `{ company_id, service_id, contact_id? }` |
| `POST` | `/api/v1/leads/bulk` | Bulk create `{ company_ids[], service_id }` |
| `GET` | `/api/v1/leads/{id}` | Full lead detail |
| `PATCH` | `/api/v1/leads/{id}` | Update lead |
| `DELETE` | `/api/v1/leads/{id}` | Delete lead |
| `POST` | `/api/v1/leads/{id}/stage` | Move stage `{ stage_id, reason? }` (Kanban drag & drop) |
| `POST` | `/api/v1/leads/bulk-stage` | Bulk move `{ lead_ids[], stage_id }` |
| `POST` | `/api/v1/leads/{id}/score` | Rescore single lead (inline) |
| `POST` | `/api/v1/leads/score` | Rescore batch → `202 { job_id }` |
| `GET` | `/api/v1/leads/{id}/timeline` | Paginated activities |
| `GET` | `/api/v1/leads/{id}/history` | Stage history (funnel velocity) |
| `POST` | `/api/v1/leads/{id}/note` | Add note `{ text }` |
| `POST` | `/api/v1/leads/{id}/win` | Mark won `{ value?, note? }` |
| `POST` | `/api/v1/leads/{id}/lose` | Mark lost `{ reason }` |

---

## Pipeline / Kanban (Módulo 17)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/pipeline/stages` | List stages |
| `POST` | `/api/v1/pipeline/stages` | Create stage |
| `PATCH` | `/api/v1/pipeline/stages/{id}` | Update stage |
| `DELETE` | `/api/v1/pipeline/stages/{id}` | Delete stage (409 if has leads; requires `move_to_stage_id`) |
| `POST` | `/api/v1/pipeline/stages/reorder` | `{ ordered_ids: [] }` |
| `GET` | `/api/v1/pipeline/board` | Full board (`?service_id=&per_stage=50`) |

---

## Activities & Tasks (Módulo 19)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/activities` | List (`?lead_id=&company_id=&type=&page=&size=`) |
| `GET` | `/api/v1/tasks` | List (`?completed=&lead_id=&due_before=&page=&size=`) |
| `POST` | `/api/v1/tasks` | Create task |
| `PATCH` | `/api/v1/tasks/{id}` | Update task |
| `POST` | `/api/v1/tasks/{id}/complete` | Mark complete |
| `DELETE` | `/api/v1/tasks/{id}` | Delete task |

---

## Calls (Módulo — Llamadas)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/call-scripts` | List call scripts (`?script_type=&service_id=&include_inactive=`) |
| `POST` | `/api/v1/call-scripts` | Create script |
| `GET` | `/api/v1/call-scripts/{id}` | Get script |
| `PATCH` | `/api/v1/call-scripts/{id}` | Update script |
| `DELETE` | `/api/v1/call-scripts/{id}` | Delete script (409 if system script) |
| `GET` | `/api/v1/calls/brief/{lead_id}` | Get call brief (`?script_type=&script_id=`) |
| `POST` | `/api/v1/calls/{lead_id}` | Log call result |
| `GET` | `/api/v1/calls` | List call logs (`?lead_id=&outcome=&page=&size=`) |

---

## Templates (Módulo 10)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/templates` | List (`?category=&service_id=`) |
| `GET` | `/api/v1/templates/variables` | Available template variables catalog |
| `POST` | `/api/v1/templates` | Create template |
| `GET` | `/api/v1/templates/{id}` | Get template |
| `PATCH` | `/api/v1/templates/{id}` | Update template |
| `DELETE` | `/api/v1/templates/{id}` | Delete template |
| `POST` | `/api/v1/templates/{id}/preview` | Render with real lead data `{ lead_ids: [id] }` |

---

## Emails (Módulos 9, 11, 12)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/emails/preview` | Render drafts without sending `{ lead_ids[], template_id, account_id? }` |
| `POST` | `/api/v1/emails/personalize` | AI-generated draft `{ lead_id, template_id?, tone?, goal? }` |
| `POST` | `/api/v1/emails/send` | Send batch `{ account_id?, drafts[] }` → `202 { job_id }` |
| `GET` | `/api/v1/emails` | List (`?lead_id=&status=&direction=&page=&size=`) |
| `GET` | `/api/v1/emails/{id}` | Email detail (includes events, links) |
| `GET` | `/api/v1/emails/{id}/events` | Tracking timeline |
| `POST` | `/api/v1/emails/{id}/cancel` | Cancel if `status=QUEUED` |
| `POST` | `/api/v1/emails/{id}/resend` | Resend failed/cancelled → `202 { job_id }` |

---

## Conversations (Módulo 16)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/conversations` | List (`?filter=all\|unanswered\|answered\|interested\|pending&channel=&q=&page=&size=`) |
| `GET` | `/api/v1/conversations/{id}` | Full thread with messages + AI intent |
| `PATCH` | `/api/v1/conversations/{id}/intent` | Correct intent `{ reply_intent, apply_suggested_stage? }` |
| `POST` | `/api/v1/conversations/{id}/reply` | Reply via channel adapter `{ subject?, body_text }` |
| `POST` | `/api/v1/conversations/{id}/read` | Mark as read |
| `POST` | `/api/v1/conversations/{id}/close` | Close conversation |

---

## Sequences (Módulo 18)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/sequences` | List (`?active_only=`) |
| `POST` | `/api/v1/sequences` | Create with nested steps |
| `GET` | `/api/v1/sequences/{id}` | Get sequence |
| `PATCH` | `/api/v1/sequences/{id}` | Update sequence |
| `DELETE` | `/api/v1/sequences/{id}` | Delete + cancel pending follow-ups |
| `POST` | `/api/v1/sequences/{id}/preview-schedule` | Preview calendar `{ lead_ids[], start_at? }` |
| `POST` | `/api/v1/sequences/{id}/enroll` | Enroll leads `{ lead_ids[], start_at? }` |

---

## Follow-ups (Módulo 18)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/follow-ups` | List (`?status=&lead_id=&due_before=&due_after=&page=&size=`) |
| `POST` | `/api/v1/follow-ups` | Create manual follow-up `{ lead_id, scheduled_at, template_id?, note? }` |
| `PATCH` | `/api/v1/follow-ups/{id}` | Update follow-up |
| `POST` | `/api/v1/follow-ups/{id}/skip` | Skip this send, keep sequence alive |
| `DELETE` | `/api/v1/follow-ups/{id}` | Cancel follow-up |
| `POST` | `/api/v1/follow-ups/run` | Force run pending follow-ups now → `202 { job_id }` |

---

## Suppression List (Módulo 14)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/suppression` | List (`?q=&page=&size=`) |
| `POST` | `/api/v1/suppression` | Add entry `{ email?, domain?, reason }` |
| `DELETE` | `/api/v1/suppression/{id}` | Remove entry |
| `POST` | `/api/v1/suppression/import` | CSV upload (columns: `email`, `domain`, `reason`, `notes`) |

---

## Metrics (Módulo 20)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/metrics/overview` | Dashboard counters (`?from=&to=&service_id=`) |
| `GET` | `/api/v1/metrics/funnel` | Funnel with step-by-step conversion + bottleneck |
| `GET` | `/api/v1/metrics/email` | Email rates (open/click/reply/bounce/unsubscribe) with numerators/denominators |
| `GET` | `/api/v1/metrics/by-template` | Template comparison (implicit A/B) |
| `GET` | `/api/v1/metrics/by-service` | Performance per service |
| `GET` | `/api/v1/metrics/by-city` | Performance per city (`?limit=20`) |
| `GET` | `/api/v1/metrics/velocity` | Mean/median days per stage |
| `GET` | `/api/v1/metrics/timeseries` | Time series (`?metric=sent\|delivered\|opened\|clicked\|replied\|bounced&granularity=day\|week\|month&from=&to=`) |
| `GET` | `/api/v1/metrics/leads-timeseries` | Lead creation/stage time series |
| `GET` | `/api/v1/metrics/attention` | What needs action today |

---

## Jobs

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/jobs` | List (`?status=&type=&page=&size=`) |
| `GET` | `/api/v1/jobs/{id}` | Job status + progress |
| `POST` | `/api/v1/jobs/{id}/cancel` | Cancel job |
| `POST` | `/api/v1/jobs/provider-health` | Run discovery provider canary → `202 { job_id }` |

---

## Settings & Email Accounts

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/settings` | Get app settings |
| `PATCH` | `/api/v1/settings` | Update settings |
| `GET` | `/api/v1/settings/ai` | AI status (configured, enabled, cost estimate) |
| `PUT` | `/api/v1/settings/ai` | Set AI provider + key (encrypted at rest) |
| `DELETE` | `/api/v1/settings/ai` | Remove AI key |
| `PUT` | `/api/v1/settings/serp` | Set SERP API credentials |
| `DELETE` | `/api/v1/settings/serp` | Remove SERP credentials |
| `GET` | `/api/v1/settings/oauth-providers` | Which OAuth buttons to show |
| `GET` | `/api/v1/settings/email-accounts` | List connected accounts |
| `POST` | `/api/v1/settings/email-accounts/smtp` | Add SMTP/IMAP account |
| `PATCH` | `/api/v1/settings/email-accounts/{id}` | Update account |
| `DELETE` | `/api/v1/settings/email-accounts/{id}` | Disconnect + revoke |
| `POST` | `/api/v1/settings/email-accounts/{id}/verify` | Test credentials |
| `POST` | `/api/v1/settings/email-accounts/{id}/resync` | Force inbox sync → `202 { job_id }` |
| `POST` | `/api/v1/settings/email-accounts/{id}/test` | Send test email to self |

---

## Hermes Contract (LOOP-16)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/hermes/dispatch` | Enqueue job (whitelist: DISCOVERY, ENRICHMENT, FOLLOWUP_TICK). Requires `Idempotency-Key` header. |
| `GET` | `/api/v1/hermes/jobs/{id}` | Job status (only jobs owned by calling identity) |

---

## Guaki Bridge (LOOP-23)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/guaki/prospects` | Prospects with classification + link (`?limit=200`) |
| `GET` | `/api/v1/guaki/funnel` | Guaki commercial funnel |
| `POST` | `/api/v1/guaki/prospects/{id}/link` | Register/update prospect↔business link |
| `POST` | `/api/v1/guaki/link/match` | Find Mapache prospect for a Guaki business |

---

## Job Types

| Type | Description |
|---|---|
| `DISCOVERY` | Run a search (scraping / Places API / SERP) |
| `ENRICHMENT` | Crawl websites, extract emails/socials/signals |
| `SCORING` | Recompute lead scores |
| `SEND_BATCH` | Send email batch |
| `INBOX_SYNC` | Sync inbound emails |
| `FOLLOWUP_TICK` | Execute due follow-ups |
| `SCRAPER_HEALTH` | Provider canary check |

---

## Key Enums

| Enum | Values |
|---|---|
| `stage_type` | NEW, QUALIFIED, CONTACT_FOUND, CONTACTED, OPENED, REPLIED, CONVERSATION, INTERESTED, MEETING, OPPORTUNITY, PROPOSAL, NEGOTIATION, WON, LOST |
| `lead_status` | OPEN, WON, LOST, DISQUALIFIED, PAUSED |
| `source_type` | GOOGLE_MAPS, GOOGLE_PLACES_API, WEBSITE, LINKEDIN, INSTAGRAM, FACEBOOK, MANUAL, AI, IMPORT |
| `email_status` | DRAFT, QUEUED, SENDING, SENT, DELIVERED, BOUNCED, FAILED, CANCELLED |
| `channel` | EMAIL, WHATSAPP, LINKEDIN, PHONE, MANUAL |
| `mail_provider` | GMAIL, MICROSOFT, SMTP |
| `reply_intent` | POSITIVE, NEUTRAL, NEGATIVE, QUESTION, PRICING, MEETING_REQUEST, OUT_OF_OFFICE, UNSUBSCRIBE, WRONG_PERSON, UNKNOWN |
| `job_status` | QUEUED, RUNNING, COMPLETED, FAILED, CANCELLED |

---

## Error Codes (selection)

| Code | Meaning |
|---|---|
| `SCHEMA_VALIDATION_ERROR` | Invalid request body (422) |
| `LEAD_NOT_FOUND` | Lead ID does not exist |
| `COMPANY_NOT_FOUND` | Company ID does not exist |
| `EMAIL_NOT_CANCELLABLE` | Email already sent |
| `JOB_TYPE_BLOCKED` | SEND_BATCH blocked for Hermes |
| `IDEMPOTENCY_KEY_REQUIRED` | Missing Idempotency-Key header |
| `AI_UNAVAILABLE_NO_TEMPLATE` | AI off and no template provided |
| `CSV_TOO_LARGE` | Suppression CSV > 2 MB |
| `DESTRUCTIVE_CONFIRM_REQUIRED` | DELETE requires `?confirm=true` |
