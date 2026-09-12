# Content OS — Pipeline unificado de contenido para redes sociales

**Estado:** esqueleto de pipeline + calendario + guía de marca, listo para ejecución manual o parcial automática.  
**Alcance:** 4 marcas — Mapache, Veyra Soluciones, Guaki, CVELIZ.  
**Base metodológica:** BRENDA_CONTENT_STUDIO (pipeline research → ideas → copy → diseño → calendario → publicación).  
**Fecha:** 2026-09-11 SA Pacific (UTC-5)

---

## 1.audiencia y propósito

Una sola máquina de contenido que sirva a 4 marcas con objetivos distintos, sin mezclar voces ni audiencias. Cada marca tiene su propio público; el pipeline compartido es operativo, no creativo.

| Marca | Qué es | Audiencia principal | Objetivo de contenido | Canal prioritario |
|---|---|---|---|---|
| **Mapache** | CRM de prospección (este repo) | PyMES/Mid-Market LATAM, CEO/fundador que vende servicios a negocios locales; agencias, freelances, consultoras | Demostrar que el CRM "sale a buscar clientes" — desmitizar que los CRM solo guardan clientes | LinkedIn + Twitter/X + email nurture |
| **Veyra Soluciones** | Marca comercial del equipo (veyrasoluciones.com) | Empresas colombianas que buscan CRM, automatización de ventas, email marketing | Convertir visitas del site en leads cualificados para Mapache | Facebook + Instagram + email |
| **Guaki** | Funnel comercial / sitio guakiweb.vercel.app | Negocios locales que quieren ser descubiertos | Generar demanda para el servicio de visibilidad/conciliación Guaki-Mapache | Instagram + Facebook |
| **CVELIZ** | Clínica/서비스 de salud (mención en importaciones como campaña previa) | Pacientes/usuarios del servicio CVELIZ, geografía específica | Retención, citas, tomOf confianza | Instagram + Facebook + WhatsApp (fuera de alcance inicial) |

> **Nota:** CVELIZ data proviene de `backend/scripts/import_leads_estado.py` como campaña previa (66 leads CONTACTED). Se necesita confirmar el sitio web, redes existentes y dueño de la cuenta antes de publicar en nombre de CVELIZ. Mientras tanto, CVELIZ entra en el pipeline como marca en descubrimiento.

---

## 2. pipeline (research → ideas → copy → diseño → calendario → publicación)

Cada etapa tiene: entrada, salida, dueño, herramienta, criterio de avance. El pipeline es secuencial por pieza de contenido, pero **múltiples piezas pueden estar en etapas distintas simultáneamente**.

### 2.1 investigación

**Entrada:** marca, objetivo de la semana, audience, tendencias del sector, performance de posts anteriores.  
**Salida:** 3–5 ángulos validados, URLs de referencia, hashtags/tendencias relevantes, post de competencia que sirve de contrapunto.  
**Dueño:** marketing + (opcional) IA para escaneo rápido.  
**Herramientas:** web_search, revisión de perfiles competencia, analítica nativa de cada red (si está conectada).  
**Criterio de avance:** al menos 1 ángulo con evidencia (no "me parece que…").

### 2.2 ideas (brief)

**Entrada:** ángulos de investigación.  
**Salida:** brief de contenido por pieza: marca, objetivo, mensaje central, CTA, tono, formato (caption + imagen/video/carrossel), duración del contenido (evergreen vs. timely).  
**Dueño:** marketing.  
**Formato del brief:** ver plantilla en sección 4.  
**Criterio de avance:** brief aprobado (por quién escribe → ve → publica; en MVP, marketing aprueba solo suya).

### 2.3 copy

**Entrada:** brief aprobado.  
**Salida:** copy escrito — caption principal + alternativas + hashtags + texto del CTA + variante corta (para Twitter/X o stories).  
**Dueño:** marketing.  
**Criterio de avance:** copy reviseado contra voz de marca (sección 3) y sin promises no respaldadas.

### 2.4 diseño

**Entrada:** copy + brief (formato, formato visual).  
**Salida:** asset listo para publicar — imagen, carrossel, video corto, o solo copy si el formato lo permite.  
**Dueño:** ui-designer / ad-creative-strategist (según alcance).  
**Herramientas:** HTML artifacts (skill claude-design / popular-web-designs), p5js para gen-art, herramienta de diseño externa.  
**Criterio de avance:** asset en forma publicable, con copy integrado si aplica.

