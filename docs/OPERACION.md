# Guía de operación

Cómo se usa Mapache por dentro: puesta en marcha, comandos, convenciones y el detalle de cada
módulo (segmentos, secuencias, IA, métricas). El [README](../README.md) es la presentación; esto
es el manual.

**Diseño completo:** [`ARQUITECTURA.md`](ARQUITECTURA.md) — el documento original de diseño, con
las 15 decisiones de arquitectura y su porqué.

---

## Puesta en marcha

Requisitos: Python 3.12+ (aquí se usa 3.13), Docker.

```bash
cd backend
make install          # crea .venv e instala dependencias
cp .env.example .env  # y genera las claves (ver abajo)
make up               # levanta Postgres (dev + test) y Mailhog
make migrate          # aplica las migraciones
make dev              # API en http://localhost:8000
```

Generar las dos claves obligatorias de `.env`:

```bash
python -c "from cryptography.fernet import Fernet; print('ENCRYPTION_KEY=' + Fernet.generate_key().decode())"
python -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(48))"
```

`ENCRYPTION_KEY` cifra las credenciales de terceros (tokens OAuth de Gmail/Outlook, contraseñas
SMTP). **Si la pierdes o la cambias, esas credenciales dejan de poder descifrarse** y hay que
reconectar las cuentas. Guárdala fuera del repositorio.

### Servicios locales

| Servicio | Puerto | Notas |
|---|---|---|
| API | 8000 | Swagger en `/docs` |
| Postgres (dev) | 5435 | 5432 y 5433 ocupados en la máquina de desarrollo |
| Postgres (test) | 5434 | En RAM (tmpfs); se borra al parar |
| Mailhog | 8025 | Bandeja web de correos de prueba; SMTP en 1025 |

---

## Comandos

```bash
make help        # lista todo
make dev         # API con recarga automática
make test        # tests
make lint        # ruff check + format --check
make fmt         # formatea y arregla lo autoarreglable
make typecheck   # mypy
make check       # lo mismo que corre CI
make revision m="descripcion"   # nueva migración (autogenerada)
make migrate     # aplica migraciones
make reset-db    # BORRA la base de desarrollo y la rehace
```

---

## Arquitectura en una pantalla

```
HTTP → Router → Controller → Service → Repository → SQLAlchemy → PostgreSQL
```

Reglas que no se rompen:

- El **Router** no importa Repositorios.
- El **Service** no importa nada de FastAPI (ni `HTTPException`): levanta `DomainError` y el
  handler global lo traduce a HTTP. Así el mismo Service sirve desde un worker o un test.
- El **Repository** no tiene lógica de negocio ni hace `commit`: la transacción la controla el
  Service que posee el caso de uso.

Los procesos pesados (scraping, enriquecimiento, envío masivo) **no corren dentro de un request**.
Se encolan y la API responde `202` con un `job_id`:

```
POST /searches/{id}/run → 202 {job_id} → DiscoveryWorker → EnrichmentWorker → ScoringWorker
```

La cola está detrás del Protocol `JobQueue`. Hoy es `InProcessQueue` (asyncio); cuando el volumen
lo pida se cambia a ARQ + Redis editando una línea en `app/core/container.py`.

---

## Estructura

```
backend/app/
├── core/          config, database, enums, exceptions, logging, security, jobs, container
├── models/        SQLAlchemy — añade el import en __init__.py o Alembic no lo verá
├── schemas/       Pydantic (entrada/salida de la API)
├── repositories/  acceso a datos
├── services/      lógica de negocio
├── controllers/   orquestación de casos de uso
├── routers/       definición de endpoints
├── scrapers/      descubrimiento (Google Maps propio, Places API, Apify)
├── enrichment/    crawler de sitios web, extracción, verificación, señales
├── mail/          Gmail API / Microsoft Graph / SMTP + plantillas + guardrails
├── channels/      abstracción multicanal (email hoy, WhatsApp después)
├── scoring/       motor de puntuación de prospectos
├── ai/            personalización y clasificación de respuestas
├── workers/       procesos en background
└── utils/
```

