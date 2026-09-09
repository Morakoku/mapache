# Napkin Runbook — Mapache CRM

## Curation Rules
- Re-prioritize on every read.
- Keep recurring, high-value notes only.
- Max 10 items per category.
- Each item includes date + "Do instead".

---

## Database & Connection (Highest Priority)

1. **[2026-09-07] Vercel serverless no conecta a Supabase PostgreSQL directo**
   Do instead: Usar Supabase PostgREST API (HTTP) con `supabase_url` + `supabase_service_role_key`. El cliente `supabase` Python funciona en serverless. No usar asyncpg/SQLAlchemy para producción en Vercel.

2. **[2026-09-07] asyncpg no acepta `sslmode` en DSN**
   Do instead: Quitar `sslmode=require` del DATABASE_URL. Si necesitas SSL, usa `ssl.create_default_context()` en `connect_args` de SQLAlchemy, o mejor usa PostgREST HTTP.

3. **[2026-09-07] Supabase service_role necesita permisos explícitos**
   Do instead: Después de migraciones, ejecutar: `GRANT USAGE ON SCHEMA public TO service_role; GRANT ALL ON ALL TABLES IN SCHEMA public TO service_role; GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO service_role;`

4. **[2026-09-08] Endpoints serverless necesitan firma `db: AsyncSession | None = Depends(get_db)`**
   Do instead: Cuando `db is None`, redirigir a `postgrest_client.py` (pg_select/pg_insert/pg_update/pg_delete) vía HTTP. Crear fallback automático en cada router que use DB directa.

---

## Vercel Deploy & Serverless

5. **[2026-09-07] Playwright no funciona en Vercel serverless**
   Do instead: Usar API-based scrapers (SerpAPI, ScraperAPI) en vez de playwright. Excluir playwright de requirements para Vercel.

6. **[2026-09-07] Bundle size excede límite Vercel (225MB)**
   Do instead: Crear requirements.vercel.txt sin dependencias pesadas (playwright, etc.). Usar `selectolax` en vez de `beautifulsoup`.

7. **[2026-09-07] Entry point debe estar en raíz del repo para Vercel**
   Do instead: Crear `api/index.py` en raíz que importe desde `backend/app/main.py`. Añadir `sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))`.

---

## Auth & Security

8. **[2026-09-07] ServiceAuthMiddleware bloquea /health si rewrite añade /api**
   Do instead: Incluir tanto `/health` como `/api/health` en `_PUBLIC_PREFIXES`. Incluir `/tracking` y `/api/tracking`.

9. **[2026-09-07] Variables de entorno sensibles en .env commitado**
   Do instead: Añadir `backend/.env` a `.gitignore`. Usar `vercel env add` para secrets. Nunca hacer commit de credenciales.

---

## Asyncpg & SQLAlchemy

10. **[2026-09-07] Error `[Errno 16] Device or resource busy` en Vercel**
    Do instead: No es problema de código, es limitación de red serverless. Usar PostgREST HTTP API como fallback automático en health check.

---

## Scraping & External APIs

11. **[2026-09-07] Scraping requiere API key en serverless**
    Do instead: Configurar SerpAPI o ScraperAPI. Almacenar API key en `app_settings` tabla, no en código. Usar `decrypt()` para leer credenciales cifradas.

12. **[2026-09-08] Scrapling integrado como scraper principal (reemplaza google-maps-scraper.exe)**
    Do instead: `ScraplingMapsScraper` en `backend/app/scrapers/scrapling_scraper.py` con `StealthyFetcher.fetch()` SÍNCRONO (no async_fetch), `extra_headers` (no headers), `Response.status` (no status_code), `css()` devuelve Selectors con `.first`. Tarjetas de Google Maps: `div.Nv2PK` + `a.hfpxzc` (aria-label=nombre), texto vía `get_all_text()`. Ficha detalle para teléfono/website: `enrich_details(url)`. Servidor local: `python -m uvicorn app.scrapers.scrapling_server:app --port 8081` (8080 lo ocupa Steam webhelper).

13. **[2026-09-08] Resend integrado como proveedor de email serverless**
    Do instead: Usar `resend_api_key` en config. El endpoint `/api/v1/email-simple/send` funciona sin DB. Dominio `veyrasoluciones.com` verificado. No requiere autorizar destinatarios (vs Mailgun sandbox).

14. **[2026-09-08] Scheduler local escribe en schema relacional, no tabla plana**
    Do instead: Los resultados van a `companies` (upsert con `dedupe_key` truncado a 40 chars), con tipos convertidos (`rating` float, `reviews_count` int). NO existe tabla `scrape_results` — el schema es `searches -> search_runs -> search_results -> companies`. PostgREST OpenAPI (`GET /rest/v1/`) lista el schema exacto: consultarlo antes de insertar.

15. **[2026-09-08] .env.local/.env.production de la raíz están sanitizados ("[SENSITIVE]")**
    Do instead: Credenciales Supabase reales: `backend/.env` (repuesto desde `$LOCALAPPDATA/Temp/supabase_keys_full.json` + ref del JWT). Nunca cargar `.env.local` sanitizado con pydantic-settings — rompe la validación (`environment='[SENSITIVE]'`).

---

## Email & Comunicación

14. **[2026-09-08] Plantilla HTML de Veyra Soluciones integrada**
    Do instead: Usar `backend/app/templates/emails/render.py` con funciones `render_welcome`, `render_followup`, `render_proposal`. Base template con header VEYRA + naranja `#ff6b35`.