### 2.5 calendario

**Entrada:** piezas con copy + asset + fecha/hora programada.  
**Salida:** calendario visible que muestra qué se publica, cuándo, en qué red, para qué marca, con estado.  
**Dueño:** marketing (bsa).  
**Herramienta:** spreadsheet o tool de calendario (Airtable?); ver sección 5.  
**Criterio de avance:** calendario confirmado antes de publicar — misma filosofía que las secuencias de email: ver el calendario antes de ejecutar.

### 2.6 publicación

**Entrada:** pieza programada con asset + copy + cuenta conectada.  
**Salida:** post publicado o programado, con registro de publicación (fecha real, post_id, estado).  
**Dueño:** sistema (Graph API) o marketing (manual + recordatorio).  
**Canales:**
- **Instagram/Facebook:** Graph API oficial donde la cuenta esté conectada y credenciales existan. Ver sección 6.
- **LinkedIn/Twitter/X:** actualmente sin integración nativa; publicación manual + recordatorio.
- **Email nurture:** ya existe en el CRM — se reusa para contenidos de Mapache/Veyra que van a leads existentes, no como "post" social.

> **Regla de publicación:** si no hay cuenta conectada + credenciales válidas para una red, la pieza se marca como "pending_manual" y entra en el recordatorio del día. Nunca se publica "porque sí" en nombre de una marca sin cuenta configurada.

---

## 3. identidad y voz por marca (guía de copy)

### 3.1 Mapache

- **Voz:** directa, técnica pero no académica, sin hype. Explica qué hace y por qué es diferente con ejemplos concretos. Frase ancla: "El CRM que sale a buscar los clientes."
- **No es:** "IA que vende sola", promesas de cifras sin fuente, lenguaje de startup hype ("disruptivo", "game-changer").
- **Content pillars:**
  1. Qué hace Mapache (feature explicado con caso, no lista de características).
  2. Por qué los CRM tradicionales no encuentran clientes (post de contraste).
  3. Caso/real dato (si hay, sin inventar).
  4. Educación: cómo prospeccionar, cómo calificar leads, cómo escribir el primer correo (sin prometer resultados).
  5. Estado real del producto (beta de un solo usuario — honesty como posicionamiento).
- **CTA típico:** leer el README, probar local, agendar charla (si hay).
- **Hashtags sugeridos:** #CRM #Ventas #Prospección #PyMES #Colombia (ajustar por publicación).
- **Ejemplo de ángulo:** "La mayoría de los CRM empiezan cuando ya tienes el cliente. Mapache empieza 6 minutos antes, cuando todavía no lo tienes! ¿por qué eso cambia todo."

### 3.2 Veyra Soluciones

- **Voz:** profesional, orientada a resultados, confianza institucional pero accesible. Marca comercial del equipo — no es el producto (Mapache) sino quien lo ofrece/bring?
- **Content pillars:**
  1. Soluciones de CRM/automatización para PyMES colombianas.
  2. Casos/de resultaos (si hay, con fuente o sin cifra inventada).
  3. Educación: email marketing, warm-up, entregabilidad, secuencias.
  4. Noticias/opinion sobre el sector.
- **CTA:** ir al site veyrasoluciones.com, formulario de intake (modo REVIEW hoy — ver estado).
- **Hashtags sugeridos:** #CRMColombia #Automatización #EmailMarketing #PyME
- **Nota:** el formulario público `veyrasoluciones.com/api/lead` está en modo REVIEW (acepta lead pero no envía email ni lo escribe a producción, según reporte 2026-09-07). Publicar CTAs a ese formulario sin confirmar que está operativo es riesg. Validar con Edwin antes de CTAs que apunten al formulario.

### 3.3 Guaki

- **Voz:** cercana, enfocada en visibilidad para negocios locales. Menos técnico que Mapache, más "tú puedes ser encontrado".
- **Content pillars:**
  1. Los negocios locales necesitan ser encontrados, no solo tener cliente.
  2. Guaki como bridge entre negocio y Mapache.
  3. Educación: Google Maps, reseñas, redes como canal de descubrimiento.
  4. Signal/method (sin inventar métricas).
- **CTA:** ir a guakiweb.vercel.app (si está operativo).
- **Hashtags sugeridos:** #NegociosLocales #GoogleMaps #Visibilidad #Colombia

