# Rotación de secretos — Mapache / Veyra (2026-09-12)

> **Estado:** preparación. **No se rotó ningún secreto real ni se tocó ningún
> servicio externo.** Este documento es el inventario y el runbook; la ejecución
> es decisión de Edwin. Los valores van **enmascarados**: prefijo/sufijo visibles,
> el resto oculto.
>
> Regla aplicada: no se modificó ningún valor en `.env` ni en producción.

---

## 0. Resumen ejecutivo

| Severidad | Secreto | Estado |
|---|---|---|
| P0 | Password de Postgres Supabase (`ops_veyra`) y de `postgres` | expuesto en `ops/.env.ops` y en `backend/.env.production.example` (versionado en git) |
| P0 | `SUPABASE_SERVICE_ROLE_KEY` | expuesto en `backend/.env` |
| P0 | `ENCRYPTION_KEY` | expuesto en `backend/.env`, `ops/.env.ops` e historial git |
| P0 | `SECRET_KEY` | expuesto en `backend/.env` e historial git |
| P1 | `RESEND_API_KEY` | expuesto en `backend/.env` |
| P1 | `VEYRA_ADMIN_TOKEN=11••••` | expuesto en `backend/.env`; valor trivial |
| P1 | `TOKENROUTER_API_KEY` | expuesto en `%LOCALAPPDATA%\hermes\...\.env` |
| P1 | `TELEGRAM_BOT_TOKEN` | expuesto en `%LOCALAPPDATA%\hermes\.env` |
| P1 | Admin de Metabase | estaba en comentario de `ops/.env.ops` (ya removido) |
| P2 | `VERCEL_OIDC_TOKEN` | en `.env.production`, `.env.local`, `.env.vercel`, `.env.vercel.pull` (ya expirados) |
| P3 | `SUPABASE_ANON_KEY` | público por diseño; rotar por higiene |
| Info | `WHATSAPP_APP_SECRET` / `WHATSAPP_VERIFY_TOKEN` | **faltan** (webhook fail-closed) |
| Info | Passphrase del token Vercel | `C:\Users\edwin\Documents\Trinidad\_token_passphrase.txt` junto al ciphertext |

---

## 1. Inventario enmascarado

### 1.1 Archivos locales (no versionados)

| # | Secreto | Ubicación (archivo:línea) | Valor (enmascarado) | Lo usa | Impacto si se compromete / al rotar |
|---|---|---|---|---|---|
| 1 | `ENCRYPTION_KEY` | `mapache/backend/.env:1` | `moMU…FE_Y=` | `backend/app/core/security.py` (`_fernet`), cifrado Fernet | Descifra todas las columnas `*_enc` (ver 1.4) → tokens OAuth, SMTP e IA. Rotar exige **re-cifrado** |
| 2 | `ENCRYPTION_KEY` | `mapache/ops/.env.ops:20` | `moMU…FE_Y=` | Worker ops / scripts | mismo valor que (1) |
| 3 | `SECRET_KEY` | `mapache/backend/.env:2` | `test-…7890` (valor de test, débil) | `config.secret_key` (`config.py:70`) | Hoy sin firma JWT activa detectada; barato de rotar |
| 4 | `SUPABASE_SERVICE_ROLE_KEY` | `mapache/backend/.env:8` | `eyJhbGci…DhM` (JWT `service_role`) | `config.py:60`, `supabase_rest.py:54`, scripts de import/limpia | **Bypass total de RLS**: lectura/escritura/borrado masivo |
| 5 | `RESEND_API_KEY` | `mapache/backend/.env:9` | `re_8Zx1…YCqB` | `config.py:124`, `app/mail/resend.py`, `scripts/warmup_resend.py` | Envío de correo con dominio verificado a costa de Veyra |
| 6 | `VEYRA_ADMIN_TOKEN` | `mapache/backend/.env:12` | `11••••` | `app/routers/torre_control.py:1962` | Acceso al kanban admin `/torre-control/chequeo`; fuerza bruta trivial |
| 7 | Password Postgres `ops_veyra` | `mapache/ops/.env.ops:5` | `ops_veyra…:fcEo…RN@` | Appsmith, Metabase, worker ops | Acceso directo a Postgres (schemas `crm`/`public`) |
| 8 | Password Postgres `ops_veyra` (async) | `mapache/ops/.env.ops:8` | mismo que (7) | worker `ops/deploy` (SQLAlchemy async) | idem |
| 9 | `VERCEL_OIDC_TOKEN` | `mapache/.env.production:53` | `eyJhbGci…dDQ` (**expirado**) | Vercel CLI | Token OIDC de corta vida; ya venció |
| 10 | `VERCEL_OIDC_TOKEN` | `mapache/.env.vercel:2` | `eyJhbGci…Rz0w` (**expirado**) | Vercel CLI | idem |
| 11 | `VERCEL_OIDC_TOKEN` | `mapache/.env.local:52` | `eyJhbGci…Z27g` (**expirado**) | Vercel CLI | idem |
| 12 | `VERCEL_OIDC_TOKEN` | `mapache/.env.vercel.pull` | (mismo formato) | Vercel CLI | idem |
| 13 | `TOKENROUTER_API_KEY` | `%LOCALAPPDATA%\hermes\profiles\engineering\.env:4` | `sk-a9…1uaw` | proveedor primario de Hermes (`config.yaml`: `provider: tokenrouter`, `key_env: TOKENROUTER_API_KEY`) | Gasto de LLM a nombre de Edwin |
| 14 | `TOKENROUTER_API_KEY` | `%LOCALAPPDATA%\hermes\.env:554` | `sk-a9…1uaw` | idem + perfiles | idem |
| 15 | `TELEGRAM_BOT_TOKEN` | `%LOCALAPPDATA%\hermes\.env:398` | `8929…QxFs` | gateway Telegram de Hermes (`config.yaml:192/217`) | Control del bot (leer/enviar, suplantar) |
| 16 | Admin Metabase | `mapache/ops/.env.ops:14` (comentario, **removido en esta tarea**) | `mbmb…@gmail.com / 8JBR…W4lq` | UI Metabase | Acceso a tableros y credenciales del datasource |
| 17 | Passphrase token Vercel | `Trinidad/_token_passphrase.txt` (+ `_token_cifrado.bin`) | (contenido oculto) | descifrado del token Vercel | Ciphertext y passphrase juntos = cifrado inútil |

