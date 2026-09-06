<div align="center">

# 🦝 Mapache

**El CRM que sale a buscar los clientes.**

Encuentra negocios en Google Maps, les saca los datos de contacto, los puntúa, escribe el primer
correo, mide qué pasó de verdad con ese correo y te dice a quién insistir hoy.

![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)
![Next.js](https://img.shields.io/badge/Next.js-16-000000?logo=nextdotjs&logoColor=white)
![Tests](https://img.shields.io/badge/tests-567%20verdes-brightgreen)
![Estado](https://img.shields.io/badge/estado-beta%20de%20un%20solo%20usuario-orange)

<!-- Al subirlo a GitHub, cambia la insignia de tests por la de CI real:
     ![CI](https://github.com/USUARIO/mapache/actions/workflows/ci.yml/badge.svg) -->

</div>

---

Los CRM saben guardar clientes. Ninguno sabe **encontrarlos**. Mapache empieza una etapa antes: en
la ciudad, el barrio y el tipo de negocio al que le vendes.

Le dices *"restaurantes en Medellín, sin sitio web propio, mínimo 4.0 de calificación"* y seis
minutos después tienes 100 fichas reales, con correo verificado, teléfono en E.164, redes, señales
detectadas y una puntuación que explica por qué cada una vale lo que vale. Desde ahí escribes,
haces seguimiento y cierras — sin salir de la misma aplicación.

Está pensado para quien vende servicios a negocios locales: agencias, freelances, estudios,
consultoras. Mercado por defecto Colombia (COP, +57, `America/Bogota`, es-CO), configurable.

## Lo que hace, en orden

```
    Búsqueda            Descubrimiento        Enriquecimiento         Calificación
 "panaderías en   →   Google Maps (scraper  →  crawler del sitio  →  score 0-100 con
   Medellín"           propio / Places API)     web, redes, MX          6 dimensiones
                                                                            ↓
    Métricas       ←     Conversación       ←      Seguimiento      ←    Contacto
  embudo real         respuesta clasificada     secuencias que se      correo con
  con denominador     (real vs autorespuesta)   paran solas           preview editable
```

Cada flecha es un job en segundo plano, no un `request` que se queda colgado. La API responde
`202 {job_id}` y el frontend muestra progreso real.

## Capturas

<!-- LO PRIMERO QUE MIRA QUIEN LLEGA. Pon aquí 3 imágenes:
     1. docs/img/prospectos.png    — la tabla con score y engagement
     2. docs/img/pipeline.png      — el kanban
     3. docs/img/dashboard.png     — el embudo con las tasas
     Formato: ![Prospectos](docs/img/prospectos.png)
-->

## Por qué no es otro CRM más

**Los números son los reales, no los bonitos.** Las aperturas de bots no cuentan: Apple MPP y el
proxy de Gmail cargan el píxel de todos los correos, así que una apertura en los primeros tres
segundos o con `User-Agent` de proxy se registra pero no suma. Las autorespuestas ("estoy fuera de
la oficina") no cuentan como respuesta. Toda tasa viaja con su fracción — `{"value": 45.0,
"numerator": 9, "denominator": 20}` — porque un 45% sobre 20 correos y uno sobre 2.000 no son lo
mismo, y sin el denominador nadie puede saberlo.

**El embudo se calcula sobre el historial, no sobre la columna de hoy.** Quien hoy está en "Ganado"
pasó antes por "Interesado" y cuenta en ambos. Cada paso declara contra qué se compara: una
respuesta sale de los entregados, no de los clicks. Encadenar por posición es lo que produce
"600% de conversión" en cuanto alguien responde sin haber pulsado un enlace.

**La entregabilidad va de serie.** Unsubscribe One-Click (RFC 8058), lista de no contactar
consultada en cada envío, control de rebotes duros y blandos, límites por hora y por día, y curva
de warm-up de 20 a 100 correos diarios en 22 días. Nada de esto es opcional ni se añade después:
el cumplimiento no es un retrofit.

**La IA sugiere, tú decides.** Sin `ANTHROPIC_API_KEY` el CRM funciona entero — las plantillas se
renderizan igual y las respuestas se clasifican con un motor de reglas en español. Con clave se
activan el borrador editable (que no envía nada) y la clasificación de intención con nivel de
confianza. Ninguna sugerencia mueve un prospecto de columna sola. La única acción automática es la
supresión ante una baja explícita, porque el error contrario es mucho peor.

**El scraper no es un `plugin` de segunda.** Google Maps con Playwright y parseo adaptativo, más
Places API y proveedores SERP detrás del mismo `Protocol`: si el DOM cambia un martes, se cambia el
proveedor en Configuración y la prospección sigue esa misma tarde.

## Empezar en cinco minutos

Requisitos: Python 3.12+ (aquí se usa 3.13), Docker, Node 20+.

```bash
# 1. Backend
cd backend
make install                 # crea .venv e instala dependencias
cp .env.example .env
python -c "from cryptography.fernet import Fernet; print('ENCRYPTION_KEY=' + Fernet.generate_key().decode())"
python -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(48))"
# pega las dos claves en .env

make up                      # Postgres (dev + test) y Mailhog
make migrate                 # aplica las migraciones
make dev                     # API en http://localhost:8000/docs
```

```bash
# 2. Frontend (otra terminal)
cd frontend
npm install
npm run dev                  # http://localhost:3000
```

```bash
# 3. Comprobar que todo está bien
cd backend && make check     # ruff + mypy + 567 tests
```

Los correos de desarrollo no salen a internet: van a Mailhog, que tiene bandeja web en
<http://localhost:8025>. **SMTP funciona desde el primer minuto**, sin credenciales de terceros;
Gmail y Outlook por OAuth están implementados pero necesitan claves propias (instrucciones en
[`docs/OPERACION.md`](docs/OPERACION.md#conectar-un-buzón)).

`ENCRYPTION_KEY` cifra las credenciales de terceros. **Si la pierdes o la cambias, esas
credenciales dejan de poder descifrarse** y hay que reconectar las cuentas. Guárdala fuera del
repositorio.

| Servicio | Puerto | Notas |
|---|---|---|
| API | 8000 | Swagger en `/docs` |
| Frontend | 3000 | |
| Postgres (dev) | 5435 | 5432 y 5433 ocupados en la máquina de desarrollo |
| Postgres (test) | 5434 | En RAM (tmpfs); se borra al parar |
| Mailhog | 8025 | Bandeja web de correos de prueba; SMTP en 1025 |

## Qué hay hecho

Verificado en este repositorio: **129 endpoints**, **29 tablas**, **567 tests en verde contra
Postgres real** (6,6 s), ~24.000 líneas de Python y ~11.500 de TypeScript.

| Módulo | Estado | Qué incluye |
|---|---|---|
| Fundación |Capas, jobs, migraciones, logging ofuscado, cifrado Fernet, CI |
| Prospección | Scraper propio de Google Maps, Places API, crawler de sitios web, verificación de correo por MX, dedupe de 4 niveles |
| CRM |  Empresas, contactos, prospectos, kanban de 14 etapas, actividades, tareas |
| Correo |  Gmail API, Microsoft Graph, SMTP/IMAP, plantillas, guardrails, warm-up, supresión |
| Tracking | Píxel, enlaces rastreados, eventos, detección de bots, unsubscribe One-Click |
| Conversaciones | Sincronización de entrada, threading, respuesta real vs autorespuesta vs rebote |
| Secuencias |  Pasos condicionales, preview del calendario antes de inscribir, 7 reglas de parada |
| Inteligencia | Score de 6 dimensiones explicable, personalización y clasificación con IA opcional |
| Analítica |  Embudo, tasas con denominador, series, por plantilla/servicio/ciudad, alarmas |
| Llamadas | Guiones por situación, `brief` listo para leer, histórico de resultados |
| Dossier | Todo lo que se sabe de una empresa en una pantalla, con hallazgos web cacheados |

**Lo que todavía no existe** (y está documentado como tal, no escondido):

- **No hay autenticación.** La API es de un solo usuario y no tiene login. Las tablas raíz ya
  llevan `owner_id` nullable para que multiusuario sea una migración trivial, pero el `login` hay
  que escribirlo. **No lo expongas a internet como está.**
- **La cola es en proceso.** `InProcessQueue` (asyncio) basta para 100 correos/día; ARQ + Redis
  está abstraído detrás del `Protocol` `JobQueue` pero no implementado.
- **WhatsApp está preparado, no construido.** `conversation_messages` y `ChannelAdapter` existen
  desde el día uno para que añadirlo no toque el inbox; falta el adaptador.
- **No hay Dockerfile de la aplicación** ni despliegue de producción. Docker levanta Postgres y
  Mailhog, nada más.
- **El frontend no tiene tests.**

Los cinco son buenas primeras contribuciones. Ver [Cómo contribuir](#cómo-contribuir).

## Cómo está construido

```
HTTP → Router → Service → Repository → SQLAlchemy 2.x async → PostgreSQL 16
```

Tres reglas que no se rompen:

- El **Router** no importa Repositorios.
- El **Service** no importa nada de FastAPI (ni `HTTPException`): levanta `DomainError` y un
  handler global lo traduce a HTTP. Así el mismo Service sirve desde un worker o desde un test.
- El **Repository** no tiene lógica de negocio ni hace `commit`: la transacción la controla el
  Service dueño del caso de uso.

Los procesos pesados —scraping, enriquecimiento, envío masivo— **no corren dentro de un request**:

```
POST /searches/{id}/run → 202 {job_id} → DiscoveryWorker → EnrichmentWorker → ScoringWorker
```

Todo lo que puede cambiar está detrás de un `Protocol`: `DiscoveryProvider` (scraper propio,
Places API, SERP), `MailProvider` (Gmail, Graph, SMTP), `ChannelAdapter` (email hoy, WhatsApp
después), `JobQueue` (en proceso hoy, ARQ mañana) y el proveedor de IA (Anthropic vía SDK oficial;
OpenAI, DeepSeek y Kimi comparten cliente porque comparten `/chat/completions`).

```
backend/app/
├── core/          config, database, enums, exceptions, logging, security, jobs, scheduler
├── models/        SQLAlchemy — añade el import en __init__.py o Alembic no lo verá
├── schemas/       Pydantic (entrada/salida de la API)
├── repositories/  acceso a datos
├── services/      lógica de negocio
├── routers/       definición de endpoints
├── scrapers/      descubrimiento (Google Maps propio, Places API, SERP social, buscador web)
├── enrichment/    crawler de sitios web, extracción, verificación, señales
├── mail/          Gmail API / Microsoft Graph / SMTP + plantillas + guardrails
├── channels/      abstracción multicanal
├── scoring/       motor de puntuación
├── ai/            personalización y clasificación de respuestas
└── workers/       discovery, enrichment, scoring, send, inbox, followup

frontend/src/
├── app/           13 pantallas (dashboard, prospectos, empresas, pipeline, conversaciones…)
├── components/    ui (shadcn), crm (paneles de dominio), layout
└── lib/           cliente de API tipado, formato, estados
```

Stack: FastAPI · SQLAlchemy 2.x async · PostgreSQL 16 (ENUM nativos, JSONB, índices parciales,
`pg_trgm`) · Alembic · structlog · Playwright · Next.js 16 · React 19 · Tailwind 4 · shadcn/ui ·
TanStack Query · dnd-kit · Recharts.

## Antes de enviar el primer correo

Configura **SPF, DKIM y DMARC** en el dominio de envío. Saltarse el warm-up con un dominio nuevo es
la forma más rápida de acabar en spam de forma permanente, y de ahí no se vuelve.

Mapache trae los controles activados, pero la responsabilidad de a quién escribes es tuya: escribe
a negocios con los que tengas algo real que ofrecer, en volúmenes que puedas sostener, y respeta
cada baja a la primera. La herramienta no intenta evadir filtros antispam ni lo hará: no hay
rotación de dominios, ni ofuscación de enlaces, ni trucos para esconder el `List-Unsubscribe`.

Sobre el scraping, dicho una vez y claro: raspar Google Maps va contra sus Términos de Servicio y
el scraper es frágil por naturaleza. Por eso Places API y los proveedores SERP están implementados
detrás del mismo `Protocol`. El crawler de sitios web respeta `robots.txt`, se identifica y va a
1 req/s por dominio. **No se implementa** bypass de CAPTCHA: si Google presenta un challenge, el
job se pausa y avisa. De LinkedIn solo se guarda la URL descubierta; no se raspa.

## Un par de cosas que sí se pueden probar hoy

```bash
# ¿Quién abrió y no respondió? La pregunta comercial del día a día.
curl "http://localhost:8000/api/v1/leads/segments/opened_no_reply?min_days=2"

# El embudo, con conversión paso a paso y el cuello de botella en una frase.
curl "http://localhost:8000/api/v1/metrics/funnel?from=2026-07-01"

# Lo que hay que hacer hoy.
curl "http://localhost:8000/api/v1/metrics/attention"
```

Segmentos disponibles: `opened_no_reply`, `clicked_no_reply`, `sent_no_open`, `replied`,
`bounced`. El detalle de cada uno, de las secuencias, de la IA y de las métricas está en
[`docs/OPERACION.md`](docs/OPERACION.md).

## Cómo contribuir

Se agradecen los PR. El proyecto está en español —código, comentarios, mensajes de error y
documentación— y así se queda: es coherente con el mercado al que apunta.

```bash
cd backend
make install && make up && make migrate
make check          # ruff + mypy + pytest. Es exactamente lo que corre CI.
```

Lo que CI exige y no negocia: `ruff check`, `ruff format --check`, `mypy` (con
`disallow_untyped_defs`), los 567 tests, que las migraciones apliquen y reviertan en limpio
(`upgrade → downgrade base → upgrade`) y que no queden migraciones pendientes de generar
(`alembic check`).

Convenciones que ahorran una ronda de revisión:

- **Migraciones.** Cada modelo nuevo se importa en `app/models/__init__.py`. Lo que no esté ahí no
  existe para Alembic, y `--autogenerate` produce una migración que borra tablas. Revisa a mano lo
  generado: `drop_table` no elimina los tipos ENUM, hay que añadirlo al `downgrade`.
- **Errores.** `DomainError` y subclases en `app/core/exceptions.py`. Cada una lleva un `code`
  estable pensado para que el frontend haga `switch`; el `message` es para humanos y puede cambiar.
- **Logs.** `structlog`, eventos en `snake_case` (`job_started`, `email_sent`). Los correos y
  teléfonos se ofuscan solos y las claves sensibles se sustituyen por `***`.
- **Tests.** Contra Postgres real, no SQLite: esto depende de ENUM nativos, JSONB, índices
  parciales y `pg_trgm`. Cada test corre en una transacción que se revierte al terminar.
- **Nada inventado.** Si un dato no se puede atribuir a una fuente —el sitio web, la ficha de
  Maps, una señal detectada—, no se guarda ni se le enseña al usuario. Esa regla es la que hace
  que un correo generado no diga una mentira sobre el negocio del destinatario.

**Buenas primeras tareas**, de menor a mayor:

1. `Dockerfile` de la API y un `docker-compose` que levante todo de una vez.
2. Tests del frontend (no hay ninguno) — Vitest para `lib/`, Playwright para el kanban.
3. Exportación a CSV de las métricas y del listado de prospectos.
4. Un segundo país: los defaults de Colombia ya salen de `app_settings`, faltan las validaciones.
5. `ArqQueue`: implementar `JobQueue` con ARQ + Redis. La abstracción ya está.
6. Autenticación de usuario y activar `owner_id` en las consultas. Es el cambio que abre la puerta
   a que otros lo usen en serio.
7. `WhatsAppAdapter` sobre `ChannelAdapter`, con plantillas pre-aprobadas y ventana de 24 h.

Antes de un PR grande, abre un issue y cuéntalo. El diseño completo, con las 15 decisiones de
arquitectura y su porqué, está en [`docs/ARQUITECTURA.md`](docs/ARQUITECTURA.md) — 2.485 líneas que
explican por qué casi todo está donde está.

## Documentación

| Documento | Qué contiene |
|---|---|
| [`docs/OPERACION.md`](docs/OPERACION.md) | Manual de uso: comandos, buzones, segmentos, secuencias, IA, métricas |
| [`docs/ARQUITECTURA.md`](docs/ARQUITECTURA.md) | Diseño completo: decisiones, modelo de datos, flujos, seguridad |

---

<div align="center">

Proyecto independiente, sin afiliación con Google, Meta ni Microsoft.

Si te sirve, una ⭐ ayuda a que lo encuentre quien lo necesita.

</div>