### 3.4 CVELIZ

- **Voz:** empática, de confianza, clínica/profesional. A definir con dueño de marca.
- **Content pillars:** por definir tras confirmar sitio, redes, dueño.
- **Acción:** antes de publicar en nombre de CVELIZ, confirmar: sitio web, Instagram/Facebook existentes, quién es el dueño de la cuenta, qué servicio se ofrece. Ver sección 8 (gaps).

---

## 4. plantilla de brief de contenido

```
# BRIEF — [Marca] — [Título corto]

Fecha de creación: YYYY-MM-DD
Dueño: [nombre]
Estado: research | ideas | copy | diseño | calendario | publicado

## Marca
- Marca: [Mapache|Veyra|Guaki|CVELIZ]
- Red(es): [Instagram|Facebook|LinkedIn|X|email]
- Formato: [caption|imagen|carrossel|video|stories]

## Objetivo
- [Qué se busca con este post: awareness, engagement, lead, nurture, etc.]

## Mensaje central (1 línea)
- [La idea principalesa del post]

## CTA
- [Qué debe hacer el lector: leer, ir a site, agendar, etc.]
- Enlace destino: [URL o "ninguno"]

## Tono/constraints
- [Voz de marca aplicable, qué evitar, largo esperado]

## Copy
- Caption principal: [...]
- Variante corta: [...]
- Hashtags: [...]

## Asset
- [Descripción del diseño necesitado o "solo copy"]
- Dueño del diseño: [nombre o "pending"]

## Programación
- Fecha/hora slated: YYYY-MM-DD HH:MM (zona America/Bogota)
- ¿Publicación automática posible? [Sí/No — cuenta conectada?]
- Si no: recordatorio a [nombre] el día D a las HH:MM

## Referencias
- [URLs de investigación que respaldan el ángulo]
```

---

## 5. calendario (estructura)

Formato: semana por marca, con frecuencia mínima sostenible. En MVP, **calidad > frecuencia**.

### 5.1 frecuencia sugerida (MVP)

| Marca | Instagram | Facebook | LinkedIn | X (Twitter) | Email nurture |
|---|---|---|---|---|---|
| Mapache | 2–3/mes | 2–3/mes | 2–3/mes | 2–3/mes | continua (existe en CRM) |
| Veyra | 2–4/mes | 2–4/mes | 1–2/mes | 1–2/mes | continua |
| Guaki | 2–3/mes | 2–3/mes | 1/mes | 1/mes | — |
| CVELIZ | tbd | tbd | — | — | — |

> CVELIZ: frecuencia tbd tras confirmación de marca.

### 5.2 estructura de hoja de calendario

Columnas mínimas:
- `id` (único)
- `marca`
- `red`
- `fecha_programada` (YYYY-MM-DD HH:MM America/Bogota)
- `estado` (briefing | copy | diseño | programado | publicado | pending_manual | skip)
- `brief_link` (referencia al brief)
- `copy` (o referencia)
- `asset_link`
- `publicacion_real` (fecha real + post_id si aplica)
- `notas`

Herramienta sugerida para MVP: spreadsheet (Airtable o similar) compartido. Skill `airtable` disponible si hay cuenta. Alternativa: archivo CSV/Markdown en repo para sincronización.

---

## 6. publicación automática — Graph API (Instagram/Facebook)

### 6.1 qué está implementado en el codebase

- **WhatsApp Cloud API (Meta):** webhook + envío oficial en `backend/app/routers/whatsapp_webhook.py`. Patrón de autenticación Meta (verify token, X-Hub-Signature-256) ya existe.
- **Microsoft Graph (Outlook):** cliente de Graph en `backend/app/mail/graph.py` — patrón de cliente Graph con Bearer token, though this is Microsoft, not Meta.
- **Extracción de redes sociales:** `scripts/scrapers/scrapling/enrich.py` y `backend/app/enrichment/extractors.py` extraen URLs de Instagram/Facebook de sitios web (solo URLs, no perfil).
- **Instagram Graph API para datos de perfil:** mencionado en `docs/ARQUITECTURA.md` (línea 50, 1295) como algo que se activa si el usuario conecta la Graph API oficial, pero **no está implementado**.

### 6.2 qué falta para publicación automática en Instagram/Facebook

