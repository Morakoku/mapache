# Operaciones (CRM + BI) — cómo se usa

Stack unificado en Docker sobre **una sola base (Supabase)**. Sustituye la idea
de "5 localhost distintos": CRM, BI y datos viven juntos.

## Qué está corriendo
| Servicio | URL / uso | Estado |
|---|---|---|
| **Appsmith** (CRM / operación) | http://localhost:8090 | requiere crear admin (1 vez) |
| **Metabase** (BI / control) | http://localhost:3001 | configurado + tablero listo |
| **worker** (`ops-worker`) | procesa la cola `jobs` | arriba |
| **redis** (`ops-redis`) | cola/caché | arriba |
| **scheduler** (scraper) | `crm-scheduler` (compose crm) | arriba |

## Encender / apagar
```powershell
cd C:\Users\edwin\Documents\Trinidad\mapache
docker compose -f ops/docker-compose.ops.yml up -d          # CRM + BI + worker + redis
docker compose -f ops/docker-compose.ops.yml stop           # apagar
docker compose -f ops/docker-compose.ops.yml logs -f worker # ver el worker
```
Con dominio (Caddy, una sola puerta): `--profile proxy` + `DOMAIN` en `ops/.env.ops`.

## Metabase — control (ya listo)
- Entra a http://localhost:3001 con `mbmbrochero510@gmail.com` y la clave de `ops/.env.ops`.
- Tablero **"Veyra — Panorama CRM"**: `/dashboard/2` (empresas, leads por etapa/estado, canales, ciudades).
- Para uno nuevo: **+ New → Question**, elige la base *"Veyra CRM (Supabase)"* y arma con el editor visual o SQL nativo.

## Appsmith — CRM (primeros pasos)
1. Abre http://localhost:8090 → **crea tu admin** (email + clave).
2. **Datasources (+)** → **PostgreSQL**, con los datos de `ops/.env.ops`:
   - Host `aws-1-us-west-2.pooler.supabase.com` · Port `6543` · DB `postgres`
   - User `ops_veyra.ppxlkpuxhjuyhaifcfwk` · Password = `SUPABASE_DB_URL` · **SSL Mode: require**
   - *Test* → *Save*.
3. **Nueva página "Leads"**: Query →
   ```sql
   select l.id, c.name as empresa, ps.name as etapa, l.status, l.score
   from leads l
   join companies c on c.id = l.company_id
   join pipeline_stages ps on ps.id = l.stage_id
   order by l.created_at desc limit 100;
   ```
   Arrastra una **Table** y enlázala al query.
4. Repite para **Empresas** (`companies`) y **Contactos** (`contacts`).

## Credenciales y secretos
- Todo lo sensible vive en **`ops/.env.ops`** (ignorado por git).
- La base se accede con un rol **dedicado `ops_veyra`** (mínimo privilegio, RLS con política propia); no se usa el usuario `postgres`.

## Estado del flujo (worker → captar leads → enviar correos)
- El **worker está arriba** y el loop funciona (reclama y ejecuta jobs).
- **Bloqueo conocido (F1):** la base de Supabase tiene columnas `varchar` donde los modelos de la app esperan **enums**, y hay drift de tipos. Mientras eso no se alinee, los jobs que tocan esas tablas fallan (p. ej. `follow_ups`).
- Mientras tanto, **captar leads** (scheduler → PostgREST) y **enviar correos** (warm-up → Resend) **ya funcionan** por sus vías actuales.
- Siguiente: alinear tipos modelo↔base (tarea F1 del Kanban) para unificar todo en el worker.

## Regla
- Dato/operación → **Appsmith/Metabase**.
- Automatización → **worker/Mapache**.
- Contenido → **Content OS** (Brenda).
- Nunca más una UI a mano.