---

## Convenciones

**Migraciones.** Cada modelo nuevo se importa en `app/models/__init__.py`. Lo que no esté ahí no
existe para Alembic, y `--autogenerate` produce una migración que borra tablas. Tras generar una
migración, revísala a mano: `drop_table` no elimina los tipos ENUM, hay que añadirlo al
`downgrade` (ver la migración inicial como referencia).

**Errores.** `DomainError` y subclases en `app/core/exceptions.py`. Cada una lleva un `code`
estable pensado para que el frontend haga `switch`; el `message` es para humanos y puede cambiar.

**Logs.** `structlog`, con eventos en `snake_case` (`job_started`, `email_sent`). Los emails y
teléfonos se ofuscan automáticamente y las claves sensibles se sustituyen por `***`.

**Tests.** Contra Postgres real, no SQLite: el CRM depende de ENUM nativos, JSONB, índices
parciales y `pg_trgm`. Cada test corre en una transacción que se revierte al terminar.

---

## Antes de enviar correos (Fase 4+)

El sistema está diseñado para prospección responsable y trae los controles activados:
unsubscribe con One-Click (RFC 8058), lista de no contactar consultada en cada envío, control de
rebotes, límites por hora y por día, y curva de warm-up de 20 a 100 correos diarios en 22 días.

Configura SPF, DKIM y DMARC en el dominio de envío antes del primer correo. Saltarse el warm-up
con un dominio nuevo es la forma más rápida de acabar en spam de forma permanente.

### Conectar un buzón

**SMTP** funciona desde el primer minuto, sin credenciales de terceros: `POST
/settings/email-accounts/smtp` con host, puerto y contraseña de aplicación. Es lo que se usa
contra Mailhog en desarrollo (`localhost:1025`, sin TLS).

**Gmail y Outlook (OAuth)** están implementados pero necesitan claves. Mientras no existan,
`GET /settings/oauth-providers` devuelve la lista vacía y la interfaz debe ofrecer solo SMTP —
no un botón que llevaría a un error del proveedor. Para habilitarlos:

1. Google Cloud Console → OAuth 2.0 Client ID (tipo *Web*), scopes `gmail.send`,
   `gmail.readonly`, `openid`, `email`; redirect `http://localhost:8000/auth/google/callback`.
2. Azure → registro de aplicación, permisos `Mail.Send`, `Mail.Read`, `User.Read`,
   `offline_access`; redirect `http://localhost:8000/auth/microsoft/callback`.
3. `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`
   en el `.env`.

El flujo usa `state` de un solo uso y PKCE S256. `POST /auth/{provider}/disconnect/{id}` revoca
el token **en el proveedor**, no solo lo borra de la base.

### Quién abrió y no respondió (Fase 6)

Es la pregunta comercial del día a día, y para responderla hacen falta las dos mitades: el
tracking de apertura (Fase 4-5) y la lectura del buzón, que es la que sabe quién sí contestó.

```bash
curl "http://localhost:8000/api/v1/leads/segments/opened_no_reply?min_days=2"
```

Devuelve el prospecto más los hechos que lo justifican: cuántos correos recibió, cuántas
aperturas, cuándo fue la última y cuántos días lleva callado. `min_days` filtra por antigüedad
—escribir el mismo día que abrió suele ser prematuro—. Segmentos disponibles:

| Segmento | Quién entra |
|---|---|
| `opened_no_reply` | Abrió y no respondió. El de seguimiento |
| `clicked_no_reply` | Visitó un enlace y no respondió. Señal más fuerte que abrir |
| `sent_no_open` | Recibió y no abrió. Revisa el asunto o la entregabilidad |
| `replied` | Contestó una persona |
| `bounced` | La dirección rebotó |

`GET /leads/segments/summary` da los cinco contadores de una vez.

La entrada se sincroniza con `POST /settings/email-accounts/{id}/resync` (job `INBOX_SYNC`), o
sola por webhook cuando haya Gmail/Outlook conectados. Al leer, el CRM separa tres cosas que
otros mezclan:

- **Respuesta real** → avanza de etapa, detiene la secuencia y el hilo queda pendiente de
  contestar.
- **Auto-respuesta** ("fuera de la oficina") → no cuenta como respuesta ni mueve el embudo; solo
  reprograma el seguimiento cinco días. Contarla inflaría la tasa de respuesta.
- **Rebote duro** (5.x.x) → a la lista de no contactar y contacto marcado. El blando (4.x.x,
  buzón lleno) no descarta a nadie.

### Secuencias de seguimiento (Fase 7)

Una secuencia es la plantilla ("3 correos, a los 3 y 7 días"); un seguimiento es la instancia
("a este prospecto, el martes a las 10"). Separarlas evita que editar la secuencia reescriba el
pasado de quien ya la recorrió.

Inscribir va en dos tiempos y **nunca** empieza solo:

```bash
curl -X POST "http://localhost:8000/api/v1/sequences/$ID/preview-schedule" \
  -H 'content-type: application/json' -d '{"lead_ids":["..."]}'
```

Eso devuelve el calendario exacto —qué correo, a quién, qué día— más los avisos por prospecto
(sin email, ya inscrito, dado de baja). Solo tras verlo se confirma con `/enroll`, que programa
**el primer paso**; los siguientes se programan al ejecutar cada uno, para que una respuesta
detenga la secuencia antes de que el correo exista.

**Reglas de parada**, en este orden: ya respondió · dijo que no le interesa · la conversación
avanzó (pidió reunión o precio) · el prospecto está cerrado · la secuencia está pausada · está
en la lista de no contactar · su correo rebotó. Fuera de la ventana horaria el envío **se
reprograma, no se pierde**.

Los pasos admiten condición: `opened_not_replied`, `not_opened`, `not_replied`. Permite
ramificar sin motor de reglas —"si abrió y no contestó, insiste; si ni abrió, cambia el asunto"—
y conecta directamente con el segmento del apartado anterior.

El planificador en proceso dispara `FOLLOWUP_TICK` cada 15 minutos e `INBOX_SYNC` cada 10
(`SCHEDULER_ENABLED`, `FOLLOWUP_TICK_MINUTES`, `INBOX_SYNC_MINUTES`). Apágalo si levantas una
segunda instancia de la API, o los ticks se duplican. `POST /follow-ups/run` lo fuerza ahora.

`GET /follow-ups?status=PENDING&due_before=hoy` es la agenda del día: lo que sale solo y lo que
apuntaste a mano ("recuérdame escribirle el martes"), en la misma lista.

### IA (Fase 8) — opcional de verdad

La IA es una mejora, no una dependencia. **Sin `ANTHROPIC_API_KEY` el CRM funciona entero**: las
plantillas se renderizan igual y las respuestas se clasifican con un motor de reglas en español.
`GET /settings/ai` dice si está disponible, para que la interfaz esconda el botón de generar en
vez de ofrecer algo que fallaría.

Con clave, se activan dos cosas:

**Redacción** (`POST /emails/personalize`) — devuelve un borrador **editable**; no envía nada. El
prompt prohíbe inventar detalles: la observación sobre la empresa sale de las señales detectadas
o es genérica y honesta. Tope de 120 palabras, CTA de bajo compromiso, sin superlativos ni
"espero que estés bien". Si la IA no responde, el endpoint devuelve la plantilla con
`is_ai_generated: false`.

**Clasificación de respuestas** — cada respuesta entrante recibe intención, confianza y etapa
sugerida. Por debajo de 0.7 de confianza la sugerencia se marca `is_confident: false` y la
interfaz debe mostrarla atenuada.

| Intención | Sugiere | Automático |
|---|---|---|
| `PRICING`, `MEETING_REQUEST` | Oportunidad / Reunión | Solo suma engagement |
| `POSITIVE`, `QUESTION` | Interesado / Conversación | Nada |
| `NEGATIVE` | Perdido | Detiene la secuencia |
| `UNSUBSCRIBE` | Perdido | **Supresión inmediata** |

