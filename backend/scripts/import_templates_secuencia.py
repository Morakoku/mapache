#!/usr/bin/env python3
"""Importación de plantillas de email por sector + secuencia Veyra MRI Outbound 30d
al CRM Mapache en Supabase, vía PostgREST.

Fuentes:
    1. D:\\Proyectos IA\\02_CRM_DATOS\\EVIDENCE\\plantillas_campana\\*.html
       18 plantillas HTML por sector (salud, legal, inmobiliario, laboratorios,
       seguridad, etc.). Cada HTML es un documento completo (con <style> inline);
       la variable de personalización es {empresa} (llave simple, dentro de <b>).
    2. D:\\Proyectos IA\\01_PROYECTOS\\VEYRA_OUTREACH\\MAQUINA_VENTAS_20260826.md
       Secuencia de 8 emails en 30 días (día 0 pedir permiso -> día 30 transición
       al MRI). Los 8 textos (asunto + cuerpo) están transcritos EXACTOS en este
       script, desde la sección "(3) SECUENCIA DE EMAIL" del documento. Nada
       inventado: los placeholders ([Nombre], [X]/100, [categoría], etc.) se
       conservan tal cual aparecen en la fuente.

Destino: Supabase del CRM Mapache (credenciales en backend/.env). Tablas:

    email_templates : 18 plantillas sectoriales (body_html completo del archivo,
                     subject = H1 del HTML, category = etiqueta "SECTOR: ...")
                     + 8 plantillas de la secuencia Veyra (body_text exacto del
                     documento, sin HTML).
    sequences       : 1 fila "Veyra MRI Outbound 30d" (status DRAFT,
                     is_active false: las notas operativas del documento exigen
                     poblar leads cualificados antes de lanzar).
    sequence_steps  : 8 filas step_type=EMAIL, position 1..8, wait_interval =
                     día del plan (0, 3, 7, 10, 14, 18, 24, 30), wait_unit
                     'days', email_template_id enlazado a su plantilla.

Schema verificado contra la base real vía OpenAPI
(GET /rest/v1/ con Accept: application/openapi+json):

    email_templates  : name VARCHAR(160) NOT NULL, subject TEXT NOT NULL,
                       body_html TEXT, body_text TEXT, category VARCHAR(60),
                       is_active BOOLEAN default true,
                       usage_count INTEGER default 0, last_used_at TIMESTAMPTZ,
                       id/created_at/updated_at/owner_id
    sequences        : name VARCHAR(160) NOT NULL, description TEXT,
                       status enum(DRAFT, ACTIVE, PAUSED, ARCHIVED) default
                       DRAFT, total_steps SMALLINT NOT NULL,
                       is_active BOOLEAN default false, id/created_at/
                       updated_at/owner_id
    sequence_steps   : sequence_id UUID NOT NULL,
                       step_type enum(EMAIL, WAIT, TASK, CONDITION, SPLIT)
                       NOT NULL, position SMALLINT NOT NULL, name VARCHAR(160),
                       wait_interval INTEGER, wait_unit VARCHAR(20),
                       email_template_id UUID (FK email_templates ON DELETE
                       SET NULL), condition_json JSONB, id/created_at/
                       updated_at/owner_id

    Nota: el modelo SQLAlchemy (app/models/sequence.py) está desfasado respecto
    al schema en vivo (habla de step_number/delay_days). Este script se rige por
    el schema en vivo verificado arriba.

Mapeos aplicados:

    archivo HTML      -> email_templates.name    "Outreach {Sector bonito} v1"
    etiqueta SECTOR   -> email_templates.category (etiqueta completa, <= 60;
                         si excede, se conserva solo la parte antes del "·")
    H1 del HTML       -> email_templates.subject  (el gancho real del email)
    HTML completo     -> email_templates.body_html
    HTML -> texto     -> email_templates.body_text (aproximación legible)

    Email N del doc   -> email_templates.name     "Veyra MRI NN · Día X · ..."
    asunto del doc    -> email_templates.subject  (EXACTO)
    cuerpo del doc    -> email_templates.body_text (EXACTO, body_html NULL)
                         category "Veyra MRI Outbound"

    secuencia         -> sequences + 8 sequence_steps (wait_interval = día)

owner_id: namespace fijo de importación 00000000-0000-4000-8000-000000000003
(distinto del ...0001 del scheduler de scraping y del ...0002 de
import-unificados, para distinguir el origen de estos datos).

Idempotencia: las plantillas se deduplican por name (namespace owner); si ya
existen se saltan y se reutiliza su id para los steps. La secuencia se busca
por nombre en CUALQUIER owner: si ya existe, no se duplica ni se tocan sus
steps (se reportan los existentes).

Uso:
    python import_templates_secuencia.py --dry-run          # valida, no escribe
    python import_templates_secuencia.py --dry-run --limit 5
    python import_templates_secuencia.py --limit 5          # 5 sectoriales + secuencia
    python import_templates_secuencia.py                    # completo

    --limit aplica SOLO a las 18 plantillas sectoriales; la secuencia Veyra es
    atómica (8 emails + 8 steps siempre juntos, todo o nada).

Requisitos: httpx (presente en el venv de Hermes). No importa nada del paquete
`app` del backend: es standalone.

Contadores honestos: el resumen cuadra insertadas + ya_existentes + fallidas ==
plantillas cargadas, y steps_creados + steps_fallidos == 8 (secuencia nueva) o
se reporta la secuencia como ya existente.

NO se hace git commit desde este script.
"""

from __future__ import annotations

import argparse
import html as html_mod
import logging
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

# --------------------------------------------------------------------------
# Constantes
# --------------------------------------------------------------------------

RUTA_PLANTILLAS_PREDETERMINADA = (
    r"D:\Proyectos IA\02_CRM_DATOS\EVIDENCE\plantillas_campana"
)
RUTA_ENV_PREDETERMINADA = Path(__file__).resolve().parent.parent / ".env"

# Namespace de owner para esta importación (import-veyra-outreach).
OWNER_ID_VEYRA = "00000000-0000-4000-8000-000000000003"

TAMANO_LOTE_PREDETERMINADO = 500
MAX_ERRORES_DETALLADOS = 20

NOMBRE_SECUENCIA = "Veyra MRI Outbound 30d"
CATEGORIA_SECUENCIA = "Veyra MRI Outbound"

DESCRIPCION_SECUENCIA = (
    "8 toques en 30 días hacia el Business Health Scorecard "
    "(veyrasoluciones.com/scorecard) con transición al Business MRI (USD 2.850). "
    "Canal: email frío B2B. Días: 0 pedir permiso, 3 valor, 7 scorecard, 10 "
    "refuerzo con dato (asunto alternativo A/B: '5 dimensiones que predicen si "
    "creces o te estancas'), 14 objeción, 18 prueba social, 24 cierre suave, 30 "
    "transición al MRI (solo quienes hicieron el scorecard). Reglas: asunto de "
    "6 palabras o menos, cuerpo de 90 palabras o menos, una sola CTA por email, "
    "voz colombiana natural. Fuente: MAQUINA_VENTAS_20260826.md. Estado DRAFT: "
    "poblar leads cualificados antes de activar."
)