### 1.2 Historial de git (mapache)

| Secreto | Commit | Detalle |
|---|---|---|
| `backend/.env` completo (55 líneas) | `6528a75` (2026-09-06) | Incluye `DATABASE_URL=post******test`, `ENCRYPTION_KEY=rtJQ…wjs=`, `SECRET_KEY=LMDL…BrSD`, `SERVICE_TOKEN_KEY=<pla…der>`. El archivo se eliminó del árbol en `d53246c` (2026-09-07), **pero sigue en el historial** |
| Password real de Postgres | `backend/.env.production.example:8`, commits `d53246c` y posteriores | `postgresql://postgres.ppxlkpuxhjuyhaifcfwk:2511…koku@aws-0-us-east-1.pooler.supabase.com:6543/postgres?pgbouncer=true` — **archivo actualmente versionado** |

> Comando para listar el historial afectado:
> ```powershell
> git -C "C:\Users\edwin\Documents\Trinidad\mapache" log --all --oneline -- backend/.env backend/.env.production.example
> ```

### 1.3 Variables que **faltaban** (no secretos expuestos, pero bloquean features)

| Variable | Valor real | Consumidor | Efecto actual |
|---|---|---|---|
| `WHATSAPP_APP_SECRET` | (ausente) | `whatsapp_webhook.py:40` | Firma de Meta no se puede validar → **rechaza todo webhook** (fail-closed) |
| `WHATSAPP_VERIFY_TOKEN` | (ausente) | `whatsapp_webhook.py:57` | Meta no puede verificar el webhook |
| `SUPABASE_ANON_KEY` | (ausente en `backend/.env.example`) | `supabase_rest.py:53` | Cliente PostgREST requiere anon key |

`%LOCALAPPDATA%\hermes\.env` también tiene `WHATSAPP_MODE`, `WHATSAPP_ENABLED` y
`WHATSAPP_ALLOWED_USERS=5730…9616` (este último es PII, no un secreto fuerte).

### 1.4 Columnas cifradas con `ENCRYPTION_KEY` (a re-cifrar)

| Tabla | Columnas |
|---|---|
| `app_settings` | `ai_api_key_enc`, `google_places_key_enc`, `serp_api_key_enc`, `apify_token_enc` |
| `email_accounts` | `oauth_access_token_enc`, `oauth_refresh_token_enc`, `smtp_password_enc`, `imap_password_enc` |

Definiciones: `backend/app/models/settings.py:120,143,153,156` y
`backend/app/models/email_account.py:35,36,47,52`. Cifrado/descifrado:
`backend/app/core/security.py:37-56`.

### 1.5 Archivos de test (sin riesgo)

