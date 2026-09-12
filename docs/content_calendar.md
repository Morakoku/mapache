# Calendario de contenido — semana inicial

**Estado:** plantilla inicial,lista para usar con primeros briefs.  
**Zona horaria:** America/Bogota (UTC-5).  
**Formato:** semana por marca, frecuencia MVP (calidad > cantidad).

---

## Estado de cuentas por marca (por confirmar)

| Marca | Instagram | Facebook | LinkedIn | X (Twitter) | Email nurture |
|---|---|---|---|---|---|
| Mapache | ? | ? | ? | ? | ✅ existe en CRM |
| Veyra | ? | ? | ? | ? | ✅ existe en CRM |
| Guaki | ? | ? | ? | ? | — |
| CVELIZ | ? | ? | — | — | — |

`?` = cuenta no confirmada. Ver sección 8 de CONTENT_OS.md para gaps.

---

## Hoja de calendario — estructura

Columnas mínimas:
- `id` — identificador único del post
- `marca` — Mapache | Veyra | Guaki | CVELIZ
- `red` — Instagram | Facebook | LinkedIn | X | email
- `fecha_programada` — YYYY-MM-DD HH:MM (America/Bogota)
- `estado` — briefing | copy | diseño | programado | publicado | pending_manual | skip
- `brief` — referencia al brief (archivo/nota)
- `copy_resumen` — versión corta del copy (o "pendiente")
- `asset` — descripción del diseño o "solo copy"
- `publicacion_real` — fecha real + post_id (si aplica)
- `notas`

---

## Semana 1 — ejemplos (por editar/reemplazar)

### Mapache

| id | marca | red | fecha_programada | estado | copy_resumen | asset | notas |
|---|---|---|---|---|---|---|---|
| M-001 | Mapache | LinkedIn | 2026-09-14 09:00 | pending_manual | "El CRM que sale a buscar los clientes. 6 min de una búsqueda → 100 fichas reales. README en el repo." | imagen simple o solo texto | Verificar cuenta LinkedIn conectada |
| M-002 | Mapache | X | 2026-09-15 10:00 | pending_manual | Tweet corto: "La mayoría de los CRM empiezan cuando ya tienes el cliente. Mapache empieza antes." | solo copy | — |
| M-003 | Mapache | Instagram | 2026-09-16 18:00 | pending_manual | Post educativo: cómo prospeccionar negocios locales sin salir del CRM. | carrossel o imagen | Verificar cuenta IG conectada |

### Veyra Soluciones

| id | marca | red | fecha_programada | estado | copy_resumen | asset | notas |
|---|---|---|---|---|---|---|---|
| V-001 | Veyra | Facebook | 2026-09-14 12:00 | pending_manual | Post institucional: qué ofrece Veyra a PyMES colombianas (CRM, email marketing, automatización). CT? a veyrasoluciones.com (validar intake). | imagen banners | Validar formulario intake operativo antes de CTA |
| V-002 | Veyra | Instagram | 2026-09-15 19:00 | pending_manual | Educativo: por qué el warm-up de dominio importa para que tus correos no caigan en spam. | imagen o carrossel | — |

### Guaki

| id | marca | red | fecha_programada | estado | copy_resumen | asset | notas |
|---|---|---|---|---|---|---|---|
| G-001 | Guaki | Instagram | 2026-09-15 18:00 | pending_manual | "¿Tu negocio aparece cuando alguien busca en Google Maps? Guaki te hace visible." CT? guakiweb.vercel.app (validar). | imagen | Validar site operativo |
| G-002 | Guaki | Facebook | 2026-09-17 13:00 | pending_manual | Post sobre la importancia de las reseñas y redes para negocios locales. | imagen o solo texto | — |

### CVELIZ

| id | marca | red | fecha_programada | estado | copy_resumen | asset | notas |
|---|---|---|---|---|---|---|---|
| C-001 | CVELIZ | Instagram | PENDING | pending_manual | [Por definir tras confirmación de marca] | tbd | **No publicar hasta confirmar sitio, redes y dueño de cuenta** |

---

## Reglas del calendario

1. **Ningún post entra en `programado` sin brief aprobado.** Ver el brief antes de publicar.
2. **Ningún post se publica automáticamente sin cuenta + credenciales conectadas.** Si la cuenta no está conectada → estado `pending_manual` + recordatorio al día.
3. **Tras publicación manual, registrar:** fecha real, post_id si aplica, notas de engagement inicial.
4. **CVELIZ: no publicar hasta gap cerrado** (confirmar sitio, redes, dueño de cuenta, servicio). Ver CONTENT_OS.md sección 8.
5. **Revisar calendario cada lunes** (o inicio de semana) para confirmar la semana siguiente.

---

## Registro de publicaciones (para llenar al publicar)

| id | fecha_real | post_id | engagement_inicial | notas |
|---|---|---|---|---|
| M-001 | | | | |
| M-002 | | | | |
| M-003 | | | | |
| V-001 | | | | |
| V-002 | | | | |
| G-001 | | | | |
| G-002 | | | | |

> Engagement_inicial: lo que la red muestra en el primer día/hora (likes, comentarios, impresiones si está disponible). Sin cifras inventadas — solo lo que la red reporta.

---

## Próximos pasos con este calendario

1. Confirmar cuentas de redes por marca (Edwin/equipo).
2. Reemplazar ejemplos con briefs reales de la semana 1.
3. Elegir herramienta de calendario: spreadsheet, Airtable, o archivo en repo. Skill `airtable` disponible si hay cuenta; alternativa CSV/Markdown para sincronizar.
4. Implementar recordatorio para posts en estado `pending_manual` (manual hoy, skill `cronjob_manage` o secuencia existente para automatizar después).

---

*Generado por Marketing (Hermes) — 2026-09-11. Parte del Content OS (t_24b5addd).*