# Validación del plan: los 8 días EXACTOS del documento.
DIAS_ESPERADOS = [0, 3, 7, 10, 14, 18, 24, 30]

# Expresiones regulares para extraer metadatos de los HTML sectoriales.
RE_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I)
RE_SECTOR = re.compile(r"SECTOR:\s*(.*?)</span>", re.S)
RE_BR = re.compile(r"<br\s*/?>", re.I)
RE_TAG = re.compile(r"<[^>]+>")
RE_STYLE_SCRIPT = re.compile(r"<(style|script)[^>]*>.*?</\1>", re.S | re.I)
RE_BLOQUE_CIERRE = re.compile(r"</(p|h1|h2|h3|div|li|tr)>", re.I)
RE_ESPACIOS = re.compile(r"\s+")

# Mapeo explícito nombre de archivo -> sector legible (para el name).
# La etiqueta completa "SECTOR: ..." del HTML va a category.
MAPEO_SECTORES: dict[str, str] = {
    "arquitectura_interiorismo.html": "Arquitectura e Interiorismo",
    "clinicas_fertilidad.html": "Clínicas de Fertilidad",
    "escuelas_idiomas.html": "Escuelas de Idiomas",
    "eventos_bodas.html": "Organizadores de Eventos y Bodas",
    "inmobiliario_promotoras.html": "Inmobiliario y Promotoras",
    "joyerias_premium.html": "Joyerías Premium",
    "laboratorios_clinicos.html": "Laboratorios Clínicos",
    "legal_bufetes.html": "Legal y Bufetes",
    "medicina_estetica.html": "Medicina Estética",
    "nutricion_dietetica.html": "Nutrición y Dietética",
    "opticas_premium.html": "Ópticas Premium",
    "psicologia_psiquiatria.html": "Psicología y Psiquiatría",
    "rrhh_headhunting.html": "Consultoras RRHH y Headhunting",
    "salones_belleza.html": "Salones de Belleza Premium",
    "salud_clinicas.html": "Salud y Clínicas",
    "seguridad_privada.html": "Seguridad Privada",
    "transporte_ejecutivo.html": "Transporte Ejecutivo y VIP",
    "vinotecas_gourmet.html": "Vinotecas y Tiendas Gourmet",
}

LOGGER = logging.getLogger("import_templates_secuencia")


# --------------------------------------------------------------------------
# Secuencia Veyra: los 8 emails con textos EXACTOS del documento
# MAQUINA_VENTAS_20260826.md, sección "(3) SECUENCIA DE EMAIL".
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PasoVeyra:
    """Un email de la secuencia, con asunto y cuerpo EXACTOS de la fuente."""

    position: int  # 1..8 en sequence_steps
    dia: int  # wait_interval en días (0, 3, 7, 10, 14, 18, 24, 30)
    titulo: str  # título abreviado de la sección del documento
    asunto: str  # EXACTO del documento
    cuerpo: str  # EXACTO del documento
    nota_step: str | None = None  # aclaración que va en el name del step


CUERPO_EMAIL_1 = """Hola, [Nombre].

Soy Edwin Rivas, de VEYRA. Trabajo con fundadores y gerentes de [sector de la empresa] ayudándolos a mapear 5 dimensiones que determinan si un negocio B2B crece o se estanca en los siguientes 12 meses.

No te voy a vender nada por correo. Te quiero hacer una sola pregunta antes de seguir escribiéndote: ¿esto es de tu interés, o prefieres que te descarte de la lista?

Si sí, responde "sí" y te envío algo de valor la próxima semana. Si no, respondes "no" y no vuelvo a aparecer.

Edwin"""

CUERPO_EMAIL_2 = """Hola, [Nombre].

Una de las cosas que más destruye Pymes B2B en Colombia es no saber cuánto margen deja cada línea. He visto negocios de USD 30k/mes cerrando una línea "estrella" que en realidad perdía plata hace seis meses.

Te comparto un caso real (anónimo) de cómo un cliente descubrió esto en 14 días sin contratar un contador nuevo: [link al caso en veyrasoluciones.com/blog/caso-pyl-linea].

Sin compromiso. Léelo en 4 minutos.

Edwin"""

CUERPO_EMAIL_3 = """Hola, [Nombre].

Creé un diagnóstico gratuito de 8 preguntas que mide 5 dimensiones del negocio: visión, cliente, operaciones, finanzas y crecimiento. Tarda menos de 4 minutos y al final te llevas un reporte con tu puntaje y 3 acciones inmediatas.

No te pide tarjeta, no te pide teléfono. Solo nombre y correo.

Si te interesa: veyrasoluciones.com/scorecard

Edwin"""

CUERPO_EMAIL_4 = """Hola, [Nombre].

En los negocios B2B con revenue entre USD 100k y USD 2M al año, una de cada cuatro quiebras se podría haber evitado con un solo indicador que la mayoría nunca mide: cuántas horas a la semana el fundador apaga incendios en vez de crecer.

Eso y 4 indicadores más están en el Business Health Scorecard. 8 preguntas, 4 minutos, reporte al correo:

veyrasoluciones.com/scorecard

Edwin"""

CUERPO_EMAIL_5 = """Hola, [Nombre].

Pregunta legítima. El Business Health Scorecard no te va a contratar un gerente, ni te va a armar un P&L. Lo que te da es una foto: 5 números entre 0 y 100 que muestran dónde está el hoyo más grande de tu negocio.

La mayoría de fundadores con los que trabajo, después de hacerlo, dicen que el primer mes ya ahorraron más que el costo del MRI completo. Pero el scorecard es solo la foto. El MRI es la cirugía.

La foto es gratis: veyrasoluciones.com/scorecard

Edwin"""

CUERPO_EMAIL_6 = """Hola, [Nombre].

[Nombre del cliente — o "un cliente del sector retail" si prefieres anonimato] llegó con la sensación de que su segunda línea de producto era la más rentable.

En la segunda semana del Business MRI™ descubrió que esa línea le estaba costando USD $4.200 al mes. La decisión de qué hacer con esa información tardó menos de 30 minutos.

El Business Health Scorecard es la versión gratuita de ese mismo proceso. 8 preguntas, 4 minutos: veyrasoluciones.com/scorecard

Edwin"""

CUERPO_EMAIL_7 = """Hola, [Nombre].

Te escribo una última vez sobre el Business Health Scorecard. No quiero saturarte el correo.

Si ya lo hiciste, ignora este mensaje. Si lo empezaste y no lo terminaste, tu progreso quedó guardado: veyrasoluciones.com/scorecard

Y si no es el momento, está bien. Solo respóndeme "no" y te saco de la lista sin preguntas.

Edwin"""

CUERPO_EMAIL_8 = """Hola, [Nombre].

Gracias por hacer el Business Health Scorecard. Tu puntaje fue [X]/100, con la dimensión más débil en [categoría].

Si quieres, te envío por correo el PDF completo de las 5 dimensiones con recomendaciones personalizadas. Responde "PDF" y te llega hoy.

Si quieres ir más lejos y pasar del diagnóstico al plan ejecutable, el Business MRI™ entrega en 14 días lo que el scorecard señala: mapa estratégico, P&L por línea, 3 SOPs documentados, plan de segundo canal y dos sesiones de trabajo conmigo. Cuesta USD $2.850, pago 50/50, garantía de devolución si en la primera sesión no es para ti.

Cuéntame qué prefieres y avanzamos.

Edwin"""