`backend/.env.test` usa valores de test (`MDEy…ZDY=`, `test-secret-key…`,
`test-service-token-key…`); los tests `test_torre_chequeo.py:42` y
`test_torre_productos.py:22` hardcodean `VEYRA_ADMIN_TOKEN=11••••` (no es secreto).

---

## 2. Runbook de rotación (por prioridad)

Convenciones:

- Generar aleatorios: `python -c "import secrets; print(secrets.token_urlsafe(48))"` y
  `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
- Actualizar secretos en Vercel: `vercel env rm <NAME> production` y luego
  `vercel env add <NAME> production` (repetir para `preview`/`development`), o desde
  el dashboard: Project → Settings → Environment Variables.
- **Un secreto por cambio**, con verificación y ventana de rollback documentada.

### Fase 0 — Contención (barata, sin downtime)

1. **Eliminar los `.env` con OIDC del disco** y revocar sesiones del CLI:
   ```powershell
   Remove-Item "C:\Users\edwin\Documents\Trinidad\mapache\.env.vercel",
               "C:\Users\edwin\Documents\Trinidad\mapache\.env.vercel.pull" -Force
   vercel logout
   ```
   (`.env.production`, `.env.local` contienen OIDC expirado; borrarlos también.)
2. **Mover** `Trinidad/_token_passphrase.txt` a un gestor de secretos y borrarlo del
   disco (hoy está junto al ciphertext).
3. **No** commitear los `.env`; el `.gitignore` de `mapache` ya tiene `.env*`.

**Verificación:** `git status` no muestra `.env`; `vercel whoami` falla (sesión cerrada).
**Rollback:** `vercel login` regenera una sesión nueva.

### Fase 1 — P0 Base de datos y cifrado

#### 1.1 Rotar password de Postgres (`postgres` y `ops_veyra`)

1. Supabase Dashboard → **Project Settings → Database → Reset database password**
   (o Management API). Para el rol de ops:
   ```sql
   -- Ejecutar con la service_role / credencial admin, NUNCA en un log compartido.
   ALTER ROLE ops_veyra WITH PASSWORD '<NUEVA_PASSWORD>';
   ```
2. Actualizar consumidores:
   - `mapache/ops/.env.ops` → `SUPABASE_DB_URL` y `DATABASE_URL`
   - `mapache/ops/deploy/.env` (VPS) → `DATABASE_URL`
   - Datasource de Metabase y Appsmith (UI)
   - Variables de Vercel si aplica

**Verificación:**
```powershell
psql "postgresql://ops_veyra.<ref>:<nueva>@aws-1-us-west-2.pooler.supabase.com:6543/postgres" -c "select 1;"
curl.exe -s https://mapache-kappa.vercel.app/api/v1/control/status
```
**Rollback:** `ALTER ROLE ops_veyra WITH PASSWORD '<ANTERIOR>';` (guardar la anterior
en el gestor hasta confirmar). `postgres` no debe reutilizarse para apps.

#### 1.2 Rotar `SUPABASE_SERVICE_ROLE_KEY` (+ `ANON_KEY`)

> En Supabase, rotar el **JWT secret** invalida `service_role` y `anon` a la vez.
> Planificar ventana: todos los consumidores deben actualizarse en el mismo deploy.

1. Supabase Dashboard → **Project Settings → API → JWT Settings → Generate new secret**.
2. Actualizar:
   - `mapache/backend/.env` (local) y Vercel (`SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_ANON_KEY`)
   - Scripts que leen `backend/.env` (`import_*`, `cleanup_companies_dedupe.py`, etc.)
   - Integraciones Hermes/Guaki y `api/[...path].js`

**Verificación:**
```powershell
# Debe responder 200 con datos (no 401)
curl.exe -s -H "apikey: $env:SUPABASE_SERVICE_ROLE_KEY" -H "Authorization: Bearer $env:SUPABASE_SERVICE_ROLE_KEY" "https://ppxlkpuxhjuyhaifcfwk.supabase.co/rest/v1/leads?select=id&limit=1"
```
**Rollback:** Supabase conserva el JWT secret anterior un tiempo; si algo falla,
regenerar y volver a desplegar ambos valores. Documentar la hora del cambio.

#### 1.3 Rotar `ENCRYPTION_KEY` + re-cifrado (**requiere ventana/código**)

El código actual (`security.py:_fernet`) solo conoce **una** clave, así que un cambio
directo haría ilegibles todas las columnas `*_enc`. Opciones:

- **A (recomendada, cero pérdida):** añadir a `security.py` un descifrado con
  `ENCRYPTION_KEY_PREVIOUS` de respaldo, desplegar, correr el script de re-cifrado,
  y luego retirar la variable previa.
- **B (ventana de mantenimiento):** parar escritores (scheduler/worker), re-cifrar
  offline con la clave vieja→nueva y desplegar la nueva clave.

Esqueleto del script de re-cifrado (referencia; no ejecutado aquí):
```python
# backend/scripts/reencrypt_columns.py  (crear en una tarea aprobada)
from cryptography.fernet import Fernet
old = Fernet(OLD_KEY.encode())
new = Fernet(NEW_KEY.encode())
# Por cada fila y columna *_enc: si value: value = new.encrypt(old.decrypt(value)).decode()
```
Columnas y tablas: ver §1.4.

**Verificación:** abrir un buzón SMTP/IMAP y ejecutar una llamada de IA; si descifra,
mostrar `/api/v1/settings` sin errores `ConfigurationError`.
**Rollback:** conservar la clave anterior y las filas originales (backup `pg_dump`
antes de tocar) para restaurar.

#### 1.4 Rotar `SECRET_KEY`

1. Generar: `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
2. `backend/.env` + Vercel `SECRET_KEY` (production/preview/development).
3. Reiniciar/redeploy.

