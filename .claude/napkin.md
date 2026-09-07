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

---

## Vercel Deploy & Serverless

4. **[2026-09-07] Playwright no funciona en Vercel serverless**
   Do instead: Usar API-based scrapers (SerpAPI, ScraperAPI) en vez de playwright. Excluir playwright de requirements para Vercel.

5. **[2026-09-07] Bundle size excede límite Vercel (225MB)**
   Do instead: Crear requirements.vercel.txt sin dependencias pesadas (playwright, etc.). Usar `selectolax` en vez de `beautifulsoup`.

6. **[2026-09-07] Entry point debe estar en raíz del repo para Vercel**
   Do instead: Crear `api/index.py` en raíz que importe desde `backend/app/main.py`. Añadir `sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))`.

---

## Auth & Security

7. **[2026-09-07] ServiceAuthMiddleware bloquea /health si rewrite añade /api**
   Do instead: Incluir tanto `/health` como `/api/health` en `_PUBLIC_PREFIXES`. Incluir `/tracking` y `/api/tracking`.

8. **[2026-09-07] Variables de entorno sensibles en .env commitado**
   Do instead: Añadir `backend/.env` a `.gitignore`. Usar `vercel env add` para secrets. Nunca hacer commit de credenciales.

---

## Asyncpg & SQLAlchemy

9. **[2026-09-07] Error `[Errno 16] Device or resource busy` en Vercel**
   Do instead: No es problema de código, es limitación de red serverless. Usar PostgREST HTTP API como fallback automático en health check.

---

## Scraping & External APIs

10. **[2026-09-07] Scraping requiere API key en serverless**
    Do instead: Configurar SerpAPI o ScraperAPI. Almacenar API key en `app_settings` tabla, no en código. Usar `decrypt()` para leer credenciales cifradas.