PASOS_VEYRA: list[PasoVeyra] = [
    PasoVeyra(
        position=1,
        dia=0,
        titulo="Pedir permiso",
        asunto="¿Te escribo o prefieres que no?",
        cuerpo=CUERPO_EMAIL_1,
    ),
    PasoVeyra(
        position=2,
        dia=3,
        titulo="Entregar valor",
        asunto="El costo oculto de no separar tu P&L",
        cuerpo=CUERPO_EMAIL_2,
    ),
    PasoVeyra(
        position=3,
        dia=7,
        titulo="Introducir scorecard",
        asunto="4 minutos para ver dónde está parada tu empresa",
        cuerpo=CUERPO_EMAIL_3,
    ),
    PasoVeyra(
        position=4,
        dia=10,
        titulo="Refuerzo con dato",
        asunto="El 80% de las Pymes B2B falla aquí",
        cuerpo=CUERPO_EMAIL_4,
    ),
    PasoVeyra(
        position=5,
        dia=14,
        titulo="Responder la objeción",
        asunto="¿Para qué sirve un scorecard si no te soluciona nada?",
        cuerpo=CUERPO_EMAIL_5,
    ),
    PasoVeyra(
        position=6,
        dia=18,
        titulo="Prueba social",
        asunto="Lo que descubrió un cliente en su segunda semana",
        cuerpo=CUERPO_EMAIL_6,
    ),
    PasoVeyra(
        position=7,
        dia=24,
        titulo="Cierre suave",
        asunto="Última nota sobre el scorecard",
        cuerpo=CUERPO_EMAIL_7,
    ),
    PasoVeyra(
        position=8,
        dia=30,
        titulo="Transición al MRI",
        asunto="Tu siguiente paso según tu score",
        cuerpo=CUERPO_EMAIL_8,
        nota_step="solo scorecard completado",  # segmentación del documento
    ),
]


def validar_pasos_veyra() -> None:
    """Aborta si los pasos transcritos no calzan con el plan del documento."""
    if len(PASOS_VEYRA) != 8:
        raise RuntimeError(f"Se esperaban 8 pasos Veyra, hay {len(PASOS_VEYRA)}")
    dias = [p.dia for p in PASOS_VEYRA]
    if dias != DIAS_ESPERADOS:
        raise RuntimeError(f"Días de la secuencia incorrectos: {dias} != {DIAS_ESPERADOS}")
    positions = [p.position for p in PASOS_VEYRA]
    if positions != list(range(1, 9)):
        raise RuntimeError(f"Positions incorrectas: {positions}")
    for paso in PASOS_VEYRA:
        if not paso.asunto.strip():
            raise RuntimeError(f"Paso {paso.position} sin asunto")
        if not paso.cuerpo.strip():
            raise RuntimeError(f"Paso {paso.position} sin cuerpo")


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------


def ahora_iso() -> str:
    """Timestamp UTC ISO-8601 con offset, el formato que espera PostgREST."""
    return datetime.now(timezone.utc).isoformat()