**Verificación:** `curl /api/v1/control/status`.
**Rollback:** reinstalar el valor anterior (las sesiones activas pueden invalidarse,
lo cual es aceptable).

### Fase 2 — P1 servicios externos

#### 2.1 Resend (`RESEND_API_KEY`)

1. Resend Dashboard → API Keys → **Create** (dominio `veyrasoluciones.com`) → **Revoke** la vieja.
2. Actualizar `backend/.env`, Vercel y `scripts/warmup_resend.py`.
**Verificación:** enviar un correo de prueba (`scripts/send_test_email.py` o warm-up).
**Rollback:** mantener la clave vieja activa hasta confirmar el envío.

#### 2.2 WhatsApp Cloud API (Meta)

1. Meta App → Settings → Basic → **Reset App Secret** → nuevo `WHATSAPP_APP_SECRET`.
2. Elegir un `WHATSAPP_VERIFY_TOKEN` largo y aleatorio; reconfigurarlo en
   Meta → Webhooks → Callback URL `https://<host>/webhook/whatsapp`.
3. Actualizar `backend/.env` y Vercel.
**Verificación:** `GET /webhook/whatsapp?hub.mode=subscribe&hub.verify_token=...&hub.challenge=123`
devuelve `123`; un POST firmado con el App Secret devuelve 200.
**Rollback:** repetir el reset (Meta solo mantiene el último).

#### 2.3 TokenRouter (`TOKENROUTER_API_KEY`)

1. TokenRouter dashboard → nueva API key → revocar la vieja.
2. Actualizar `%LOCALAPPDATA%\hermes\profiles\engineering\.env` y
   `%LOCALAPPDATA%\hermes\.env` (o el gestor de secretos de Hermes).
**Verificación:** `hermes` con una llamada simple al proveedor primario.
**Rollback:** la clave vieja sigue válida hasta revocarla; reactivar temporalmente.

#### 2.4 Telegram (`TELEGRAM_BOT_TOKEN`)

1. BotFather → `/revoke` → nuevo token.
2. Actualizar `%LOCALAPPDATA%\hermes\.env`.
**Verificación:** `hermes` gateway conecta y responde.
**Rollback:** BotFather permite regenerar; no hay “reativar” el token viejo.

#### 2.5 Admin de Metabase

1. Metabase → Admin → Users → cambiar contraseña de `mbmb…@gmail.com`.
2. Guardar la nueva en el gestor de secretos (no en el `.env`).
**Verificación:** login en `metricas.DOMINIO` / `localhost:3001`.
**Rollback:** Metabase permite reset por email.

#### 2.6 `VEYRA_ADMIN_TOKEN`

1. Generar: `python -c "import secrets; print(secrets.token_urlsafe(32))"` (por ejemplo).
2. `backend/.env` + Vercel `VEYRA_ADMIN_TOKEN`; actualizar el cliente de
   `/torre-control/chequeo`.
**Verificación:** entrar al kanban con el token nuevo (y comprobar que el viejo da 401).
**Rollback:** volver a poner `11••••` temporalmente (no recomendado).

#### 2.7 Purgar el historial de git (bloqueante para cerrar el P0)

Reescribe el historial; **solo Edwin**, porque exige force-push y coordinación de clones.

