# Dry-run de migración a `crm` — informe (2026-09-11)

**Objetivo:** validar si la migración de `public` → `crm` es viable, con evidencia
(conteos + checksums) antes de comprometer la decisión A.

**Método:** conexión directa (rol `ops_veyra`), por tabla: contar filas y comparar
un **checksum MD5** sobre las columnas compartidas, origen vs destino.

## Resultado por tabla
| Tabla | Resultado | public | crm | Causa |
|---|---|---|---|---|
| `pipeline_stages` | **PASS** | 7 | 7 | Checksum idéntico |
| `email_templates` | **PASS con mapeo** | 26 | 26 | Igual tras ampliar `category`/`name` (excedían `varchar(40)`) |
| `sequences` | **PASS con mapeo** | 1 | 1 | Diferencia solo NULL→default en NOT NULL (`stop_on_*`, `max_steps`) |
| `email_accounts` | **PASS con mapeo** | 1 | 1 | Diferencia solo NULL→default (`sent_today`, `smtp_use_tls`) |
| `companies` | **FAIL (copy vivo)** | 9591 | 9591 | Mismos conteos, **24 ids divergen en cada lado** |
| `contacts` | **FAIL (copy vivo)** | 3647 | 3646 | **1 fila** insertada en public después del copiado |

## Causas raíz (verificadas)
1. **`public` es un blanco móvil.** El scheduler (Docker) **escribe y deduplica en vivo**:
   crea empresas nuevas y **fusiona duplicados cambiando ids**. Por eso una copia de un
   solo momento diverge de inmediato (24 ids en cada lado). → **No se puede migrar con
   copia puntual: hace falta un corte controlado** (congelar writes → sync final → cambiar).
2. **NULL→default en columnas NOT NULL.** Igual conteo, checksum distinto, **sin pérdida de
   datos**: donde public tiene `NULL`, crm exige NOT NULL y se inyectó un default
   (`0`/`false`/`[]`). Es una **decisión de mapeo** (aceptar default o volver nullable).
3. **Longitudes del modelo.** `email_templates.category` (varchar 40) era menor que la data
   real → se amplió. (Corregir el modelo o conservar la longitud real.)

## Veredicto
**A es viable**, pero **no como copia única**. Requiere:
- **Corte controlado**: pausar el scheduler (y cualquier escritura a `public`), sync final, y reapuntar todo a `crm`.
- **Mapeo documentado** NULL→default en las columnas NOT NULL.
- **Reapuntar la automatización** (scheduler, warm-up, Recepcionista) a `crm` en el mismo corte.

## Próximo paso
Runbook de corte: 1) congelar scheduler/escrituras; 2) sync final (empresas/contactos + tablas con mapeo); 3) verificar checksums = PASS; 4) apuntar Appsmith/Metabase/worker/scripts a `crm`; 5) descongelar. `public` queda en solo-lectura (legacy) hasta retirarlo.