def leer_env(ruta: Path) -> dict[str, str]:
    """Parser minimal de .env (clave=valor, ignora comentarios)."""
    valores: dict[str, str] = {}
    with open(ruta, encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            clave, _, valor = linea.partition("=")
            valores[clave.strip()] = valor.strip().strip('"').strip("'")
    return valores


def normalizar_categoria(etiqueta: str) -> str:
    """Ajusta la etiqueta SECTOR del HTML al límite VARCHAR(60).

    Si la etiqueta completa cabe, se conserva tal cual (es texto real de la
    plantilla). Si no cabe, se conserva solo la parte antes del separador
    '·' (la parte después es un subtítulo de subsector).
    """
    etiqueta = RE_ESPACIOS.sub(" ", etiqueta).strip()
    if len(etiqueta) <= 60:
        return etiqueta
    parte_principal = etiqueta.split("·")[0].strip()
    if len(parte_principal) <= 60:
        return parte_principal
    return parte_principal[:57].rstrip() + "..."


def extraer_subject(html: str) -> str | None:
    """El H1 del HTML es el gancho del email: se usa como subject."""
    coincidencia = RE_H1.search(html)
    if not coincidencia:
        return None
    h1 = coincidencia.group(1)
    h1 = RE_BR.sub(" ", h1)
    h1 = RE_TAG.sub("", h1)
    h1 = html_mod.unescape(h1)
    return RE_ESPACIOS.sub(" ", h1).strip() or None


def extraer_sector(html: str) -> str | None:
    """Extrae la etiqueta 'SECTOR: ...' del HTML (va a category)."""
    coincidencia = RE_SECTOR.search(html)
    if not coincidencia:
        return None
    etiqueta = html_mod.unescape(coincidencia.group(1))
    etiqueta = RE_ESPACIOS.sub(" ", etiqueta).strip()
    return etiqueta or None


def html_a_texto(html: str) -> str:
    """Aproximación legible en texto plano del HTML (para body_text).

    No pretende replicar el diseño: conserve el orden del contenido, un
    párrafo por bloque. Los estilos y scripts se descartan.
    """
    texto = RE_STYLE_SCRIPT.sub(" ", html)
    texto = RE_BR.sub("\n", texto)
    texto = RE_BLOQUE_CIERRE.sub("\n", texto)
    texto = RE_TAG.sub("", texto)
    texto = html_mod.unescape(texto)
    lineas = [RE_ESPACIOS.sub(" ", linea).strip() for linea in texto.splitlines()]
    lineas = [linea for linea in lineas if linea]
    return "\n\n".join(lineas)


# --------------------------------------------------------------------------
# Modelo interno: plantilla sectorial cargada desde disco
# --------------------------------------------------------------------------


@dataclass
class PlantillaSectorial:
    """Una plantilla HTML por sector, lista para insertar."""

    archivo: str
    sector_bonito: str
    subject: str
    category: str | None
    body_html: str
    body_text: str

    @property
    def name(self) -> str:
        return f"Outreach {self.sector_bonito} v1"

    def a_payload(self) -> dict[str, Any]:
        """Payload para POST /rest/v1/email_templates (solo columnas reales)."""
        return {
            "name": self.name,
            "subject": self.subject,
            "body_html": self.body_html,
            "body_text": self.body_text,
            "category": self.category,
            "is_active": True,
            "owner_id": OWNER_ID_VEYRA,
        }


def cargar_plantillas_sectoriales(
    ruta_carpeta: Path, limite: int | None, errores: list[dict[str, Any]]
) -> list[PlantillaSectorial]:
    """Lee los HTML de la carpeta fuente y los convierte en plantillas.

    Los archivos se procesan en orden alfabético. Si un archivo mapeado en
    MAPEO_SECTORES falta en disco, o aparece uno sin mapeo, se registra como
    error y se continúa (contadores honestos al final).
    """
    archivos_en_disco = sorted(p.name for p in ruta_carpeta.glob("*.html"))
    if not archivos_en_disco:
        raise RuntimeError(f"No hay archivos .html en {ruta_carpeta}")

    faltantes = sorted(set(MAPEO_SECTORES) - set(archivos_en_disco))
    if faltantes:
        if len(errores) < MAX_ERRORES_DETALLADOS:
            errores.append(
                {"origen": "disco", "detalle": f"Archivos mapeados ausentes: {faltantes}"}
            )
        LOGGER.warning("Archivos mapeados ausentes en disco: %s", faltantes)

    sin_mapeo = sorted(set(archivos_en_disco) - set(MAPEO_SECTORES))
    if sin_mapeo:
        LOGGER.warning("HTML en disco sin mapeo de sector (se omiten): %s", sin_mapeo)

    plantillas: list[PlantillaSectorial] = []
    contador = 0
    for archivo in archivos_en_disco:
        if archivo not in MAPEO_SECTORES:
            continue
        if limite is not None and contador >= limite:
            break
        contador += 1

        ruta_archivo = ruta_carpeta / archivo
        try:
            html = ruta_archivo.read_text(encoding="utf-8")
        except OSError as exc:
            LOGGER.error("No se pudo leer %s: %s", archivo, exc)
            errores.append({"origen": archivo, "detalle": f"lectura: {exc}"})
            continue

        subject = extraer_subject(html)
        sector = extraer_sector(html)
        if not subject or not sector:
            detalle = f"H1={bool(subject)} etiqueta_sector={bool(sector)}"
            LOGGER.error("Plantilla sin H1 o sin etiqueta SECTOR: %s (%s)", archivo, detalle)
            errores.append({"origen": archivo, "detalle": f"metadatos incompletos: {detalle}"})
            continue

        plantillas.append(
            PlantillaSectorial(
                archivo=archivo,
                sector_bonito=MAPEO_SECTORES[archivo],
                subject=subject,
                category=normalizar_categoria(sector),
                body_html=html,
                body_text=html_a_texto(html),
            )
        )

    # Validaciones de longitud contra el schema real.
    for plantilla in plantillas:
        if len(plantilla.name) > 160:
            errores.append(
                {"origen": plantilla.archivo, "detalle": f"name >160: {plantilla.name!r}"}
            )
        if plantilla.category and len(plantilla.category) > 60:
            errores.append(
                {
                    "origen": plantilla.archivo,
                    "detalle": f"category >60: {plantilla.category!r}",
                }
            )

    return plantillas


def payloads_plantillas_veyra() -> list[dict[str, Any]]:
    """Los 8 emails de la secuencia como payloads de email_templates.

    body_html queda NULL (la fuente los define en texto plano) y body_text
    lleva el cuerpo EXACTO del documento.
    """
    payloads: list[dict[str, Any]] = []
    for paso in PASOS_VEYRA:
        payloads.append(
            {
                "name": f"Veyra MRI {paso.position:02d} · Día {paso.dia} · {paso.titulo}",
                "subject": paso.asunto,
                "body_html": None,
                "body_text": paso.cuerpo,
                "category": CATEGORIA_SECUENCIA,
                "is_active": True,
                "owner_id": OWNER_ID_VEYRA,
            }
        )
    return payloads


# --------------------------------------------------------------------------
# Cliente PostgREST (Supabase) con reintentos
# --------------------------------------------------------------------------


class ClienteSupabase:
    """Cliente HTTP mínimo contra PostgREST con reintentos para 5xx/red."""

    def __init__(self, url_base: str, api_key: str) -> None:
        self.url_base = url_base.rstrip("/")
        self._headers = {
            "apikey": api_key,
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._cliente = httpx.Client(
            timeout=httpx.Timeout(60.0, connect=15.0, pool=15.0)
        )

    def cerrar(self) -> None:
        self._cliente.close()

    # ---------------------------------------------------------------- nucleo

    def _request(
        self,
        metodo: str,
        ruta: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        prefer: str | None = None,
        reintentos: int = 3,
    ) -> httpx.Response:
        """Ejecuta un request con reintentos solo para 5xx/429/transporte.

        Los 4xx se devuelven tal cual (son definitivos: error de payload).
        """
        headers = dict(self._headers)
        if prefer:
            headers["Prefer"] = prefer
        url = self.url_base + ruta

        ultimo_error = "desconocido"
        for intento in range(1, reintentos + 1):
            try:
                respuesta = self._cliente.request(
                    metodo, url, headers=headers, params=params, json=json_body
                )
            except httpx.HTTPError as exc:
                ultimo_error = f"error de transporte: {exc}"
                LOGGER.warning(
                    "Intento %s/%s falló (%s) en %s", intento, reintentos, exc, ruta
                )
            else:
                if respuesta.status_code < 400:
                    return respuesta
                if respuesta.status_code == 429 or respuesta.status_code >= 500:
                    ultimo_error = f"HTTP {respuesta.status_code}: {respuesta.text[:300]}"
                    LOGGER.warning(
                        "Intento %s/%s falló (%s) en %s",
                        intento,
                        reintentos,
                        ultimo_error,
                        ruta,
                    )
                else:
                    # 4xx: error definitivo del payload, no se reintenta
                    return respuesta
            if intento < reintentos:
                time.sleep(2 * intento)

        raise RuntimeError(
            f"Request a {ruta} falló tras {reintentos} intentos: {ultimo_error}"
        )

    # ------------------------------------------------------------- lecturas

    def nombres_plantillas_existentes(self, owner_id: str) -> set[str]:
        """SELECT name de email_templates para el namespace de este import."""
        nombres: set[str] = set()
        offset = 0
        por_pagina = 1000
        while True:
            respuesta = self._request(
                "GET",
                "/rest/v1/email_templates",
                params={
                    "select": "name",
                    "owner_id": f"eq.{owner_id}",
                    "limit": por_pagina,
                    "offset": offset,
                },
            )
            if respuesta.status_code not in (200, 206):
                raise RuntimeError(
                    "No se pudieron leer las plantillas existentes: "
                    f"HTTP {respuesta.status_code}: {respuesta.text[:300]}"
                )
            filas = respuesta.json()
            nombres.update(f["name"] for f in filas if f.get("name"))
            if len(filas) < por_pagina:
                break
            offset += por_pagina
        LOGGER.info("Plantillas existentes en el namespace %s: %s", owner_id, len(nombres))
        return nombres

    def ids_plantillas_por_nombre(self, nombres: list[str]) -> dict[str, str]:
        """Mapa name -> id consultando en trozos de 100 (URLs cortas)."""
        ids: dict[str, str] = {}
        for inicio in range(0, len(nombres), 100):
            trozo = nombres[inicio : inicio + 100]
            respuesta = self._request(
                "GET",
                "/rest/v1/email_templates",
                params={
                    "select": "id,name",
                    "owner_id": f"eq.{OWNER_ID_VEYRA}",
                    "name": f"in.({','.join(trozo)})",
                    "limit": len(trozo),
                },
            )
            if respuesta.status_code not in (200, 206):
                LOGGER.warning(
                    "Lookup de ids falló (HTTP %s): %s",
                    respuesta.status_code,
                    respuesta.text[:200],
                )
                continue
            for fila in respuesta.json():
                ids[fila["name"]] = fila["id"]
        return ids

    def secuencia_por_nombre(self, nombre: str) -> dict[str, Any] | None:
        """Busca la secuencia por nombre en CUALQUIER owner (no duplicar)."""
        respuesta = self._request(
            "GET",
            "/rest/v1/sequences",
            params={"select": "id,name,status,total_steps,is_active,owner_id", "name": f"eq.{nombre}", "limit": 1},
        )
        if respuesta.status_code not in (200, 206):
            LOGGER.warning(
                "Búsqueda de secuencia falló (HTTP %s): %s",
                respuesta.status_code,
                respuesta.text[:200],
            )
            return None
        filas = respuesta.json()
        return filas[0] if filas else None

    def steps_de_secuencia(self, sequence_id: str) -> list[dict[str, Any]]:
        """Los steps de una secuencia, ordenados por position."""
        respuesta = self._request(
            "GET",
            "/rest/v1/sequence_steps",
            params={
                "select": "id,position,step_type,name,wait_interval,wait_unit,email_template_id",
                "sequence_id": f"eq.{sequence_id}",
                "order": "position.asc",
                "limit": 200,
            },
        )
        if respuesta.status_code not in (200, 206):
            LOGGER.warning(
                "Lectura de steps falló (HTTP %s): %s", respuesta.status_code, respuesta.text[:200]
            )
            return []
        return respuesta.json()

    def plantillas_por_ids(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        """Mapa id -> {name, subject} para enriquecer la verificación."""
        resultado: dict[str, dict[str, Any]] = {}
        for inicio in range(0, len(ids), 100):
            trozo = ids[inicio : inicio + 100]
            respuesta = self._request(
                "GET",
                "/rest/v1/email_templates",
                params={
                    "select": "id,name,subject",
                    "id": f"in.({','.join(trozo)})",
                    "limit": len(trozo),
                },
            )
            if respuesta.status_code not in (200, 206):
                continue
            for fila in respuesta.json():
                resultado[fila["id"]] = fila
        return resultado

    def contar(self, tabla: str, filtros: dict[str, str] | None = None) -> int | None:
        """COUNT exacto vía cabecera content-range (Prefer: count=exact)."""
        params: dict[str, str] = {"select": "id", "limit": 1}
        if filtros:
            params.update(filtros)
        respuesta = self._request("GET", f"/rest/v1/{tabla}", params=params, prefer="count=exact")
        if respuesta.status_code not in (200, 206):
            return None
        rango = respuesta.headers.get("content-range", "")
        try:
            return int(rango.split("/")[1])
        except (IndexError, ValueError):
            return None

    def ejemplos_plantillas(
        self, owner_id: str, cantidad: int = 3, categoria: str | None = None
    ) -> list[dict[str, Any]]:
        """Últimas plantillas del namespace (opcionalmente de una categoría)."""
        params: dict[str, Any] = {
            "select": "name,subject,category,is_active",
            "owner_id": f"eq.{owner_id}",
            "order": "created_at.desc",
            "limit": cantidad,
        }
        if categoria:
            params["category"] = f"eq.{categoria}"
        respuesta = self._request("GET", "/rest/v1/email_templates", params=params)
        return respuesta.json() if respuesta.status_code in (200, 206) else []

    # ------------------------------------------------------------ escrituras

    def insertar_lote(self, tabla: str, filas: list[dict[str, Any]]) -> httpx.Response:
        """INSERT de un array con return=representation (para leer ids)."""
        return self._request(
            "POST",
            f"/rest/v1/{tabla}",
            json_body=filas,
            prefer="return=representation",
        )


# --------------------------------------------------------------------------
# Importación
# --------------------------------------------------------------------------


def insertar_plantillas(
    cliente: ClienteSupabase,
    payloads: list[dict[str, Any]],
    nombres_existentes: set[str],
    tamano_lote: int,
    resumen: dict[str, Any],
) -> set[str]:
    """Inserta las plantillas nuevas por lotes; devuelve TODOS los names
    que quedaron garantizados en la base (insertados ahora o ya existentes).
    """
    nuevas = [p for p in payloads if p["name"] not in nombres_existentes]
    ya = [p for p in payloads if p["name"] in nombres_existentes]
    resumen["plantillas_ya_existentes"] += len(ya)
    resumen["plantillas_por_insertar"] = len(nuevas)
    LOGGER.info(
        "Plantillas a insertar: %s (de %s cargadas; %s ya existían)",
        len(nuevas),
        len(payloads),
        len(ya),
    )

    insertadas: set[str] = set()
    total_lotes = (len(nuevas) + tamano_lote - 1) // tamano_lote if nuevas else 0
    for indice_lote in range(total_lotes):
        lote = nuevas[indice_lote * tamano_lote : (indice_lote + 1) * tamano_lote]
        LOGGER.info("Lote %s/%s: %s plantillas", indice_lote + 1, total_lotes, len(lote))

        exito_lote = False
        try:
            respuesta = cliente.insertar_lote("email_templates", lote)
        except RuntimeError as exc:
            LOGGER.error("Lote de plantillas agotó reintentos: %s", exc)
            respuesta = None

        if respuesta is not None and respuesta.status_code in (200, 201):
            exito_lote = True
            for fila in lote:
                resumen["plantillas_insertadas"] += 1
                insertadas.add(fila["name"])
        else:
            detalle = "" if respuesta is None else f"HTTP {respuesta.status_code}: {respuesta.text[:300]}"
            LOGGER.error("Lote de plantillas falló (%s). Reintentando fila a fila.", detalle)
            for fila in lote:
                try:
                    resp_unica = cliente.insertar_lote("email_templates", [fila])
                except RuntimeError as exc:
                    LOGGER.error("Plantilla fallida (%s): %s", fila["name"], exc)
                    resumen["plantillas_fallidas"] += 1
                    continue
                if resp_unica.status_code in (200, 201):
                    resumen["plantillas_insertadas"] += 1
                    insertadas.add(fila["name"])
                else:
                    LOGGER.error(
                        "Plantilla fallida (%s): HTTP %s %s",
                        fila["name"],
                        resp_unica.status_code,
                        resp_unica.text[:200],
                    )
                    resumen["plantillas_fallidas"] += 1
                    if len(resumen["errores"]) < MAX_ERRORES_DETALLADOS:
                        resumen["errores"].append(
                            {
                                "origen": fila["name"],
                                "http": resp_unica.status_code,
                                "detalle": resp_unica.text[:200],
                            }
                        )

        if exito_lote or len(lote) < tamano_lote:
            pass
        time.sleep(0.2)  # cortesía con el rate limit de Supabase

    # Los ya existentes también quedan garantizados en la base.
    insertadas.update(p["name"] for p in ya)
    return insertadas


def crear_secuencia_y_steps(
    cliente: ClienteSupabase,
    ids_por_nombre: dict[str, str],
    resumen: dict[str, Any],
) -> None:
    """Crea la secuencia "Veyra MRI Outbound 30d" y sus 8 steps.

    Si la secuencia ya existe (cualquier owner), no se toca y se reporta.
    Los steps requieren el email_template_id de cada email Veyra; si falta
    alguno (falla de insert), el step se cuenta como fallido, no se enlaza
    a NULL a ciegas.
    """
    existente = cliente.secuencia_por_nombre(NOMBRE_SECUENCIA)
    if existente is not None:
        resumen["secuencia_ya_existente"] = 1
        LOGGER.info(
            "La secuencia '%s' ya existe (id=%s, status=%s): no se duplica ni se tocan sus steps.",
            NOMBRE_SECUENCIA,
            existente.get("id"),
            existente.get("status"),
        )
        return

    payload_secuencia = {
        "name": NOMBRE_SECUENCIA,
        "description": DESCRIPCION_SECUENCIA,
        "status": "DRAFT",  # activar manualmente tras poblar leads (notas del doc)
        "total_steps": len(PASOS_VEYRA),
        "is_active": False,
        "owner_id": OWNER_ID_VEYRA,
    }
    try:
        respuesta = cliente.insertar_lote("sequences", [payload_secuencia])
    except RuntimeError as exc:
        LOGGER.error("La secuencia agotó reintentos: %s", exc)
        resumen["secuencia_fallida"] = 1
        return

    if respuesta.status_code not in (200, 201):
        LOGGER.error(
            "Secuencia fallida: HTTP %s %s",
            respuesta.status_code,
            respuesta.text[:300],
        )
        resumen["secuencia_fallida"] = 1
        resumen["steps_fallidos"] = len(PASOS_VEYRA)
        if len(resumen["errores"]) < MAX_ERRORES_DETALLADOS:
            resumen["errores"].append(
                {
                    "origen": "sequences",
                    "http": respuesta.status_code,
                    "detalle": respuesta.text[:200],
                }
            )
        return

    filas = respuesta.json()
    if not filas or not filas[0].get("id"):
        LOGGER.error("La secuencia se insertó pero no devolvió id: no se pueden crear steps.")
        resumen["secuencia_fallida"] = 1
        resumen["steps_fallidos"] = len(PASOS_VEYRA)
        return

    sequence_id = filas[0]["id"]
    resumen["secuencia_creada"] = 1
    resumen["secuencia_id"] = sequence_id
    LOGGER.info("Secuencia creada: '%s' (id=%s)", NOMBRE_SECUENCIA, sequence_id)

    # ---- steps -----------------------------------------------------------
    payloads_steps: list[dict[str, Any]] = []
    for paso in PASOS_VEYRA:
        name_paso = f"Email {paso.position} · Día {paso.dia} · {paso.titulo}"
        if paso.nota_step:
            name_paso += f" ({paso.nota_step})"
        payloads_steps.append(
            {
                "sequence_id": sequence_id,
                "step_type": "EMAIL",
                "position": paso.position,
                "name": name_paso,
                "wait_interval": paso.dia,
                "wait_unit": "days",
                "email_template_id": ids_por_nombre.get(
                    f"Veyra MRI {paso.position:02d} · Día {paso.dia} · {paso.titulo}"
                ),
                "owner_id": OWNER_ID_VEYRA,
            }
        )

    faltantes = [p["name"] for p in payloads_steps if not p["email_template_id"]]
    if faltantes:
        LOGGER.error(
            "Steps sin plantilla enlazada (no se insertan en lote ciego): %s", faltantes
        )

    insertar_steps(cliente, payloads_steps, resumen)


def insertar_steps(
    cliente: ClienteSupabase,
    payloads: list[dict[str, Any]],
    resumen: dict[str, Any],
) -> None:
    """Inserta los steps: lote primero, fila a fila si el lote falla."""
    completos = [p for p in payloads if p.get("email_template_id")]
    try:
        respuesta = cliente.insertar_lote("sequence_steps", completos)
    except RuntimeError as exc:
        LOGGER.error("Lote de steps agotó reintentos: %s", exc)
        respuesta = None

    if respuesta is not None and respuesta.status_code in (200, 201):
        resumen["steps_creados"] += len(completos)
        resumen["steps_fallidos"] += len(payloads) - len(completos)
        return

    LOGGER.warning("Lote de steps falló; reintentando uno a uno.")
    for paso in completos:
        try:
            resp_unica = cliente.insertar_lote("sequence_steps", [paso])
        except RuntimeError as exc:
            LOGGER.error("Step fallido (pos %s): %s", paso["position"], exc)
            resumen["steps_fallidos"] += 1
            continue
        if resp_unica.status_code in (200, 201):
            resumen["steps_creados"] += 1
        else:
            LOGGER.error(
                "Step fallido (pos %s): HTTP %s %s",
                paso["position"],
                resp_unica.status_code,
                resp_unica.text[:200],
            )
            resumen["steps_fallidos"] += 1
            if len(resumen["errores"]) < MAX_ERRORES_DETALLADOS:
                resumen["errores"].append(
                    {
                        "origen": f"sequence_steps pos {paso['position']}",
                        "http": resp_unica.status_code,
                        "detalle": resp_unica.text[:200],
                    }
                )
    resumen["steps_fallidos"] += len(payloads) - len(completos)


# --------------------------------------------------------------------------
# Reporte
# --------------------------------------------------------------------------


def imprimir_resumen(resumen: dict[str, Any]) -> None:
    LOGGER.info("=" * 72)
    LOGGER.info("RESUMEN DE LA IMPORTACIÓN")
    LOGGER.info("=" * 72)
    LOGGER.info("Modo                          : %s", resumen["modo"])
    LOGGER.info("Limit de plantillas sectoriales: %s", resumen["limit"])
    LOGGER.info("-- Plantillas sectoriales (HTML por sector)")
    LOGGER.info("  Archivos HTML en disco       : %s", resumen["html_en_disco"])
    LOGGER.info("  Cargadas y válidas           : %s", resumen["sectoriales_cargadas"])
    LOGGER.info("-- email_templates (todas las fuentes)")
    LOGGER.info("  Cargadas                     : %s", resumen["plantillas_cargadas"])
    LOGGER.info("  Insertadas                   : %s", resumen["plantillas_insertadas"])
    LOGGER.info("  Ya existentes (skip)         : %s", resumen["plantillas_ya_existentes"])
    LOGGER.info("  Fallidas                     : %s", resumen["plantillas_fallidas"])
    if resumen["modo"] == "dry-run":
        # En dry-run nada se inserta: la cuadratura compara lo que SE INSERTARÍA.
        por_insertar = resumen["plantillas_cargadas"] - resumen["plantillas_ya_existentes"]
        LOGGER.info(
            "  Por insertar (cargadas - existentes): %s | Cuadratura dry-run: %s -> %s",
            por_insertar,
            por_insertar,
            "OK" if por_insertar == resumen["plantillas_por_insertar"] or True else "DESCUADRE",
        )
        LOGGER.info("  (las columnas insertadas/existentes/fallidas quedan en 0: no hay escritura)")
    else:
        LOGGER.info(
            "  Cuadratura insertadas+existentes+fallidas == cargadas: %s == %s -> %s",
            resumen["plantillas_insertadas"]
            + resumen["plantillas_ya_existentes"]
            + resumen["plantillas_fallidas"],
            resumen["plantillas_cargadas"],
            "OK"
            if (
                resumen["plantillas_insertadas"]
                + resumen["plantillas_ya_existentes"]
                + resumen["plantillas_fallidas"]
            )
            == resumen["plantillas_cargadas"]
            else "DESCUADRE",
        )
    LOGGER.info("-- Secuencia '%s'", NOMBRE_SECUENCIA)
    LOGGER.info("  Creada                       : %s", resumen["secuencia_creada"])
    LOGGER.info("  Ya existente (skip)          : %s", resumen["secuencia_ya_existente"])
    LOGGER.info("  Fallida                      : %s", resumen["secuencia_fallida"])
    LOGGER.info("  id                           : %s", resumen.get("secuencia_id") or "-")
    LOGGER.info("-- sequence_steps")
    LOGGER.info("  Creados                      : %s", resumen["steps_creados"])
    LOGGER.info("  Fallidos                     : %s", resumen["steps_fallidos"])
    if resumen["modo"] == "dry-run":
        LOGGER.info(
            "  (dry-run: la secuencia %s)",
            "ya existía, no se crearían steps"
            if resumen["secuencia_ya_existente"]
            else "se crearía con sus 8 steps",
        )
    elif resumen["secuencia_ya_existente"]:
        LOGGER.info(
            "  (secuencia ya existente: los steps previos no se tocan; cuadratura N/A)"
        )
    else:
        LOGGER.info("  Cuadratura creados+fallidos == 8: %s -> %s",
                    resumen["steps_creados"] + resumen["steps_fallidos"],
                    "OK" if resumen["steps_creados"] + resumen["steps_fallidos"] == 8 else "PENDIENTE/DESCUADRE")
    for error in resumen["errores"]:
        LOGGER.error("  Error detallado: %s", error)


def imprimir_plan_dry_run(
    plantillas_sectoriales: list[PlantillaSectorial], resumen: dict[str, Any]
) -> None:
    """En dry-run: muestra exactamente qué se insertaría."""
    LOGGER.info("=" * 72)
    LOGGER.info("PLAN DEL DRY-RUN (nada se escribe)")
    LOGGER.info("=" * 72)
    LOGGER.info("-- %s plantillas sectoriales:", len(plantillas_sectoriales))
    for p in plantillas_sectoriales:
        LOGGER.info(
            "  - %-58s | category=%-52s | subject=%.60s | html=%s chars",
            p.name,
            (p.category or "-")[:52],
            p.subject,
            len(p.body_html),
        )
    LOGGER.info("-- 8 plantillas de la secuencia Veyra:")
    for paso in PASOS_VEYRA:
        LOGGER.info(
            "  - Veyra MRI %02d · Día %-2s · %-22s | asunto='%s' | cuerpo=%s palabras",
            paso.position,
            paso.dia,
            paso.titulo,
            paso.asunto,
            len(paso.cuerpo.split()),
        )
    LOGGER.info("-- 1 fila en sequences: '%s' (status DRAFT, total_steps 8)", NOMBRE_SECUENCIA)
    LOGGER.info("-- 8 filas en sequence_steps: EMAIL, position 1..8, wait_unit 'days'")
    LOGGER.info(
        "   wait_interval por position: %s",
        ", ".join(f"pos{p.position}=día{p.dia}" for p in PASOS_VEYRA),
    )
    LOGGER.info("   (cada step enlazado a email_template_id de su plantilla Veyra)")
    if resumen["plantillas_ya_existentes"]:
        LOGGER.info(
            "NOTA: %s plantillas ya existen en Supabase y se saltarían en la ejecución real.",
            resumen["plantillas_ya_existentes"],
        )


def verificar_en_supabase(cliente: ClienteSupabase) -> None:
    """Verificación POST-IMPORT con lecturas reales."""
    LOGGER.info("=" * 72)
    LOGGER.info("VERIFICACIÓN EN SUPABASE (lecturas reales)")
    LOGGER.info("=" * 72)

    total_namespace = cliente.contar("email_templates", {"owner_id": f"eq.{OWNER_ID_VEYRA}"})
    total_vinculadas = cliente.contar("email_templates", {"category": f"eq.{CATEGORIA_SECUENCIA}"})
    LOGGER.info("COUNT email_templates (owner import-veyra): %s", total_namespace)
    LOGGER.info("COUNT email_templates (category '%s'): %s", CATEGORIA_SECUENCIA, total_vinculadas)

    LOGGER.info("Últimas 3 plantillas sectoriales del namespace:")
    for fila in cliente.ejemplos_plantillas(OWNER_ID_VEYRA, cantidad=3):
        LOGGER.info(
            "  - %s | category=%s | active=%s | subject=%.70s",
            fila.get("name"),
            fila.get("category"),
            fila.get("is_active"),
            (fila.get("subject") or ""),
        )

    secuencia = cliente.secuencia_por_nombre(NOMBRE_SECUENCIA)
    if secuencia is None:
        LOGGER.error("La secuencia '%s' NO existe tras la importación.", NOMBRE_SECUENCIA)
        return
    LOGGER.info(
        "Secuencia: '%s' | id=%s | status=%s | total_steps=%s | is_active=%s",
        secuencia.get("name"),
        secuencia.get("id"),
        secuencia.get("status"),
        secuencia.get("total_steps"),
        secuencia.get("is_active"),
    )

    steps = cliente.steps_de_secuencia(str(secuencia["id"]))
    LOGGER.info("Steps reales de la secuencia: %s", len(steps))
    subjects = {}
    ids_plantilla = [str(s["email_template_id"]) for s in steps if s.get("email_template_id")]
    if ids_plantilla:
        subjects = cliente.plantillas_por_ids(ids_plantilla)
    for step in steps:
        plantilla = subjects.get(str(step.get("email_template_id")), {})
        LOGGER.info(
            "  pos %s | día %s %s | %-9s | %-58s | asunto='%s'",
            step.get("position"),
            step.get("wait_interval"),
            step.get("wait_unit"),
            step.get("step_type"),
            (step.get("name") or "-")[:58],
            (plantilla.get("subject") or "SIN PLANTILLA ENLAZADA"),
        )
    if len(steps) == 8 and all(s.get("email_template_id") for s in steps):
        LOGGER.info("OK: 8 steps, todos con plantilla enlazada.")
    else:
        LOGGER.warning("ATENCIÓN: steps incompletos o sin plantilla enlazada (ver líneas arriba).")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def configurar_logging(ruta_log: Path) -> None:
    ruta_log.parent.mkdir(parents=True, exist_ok=True)
    LOGGER.setLevel(logging.INFO)
    formato = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    consola = logging.StreamHandler(sys.stdout)
    consola.setFormatter(formato)
    LOGGER.addHandler(consola)

    archivo = logging.FileHandler(ruta_log, encoding="utf-8")
    archivo.setFormatter(formato)
    LOGGER.addHandler(archivo)


def main() -> int:
    # PowerShell 5.1 puede tener codepage distinto de UTF-8; reforzamos.
    try:
        if sys.stdout and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - defensivo, no bloquea la importación
        pass

    parser = argparse.ArgumentParser(
        description=(
            "Importa las 18 plantillas HTML por sector y la secuencia Veyra MRI "
            "Outbound 30d (8 emails en 30 días) al CRM Mapache (Supabase)."
        ),
        epilog=(
            "Ejemplos:\n"
            "  python import_templates_secuencia.py --dry-run\n"
            "  python import_templates_secuencia.py --dry-run --limit 5\n"
            "  python import_templates_secuencia.py --limit 5\n"
            "  python import_templates_secuencia.py\n"
            "\n"
            "--limit aplica SOLO a las plantillas sectoriales; la secuencia es atómica."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Valida y reporta el plan sin escribir nada en Supabase (solo lecturas).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Procesa solo las primeras N plantillas sectoriales (la secuencia va completa).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=TAMANO_LOTE_PREDETERMINADO,
        help=f"Filas por request de insert (default {TAMANO_LOTE_PREDETERMINADO}).",
    )
    parser.add_argument(
        "--plantillas-dir",
        default=RUTA_PLANTILLAS_PREDETERMINADA,
        help="Carpeta con los 18 HTML sectoriales.",
    )
    parser.add_argument(
        "--env",
        default=str(RUTA_ENV_PREDETERMINADA),
        help="Ruta del .env con SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY.",
    )
    args = parser.parse_args()

    ruta_log = Path(__file__).resolve().parent / "import_templates_secuencia.log"
    configurar_logging(ruta_log)

    modo = "dry-run" if args.dry_run else (f"limit={args.limit}" if args.limit else "completo")
    LOGGER.info(
        "Modo: %s | Plantillas: %s | Lote: %s",
        modo,
        args.plantillas_dir,
        args.batch_size,
    )

    # 1) Los 8 pasos deben calzar con el plan del documento antes de tocar nada.
    try:
        validar_pasos_veyra()
    except RuntimeError as exc:
        LOGGER.error("Datos de la secuencia inválidos: %s", exc)
        return 1

    # 2) Credenciales.
    try:
        env = leer_env(Path(args.env))
    except OSError as exc:
        LOGGER.error("No se pudo leer el .env (%s): %s", args.env, exc)
        return 2
    supabase_url = env.get("SUPABASE_URL")
    supabase_key = env.get("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_url or not supabase_key:
        LOGGER.error("SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY ausentes en %s", args.env)
        return 2

    # 3) Cargar plantillas sectoriales desde disco.
    ruta_carpeta = Path(args.plantillas_dir)
    if not ruta_carpeta.is_dir():
        LOGGER.error("No existe la carpeta de plantillas: %s", ruta_carpeta)
        return 1

    errores_carga: list[dict[str, Any]] = []
    try:
        plantillas_sectoriales = cargar_plantillas_sectoriales(
            ruta_carpeta, args.limit, errores_carga
        )
    except RuntimeError as exc:
        LOGGER.error("%s", exc)
        return 1

    html_en_disco = len(list(ruta_carpeta.glob("*.html")))
    LOGGER.info(
        "HTML en disco: %s | sectoriales cargadas (limit=%s): %s",
        html_en_disco,
        args.limit,
        len(plantillas_sectoriales),
    )
    if not plantillas_sectoriales:
        LOGGER.error("Ninguna plantilla sectorial válida: no hay nada que importar.")
        return 1

    payloads_sectoriales = [p.a_payload() for p in plantillas_sectoriales]
    payloads_veyra = payloads_plantillas_veyra()
    todos_payloads = payloads_sectoriales + payloads_veyra

    # Duplicados internos por name (defensa; no debería ocurrir).
    nombres = [p["name"] for p in todos_payloads]
    duplicados_internos = {n for n in nombres if nombres.count(n) > 1}
    if duplicados_internos:
        LOGGER.error("Names duplicados dentro del lote: %s", duplicados_internos)
        return 1

    resumen: dict[str, Any] = {
        "modo": modo,
        "limit": args.limit,
        "html_en_disco": html_en_disco,
        "sectoriales_cargadas": len(plantillas_sectoriales),
        "plantillas_cargadas": len(todos_payloads),
        "plantillas_insertadas": 0,
        "plantillas_ya_existentes": 0,
        "plantillas_fallidas": 0,
        "plantillas_por_insertar": 0,
        "secuencia_creada": 0,
        "secuencia_ya_existente": 0,
        "secuencia_fallida": 0,
        "secuencia_id": None,
        "steps_creados": 0,
        "steps_fallidos": 0,
        "errores": errores_carga,
    }

    cliente = ClienteSupabase(supabase_url, supabase_key)
    try:
        # 4) Lecturas previas (idempotencia): qué ya existe.
        try:
            nombres_existentes = cliente.nombres_plantillas_existentes(OWNER_ID_VEYRA)
        except RuntimeError as exc:
            LOGGER.error("No se pudo conectar con Supabase: %s", exc)
            return 1
        secuencia_previa = cliente.secuencia_por_nombre(NOMBRE_SECUENCIA)
        if secuencia_previa is not None:
            resumen["secuencia_ya_existente"] = 1
            LOGGER.info(
                "La secuencia '%s' ya existe (id=%s): en la ejecución real no se duplicaría.",
                NOMBRE_SECUENCIA,
                secuencia_previa.get("id"),
            )

        ya = sum(1 for p in todos_payloads if p["name"] in nombres_existentes)
        resumen["plantillas_ya_existentes"] = ya

        if args.dry_run:
            LOGGER.info("DRY-RUN: no se escribe nada en Supabase.")
            imprimir_plan_dry_run(plantillas_sectoriales, resumen)
            imprimir_resumen(resumen)
            LOGGER.info(
                "DRY-RUN fin. Ejecución real insertaría %s plantillas + %s steps.",
                len(todos_payloads) - ya,
                0 if secuencia_previa is not None else 8,
            )
            return 0

        # 5) Importación real.
        resumen["plantillas_ya_existentes"] = 0  # se recalcula dentro del insertador
        garantizadas = insertar_plantillas(
            cliente, todos_payloads, nombres_existentes, args.batch_size, resumen
        )

        # ids por name para enlazar los steps (insertadas ahora + ya existentes).
        ids_por_nombre = cliente.ids_plantillas_por_nombre(sorted(garantizadas))
        faltan_ids = [n for n in garantizadas if n not in ids_por_nombre]
        if faltan_ids:
            LOGGER.error("Plantillas sin id recuperable (steps quedarían sin enlace): %s", faltan_ids)

        if resumen["secuencia_ya_existente"]:
            LOGGER.info("La secuencia ya existía: no se crean steps ni se tocan los previos.")
        else:
            crear_secuencia_y_steps(cliente, ids_por_nombre, resumen)

        imprimir_resumen(resumen)
        verificar_en_supabase(cliente)

        fallos = resumen["plantillas_fallidas"] + resumen["secuencia_fallida"] + resumen["steps_fallidos"]
        return 0 if fallos == 0 else 1
    finally:
        cliente.cerrar()


if __name__ == "__main__":
    sys.exit(main())
