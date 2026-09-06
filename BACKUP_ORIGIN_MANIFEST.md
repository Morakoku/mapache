# BACKUP / ORIGIN MANIFEST — MapacheCRM

**Fecha de captura:** 2026-08-12  
**Candidato:** `MAP-AG` — candidato provisional de procedencia  
**Estado:** `PROVISIONAL / NOT_CANONICAL`

## Procedencia

- **Origen leído:** `D:\Proyectos IA\AI_STUDIO_RECOVERY\04_PROJECTS\MapacheCRM`
- **Destino de trabajo:** `D:\Proyectos IA\AI_STUDIO\MAPACHE`
- **Fuente:** candidato recuperado atribuido a repositorio propio/Antigravity.
- **Commit reportado por Recovery-04A:** `6e1ba29979e5cc9a0889a8bd1651f6412b31c85a`.
- **Git local en la copia evaluada:** `NOT_FOUND`; el commit reportado no es reproducible localmente todavía.
- **Propietario operativo/legal exacto:** `UNKNOWN`.
- **Relación con deployment:** `UNKNOWN`.

## Integridad de la copia

Hash lógico de árbol SHA-256, calculado sobre rutas, tamaño y SHA-256 de archivos de código/manifiestos, excluyendo artefactos generados, entornos y secretos locales:

```text
LOGICAL SOURCE: 1f2c182c7b751dc92222581e94c3a41ec0b18a826dfc8f86b097cc3cba398de4  (233 archivos)
TARGET:         1f2c182c7b751dc92222581e94c3a41ec0b18a826dfc8f86b097cc3cba398de4  (233 archivos)
MATCH:          PASS
```

Hashes de archivos de referencia:

| Archivo | SHA-256 |
|---|---|
| `backend/pyproject.toml` | `0a6b0a6494641934bed961421cee1d037d7992888074859c80641f2e3bbd37f2` |
| `backend/.env.example` | `0d459f770d941ec4442109fa2329ede1d80582d4f864ed54759bce46c5311858` |
| `docker-compose.yml` | `0ecac28a140ece509ce07c4d56e5c76fe72ceaef9092d16daa0f712062eac036` |
| `README.md` | `b7ba03b84f007131ee106f2ded8378bf35dc2ea1240ad5721892cbc40e8d80ae` |

## Exclusiones y cambios de evaluación

- No se copiaron `backend/.env`, `backend/.env.test` ni otros secretos locales.
- No se copiaron `.venv`, cachés, `crm_backend.egg-info` ni bytecode como parte de la copia de origen.
- Las herramientas locales pueden generar cachés de validación en el destino; no son datos de negocio ni parte del hash lógico.
- No se inició Docker/Postgres, no se ejecutaron migraciones y no se copiaron bases, leads ni dumps.
- No se modificó la bóveda; en esta fase solo se leyeron rutas y hashes.

## Límites

Este manifest conserva la evidencia de procedencia, pero el commit, propietario, auth efectiva, tenant isolation y deployment siguen sin demostrarse.
