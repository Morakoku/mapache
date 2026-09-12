# Manual de operación — sistema Veyra

Guía única para arrancar, operar y entender todo. Si algo no está aquí, es que no se usa.

## Qué es cada cosa
| Pieza | Para qué | Dónde |
|---|---|---|
| **Homepage** | Panel de inicio: estado y acceso a todo | `home.DOMINIO` |
| **Appsmith** | UI de CRM/operación (leads, empresas, contactos) | `crm.DOMINIO` |
| **Metabase** | Tableros / control (métricas) | `metricas.DOMINIO` |
| **n8n** | Automatizaciones visuales (sin código) | `flujos.DOMINIO` |
| **Portainer** | Ver/operar contenedores | `admin.DOMINIO` |
| **Worker** | Procesa la cola `jobs` (captar→enviar→seguimiento) | contenedor `ops-worker` |
| **Scheduler** | Descubre empresas (scraping) | contenedor `ops-scheduler` |
| **Mapache API** | API + webhooks (intake, recepcionista) | Vercel `mapache-kappa.vercel.app` |
| **Sitio Veyra** | Web pública | `veyrasoluciones.com` (Vercel) |
| **Hermes** | Agentes + Kanban + cron | VPS (migrado por la sesión de Guaki) |
| **Base de datos** | Datos (schema `crm`) | Supabase (hoy) |

## Regla de oro (dónde va cada cosa)
- **Dato / operación** → Appsmith o Metabase.
- **Automatización** → worker/scheduler o un flujo n8n.
- **Contenido** → Content OS (`docs/CONTENT_OS.md`).
- **Nunca** una interfaz a mano (nada de HTML de dashboard escrito a medida).

## Arrancar / parar
```bash
cd /opt/veyra/ops/deploy
docker compose up -d          # levanta todo
docker compose ps             # estado
docker compose logs -f worker # ver el worker
docker compose down           # apaga
```
El panel `home.DOMINIO` muestra el estado de todos los contenedores.

## Verificar que el flujo funciona
1. **Scraping:** `docker compose logs --tail 20 scheduler` → debe decir `guardados`.
2. **Cola:** `docker compose logs --tail 20 worker` → `job_ok`.
3. **Envío:** n8n o warm-up envían correos (Resend).
4. **UI:** en Appsmith, la tabla de `crm.leads` crece; en Metabase, el tablero "Panorama CRM".

## Añadir una función (sin programar)
1. Entra a **n8n** (`flujos.DOMINIO`).
2. Crea un flujo: disparador (webhook/horario) → acción (HTTP a la API de Mapache, correo, etc.).
3. Guárdalo. **Exporta el JSON** y guárdalo en `ops/n8n/` para que quede versionado.

## Backups
- **Supabase:** backups del plan + `pg_dump` diario.
- **Volúmenes** (Appsmith/Metabase/n8n/Portainer): copia semanal del directorio de volúmenes.
- **n8n:** los flujos exportados (JSON) viven en `ops/n8n/` (versionados).

## Si algo falla
- Servicio caído → Portainer (`admin.DOMINIO`) → reinicia el contenedor.
- Contenedor en bucle → `docker compose logs <servicio>`.
- Certificado TLS → Caddy lo renueva solo; revisa que el subdominio apunte a la IP.
- Base de datos → revisa `DATABASE_URL` en `ops/deploy/.env`.

## Seguridad
- SSH solo con clave, usuario no-root.
- Security Group: **solo** 22/80/443.
- Secretos en `ops/deploy/.env` (nunca en Git).
- Postgres no se expone a Internet.