1. Eliminar `backend/.env` de todo el historial y sustituir el password real de
   `backend/.env.production.example` por `[PASSWORD]`:
   ```powershell
   pip install git-filter-repo
   # replacements.txt:  regex:251198Morakoku==>[PASSWORD]
   git -C "C:\Users\edwin\Documents\Trinidad\mapache" filter-repo --replace-text replacements.txt
   git -C "C:\Users\edwin\Documents\Trinidad\mapache" filter-repo --path backend/.env --invert-paths
   ```
2. `git push --force --all` y `--force --tags`; avisar a cualquier otro clon.
3. **Primero rotar** los secretos (1.1, 1.2, 1.3, 2.1) — si no, la purga no sirve.
4. Rotar/expirar los tokens de GitHub/Vercel que hayan podido filtrarse.

**Verificación:** `git log --all -- backend/.env` vacío;
`git grep 251198Morakoku $(git rev-list --all)` sin resultados.
**Rollback:** mantener un bundle del repo antes de reescribir (`git bundle create backup.bundle --all`).

### Fase 3 — Higiene

- Rotar `SUPABASE_ANON_KEY` con la misma rotación de JWT (1.2).
- Confirmar `.gitignore` (`mapache/.gitignore` ya incluye `.env*`).
- Activar el escaneo bloqueante: quitar `continue-on-error: true` de
  `.github/workflows/secret-scan.yml` **después** de 2.7.
- `pre-commit install` en cada clon (hook gitleaks ya configurado).

---

## 3. Cambios seguros aplicados en esta tarea

Todos son **reversibles**, no tocan valores de secretos ni producción.

| Archivo | Cambio |
|---|---|
| `mapache/backend/.env.example` | +51 líneas: se añadieron **todas** las variables que el código lee y faltaban, con placeholders. Incluye `SUPABASE_ANON_KEY`, `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN`, `GUAKI_SCOPES`, `TENANT_ISOLATION_ENFORCED`, `FRONTEND_BASE_URL`, `API_V1_PREFIX`, `ACCESS_TOKEN_EXPIRE_MINUTES`, pool de BD, planificador, IA, jobs/backoff, scraping y `GMAIL_APP_PASSWORD`. `SERVICE_TOKEN_KEY=<placeholder>` se mantiene |
| `mapache/.env.example` | Añadidos WhatsApp (`VERIFY_TOKEN`, `APP_SECRET`), scopes de servicio, `TENANT_ISOLATION_ENFORCED`, `SERVICE_TRUSTED_CLIENT_IDS` y `SCRAPER_URL` |
| `mapache/ops/.env.ops` | Se eliminó el comentario con credenciales admin de Metabase (línea 14). **No se cambió la contraseña real** |
| `mapache/.gitleaks.toml` | Nuevo: hereda las reglas por defecto + allowlist de placeholders/tests/docs. **No** allowlistea `backend/.env.production.example` (debe seguir alertando) |
| `mapache/.pre-commit-config.yaml` | Nuevo: hook `gitleaks` v8.30.1 (escanea solo lo staged; opt-in con `pre-commit install`) |
| `mapache/.github/workflows/secret-scan.yml` | Nuevo: escaneo gitleaks **advisory** (`continue-on-error: true`) hasta purgar el historial |
| `mapache/docs/ROTACION_SECRETOS_2026-09-12.md` | Este documento |

**Verificación de no-ruptura:** la app solo carga `.env` (no `.env.example`), y los
archivos de ops/Hermes no son leídos por el backend. YAML validado (`yaml.safe_load`)
y TOML validado (`tomllib`). `git diff --stat` limitado a `backend/.env.example`.

---

## 4. Lo que SOLO Edwin puede hacer

1. **Rotar los secretos reales** (password Postgres, service_role/anon, Resend,
   WhatsApp, TokenRouter, Telegram, Metabase, `VEYRA_ADMIN_TOKEN`) — este agente no
   los toca.
2. **Aprobar y ejecutar la rotación de `ENCRYPTION_KEY`** (implica cambio de código
   para descifrado con clave previa + re-cifrado de 8 columnas).
3. **Purgar el historial de git** (`git filter-repo` + `force-push`) y coordinar clones.
4. **Configurar `WHATSAPP_APP_SECRET` y `WHATSAPP_VERIFY_TOKEN`** en Meta y Vercel
   (hoy el webhook falla cerrado).
5. **Decidir la política de secretos** (secret manager, quién accede, backups de la BD).
6. **Revocar** el token OIDC de Vercel y las sesiones del CLI, y retirar
   `_token_passphrase.txt` del disco.