**La IA sugiere, el usuario decide.** Nada mueve un prospecto de columna solo. La única acción
automática es la supresión ante una baja explícita, porque el error contrario —seguir
escribiendo a quien pidió parar— es mucho peor. Esa detección corre por reglas, independiente de
la IA, para que no dependa de que el modelo conteste.

`PATCH /conversations/{id}/intent` corrige la etiqueta; con `apply_suggested_stage: true` mueve
además la etapa. Una corrección marca el hilo como revisado y ninguna clasificación posterior la
pisa.

**Coste** (§12 del diseño, ~100 correos/día): ~$27/mes con `claude-opus-5`, ~$16 con
`claude-sonnet-5`, ~$5 con `claude-haiku-4-5` — se cambia en Configuración, sin tocar código. El
bloque de sistema va marcado para caché, que es de dónde sale el descuento.

### Métricas y embudo (Fase 9)

```bash
curl "http://localhost:8000/api/v1/metrics/funnel?from=2026-07-01"
```

El embudo se calcula sobre `lead_stage_history`, no sobre la etapa actual: quien hoy está en
"Ganado" pasó antes por "Interesado" y cuenta en ambos. Mirar solo la columna de hoy perdería a
todos los que avanzaron.

Cada paso declara **contra qué se compara** (`baseline_key`), porque el embudo no es una sola
cadena: una respuesta sale de los entregados, no de los clicks. Encadenar por posición produce
cosas como "600% de conversión" en cuanto alguien responde sin haber pulsado un enlace. La
respuesta trae además `bottleneck`: la lectura en una frase, para no dejarle al usuario comparar
trece porcentajes a ojo.

Las tasas viajan siempre con su fracción — `{"value": 45.0, "numerator": 9, "denominator": 20}` —
porque un 45% sobre 20 correos y uno sobre 2.000 no son lo mismo, y sin el denominador nadie
puede saberlo. Con menos de 30 envíos el propio endpoint lo advierte.

**Dos alarmas** que no son métricas más, sino avisos de que el dominio se está quemando: rebotes
por encima del 5% y bajas por encima del 0,5%. Vienen con `is_alarm: true` y una explicación en
lenguaje llano.

Los números salen **más bajos** que en otras herramientas, y son los reales: las aperturas de
bots no cuentan (Apple MPP y el proxy de Gmail cargan el pixel de todo) y las auto-respuestas
tampoco cuentan como respuesta.

| Endpoint | Para qué |
|---|---|
| `/metrics/overview` | Contadores del panel |
| `/metrics/funnel` | Embudo con conversión paso a paso |
| `/metrics/email` | Apertura, click, respuesta, rebote, baja |
| `/metrics/by-template` | Comparativa de plantillas — el test A/B implícito |
| `/metrics/by-service`, `/by-city` | Qué vendes mejor y dónde |
| `/metrics/velocity` | Días medios y **medianos** por etapa |
| `/metrics/timeseries` | Series por día, semana o mes |
| `/metrics/attention` | Lo que hay que hacer hoy |

Todo se agrega en Postgres, no en Python: con 3.000 prospectos, contar en un bucle es la
diferencia entre 40 ms y 8 segundos.

### Detalles que no son cosméticos

- Las horas se evalúan en la zona configurada (`America/Bogota` por defecto). La ventana de
  envío, el corte del contador diario y el día de la rampa son hora local, no UTC.
- Las cabeceras del MIME se emiten sin plegar: plegadas, una URL larga sin espacios acaba
  codificada en RFC 2047 y ni Gmail ni Outlook reconocen ahí el `List-Unsubscribe`.
- Una apertura detectada en los tres primeros segundos, o con `User-Agent` de proxy, se registra
  pero no cuenta: Apple MPP y el proxy de Gmail cargan el pixel de todos los correos. La interfaz
  dice "apertura detectada", nunca "lo leyó".