| Requisito | Estado | Acción |
|---|---|---|
| Cuenta de Instagram Business o Creator conectada a Facebook Page | **Desconocido** — verificar | Identificar cuentas por marca, confirmar tipo de cuenta |
| Facebook App con permisos `instagram_content_publish`, `pages_manage_posts`, etc. | **No existe** | Crear app en Meta for Developers, configurar OAuth |
| Access token válido (usuari o page token) guardado de forma segura | **No existe** | Implementar almacenamiento de credenciales (cifrado, como el patrón de OAuth de correo en `backend/app/mail/oauth.py`) |
| Cliente Graph para Instagram publishing | **No implementado** | Crear `backend/app/social/instagram_graph.py` siguiendo patrón de `graph.py` (Microsoft) y `whatsapp_webhook.py` (Meta) |
| Endpoint de calendario/publicación en API | **No existe** | Crear router `backend/app/routers/social.py` con scheduling |
| Worker de publicación programada | **No existe** | Reutilizar patrón de `followup_worker.py` (tick + reglas de parada) para publicaciones |

### 6.3 endpoints de Graph API relevantes (documentación oficial Meta)

Para publicación en Instagram (Business/Creator con cuenta conectada):
- Crear media object: `POST /{ig-user-id}/media` (caption + image_url)
- Publicar: `POST /{ig-user-id}/media_publish`
- Estado del media: `GET /{ig-user-id}/media` con fields

Para Facebook Pages:
- `POST /{page-id}/photos` o `/messages`
- Programar publish (si está soportado para la cuenta)

Ver documentación oficial: https://developers.facebook.com/docs/instagram-api/guides/content-publishing/

> **Nota:** los detalles exactos de endpoints y permisos requieren consulta a la documentación oficial de Meta, que cambia. No usar cifras o flujos inventados. Verificar en el momento de implementación.

### 6.4 estado de cuentas por marca (por confirmar)

| Marca | Instagram | Facebook | LinkedIn | X |
|---|---|---|---|---|
| Mapache | ? | ? | ? | ? |
| Veyra | ? | ? | ? | ? |
| Guaki | ? | ? | ? | ? |
| CVELIZ | ? | ? | — | — |

Acción: listar cuentas existentes por marca antes de intentar conectar Graph API. Sin cuenta, no hay publicación automática.

---

## 7. publication manual + recordatorio (resto de canales y casos sin Graph)

### 7.1 flujo manual

1. El calendario muestra las piezas en estado `programado` o `pending_manual` para el día.
2. Al inicio del día (o en la hora programada), el sistema/envío manual:
   - Si la red tiene cuenta conectada → publicación automática (si está implementado).
   - Si no → recordatorio a marketing con: marca, red, copy, asset, hora, enlace al brief.
3. Tras publicación manual, registrar en calendario: fecha real, post_id si aplica, notas.

### 7.2 recordatorio

Implementación sugerida: reutilizar el sistema de secuencias/follow-ups existente (`backend/app/workers/followup_worker.py`) adaptado a "tareas de publicación" en vez de "correos". O bien: skill `cronjob_manage` para recordatorios programados. O bien: columna de calendario con state `pending_manual` que el equipo revisa diariamente.

---

## 8. gaps y acciones previas (antes de publicar)

### 8.1 gaps de datos de marca

| Gap | Impacto | Acción |
|---|---|---|
| CVELIZ: sitio web, redes existentes, dueño de cuenta, qué servicio ofrece | No se puede publicar en nombre de CVELIZ con propiedad | Confirmar con Edwin/equipo antes de incluir CVELIZ en publicaciones reales |
| Veyra: estado del formulario `veyrasoluciones.com/api/lead` (modo REVIEW) | CTAs que apuntan al formulario pueden no funcionar | Validar operatividad del intake antes de CTAs |
| Guaki: estado de guakiweb.vercel.app, cuentas de redes | CTAs y publicación dependen de cuenta operativa | Validar |
| Mapache: no hay cuenta de redes conectada confirmada | Publicación automática en redes requiere cuenta + credenciales | Identificar/crear cuentas, conectar Graph API |
| Ninguna marca: credenciales Graph API guardadas | Imposible publicación automática | Implementar almacenamiento de tokens (ver sección 6.2) |
| CVELIZ 66 leads CONTACTED sin fecha de contacto (import_leads_estado.py) | Dato histórico, no bloquea contenido, pero afecta manual de CRM | Opcional: completar fechas si se conocen |

