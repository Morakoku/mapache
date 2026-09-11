"""Importacion del estado de contacto de leads calificados al CRM Mapache.

Fuentes (D:\\Proyectos IA\\03_DATOS\\CRM_DATASETS):
    1. outreach_status.json      - 856 leads calificados con su estado de
                                   contacto (7 CONTACTADO por WhatsApp, 849
                                   PENDIENTE). Cada lead se identifica por la
                                   llave "nombre::ciudad" en minusculas.
    2. LEADS_UNIFICADOS_20260831.csv - los 66 leads "contactado" (campana
                                   CVELIZ) y 8 "SCHEDULED_FOR_DISPATCH" (cola
                                   Hermes del 2026-08-24) que NO aparecen en
                                   el outreach_status.json (overlap 0,
                                   verificado).
    3. outreach_audit_events.jsonl  - registro de auditoria: 854 de los 856
                                   recibieron EMAIL de Hermes el 24-25/08 y el
                                   operador los reseteo a PENDIENTE (tanda
                                   warm-up Resend a corregir). Se conserva como
                                   metadata historica en la actividad del lead.

Destino: Supabase del CRM Mapache, tabla `leads` (+ `activities` NOTE por lead
creado). Las `companies` existentes SOLO SE LEEN para el match (mandato
explicito: no tocar las 8.552 importadas).

Schemas reales verificados contra la BD (OpenAPI + probes):
    leads          : company_id/stage_id/contact_id nullable (FK), status enum
                    {NEW, CONTACTED, QUALIFIED, PROPOSAL, NEGOTIATION, WON,
                    LOST}, source enum {REFERRAL, WEBSITE, COLD_OUTREACH,
                    EVENT, OTHER} con default OTHER, priority NOT NULL sin
                    default, owner_id nullable. NO existe service_id en la BD
                    desplegada (el repo local esta adelantado).
    pipeline_stages: stage_type enum de 14 valores, todas las columnas NOT
                    NULL excepto owner_id; estaba VACIA -> se crean seeds.
    services       : no es FK de leads en el schema real -> NO se crean seeds.
    sequences / sequence_steps: no participan en este import.

Mapeo de estados:
    7  CONTACTADO del JSON (handoff WhatsApp 2026-08-25 23:20 UTC)
        -> status CONTACTED, stage contacted, priority 4, first_contact_at,
           next_followup_note "WhatsApp ya usado - no recontacto en frio",
           actividad NOTE con metadata del canal.
    66 contactado del CSV (campana CVELIZ, sin fecha de contacto)
        -> status CONTACTED, stage contacted, priority 3.
    8  SCHEDULED_FOR_DISPATCH del CSV (cola Hermes top-25 del 24/08)
        -> status NEW (aun no contactados), stage new, priority 5,
           next_followup_note documentando la cola de despacho.
    849 PENDIENTE del JSON
        -> status NEW, stage new, priority 2. Los que tienen evento de email
           previo en el audit llevan la fecha historica del email en la
           metadata de la actividad (el reset del operador invalido ese
           contacto, por eso NO se marca first_contact_at).

Match con companies: clave dedupe_key = slug(nombre)[:31] + '-' +
slug(municipio)[:8] (misma formula de import_leads_unificados.py que creo las
8.552 companies). Fallback: slug de nombre a secas cuando es unico y la
ciudad de la company no contradice la del lead.

Idempotencia: en re-ejecucion no se duplican leads (un lead por company);
los ya existentes con source=COLD_OUTREACH se actualizan si difiere algo y
los de otro source no se tocan (protegidos).

Uso:
    python import_leads_estado.py --dry-run --limit 50
    python import_leads_estado.py --dry-run
    python import_leads_estado.py --limit 50
    python import_leads_estado.py

Contadores honestos: la cuadratura final creados + actualizados + ya_iguales
+ protegidos + sin_company + fallidos == registros procesados.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

# --------------------------------------------------------------------------
# Constantes
# --------------------------------------------------------------------------

RUTA_OUTREACH_JSON = r"D:\Proyectos IA\03_DATOS\CRM_DATASETS\outreach_status.json"
RUTA_CSV_UNIFICADOS = r"D:\Proyectos IA\03_DATOS\CRM_DATASETS\LEADS_UNIFICADOS_20260831.csv"
RUTA_AUDIT_JSONL = r"D:\Proyectos IA\03_DATOS\CRM_DATASETS\outreach_audit_events.jsonl"
RUTA_ENV_PREDETERMINADA = Path(__file__).resolve().parent.parent / ".env"

# Owner de importacion (mismo namespace que las 8.552 companies importadas).
OWNER_ID_IMPORTACION = "00000000-0000-4000-8000-000000000002"

# El schema real no tiene service_id; el origen del lead se marca con source.
SOURCE_LEAD = "COLD_OUTREACH"

TAMANO_LOTE_PREDETERMINADO = 500
MAX_ERRORES_DETALLADOS = 20
POR_PAGINA = 1000

RE_ESPACIOS = re.compile(r"\s+")
RE_PUNTUACION = re.compile(r"[^\w\s]", flags=re.UNICODE)

# Nota estandar para los 7 contactados por WhatsApp (no recontacto en frio).
NOTA_NO_WHATSAPP = "WhatsApp ya usado - no recontacto en frio por WhatsApp"

# Seeds de pipeline_stages (la tabla estaba VACIA en Supabase). stage_key,
# nombre, tipo (enum real de 14 valores), posicion, color y flags. Se crean
# SOLO las 7 etapas del embudo solicitado: NEW->CONTACTED->QUALIFIED->
# PROPOSAL->(NEGOTIATION)->WON/LOST.
STAGES_SEED: list[dict[str, Any]] = [
    {"stage_key": "new", "name": "Nuevo", "stage_type": "NEW",
     "position": 1, "color": "#64748b", "is_default": True},
    {"stage_key": "contacted", "name": "Contactado", "stage_type": "CONTACTED",
     "position": 2, "color": "#3b82f6", "is_default": False},
    {"stage_key": "qualified", "name": "Calificado", "stage_type": "QUALIFIED",
     "position": 3, "color": "#8b5cf6", "is_default": False},
    {"stage_key": "proposal", "name": "Propuesta", "stage_type": "PROPOSAL",
     "position": 4, "color": "#f59e0b", "is_default": False},
    {"stage_key": "negotiation", "name": "Negociacion", "stage_type": "NEGOTIATION",
     "position": 5, "color": "#f97316", "is_default": False},
    {"stage_key": "won", "name": "Ganado", "stage_type": "WON",
     "position": 6, "color": "#22c55e", "is_default": False, "es_won": True},
    {"stage_key": "lost", "name": "Perdido", "stage_type": "LOST",
     "position": 7, "color": "#ef4444", "is_default": False, "es_lost": True},
]

# Los 7 contactados por WhatsApp segun CRM_HANDOFF_SESSION_STATE.md: canal y
# sector reportados por el operador. La llave es el lead_key EXACTO del JSON.
INFO_HANDOFF_7: dict[str, dict[str, str]] = {
    "clínica pink, cirugía plástica::bucaramanga": {
        "canal": "+573142669037", "sector": "Clínica Cirugía Plástica y Estética",
        "destino": "GUAKI"},
    "cime centro integral de medicina estética::bucaramanga": {
        "canal": "+573187068768", "sector": "Clínica Cirugía Plástica y Estética",
        "destino": "GUAKI"},
    "vani clinic sede chico::bogotá": {
        "canal": "+573012945398", "sector": "Medicina Estética No Quirúrgica",
        "destino": "GUAKI"},
    "lcda. ninoska rodríguez | nutrición clínica y oncológica::medellín": {
        "canal": "+573052597545", "sector": "Nutrición y Dietética Clínica",
        "destino": "GUAKI"},
    "abogados en medellín - jurismacs - centro de conciliación, arbitraje y amigable composición itagüí::medellín": {
        "canal": "+576044483155", "sector": "Firma Legal y Consultoría Jurídica",
        "destino": "GUAKI"},
    "psicólogos bogotá cuerpo, arte y palabra - terapias de psicología clínica (psicoterapia individual. terapia de pareja en bogotá. psicología para niños y adolescentes)::caracas": {
        "canal": "+583219465830", "sector": "Clínica Psicología y Salud Mental",
        "destino": "VEYRA"},
    "aston medical ips sas / examenes ocupacionales y crc (licencia de conducción)::barquisimeto": {
        "canal": "+583214627228", "sector": "Clínica Médica y Cirugía",
        "destino": "VEYRA"},
}

# Fecha de despacho programada de la cola Hermes top-25 (para metadata).
FECHA_COLA_HERMES = "2026-08-24T20:03:41+00:00"

# Ciudades venezolanas del ecosistema binacional (en formato slug, sin
# tildes) para inferir el pais de las companies minimas creadas para los
# no-matched: el JSON de outreach es binacional pero solo trae la ciudad.
CIUDADES_VENEZUELA = {
    "caracas", "maracaibo", "valencia", "maracay", "barquisimeto", "lecheria",
    "puerto-la-cruz", "puerto-ordaz", "puerto-la-cruz", "maturin", "merida",
    "ciudad-bolivar", "cumana", "la-guaira", "san-cristobal", "barcelona",
    "punto-fijo", "el-tigre", "los-teques", "guatire", "guarenas", "petare",
}

# Validacion de email para las companies minimas (mismo criterio que el
# import de companies).
RE_EMAIL_VALIDO = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

# Rutas de los datasets externos con datos de contacto para enriquecer las
# companies minimas de los no-matched (csv manual de WhatsApp, datasets
# permanentes GUAKI/VEYRA/DUAL y la cola de Hermes).
RUTA_CSV_MANUAL = (
    r"D:\Proyectos IA\03_DATOS\CRM_DATASETS\06_EXPORTACIONES_CSV_EXCEL"
    r"\LEADS_WHATSAPP_TRABAJO_MANUAL.csv"
)
RUTAS_DATASETS_EXTERNOS = [
    r"D:\Proyectos IA\03_DATOS\CRM_DATASETS\01_GUAKI_OPORTUNIDADES_WEB\guaki_todos_los_leads.json",
    r"D:\Proyectos IA\03_DATOS\CRM_DATASETS\02_VEYRA_OPORTUNIDADES_B2B\veyra_todos_los_leads.json",
    r"D:\Proyectos IA\03_DATOS\CRM_DATASETS\03_DUAL_DOBLE_OPORTUNIDAD\dual_todos_los_leads.json",
]
RUTA_HERMES_QUEUE = (
    r"D:\Proyectos IA\03_DATOS\CRM_DATASETS\04_HERMES_CAMPAÑAS_OUTREACH"
    r"\hermes-campaign-queue.json"
)

# Score de calidad para las companies minimas: claramente inferior al 50 de
# las importadas del CSV unificado, para distinguirlas en el CRM.
SCORE_COMPANY_MINIMA = 20

LOGGER = logging.getLogger("import_leads_estado")


# --------------------------------------------------------------------------
# Utilidades de texto
# --------------------------------------------------------------------------

def quitar_tildes(valor: str) -> str:
    """Quita tildes y diacriticos ('Medellin' desde 'Medellín')."""
    descompuesto = unicodedata.normalize("NFD", valor)
    return "".join(ch for ch in descompuesto if unicodedata.category(ch) != "Mn")


def slugificar(valor: str) -> str:
    """Slug ASCII en minusculas separado por guiones (como el backend)."""
    limpio = RE_PUNTUACION.sub(" ", quitar_tildes(valor).lower())
    return RE_ESPACIOS.sub("-", limpio.strip()).strip("-")


def clave_dedupe(nombre: str, municipio: str) -> str:
    """slug(nombre)[:31] + '-' + slug(municipio)[:8]; misma formula del import
    de companies para que el match sea exacto."""
    parte_nombre = slugificar(nombre)[:31]
    parte_ciudad = slugificar(municipio)[:8] if municipio else ""
    if parte_ciudad:
        return f"{parte_nombre}-{parte_ciudad}"
    return parte_nombre


def clave_nombre(nombre: str) -> str:
    """Slug del nombre recortado a 31 (indice de fallback por nombre)."""
    return slugificar(nombre)[:31]


def parsear_ts_utc(texto: str | None) -> str | None:
    """Convierte '2026-08-25 23:20:00 UTC' a ISO-8601 con offset UTC."""
    if not texto:
        return None
    limpio = texto.strip().replace(" UTC", "").replace(" ", "T")
    try:
        dt = datetime.fromisoformat(limpio)
    except ValueError:
        LOGGER.warning("Timestamp ilegible, se ignora: %r", texto)
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


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


# --------------------------------------------------------------------------
# Cliente PostgREST (Supabase) con reintentos
# --------------------------------------------------------------------------

class ClienteSupabase:
    """Cliente HTTP minimo contra PostgREST con reintentos para 5xx/red."""

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
                LOGGER.warning("Intento %s/%s fallo (%s) en %s", intento, reintentos, exc, ruta)
            else:
                if respuesta.status_code < 400:
                    return respuesta
                if respuesta.status_code == 429 or respuesta.status_code >= 500:
                    ultimo_error = f"HTTP {respuesta.status_code}: {respuesta.text[:300]}"
                    LOGGER.warning(
                        "Intento %s/%s fallo (%s) en %s",
                        intento, reintentos, ultimo_error, ruta,
                    )
                else:
                    # 4xx: error definitivo del payload, no se reintenta
                    return respuesta
            if intento < reintentos:
                time.sleep(2 * intento)
        raise RuntimeError(f"Request a {ruta} fallo tras {reintentos} intentos: {ultimo_error}")

    # ------------------------------------------------------------- lecturas

    def seleccionar_paginado(
        self,
        tabla: str,
        select: str,
        filtros: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """Pagina un SELECT completo de la tabla (paginas de 1000)."""
        filas: list[dict[str, Any]] = []
        offset = 0
        while True:
            params: dict[str, Any] = {"select": select, "limit": POR_PAGINA, "offset": offset}
            if filtros:
                params.update(filtros)
            respuesta = self._request("GET", f"/rest/v1/{tabla}", params=params)
            if respuesta.status_code not in (200, 206):
                raise RuntimeError(
                    f"GET {tabla} fallo: HTTP {respuesta.status_code}: "
                    f"{respuesta.text[:300]}"
                )
            lote = respuesta.json()
            filas.extend(lote)
            if len(lote) < POR_PAGINA:
                break
            offset += POR_PAGINA
        return filas

    def contar(self, tabla: str, filtros: dict[str, str] | None = None) -> int:
        """COUNT exacto via cabecera content-range (Prefer: count=exact)."""
        params: dict[str, Any] = {"select": "id", "limit": 1}
        if filtros:
            params.update(filtros)
        respuesta = self._request(
            "GET", f"/rest/v1/{tabla}", params=params, prefer="count=exact"
        )
        if respuesta.status_code not in (200, 206):
            return -1
        rango = respuesta.headers.get("content-range", "")
        try:
            return int(rango.split("/")[1])
        except (IndexError, ValueError):
            return -1

    # ------------------------------------------------------------ escrituras

    def insertar_lote(self, tabla: str, filas: list[dict[str, Any]]) -> httpx.Response:
        """INSERT de un array con return=representation (devuelve los ids)."""
        return self._request(
            "POST", f"/rest/v1/{tabla}", json_body=filas, prefer="return=representation"
        )

    def actualizar(
        self, tabla: str, filtros: dict[str, str], cambios: dict[str, Any]
    ) -> httpx.Response:
        """PATCH con filtros (p.ej. id=eq.xxx) y cambios parciales."""
        return self._request(
            "PATCH",
            f"/rest/v1/{tabla}",
            params=dict(filtros),
            json_body=cambios,
            prefer="return=representation",
        )


# --------------------------------------------------------------------------
# Modelo interno del import
# --------------------------------------------------------------------------

@dataclass
class LeadImportar:
    """Un lead resuelto listo para crear/actualizar en la tabla leads."""

    nombre: str
    ciudad: str
    clave: str                      # dedupe_key de la company esperada
    origen: str                     # etiqueta de la fuente
    status_lead: str                # enum real de leads.status
    stage_type: str                 # stage del embudo (NEW/CONTACTED/...)
    priority: int
    first_contact_at: str | None = None
    last_activity_at: str | None = None
    next_followup_note: str | None = None
    notas_operador: str | None = None
    canal: str | None = None        # WHATSAPP / EMAIL / None
    telefono_canal: str | None = None
    sector: str | None = None
    destino: str | None = None      # VEYRA / GUAKI / None
    fecha_contacto: str | None = None      # ISO del contacto real (si hay)
    audit_email_previo: dict[str, Any] | None = None  # evento Hermes 24-25/08
    fuente_dataset: str | None = None
    mensaje_len: int = 0
    company_id: str | None = None    # resuelto en la fase de match
    metodo_match: str | None = None  # exacto | nombre_unico | None
    lead_id: str | None = None      # id si ya existia en leads

    def payload_lead(self, stage_id: str) -> dict[str, Any]:
        """Payload para POST /rest/v1/leads (solo columnas del schema real)."""
        return {
            "company_id": self.company_id,
            "stage_id": stage_id,
            "status": self.status_lead,
            "source": SOURCE_LEAD,
            "priority": self.priority,
            "first_contact_at": self.first_contact_at,
            "last_activity_at": self.last_activity_at,
            "next_followup_note": self.next_followup_note,
            "owner_id": OWNER_ID_IMPORTACION,
        }

    def payload_actividad(self) -> dict[str, Any] | None:
        """Actividad NOTE con el contexto de outreach del lead."""
        metadata: dict[str, Any] = {
            "origen": self.origen,
            "canal": self.canal,
            "estado_fuente": self.status_lead,
        }
        if self.destino:
            metadata["destino"] = self.destino
        if self.sector:
            metadata["sector"] = self.sector
        if self.fuente_dataset:
            metadata["fuente_dataset"] = self.fuente_dataset
        if self.fecha_contacto:
            metadata["fecha_contacto"] = self.fecha_contacto

        if self.origen == "WHATSAPP_HANDOFF":
            # Los 7 del handoff: WhatsApp ya usado, no recontacto en frio.
            metadata["whatsapp_usado"] = True
            metadata["no_recontacto_frio_whatsapp"] = True
            metadata["telefono_canal"] = self.telefono_canal
            if self.notas_operador:
                metadata["notas_operador"] = self.notas_operador
            subject = "Contactado por WhatsApp (handoff operador)"
            body = (
                f"Contactado por WhatsApp el {self.fecha_contacto} "
                f"(canal {self.telefono_canal}). Sector: {self.sector}. "
                f"Destino: {self.destino}. No volver a contactar en frio "
                f"por WhatsApp."
            )
            if self.notas_operador:
                body += f" Notas del operador: {self.notas_operador}"
        elif self.origen == "CVELIZ_CONTACTADO":
            subject = "Contactado en campana previa (CVELIZ)"
            body = (
                "Lead importado como CONTACTED: figura como contactado en "
                "la unificacion de leads del 2026-08-31 (fuente "
                "CVELIZ_leads_db). Sin fecha de contacto conocida en la "
                "fuente: no se fija first_contact_at."
            )
        elif self.origen == "HERMES_SCHEDULED":
            subject = "Programado para despacho (cola Hermes)"
            metadata["programado"] = FECHA_COLA_HERMES
            body = (
                "Programado para la primera tanda de outreach de Hermes "
                "(2026-08-24, top 25 autorizado, fuente "
                "hermes_campaign_queue). Aun sin contacto confirmado: se "
                "importa como NEW con prioridad alta."
            )
        elif self.audit_email_previo:
            subject = "Lead calificado pendiente (email previo reseteado)"
            metadata["email_previo_hermes"] = self.audit_email_previo
            metadata["resetado_a_pendiente"] = True
            body = (
                "Lead calificado pendiente de primer contacto (PENDIENTE en "
                "outreach_status.json). Recibio un email de Hermes el "
                f"{self.audit_email_previo.get('fecha')} y el operador lo "
                "reseteo a PENDIENTE (tanda warm-up Resend a corregir): no "
                "cuenta como contacto valido."
            )
        else:
            subject = "Lead calificado pendiente de contacto"
            body = (
                "Lead calificado pendiente de primer contacto "
                "(PENDIENTE en outreach_status.json)."
            )

        return {
            "company_id": self.company_id,
            "activity_type": "NOTE",
            "actor_type": "SYSTEM",
            "subject": subject,
            "body": body,
            "metadata": metadata,
            "owner_id": OWNER_ID_IMPORTACION,
        }


# --------------------------------------------------------------------------
# Lectura de fuentes
# --------------------------------------------------------------------------

def leer_audit(ruta: str) -> dict[str, dict[str, Any]]:
    """lead_key -> primer evento CONTACTADO (fecha/canal/operador).

    Solo eventos con status_after=CONTACTADO; se conserva el primero por
    fecha. El lead 'test::test' del arranque se excluye.
    """
    auditoria: dict[str, dict[str, Any]] = {}
    with open(ruta, encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if not linea:
                continue
            try:
                evento = json.loads(linea)
            except json.JSONDecodeError:
                continue
            if evento.get("status_after") != "CONTACTADO":
                continue
            lead_key = evento.get("lead_key")
            if not lead_key or lead_key == "test::test":
                continue
            registro = {
                "fecha": evento.get("timestamp"),
                "fecha_iso": parsear_ts_utc(evento.get("timestamp")),
                "canal": evento.get("channel"),
                "operador": evento.get("operator"),
            }
            actual = auditoria.get(lead_key)
            if actual is None or (registro["fecha"] or "") < (actual["fecha"] or ""):
                auditoria[lead_key] = registro
    LOGGER.info("Audit: %s lead_keys con CONTACTADO registrado", len(auditoria))
    return auditoria


def leer_outreach_json(
    ruta: str, auditoria: dict[str, dict[str, Any]]
) -> list[LeadImportar]:
    """Lee outreach_status.json: 7 contactados por WhatsApp + 849 pendientes."""
    with open(ruta, encoding="utf-8") as f:
        data: dict[str, dict[str, Any]] = json.load(f)

    contactados: list[LeadImportar] = []
    pendientes: list[LeadImportar] = []
    for lead_key, info in data.items():
        try:
            nombre, ciudad = lead_key.rsplit("::", 1)
        except ValueError:
            LOGGER.warning("Llave sin '::' en el JSON, se salta: %r", lead_key)
            continue
        status_fuente = (info.get("status") or "").strip().upper()
        mensaje = info.get("custom_message") or ""
        notas = (info.get("notes") or "").strip() or None
        fecha_iso = parsear_ts_utc(info.get("contacted_at"))

        base = {
            "nombre": nombre,
            "ciudad": ciudad,
            "clave": clave_dedupe(nombre, ciudad),
            "notas_operador": notas,
            "mensaje_len": len(mensaje),
        }
        if status_fuente == "CONTACTADO":
            extra = INFO_HANDOFF_7.get(lead_key, {})
            contactados.append(LeadImportar(
                origen="WHATSAPP_HANDOFF",
                status_lead="CONTACTED",
                stage_type="CONTACTED",
                priority=4,
                first_contact_at=fecha_iso,
                last_activity_at=fecha_iso,
                next_followup_note=NOTA_NO_WHATSAPP,
                canal="WHATSAPP",
                telefono_canal=extra.get("canal"),
                sector=extra.get("sector"),
                destino=extra.get("destino"),
                fecha_contacto=fecha_iso,
                **base,
            ))
        else:
            audit = auditoria.get(lead_key)
            pendientes.append(LeadImportar(
                origen="JSON_PENDIENTE",
                status_lead="NEW",
                stage_type="NEW",
                priority=2,
                audit_email_previo=audit,
                **base,
            ))
    LOGGER.info(
        "outreach_status.json: %s contactados + %s pendientes",
        len(contactados), len(pendientes),
    )
    return contactados + pendientes


def leer_csv_unificados(ruta: str) -> list[LeadImportar]:
    """Extrae del CSV unificado los 66 'contactado' (CVELIZ) y los 8
    'SCHEDULED_FOR_DISPATCH' (cola Hermes): no estan en outreach_status.json
    y no deben perder su estado en el CRM."""
    contactados: list[LeadImportar] = []
    programados: list[LeadImportar] = []
    with open(ruta, encoding="utf-8-sig", newline="") as f:
        lector = csv.DictReader(f)
        for fila in lector:
            estado = (fila.get("estado") or "").strip()
            nombre = (fila.get("nombre") or "").strip()
            municipio = (fila.get("municipio") or "").strip()
            if not nombre:
                continue
            if estado == "contactado":
                contactados.append(LeadImportar(
                    nombre=nombre, ciudad=municipio,
                    clave=clave_dedupe(nombre, municipio),
                    origen="CVELIZ_CONTACTADO", status_lead="CONTACTED",
                    stage_type="CONTACTED", priority=3,
                    fuente_dataset=fila.get("fuente_dataset") or None,
                ))
            elif estado == "SCHEDULED_FOR_DISPATCH":
                programados.append(LeadImportar(
                    nombre=nombre, ciudad=municipio,
                    clave=clave_dedupe(nombre, municipio),
                    origen="HERMES_SCHEDULED", status_lead="NEW",
                    stage_type="NEW", priority=5,
                    fuente_dataset=fila.get("fuente_dataset") or None,
                ))
    LOGGER.info(
        "CSV unificados: %s contactado + %s scheduled_for_dispatch",
        len(contactados), len(programados),
    )
    return contactados + programados


# --------------------------------------------------------------------------
# Enriquecimiento externo para los no-matched
# --------------------------------------------------------------------------

@dataclass
class IndiceExterno:
    """Indice slug(nombre) -> datos de contacto de fuentes externas."""

    por_nombre: dict[str, dict[str, Any]] = field(default_factory=dict)


def _agregar_externo(indice: IndiceExterno, nombre: str, datos: dict[str, Any]) -> None:
    """Agrega datos de contacto externos si el nombre no estaba ya (gana el
    primero; el CSV manual es la fuente mas rica en telefonos)."""
    slug = slugificar(nombre or "")
    if slug and slug not in indice.por_nombre:
        indice.por_nombre[slug] = datos


def cargar_indice_externo() -> IndiceExterno:
    """Lee el CSV manual de WhatsApp, los datasets permanentes GUAKI/VEYRA/
    DUAL y la cola de Hermes: datos de contacto para enriquecer las companies
    minimas de los no-matched. Cada fuente es opcional (si falta, se salta)."""
    indice = IndiceExterno()

    # 1) CSV de trabajo manual por WhatsApp (el mas rico en telefonos).
    try:
        with open(RUTA_CSV_MANUAL, encoding="utf-8-sig", newline="") as f:
            for fila in csv.DictReader(f):
                _agregar_externo(indice, fila.get("company_name"), {
                    "city": (fila.get("city") or "").strip() or None,
                    "country": (fila.get("country") or "").strip() or None,
                    "sector": (fila.get("sector") or "").strip() or None,
                    "phone": (fila.get("phone") or "").strip() or None,
                    "whatsapp": (fila.get("whatsapp") or "").strip() or None,
                    "email": (fila.get("email") or "").strip() or None,
                    "website": (fila.get("website") or "").strip() or None,
                })
    except OSError as exc:
        LOGGER.warning("CSV manual no disponible (%s): %s", RUTA_CSV_MANUAL, exc)

    # 2) Datasets permanentes GUAKI/VEYRA/DUAL.
    for ruta in RUTAS_DATASETS_EXTERNOS:
        try:
            with open(ruta, encoding="utf-8") as f:
                for item in json.load(f):
                    _agregar_externo(indice, item.get("company_name"), {
                        "city": item.get("city"),
                        "country": item.get("country"),
                        "sector": item.get("sector"),
                        "phone": item.get("phone_clean"),
                        "whatsapp": item.get("whatsapp_clean"),
                        "email": item.get("email_clean"),
                    })
        except OSError as exc:
            LOGGER.warning("Dataset externo no disponible (%s): %s", ruta, exc)

    # 3) Cola de campana de Hermes (25 programados).
    try:
        with open(RUTA_HERMES_QUEUE, encoding="utf-8") as f:
            for item in json.load(f):
                _agregar_externo(indice, item.get("company_name"), {
                    "city": item.get("city"),
                    "country": item.get("country"),
                    "sector": item.get("sector"),
                })
    except OSError as exc:
        LOGGER.warning("Cola de Hermes no disponible (%s): %s", RUTA_HERMES_QUEUE, exc)

    LOGGER.info("Indice externo cargado: %s nombres", len(indice.por_nombre))
    return indice


def _inferir_pais(ciudad: str) -> str:
    """Infiere el pais por la ciudad (slug): el ecosistema es binacional
    Venezuela/Colombia y el JSON de outreach solo trae el nombre de ciudad."""
    if slugificar(ciudad) in CIUDADES_VENEZUELA:
        return "Venezuela"
    return "Colombia"


def _payload_company_minima(
    registro: LeadImportar, externo: dict[str, Any] | None
) -> dict[str, Any]:
    """Company minima para un lead calificado sin company en el CRM.

    Solo INSERT (no toca las existentes): name, dedupe_key con la misma
    formula del import original, pais inferido por ciudad y los datos de
    contacto que aporte el indice externo (si hay). data_quality_score=20
    la distingue de las importadas del CSV unificado (50).
    """
    telefono = None
    email = None
    if externo:
        telefono = (externo.get("phone") or externo.get("whatsapp") or "").strip() or None
        email = externo.get("email")
        if email and not RE_EMAIL_VALIDO.fullmatch(email):
            email = None
    # El pais del externo manda si es consistente; si no, se infiere.
    pais = None
    if externo and externo.get("country"):
        pais = externo["country"].strip() or None
    if not pais:
        pais = _inferir_pais(registro.ciudad)
    # La ciudad del externo manda si trae una; si no, la del lead con
    # capitalizacion limpia (el JSON llega en minusculas).
    ciudad = None
    if registro.ciudad:
        ciudad = ((externo or {}).get("city") or "").strip() or registro.ciudad.title()
    sector = (externo or {}).get("sector") or None
    return {
        "name": registro.nombre,
        "category": sector,
        "categories": [sector] if sector else [],
        "city": ciudad,
        "country": pais,
        "phone": telefono,
        "email": email,
        "data_quality_score": SCORE_COMPANY_MINIMA,
        "dedupe_key": registro.clave,
        "owner_id": OWNER_ID_IMPORTACION,
        "is_permanently_closed": False,
        "first_extracted_at": datetime.now(timezone.utc).isoformat(),
        "last_extracted_at": datetime.now(timezone.utc).isoformat(),
    }


def crear_companies_minimas(
    cliente: ClienteSupabase,
    registros: list[LeadImportar],
    indice_companies: IndiceCompanies,
    indice_externo: IndiceExterno,
    tamano_lote: int,
    resumen: dict[str, Any],
    dry_run: bool,
) -> None:
    """Crea companies minimas para los leads calificados sin company.

    Sin esto, los leads de Venezuela (que nunca se importaron como
    companies) quedarian con company_id NULL y los 5 del handoff no
    matcheados perderian su estado de contacto. Los INSERTs son nuevos:
    las 8.552 companies existentes no se tocan (solo se leen).
    """
    faltantes = [r for r in registros if r.company_id is None]
    if not faltantes:
        return
    LOGGER.info(
        "Leads sin company: %s. Se crearan companies minimas con datos "
        "externos cuando existan.", len(faltantes),
    )
    if dry_run:
        con_datos = sum(
            1 for r in faltantes
            if indice_externo.por_nombre.get(slugificar(r.nombre))
        )
        LOGGER.info(
            "DRY-RUN: se crearian %s companies minimas (%s con datos de "
            "contacto externos).", len(faltantes), con_datos,
        )
        resumen["companies_minimas_creadas"] = len(faltantes)
        for registro in faltantes:
            registro.company_id = "DRY-RUN"  # marca para el plan de escritura
            registro.metodo_match = "company_creada"
        return

    # Excluir duplicados internos: dos leads con la misma dedupe_key recien
    # creada (mismo slug de nombre y ciudad) comparten company.
    por_clave_nueva: dict[str, str] = {}
    total_lotes = (len(faltantes) + tamano_lote - 1) // tamano_lote
    for indice_lote in range(total_lotes):
        lote = faltantes[indice_lote * tamano_lote : (indice_lote + 1) * tamano_lote]
        # Payloads sin duplicados internos de dedupe_key.
        payloads: list[dict[str, Any]] = []
        sin_duplicar: list[LeadImportar] = []
        vistos: set[str] = set()
        for registro in lote:
            if registro.clave in vistos or registro.clave in por_clave_nueva:
                continue  # otro lead del mismo slug ya lleva esta company
            vistos.add(registro.clave)
            payloads.append(
                _payload_company_minima(
                    registro, indice_externo.por_nombre.get(slugificar(registro.nombre))
                )
            )
            sin_duplicar.append(registro)
        if not payloads:
            continue
        LOGGER.info(
            "Lote de companies minimas %s/%s: %s", indice_lote + 1, total_lotes, len(payloads)
        )
        respuesta = cliente.insertar_lote("companies", payloads)
        if respuesta.status_code == 201:
            filas = respuesta.json()
            for fila, registro in zip(filas, sin_duplicar):
                registro.company_id = fila["id"]
                registro.metodo_match = "company_creada"
                # Registrar tambien en el indice en memoria para lotes
                # posteriores con la misma clave (duplicados del mismo slug).
                por_clave_nueva[registro.clave] = fila["id"]
                indice_companies.por_clave[registro.clave] = {
                    "id": fila["id"], "city": fila.get("city"),
                    "owner_id": OWNER_ID_IMPORTACION,
                }
                slug = clave_nombre(registro.nombre)
                indice_companies.por_nombre.setdefault(slug, []).append(
                    {"id": fila["id"], "city": fila.get("city"),
                     "owner_id": OWNER_ID_IMPORTACION}
                )
            resumen["companies_minimas_creadas"] += len(filas)
        else:
            LOGGER.error(
                "Lote de companies minimas fallo (HTTP %s): %s. Se reintenta "
                "fila a fila.", respuesta.status_code, respuesta.text[:300],
            )
            for registro in sin_duplicar:
                payload = _payload_company_minima(
                    registro, indice_externo.por_nombre.get(slugificar(registro.nombre))
                )
                resp_unica = cliente.insertar_lote("companies", [payload])
                if resp_unica.status_code == 201:
                    fila = resp_unica.json()[0]
                    registro.company_id = fila["id"]
                    registro.metodo_match = "company_creada"
                    por_clave_nueva[registro.clave] = fila["id"]
                    resumen["companies_minimas_creadas"] += 1
                elif resp_unica.status_code == 409:
                    # Ya existe una company con esa dedupe_key (carrera):
                    # se recupera su id con un GET puntual.
                    filas_get = cliente.seleccionar_paginado(
                        "companies", "id", {"dedupe_key": f"eq.{registro.clave}"}
                    )
                    if filas_get:
                        registro.company_id = filas_get[0]["id"]
                        registro.metodo_match = "company_creada"
                        por_clave_nueva[registro.clave] = filas_get[0]["id"]
                    else:
                        resumen["companies_minimas_fallidas"] += 1
                else:
                    resumen["companies_minimas_fallidas"] += 1
                    LOGGER.error(
                        "Company minima fallida '%s': HTTP %s %s",
                        registro.nombre, resp_unica.status_code, resp_unica.text[:200],
                    )
        time.sleep(0.2)

    # Los leads que compartian clave con una company recien creada se
    # resuelven al final contra el indice actualizado.
    resueltos = 0
    for registro in registros:
        if registro.company_id is None:
            id_previo = por_clave_nueva.get(registro.clave)
            if id_previo:
                registro.company_id = id_previo
                registro.metodo_match = "company_creada"
                resueltos += 1
                continue
            resultado = resolver_company(registro, indice_companies)
            if resultado:
                resueltos += 1
    if resueltos:
        LOGGER.info("%s leads adicionales resueltos tras crear companies", resueltos)
    restantes = sum(1 for r in registros if r.company_id is None)
    resumen["sin_company"] = restantes
    LOGGER.info("Leads que quedan sin company tras crear minimas: %s", restantes)


# --------------------------------------------------------------------------
# Seeds de stages
# --------------------------------------------------------------------------

def asegurar_stages(
    cliente: ClienteSupabase, dry_run: bool
) -> tuple[dict[str, str], int, int]:
    """Garantiza los 7 stages del embudo; devuelve ({stage_type: id},
    creados, ya_existian). Si falta alguno de los 7 seeds y no es dry-run,
    se inserta el faltante (idempotente por stage_key)."""
    existentes = cliente.seleccionar_paginado(
        "pipeline_stages", "id,stage_key,stage_type"
    )
    por_clave = {s["stage_key"]: s["id"] for s in existentes if s.get("stage_key")}
    creados = 0

    faltantes = [seed for seed in STAGES_SEED if seed["stage_key"] not in por_clave]
    if faltantes and not dry_run:
        payloads = [
            {
                "name": seed["name"],
                "stage_key": seed["stage_key"],
                "stage_type": seed["stage_type"],
                "position": seed["position"],
                "color": seed["color"],
                "is_default": seed.get("is_default", False),
                "is_won": bool(seed.get("es_won", False)),
                "is_lost": bool(seed.get("es_lost", False)),
                "is_system": True,
                "auto_advance_on": [],
            }
            for seed in faltantes
        ]
        respuesta = cliente.insertar_lote("pipeline_stages", payloads)
        if respuesta.status_code != 201:
            raise RuntimeError(
                f"No se pudieron crear los stages: HTTP "
                f"{respuesta.status_code}: {respuesta.text[:300]}"
            )
        for fila in respuesta.json():
            por_clave[fila["stage_key"]] = fila["id"]
        creados = len(payloads)
        LOGGER.info("Stages creados: %s", [s["stage_key"] for s in faltantes])
    elif faltantes and dry_run:
        LOGGER.info(
            "DRY-RUN: faltarian crear %s stages: %s",
            len(faltantes), [s["stage_key"] for s in faltantes],
        )
    else:
        LOGGER.info("Los stages del embudo ya existen (%s)", len(existentes))

    # Mapa stage_type -> id (los seeds mandan; si una key no existe pero hay
    # un stage con ese stage_type, se reusa por tipo).
    por_tipo: dict[str, str] = {}
    for s in existentes:
        if s.get("stage_type"):
            por_tipo.setdefault(s["stage_type"], s["id"])
    for seed in STAGES_SEED:
        id_stage = por_clave.get(seed["stage_key"]) or por_tipo.get(seed["stage_type"])
        if id_stage:
            por_tipo[seed["stage_type"]] = id_stage
    return por_tipo, creados, len(existentes)


# --------------------------------------------------------------------------
# Match con companies
# --------------------------------------------------------------------------

@dataclass
class IndiceCompanies:
    """Indices de companies para resolver el match sin tocar la tabla."""

    por_clave: dict[str, dict[str, Any]] = field(default_factory=dict)
    por_nombre: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


def cargar_indice_companies(cliente: ClienteSupabase) -> IndiceCompanies:
    """Lee id, name, dedupe_key, city, owner_id de TODAS las companies.

    Solo lectura: el mandato es no tocar las companies existentes. El indice
    por nombre usa slug(name)[:31], la misma porcion con la que se construyo
    la dedupe_key en el import original (ver clave_dedupe).
    """
    filas = cliente.seleccionar_paginado(
        "companies", "id,name,dedupe_key,city,owner_id"
    )
    indice = IndiceCompanies()
    vistos_por_nombre: dict[str, set[str]] = {}
    for fila in filas:
        clave = fila.get("dedupe_key")
        if clave:
            # En colision de dedupe_key entre owners gana el namespace 0002
            # (el de las companies importadas por este mismo pipeline).
            actual = indice.por_clave.get(clave)
            if actual is None or (
                actual.get("owner_id") != OWNER_ID_IMPORTACION
                and fila.get("owner_id") == OWNER_ID_IMPORTACION
            ):
                indice.por_clave[clave] = fila
        nombre = fila.get("name")
        if nombre:
            slug = clave_nombre(nombre)
            if slug not in vistos_por_nombre:
                vistos_por_nombre[slug] = set()
            if clave not in vistos_por_nombre[slug]:  # sin dupes por dedupe_key
                vistos_por_nombre[slug].add(clave or fila["id"])
                indice.por_nombre.setdefault(slug, []).append(fila)
    LOGGER.info(
        "Companies leidas: %s | claves dedupe unicas: %s",
        len(filas), len(indice.por_clave),
    )
    return indice


def resolver_company(registro: LeadImportar, indice: IndiceCompanies) -> str | None:
    """Matchea contra companies por dedupe_key exacta y, si falla, por slug
    de nombre: unico sin contradiccion de ciudad, o varios disambiguados por
    ciudad exacta."""
    # 1) Match exacto con la misma formula del import de companies.
    fila = indice.por_clave.get(registro.clave)
    if fila is not None:
        registro.company_id = fila["id"]
        registro.metodo_match = "exacto"
        return "exacto"

    # 2) Fallback por slug de nombre (mismo slug con el que se construyo la
    #    dedupe_key del import original).
    candidatos = indice.por_nombre.get(clave_nombre(registro.nombre), [])
    if not candidatos:
        return None
    if len(candidatos) == 1:
        unico = candidatos[0]
        slug_ciudad_company = slugificar((unico.get("city") or "").strip())
        slug_ciudad_lead = slugificar(registro.ciudad)
        if not slug_ciudad_company or slug_ciudad_company == slug_ciudad_lead:
            registro.company_id = unico["id"]
            registro.metodo_match = "nombre_unico"
            return "nombre_unico"
        return None
    # 3) Varios con el mismo nombre: la ciudad del lead disambigua si deja
    #    exactamente una candidata.
    slug_ciudad_lead = slugificar(registro.ciudad)
    por_ciudad = [
        c for c in candidatos
        if slugificar((c.get("city") or "").strip()) == slug_ciudad_lead
    ]
    if len(por_ciudad) == 1:
        registro.company_id = por_ciudad[0]["id"]
        registro.metodo_match = "nombre_unico"
        return "nombre_unico"
    return None


# --------------------------------------------------------------------------
# Ejecucion de la importacion
# --------------------------------------------------------------------------

def _agrupar_por_origen(registros: list[LeadImportar]) -> dict[str, int]:
    conteo: dict[str, int] = {}
    for r in registros:
        conteo[r.origen] = conteo.get(r.origen, 0) + 1
    return conteo


def _cargar_leads_existentes(cliente: ClienteSupabase) -> dict[str, dict[str, Any]]:
    """company_id -> lead existente (para idempotencia y updates).

    Un lead por company: el schema real de leads no tiene unique
    company_id+service_id (la BD desplegada no tiene service_id), asi que la
    idempotencia se garantiza aca en el cliente.
    """
    filas = cliente.seleccionar_paginado(
        "leads",
        "id,company_id,status,source,priority,stage_id,first_contact_at,"
        "last_activity_at,next_followup_note",
    )
    por_company: dict[str, dict[str, Any]] = {}
    for fila in filas:
        cid = fila.get("company_id")
        if cid and cid not in por_company:  # el primero gana si hubiera varios
            por_company[cid] = fila
    LOGGER.info("Leads ya existentes en el CRM: %s", len(filas))
    return por_company


def _diferencias_con_lead_existente(
    registro: LeadImportar,
    lead: dict[str, Any],
    stage_ids: dict[str, str],
) -> dict[str, Any]:
    """Campos que cambiarian si se sincroniza el lead existente."""
    cambios: dict[str, Any] = {}
    if lead.get("status") != registro.status_lead:
        cambios["status"] = registro.status_lead
    id_stage = stage_ids.get(registro.stage_type)
    if id_stage and lead.get("stage_id") != id_stage:
        cambios["stage_id"] = id_stage
    if lead.get("priority") != registro.priority:
        cambios["priority"] = registro.priority
    if registro.first_contact_at and lead.get("first_contact_at") != registro.first_contact_at:
        cambios["first_contact_at"] = registro.first_contact_at
    if registro.last_activity_at and lead.get("last_activity_at") != registro.last_activity_at:
        cambios["last_activity_at"] = registro.last_activity_at
    if (
        registro.next_followup_note
        and lead.get("next_followup_note") != registro.next_followup_note
    ):
        cambios["next_followup_note"] = registro.next_followup_note
    return cambios


def ejecutar(
    cliente: ClienteSupabase,
    registros: list[LeadImportar],
    stage_ids: dict[str, str],
    tamano_lote: int,
    resumen: dict[str, Any],
    dry_run: bool,
) -> None:
    """Crea/actualiza los leads y sus actividades NOTE en lotes.

    Los registros sin company_id resuelta se omiten y cuentan como
    sin_company: un lead huerfano no puede recibir actividades ni seguir
    el embudo en el CRM.
    """
    # En dry-run los registros llevan la marca 'DRY-RUN' (company simulada):
    # para planificar la escritura se tratan como si tuvieran company.
    sin_company = [r for r in registros if r.company_id is None]
    if sin_company:
        LOGGER.warning(
            "%s registros sin company_id resuelta se omiten de la escritura",
            len(sin_company),
        )
        registros = [r for r in registros if r.company_id is not None]

    leads_existentes = _cargar_leads_existentes(cliente)

    por_crear: list[LeadImportar] = []
    por_actualizar: list[tuple[LeadImportar, dict[str, Any]]] = []
    for registro in registros:
        lead_previo = leads_existentes.get(registro.company_id or "")
        if lead_previo is None:
            por_crear.append(registro)
            continue
        registro.lead_id = lead_previo["id"]
        if lead_previo.get("source") != SOURCE_LEAD:
            resumen["protegidos_otro_source"] += 1
            LOGGER.info(
                "Lead existente con source=%s no pertenece a este import, "
                "se protege: %s",
                lead_previo.get("source"), registro.nombre,
            )
            continue
        cambios = _diferencias_con_lead_existente(registro, lead_previo, stage_ids)
        if cambios:
            por_actualizar.append((registro, cambios))
        else:
            resumen["ya_iguales"] += 1

    resumen["por_crear"] = len(por_crear)
    resumen["por_actualizar"] = len(por_actualizar)
    LOGGER.info(
        "Plan de escritura: %s leads nuevos, %s updates, %s ya sincronizados, "
        "%s protegidos (source distinto)",
        len(por_crear), len(por_actualizar), resumen["ya_iguales"],
        resumen["protegidos_otro_source"],
    )
    if dry_run:
        # En dry-run la cuadratura se hace contra el plan (los registros que
        # SE crearian), no contra escrituras reales.
        resumen["leads_creados"] = len(por_crear)
        resumen["leads_actualizados"] = len(por_actualizar)
        LOGGER.info(
            "DRY-RUN: se crearian %s leads, %s updates y %s actividades NOTE",
            len(por_crear), len(por_actualizar),
            sum(1 for r in por_crear if r.payload_actividad() is not None),
        )
        return

    # ---------------- Creacion de leads por lotes ----------------
    total_lotes = (len(por_crear) + tamano_lote - 1) // tamano_lote
    for indice_lote in range(total_lotes):
        lote = por_crear[indice_lote * tamano_lote : (indice_lote + 1) * tamano_lote]
        LOGGER.info(
            "Lote de leads %s/%s: %s registros",
            indice_lote + 1, total_lotes, len(lote),
        )
        payloads = [r.payload_lead(stage_ids[r.stage_type]) for r in lote]
        respuesta = cliente.insertar_lote("leads", payloads)
        if respuesta.status_code == 201:
            filas = respuesta.json()
            resumen["leads_creados"] += len(filas)
            actividades = []
            for registro, fila in zip(lote, filas):
                registro.lead_id = fila["id"]
                actividad = registro.payload_actividad()
                if actividad is not None:
                    actividad["lead_id"] = fila["id"]
                    actividades.append(actividad)
            _insertar_actividades(cliente, actividades, resumen)
        else:
            LOGGER.error(
                "Lote de leads fallo (HTTP %s): %s. Reintentando fila a fila.",
                respuesta.status_code, respuesta.text[:300],
            )
            for registro in lote:
                resp_unica = cliente.insertar_lote(
                    "leads", [registro.payload_lead(stage_ids[registro.stage_type])]
                )
                if resp_unica.status_code == 201:
                    resumen["leads_creados"] += 1
                    fila = resp_unica.json()[0]
                    registro.lead_id = fila["id"]
                    actividad = registro.payload_actividad()
                    if actividad is not None:
                        actividad["lead_id"] = fila["id"]
                        _insertar_actividades(cliente, [actividad], resumen)
                else:
                    resumen["leads_fallidos"] += 1
                    LOGGER.error(
                        "Lead fallido '%s': HTTP %s %s",
                        registro.nombre, resp_unica.status_code, resp_unica.text[:200],
                    )
                    if len(resumen["errores"]) < MAX_ERRORES_DETALLADOS:
                        resumen["errores"].append({
                            "nombre": registro.nombre,
                            "http": resp_unica.status_code,
                            "detalle": resp_unica.text[:200],
                        })
        time.sleep(0.2)  # cortesia con el rate limit de Supabase

    # ---------------- Updates de leads existentes ----------------
    for registro, cambios in por_actualizar:
        respuesta = cliente.actualizar(
            "leads", {"id": f"eq.{registro.lead_id}"}, cambios
        )
        if respuesta.status_code in (200, 204):
            resumen["leads_actualizados"] += 1
        else:
            resumen["updates_fallidos"] += 1
            LOGGER.error(
                "Update fallido '%s' (lead %s): HTTP %s %s",
                registro.nombre, registro.lead_id, respuesta.status_code,
                respuesta.text[:200],
            )
        time.sleep(0.05)


def _insertar_actividades(
    cliente: ClienteSupabase,
    actividades: list[dict[str, Any]],
    resumen: dict[str, Any],
) -> None:
    """Inserta actividades NOTE en lotes; reintenta una a una si falla."""
    if not actividades:
        return
    respuesta = cliente.insertar_lote("activities", actividades)
    if respuesta.status_code == 201:
        resumen["actividades_creadas"] += len(actividades)
        return
    LOGGER.warning(
        "Lote de actividades fallo (HTTP %s): %s. Reintentando una a una.",
        respuesta.status_code, respuesta.text[:200],
    )
    for actividad in actividades:
        resp = cliente.insertar_lote("activities", [actividad])
        if resp.status_code == 201:
            resumen["actividades_creadas"] += 1
        else:
            resumen["actividades_fallidas"] += 1
            LOGGER.error(
                "Actividad fallida (lead %s): HTTP %s %s",
                actividad.get("lead_id"), resp.status_code, resp.text[:200],
            )


# --------------------------------------------------------------------------
# Reportes
# --------------------------------------------------------------------------

def imprimir_resumen(resumen: dict[str, Any]) -> None:
    LOGGER.info("=" * 72)
    LOGGER.info("RESUMEN DE LA IMPORTACION DE ESTADO DE LEADS")
    LOGGER.info("=" * 72)
    LOGGER.info("Modo                        : %s", resumen["modo"])
    LOGGER.info(
        "Registros procesados        : %s (limit=%s)",
        resumen["registros_leidos"], resumen["limit"],
    )
    LOGGER.info("  por origen                : %s", resumen["por_origen"])
    LOGGER.info("-- Stages (pipeline_stages)")
    LOGGER.info("  Creados                   : %s", resumen["stages_creados"])
    LOGGER.info("  Ya existentes             : %s", resumen["stages_ya_existian"])
    LOGGER.info("-- Match con companies")
    LOGGER.info("  Matched exacto            : %s", resumen["matched_exacto"])
    LOGGER.info("  Matched por nombre        : %s", resumen["matched_nombre"])
    LOGGER.info("  Company minima creada     : %s", resumen["matched_company_creada"])
    LOGGER.info("  Companies minimas nuevas  : %s", resumen["companies_minimas_creadas"])
    LOGGER.info("  Companies minimas fallidas: %s", resumen["companies_minimas_fallidas"])
    LOGGER.info("  No matched                : %s", resumen["sin_company"])
    LOGGER.info("-- Escritura en leads")
    LOGGER.info("  Leads creados             : %s", resumen["leads_creados"])
    LOGGER.info("  Leads actualizados        : %s", resumen["leads_actualizados"])
    LOGGER.info("  Leads ya sincronizados    : %s", resumen["ya_iguales"])
    LOGGER.info("  Protegidos (otro source)  : %s", resumen["protegidos_otro_source"])
    LOGGER.info("  Leads fallidos            : %s", resumen["leads_fallidos"])
    LOGGER.info("  Updates fallidos          : %s", resumen["updates_fallidos"])
    LOGGER.info("-- Actividades NOTE")
    LOGGER.info("  Creadas                   : %s", resumen["actividades_creadas"])
    LOGGER.info("  Fallidas                  : %s", resumen["actividades_fallidas"])
    LOGGER.info("-- Estados previstos en leads")
    LOGGER.info("  CONTACTED (7 handoff + 66 CVELIZ): %s", resumen["estado_contacted"])
    LOGGER.info("  NEW (849 pendientes + 8 programados): %s", resumen["estado_new"])

    cuadratura = (
        resumen["leads_creados"]
        + resumen["leads_actualizados"]
        + resumen["ya_iguales"]
        + resumen["protegidos_otro_source"]
        + resumen["sin_company"]
        + resumen["leads_fallidos"]
        + resumen["updates_fallidos"]
    )
    LOGGER.info(
        "Cuadratura (creados+actualizados+iguales+protegidos+sin_company+fallidos): "
        "%s == %s -> %s",
        cuadratura, resumen["registros_leidos"],
        "OK" if cuadratura == resumen["registros_leidos"] else "DESCUADRE",
    )
    for error in resumen["errores"]:
        LOGGER.error("  Error detallado: %s", error)


def guardar_no_match(ruta: Path, registros: list[LeadImportar], dry_run: bool = False) -> None:
    """Escribe los leads que quedan sin company para revision posterior.

    En dry-run los registros marcados 'DRY-RUN' son leads a los que se les
    CREARIA una company minima: no cuentan como no-match real.
    """
    con_sin_company = [
        {
            "nombre": r.nombre,
            "ciudad": r.ciudad,
            "clave": r.clave,
            "origen": r.origen,
            "status_fuente": r.status_lead,
        }
        for r in registros
        if r.company_id is None
    ]
    try:
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(con_sin_company, f, ensure_ascii=False, indent=2)
        LOGGER.info("No-match guardados en %s (%s)", ruta, len(con_sin_company))
    except OSError as exc:
        LOGGER.warning("No se pudo escribir el archivo de no-match: %s", exc)


def verificar_en_supabase(cliente: ClienteSupabase) -> None:
    """Verificacion POST-IMPORT con counts reales por status."""
    LOGGER.info("=" * 72)
    LOGGER.info("VERIFICACION EN SUPABASE (lecturas reales)")
    LOGGER.info("=" * 72)
    LOGGER.info("COUNT leads (total)                    : %s", cliente.contar("leads"))
    for status in ("NEW", "CONTACTED", "QUALIFIED", "PROPOSAL",
                   "NEGOTIATION", "WON", "LOST"):
        LOGGER.info(
            "COUNT leads status=%-12s: %s",
            status, cliente.contar("leads", {"status": f"eq.{status}"}),
        )
    LOGGER.info(
        "COUNT stages (pipeline_stages)         : %s", cliente.contar("pipeline_stages")
    )
    LOGGER.info(
        "COUNT activities NOTE (este owner)     : %s",
        cliente.contar("activities", {
            "activity_type": "eq.NOTE",
            "owner_id": f"eq.{OWNER_ID_IMPORTACION}",
        }),
    )
    LOGGER.info(
        "COUNT companies (intacto, total)       : %s", cliente.contar("companies")
    )

    # Los 7 del handoff: primera fila con company embebida para inspeccion.
    LOGGER.info("-- Contactados con first_contact_at (los 7 del handoff):")
    filas = cliente.seleccionar_paginado(
        "leads",
        "status,priority,first_contact_at,next_followup_note,companies(name)",
        {"status": "eq.CONTACTED", "first_contact_at": "not.is.null"},
    )
    for fila in filas[:10]:
        company = (fila.get("companies") or {}).get("name")
        LOGGER.info(
            "  - %s | first_contact_at=%s | note=%s",
            company, fila.get("first_contact_at"),
            (fila.get("next_followup_note") or "")[:70],
        )


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
    except Exception:  # noqa: BLE001 - defensivo, no bloquea la importacion
        pass

    parser = argparse.ArgumentParser(
        description=(
            "Importa el estado de contacto de los leads calificados "
            "(outreach_status.json + 66 CVELIZ + 8 programados) al CRM Mapache."
        ),
        epilog=(
            "Ejemplos:\n"
            "  python import_leads_estado.py --dry-run --limit 50\n"
            "  python import_leads_estado.py --dry-run\n"
            "  python import_leads_estado.py --limit 50\n"
            "  python import_leads_estado.py\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Valida y reporta sin escribir nada en Supabase (solo lecturas).",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Procesa solo los primeros N registros (contactados primero).",
    )
    parser.add_argument(
        "--batch-size", type=int, default=TAMANO_LOTE_PREDETERMINADO,
        help=f"Registros por insert (default {TAMANO_LOTE_PREDETERMINADO}).",
    )
    parser.add_argument("--json", default=RUTA_OUTREACH_JSON,
                        help="Ruta del outreach_status.json.")
    parser.add_argument("--csv", default=RUTA_CSV_UNIFICADOS,
                        help="Ruta del CSV unificado (66 contactados + 8 programados).")
    parser.add_argument("--audit", default=RUTA_AUDIT_JSONL,
                        help="Ruta del jsonl de auditoria de outreach.")
    parser.add_argument("--env", default=str(RUTA_ENV_PREDETERMINADA),
                        help="Ruta del .env con SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY.")
    args = parser.parse_args()

    ruta_log = Path(__file__).resolve().parent / "import_leads_estado.log"
    configurar_logging(ruta_log)

    modo = "dry-run" if args.dry_run else (f"limit={args.limit}" if args.limit else "completo")
    LOGGER.info("Modo: %s | Lote: %s", modo, args.batch_size)

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

    # ------------------------------------------------------------- fuentes
    try:
        auditoria = leer_audit(args.audit)
        registros_json = leer_outreach_json(args.json, auditoria)
        registros_csv = leer_csv_unificados(args.csv)
    except FileNotFoundError as exc:
        LOGGER.error("Fuente no encontrada: %s", exc)
        return 1

    # Orden estable: los 81 con estado especial primero (7 handoff, 66
    # CVELIZ, 8 programados), despues los 849 pendientes del JSON.
    registros = registros_csv + registros_json
    if args.limit is not None:
        registros = registros[: args.limit]

    resumen: dict[str, Any] = {
        "modo": modo,
        "limit": args.limit,
        "registros_leidos": len(registros),
        "por_origen": _agrupar_por_origen(registros),
        "stages_creados": 0,
        "stages_ya_existian": 0,
        "matched_exacto": 0,
        "matched_nombre": 0,
        "matched_company_creada": 0,
        "sin_company": 0,
        "companies_minimas_creadas": 0,
        "companies_minimas_fallidas": 0,
        "por_crear": 0,
        "por_actualizar": 0,
        "leads_creados": 0,
        "leads_actualizados": 0,
        "ya_iguales": 0,
        "protegidos_otro_source": 0,
        "leads_fallidos": 0,
        "updates_fallidos": 0,
        "actividades_creadas": 0,
        "actividades_fallidas": 0,
        "estado_contacted": sum(1 for r in registros if r.status_lead == "CONTACTED"),
        "estado_new": sum(1 for r in registros if r.status_lead == "NEW"),
        "errores": [],
    }

    cliente = ClienteSupabase(supabase_url, supabase_key)
    try:
        # 1) Stages del embudo (seeds solo si faltan; idempotente).
        try:
            stage_ids, creados, ya_existian = asegurar_stages(cliente, args.dry_run)
            resumen["stages_creados"] = creados
            resumen["stages_ya_existian"] = ya_existian
        except RuntimeError as exc:
            LOGGER.error("%s", exc)
            return 1

        # 2) Indice de companies (read-only) y match de cada registro.
        try:
            indice = cargar_indice_companies(cliente)
        except RuntimeError as exc:
            LOGGER.error("No se pudo leer companies: %s", exc)
            return 1
        for registro in registros:
            resultado = resolver_company(registro, indice)
            if resultado == "exacto":
                resumen["matched_exacto"] += 1
            elif resultado == "nombre_unico":
                resumen["matched_nombre"] += 1
            else:
                resumen["sin_company"] += 1

        # 3) Companies minimas para los no-matched: el CRM solo tiene las
        #    companies de Colombia (CSV unificado); los leads de Venezuela
        #    del outreach binacional nunca se importaron. Sin esto, 5 de los
        #    7 contactados por WhatsApp quedarian huerfanos. Son INSERTs
        #    nuevos: las 8.552 existentes no se tocan.
        if resumen["sin_company"] > 0:
            indice_externo = cargar_indice_externo()
            crear_companies_minimas(
                cliente, registros, indice, indice_externo,
                args.batch_size, resumen, args.dry_run,
            )
            # Recalcular matched tras crear minimas.
            resumen["matched_company_creada"] = sum(
                1 for r in registros if r.metodo_match == "company_creada"
            )
            resumen["sin_company"] = sum(1 for r in registros if r.company_id is None)

        # 4) Escritura (o estimacion en dry-run).
        try:
            ejecutar(cliente, registros, stage_ids, args.batch_size, resumen, args.dry_run)
        except RuntimeError as exc:
            LOGGER.error("Fallo la escritura: %s", exc)
            imprimir_resumen(resumen)
            return 1

        imprimir_resumen(resumen)
        guardar_no_match(
            Path(__file__).resolve().parent / "import_leads_estado_no_match.json",
            registros,
            dry_run=args.dry_run,
        )
        if not args.dry_run:
            verificar_en_supabase(cliente)
        return 0 if (resumen["leads_fallidos"] + resumen["updates_fallidos"]) == 0 else 1
    finally:
        cliente.cerrar()


if __name__ == "__main__":
    sys.exit(main())