### 8.2 gaps de implementación

| Gap | Estado |
|---|---|
| Cliente Instagram Graph para publishing | No implementado |
| Router de social en API | No implementado |
| Worker de publicación programada | No implementado |
| Almacenamiento seguro de tokens Meta | No implementado (patrón OAuth de correo existe como referencia) |
| Calendario de contenido (herramienta) | No especificado; sugerir spreadsheet/Airtable/CSV |

---

## 9. fases de implementación sugeridas

### Fase 0 — Descubrimiento de cuentas (1–2 días)
- Listar cuentas de redes existentes por marca (Instagram, Facebook, LinkedIn, X).
- Confirmar CVELIZ: sitio, redes, dueño, servicio.
- Confirmar Veyra intake operatividad.
- Confirmar Guaki site operatividad.
- Decidir qué cuentas conectar primero a Graph API.

### Fase 1 — Pipeline operativo manual (1 semana)
- Usar brief + calendario (Markdown/CSV/Airtable) para producir contenido sin automatización.
- Publicar manualmente en cuentas existentes.
- Registrar publicaciones reales en calendario.
- Iterar sobre voz/ángulos con datos reales de engagement (si hay cuentas con analítica).

### Fase 2 — Graph API para Instagram/Facebook (2–4 semanas, si hay cuentas + permisos)
- Crear Facebook App, solicitar permisos.
- Implementar almacenamiento de tokens (cifrado, patrón oauth.py).
- Implementar cliente Instagram Graph (`backend/app/social/instagram_graph.py`).
- Implementar router + worker de publicación programada.
- Conectar cuentas por marca.

### Fase 3 — Otros canales (después, si hay demanda)
- LinkedIn/Twitter publishing (si hay API/oBSP).
- WhatsApp Business (ya hay webhook; publishing de plantillas es diferente — ver notas de ARQUITECTURA.md sobre reglas más estrictas).

### Fase 4 — Medición e iteración
- Conectar analítica nativa de cada red (si está disponible) para medir engagement.
- Ajustar frecuencia, ángulos, formatos.
- Sin métricas inventadas: medir lo que las redes reportan, con denominador cuando aplique.

---

## 10. entregables de esta tarea

1. **Este archivo** (`CONTENT_OS.md`) — pipeline, guía de marca, calendario, estado de implementación.
2. **Calendario inicial** — archivo separado `content_calendar.md` o CSV listo para usar, con estructura de columnas definida, vacío para la primera semana (o con ejemplos de briefs).
3. **Plantilla de brief** — sección 4, reusable.
4. **Lista de gaps + acciones** — sección 8, para Edwin/equipo.

### Archivos creados

- `CONTENT_OS.md` (este archivo)
- `content_calendar.md` — calendario vacío con estructura + ejemplos
- `brief_templates/` — carpeta con briefs de ejemplo por marca

---

## 11. reglas de uso

1. **Nada de cifras inventadas.** Si no hay dato real de engagement, decir "sin datos todavía" o usar cifras con fuente+FECHA+URL.
2. **Calendario antes de publicar.** Ver el calendario antes de publicar, igual que las secuencias de email: "ver el calendario no inscribe a nadie" (secuencia_svc.py). Publicar sin calendario = improvisar.
3. **Cero automatización sin cuenta configurada.** Si no hay cuenta + credenciales para una red, la pieza va a `pending_manual` + recordatorio, nunca se publica automáticamente "porque el sistema lo intenta".
4. **Voz por marca, sin mezclar.** Cada pieza pertenece a una marca; no hablar de Mapache como si fuera Veyra.
5. **Honestidad sobre estado.** Mapache es beta de un solo usuario hoy (README.md línea 15). No publicar como si fuera producto maduro con métricas de clientes sin datos.
6. **CTAs validados.** Antes de publicar un CTA que apunta a un formulario/site, confirmar que está operativo (Veyra intake, Guaki site).

---

**Próximo paso:** Edwin confirma cuentas de redes por marca + decide qué marcas conectar primero a Graph API. Mientras, el pipeline manual (Fase 1) es ejecutable con el calendario + briefs.

---

*Generado por Marketing (Hermes) — 2026-09-11. Basado en código y docs existentes del repo mapache. BRENDA_CONTENT_STUDIO interpretado como marco metodológico de pipeline de contenido; si hay un documento específico de BRENDA, incorporarlo en la próxima revisión.*
