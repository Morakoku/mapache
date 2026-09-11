#!/usr/bin/env python3
"""Warm-up de dominio via Resend para edwin@veyrasoluciones.com — Veyra.

Calienta la reputacion del dominio enviando el email 1 de la secuencia
"Veyra MRI Outbound 30d" ("¿Te escribo o prefieres que no?" — pedir permiso)
a leads del CRM Mapache, con una rampa de volumen creciente de 14 dias.

Decisiones de diseno (documentadas, tomadas contra el schema VERIFICADO en
vivo via OpenAPI de PostgREST el 2026-09-09):

1. ESTADO DEL WARM-UP: archivo JSON local backend/scripts/warmup_state.json.
   El schema en vivo NO tiene una tabla adecuada para el registro de warm-up
   (no existe tabla de control y PostgREST no permite DDL para crearla).
   El JSON es la fuente de verdad de la idempotencia; Supabase queda como
   espejo visible (lead.status=CONTACTED + activity EMAIL_SENT) para que la
   Torre de Control refleje el progreso. El JSON NO guarda el email real:
   guarda sha256(email normalizado) + version enmascarada (privacidad en
   disco). Escritura atomica (tmp + os.replace).

2. IDEMPOTENCIA (nunca reenviar al mismo email):
   - status SENT (cualquier dia): el email queda bloqueado para SIEMPRE.
   - status INTENDED/FAILED del dia EN CURSO: bloqueado HOY (re-correr el
     mismo dia no reintenta, evita duplicados si un envio quedo en vuelo),
     pero puede reintentarse en un dia SIGUIENTE (un run fallido completo
     no quema a los leads para siempre).
   - Antes de cada envio se registra INTENTION; despues se confirma SENT con
     el id de Resend (o FAILED con el error).

3. PLANTILLA DIA 0: se trae EXACTA de email_templates (subject
   "¿Te escribo o prefieres que no?", category "Veyra MRI Outbound").
   Los placeholders REALES de la plantilla importada son "[Nombre]" y
   "[sector de la empresa]" (transcripcion exacta de
   MAQUINA_VENTAS_20260826.md; el enunciado hablaba de {empresa} pero la
   fuente real usa esos corchetes). Reglas de personalizacion:
   - [Nombre]          -> first_name/full_name del contact; si no hay,
                          se deja "Hola." natural (nunca se envia un
                          corcheto crudo).
   - [sector de la empresa] -> category de la company; si no tiene, el
                          nombre de la empresa (el "empresa" del enunciado).
   Si la plantilla no existe o esta inactiva: error claro, exit 1. No se
   improvisa texto.

4. RUTA DE EMAIL: los leads scrapeados sin opt-in van por EMAIL (regla
   anti-ban del ECOSISTEMA), los 7 leads con whatsapp_usado=true en
   activities.metadata NO se recontactan, los CONTACTED no se reenvuelven
   a seleccionar, y do_not_contact se respeta.

5. RAMPA: tabla configurable RAMP_RANGES mas abajo. --days N re-escala la
   rampa de 14 dias base a N dias conservando la forma (mapeo proporcional
   del dia). Dias saltados se pierden: nunca se "alcanza" la rampa atrasada
   (anti-ban). El cupo diario cuenta SOLO envios SENT reales.

6. DIAS 1-2 VAN A DESTINATARIOS DE CONTROL: primero un auto-envio a
   edwin@veyrasoluciones.com (una sola vez en todo el warm-up, dia 1),
   luego leads de prueba (dominios example.com/test.com), y solo si queda
   cupo, los primeros NEW con email. Desde el dia 3: solo leads NEW reales
   ordenados por priority DESC.

7. SIN REINTENTOS en POST /emails de Resend: un retry tras timeout podria
   duplicar un envio. Un fallo queda FAILED y se reintenta otro dia.

8. SEGURIDAD: la API key SOLO se lee de backend/.env (RESEND_API_KEY),
   jamas se imprime ni se loguea. Todos los emails en logs y consola van
   enmascarados (ed***@dominio.com).

Uso:
    python warmup_resend.py --plan              # rampa + primeros destinatarios
    python warmup_resend.py --plan --days 21    # rampa escalada a 21 dias
    python warmup_resend.py --status            # progreso + opens/bounces Resend
    python warmup_resend.py --send              # envia los del dia actual

Requisitos: httpx (presente en el venv de Hermes). Standalone: no importa
nada del paquete `app` del backend (los modelos SQLAlchemy estan desfasados
respecto al schema en vivo; este script se rige por el schema en vivo).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import httpx

# ==========================================================================
# CONFIGURACION — la rampa y todo lo ajustable vive aqui arriba
# ==========================================================================

# Rampa de 14 dias: (dia_desde, dia_hasta, envios_por_dia).
# Base de la escala: con --days N != 14 el dia se mapea proporcionalmente
# (dia_equivalente = ceil(dia * 14 / N)) y se aplica el cupo de ese tramo.
RAMP_RANGES: list[tuple[int, int, int]] = [
    (1, 2, 5),      # dias 1-2:    5 envios/dia (destinatarios de control)
    (3, 5, 10),     # dias 3-5:   10 envios/dia
    (6, 9, 20),     # dias 6-9:   20 envios/dia
    (10, 12, 35),   # dias 10-12: 35 envios/dia
    (13, 14, 50),   # dias 13-14: 50 envios/dia
]
RAMP_DIAS_BASE = 14
DIAS_PREDETERMINADOS = 14

# Identidad de envio (dominio verificado en Resend, funcionando en produccion).
FROM_EMAIL = "Veyra Soluciones <edwin@veyrasoluciones.com>"

# Destinatario de control del dia 1 (auto-envio: calienta el buzon propio).
EMAIL_CONTROL = "edwin@veyrasoluciones.com"
NOMBRE_CONTROL = "Edwin"
SECTOR_CONTROL = "consultoria"

# Dominos considerados de prueba (leads dummy del CRM, buzones de control).
DOMINIOS_CONTROL = {"example.com", "example.org", "example.net", "test.com"}

# Plantilla del dia 0: se busca EXACTA por subject + category.
CATEGORIA_SECUENCIA = "Veyra MRI Outbound"
SUBJECT_DIA_0 = "¿Te escribo o prefieres que no?"
NOMBRE_PLANTILLA_ESPERADO = "Veyra MRI 01 · Día 0 · Pedir permiso"

# Rutas.
RUTA_BACKEND = Path(__file__).resolve().parent.parent
RUTA_ENV_PREDETERMINADA = RUTA_BACKEND / ".env"
RUTA_ESTADO = Path(__file__).resolve().parent / "warmup_state.json"
RUTA_LOG = Path(__file__).resolve().parent / "warmup_resend.log"

# APIs.
URL_RESEND = "https://api.resend.com"
TIMEOUT_RESEND = 30.0
TIMEOUT_SUPABASE = 60.0
LIMITE_LISTADO_RESEND = 100  # GET /emails para el monitoreo de --status

# Paginacion Supabase (max PostgREST por defecto: 1000).
PAGINA_LEADS = 500

# Idempotencia del archivo de estado.
VERSION_ESTADO = 1

STATUS_INTENDED = "INTENDED"
STATUS_SENT = "SENT"
STATUS_FAILED = "FAILED"

LOGGER = logging.getLogger("warmup_resend")


# ==========================================================================
# Utilidades
# ==========================================================================


def ahora_utc_iso() -> str:
    """Timestamp UTC ISO-8601 con offset (formato que espera PostgREST)."""
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


def enmascarar_email(email: str) -> str:
    """ed***@dominio.com — nunca se loguea un email completo de tercero."""
    email = email.strip()
    if "@" not in email:
        return email[:2] + "***"
    local, dominio = email.rsplit("@", 1)
    prefijo = local[:2] if len(local) >= 3 else local[:1]
    return f"{prefijo}***@{dominio}"


def hash_email(email: str) -> str:
    """sha256 del email normalizado (lower). Clave de idempotencia sin PII."""
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


def dominio_email(email: str) -> str:
    return email.rsplit("@", 1)[1].strip().lower() if "@" in email else ""


def cuerpo_a_html(texto: str) -> str:
    """HTML minimo y honesto del body_text: parrafos, sin imagenes ni links."""
    import html as html_mod

    parrafos = [p.strip() for p in texto.split("\n\n") if p.strip()]
    partes = []
    for p in parrafos:
        escapado = html_mod.escape(p).replace("\n", "<br>")
        partes.append(f"<p style=\"margin:0 0 12px 0\">{escapado}</p>")
    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;'
        'color:#1a1a1a;line-height:1.5;max-width:560px">'
        + "".join(partes)
        + "</div>"
    )


# ==========================================================================
# Estado del warm-up (JSON local, fuente de verdad de la idempotencia)
# ==========================================================================


class EstadoWarmup:
    """Registro de envios del warm-up en warmup_state.json.

    Estructura:
        {
          "version": 1,
          "fecha_inicio": "2026-09-09",           # fecha LOCAL del primer run
          "envios": [
            {"lead_id": "...|null", "email_hash": "sha256",
             "email_enmascarado": "ed***@x.com", "dominio": "x.com",
             "dia": 1, "sent_at": "...", "status": "SENT",
             "resend_id": "...", "tipo": "CONTROL|LEAD", "error": null}
          ]
        }
    """

    def __init__(self, ruta: Path) -> None:
        self.ruta = ruta
        self.datos: dict[str, Any] = {"version": VERSION_ESTADO, "fecha_inicio": None, "envios": []}
        self._cargar()

    def _cargar(self) -> None:
        if not self.ruta.exists():
            return
        try:
            with open(self.ruta, encoding="utf-8") as f:
                datos = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"El archivo de estado {self.ruta} existe pero esta corrupto: {exc}. "
                "Corrijalo manualmente o borrelo SOLO si entiende que pierde el "
                "registro de idempotencia (riesgo de re-enviar)."
            ) from exc
        if not isinstance(datos, dict) or "envios" not in datos:
            raise RuntimeError(f"El archivo de estado {self.ruta} no tiene el formato esperado.")
        self.datos = datos

    def guardar(self) -> None:
        """Escritura atomica: tmp + os.replace (no deja estados a medias)."""
        self.datos["version"] = VERSION_ESTADO
        tmp = self.ruta.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.datos, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.ruta)

    # ---------------------------------------------------------------- dias

    @property
    def fecha_inicio(self) -> date | None:
        fi = self.datos.get("fecha_inicio")
        return date.fromisoformat(fi) if fi else None

    def fijar_fecha_inicio(self, hoy: date) -> None:
        if not self.datos.get("fecha_inicio"):
            self.datos["fecha_inicio"] = hoy.isoformat()
            self.guardar()

    def dia_actual(self, hoy: date) -> int:
        """Dia 1 = primer dia de envio. Los dias saltados NO se recuperan."""
        fi = self.fecha_inicio
        if fi is None:
            return 1
        return max(1, (hoy - fi).days + 1)

    # ------------------------------------------------------------- envios

    @property
    def envios(self) -> list[dict[str, Any]]:
        return self.datos.get("envios", [])

    def enviados_del_dia(self, dia: int) -> list[dict[str, Any]]:
        return [e for e in self.envios if e.get("dia") == dia and e.get("status") == STATUS_SENT]

    def hashes_bloqueados_hoy(self, dia: int) -> set[str]:
        """Cualquier status del dia actual bloquea el email HOY."""
        return {e["email_hash"] for e in self.envios if e.get("dia") == dia}

    def hashes_sent(self) -> set[str]:
        """Los SENT bloquean el email para SIEMPRE."""
        return {e["email_hash"] for e in self.envios if e.get("status") == STATUS_SENT}

    # ---------------------------------------------------------- supresiones

    def hashes_suprimidos(self) -> set[str]:
        """Emails suprimidos (rebote duro, queja o baja): nunca se vuelven a tocar."""
        return {s.get("email_hash") for s in self.datos.get("suprimidos", []) if s.get("email_hash")}

    def suprimir(self, email: str, motivo: str, resend_id: str | None = None) -> bool:
        """Agrega un email a la lista de supresión. Devuelve True si era nuevo."""
        h = hash_email(email)
        if h in self.hashes_suprimidos():
            return False
        self.datos.setdefault("suprimidos", []).append(
            {
                "email_hash": h,
                "email_enmascarado": enmascarar_email(email),
                "dominio": dominio_email(email),
                "motivo": motivo,
                "resend_id": resend_id,
                "fecha": ahora_utc_iso(),
            }
        )
        self.guardar()
        return True

    def registrar_intencion(
        self, lead_id: str | None, email: str, dia: int, tipo: str
    ) -> dict[str, Any]:
        registro = {
            "lead_id": lead_id,
            "email_hash": hash_email(email),
            "email_enmascarado": enmascarar_email(email),
            "dominio": dominio_email(email),
            "dia": dia,
            "sent_at": ahora_utc_iso(),
            "status": STATUS_INTENDED,
            "resend_id": None,
            "tipo": tipo,
            "error": None,
        }
        self.datos["envios"].append(registro)
        self.guardar()
        return registro

    def confirmar(self, registro: dict[str, Any], resend_id: str) -> None:
        registro["status"] = STATUS_SENT
        registro["resend_id"] = resend_id
        registro["sent_at"] = ahora_utc_iso()
        self.guardar()

    def marcar_fallido(self, registro: dict[str, Any], error: str) -> None:
        registro["status"] = STATUS_FAILED
        registro["error"] = error[:400]
        self.guardar()


# ==========================================================================
# Rampa
# ==========================================================================


def cupo_para_dia(dia: int, total_dias: int) -> int | None:
    """Cupo de envios del dia `dia` con la rampa escalada a `total_dias`.

    None => el dia esta fuera de la rampa (warm-up terminado).
    """
    if dia < 1:
        return None
    if total_dias == RAMP_DIAS_BASE:
        dia_equivalente = dia
    else:
        ratio = RAMP_DIAS_BASE / total_dias
        dia_equivalente = min(RAMP_DIAS_BASE, max(1, math.ceil(dia * ratio)))
    if dia > total_dias:
        return None
    for desde, hasta, cupo in RAMP_RANGES:
        if desde <= dia_equivalente <= hasta:
            return cupo
    # dia_equivalente > ultimo tramo (rampa base 14): fuera de rampa.
    return None


def rampa_completa(total_dias: int) -> list[tuple[int, int]]:
    """[(dia, cupo)] para todos los dias de la rampa escalada."""
    resultado: list[tuple[int, int]] = []
    for dia in range(1, total_dias + 1):
        cupo = cupo_para_dia(dia, total_dias)
        if cupo is not None:
            resultado.append((dia, cupo))
    return resultado


# ==========================================================================
# Cliente Supabase (PostgREST, lecturas y escrituras del CRM)
# ==========================================================================


class ClienteSupabase:
    """Cliente HTTP minimo contra PostgREST con reintentos para 5xx/red."""

    def __init__(self, url_base: str, api_key: str) -> None:
        self.url_base = url_base.rstrip("/")
        self._headers = {
            "apikey": api_key,
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._cliente = httpx.Client(timeout=TIMEOUT_SUPABASE)

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
                    LOGGER.warning("Intento %s/%s fallo (%s) en %s", intento, reintentos, ultimo_error, ruta)
                else:
                    return respuesta  # 4xx definitivo
            if intento < reintentos:
                time.sleep(2 * intento)

        raise RuntimeError(f"Request a {ruta} fallo tras {reintentos} intentos: {ultimo_error}")

    # ------------------------------------------------------------- lecturas

    def plantilla_dia_0(self) -> dict[str, Any]:
        """Trae la plantilla EXACTA del email 1 de la secuencia Veyra."""
        respuesta = self._request(
            "GET",
            "/rest/v1/email_templates",
            params={
                "select": "id,name,subject,body_text,body_html,is_active,category",
                "category": f"eq.{CATEGORIA_SECUENCIA}",
                "subject": f"eq.{SUBJECT_DIA_0}",
                "limit": 5,
            },
        )
        if respuesta.status_code != 200:
            raise RuntimeError(
                f"No se pudo consultar email_templates (HTTP {respuesta.status_code}): "
                f"{respuesta.text[:300]}"
            )
        filas = respuesta.json()
        if not filas:
            raise RuntimeError(
                f"ERROR: no se encontro la plantilla del dia 0 en Supabase. "
                f"Se busca subject='{SUBJECT_DIA_0}' con category='{CATEGORIA_SECUENCIA}' "
                f"(esperada: '{NOMBRE_PLANTILLA_ESPERADO}'). No se improvisa texto: "
                f"importe la secuencia con import_templates_secuencia.py."
            )
        plantilla = filas[0]
        if not plantilla.get("is_active"):
            raise RuntimeError(
                f"ERROR: la plantilla del dia 0 ('{plantilla.get('name')}') existe pero "
                f"is_active=false. Activala en el CRM antes de enviar."
            )
        if not (plantilla.get("body_text") or plantilla.get("body_html")):
            raise RuntimeError(
                f"ERROR: la plantilla del dia 0 ('{plantilla.get('name')}') no tiene "
                f"body_text ni body_html. Corrigela en el CRM."
            )
        return plantilla

    def lead_ids_whatsapp_usado(self) -> set[str]:
        """Los 7 (o los que sean) con metadata.whatsapp_usado=true: NO recontactar."""
        respuesta = self._request(
            "GET",
            "/rest/v1/activities",
            params={
                "select": "lead_id",
                "metadata->>whatsapp_usado": "eq.true",
                "limit": 100,
            },
        )
        if respuesta.status_code != 200:
            LOGGER.warning(
                "No se pudo leer whatsapp_usado (HTTP %s): %s",
                respuesta.status_code,
                respuesta.text[:200],
            )
            return set()
        return {f["lead_id"] for f in respuesta.json() if f.get("lead_id")}

    def leads_new_con_email(self, max_filas: int = 1200) -> list[dict[str, Any]]:
        """Leads NEW con email disponible (contact directo o contacts de la company).

        Orden: priority DESC, created_at ASC (los priority 5 primero).
        El email se resuelve: 1) contact_id -> contacts.email, 2) fallback
        contacts de la company (preferido is_primary), respetando
        do_not_contact en ambos casos.
        """
        offset = 0
        crudos: list[dict[str, Any]] = []
        while len(crudos) < max_filas:
            respuesta = self._request(
                "GET",
                "/rest/v1/leads",
                params={
                    "select": (
                        "id,company_id,contact_id,priority,owner_id,first_contact_at,"
                        "contact:contact_id(email,do_not_contact,first_name,full_name),"
                        "company:company_id(name,category,"
                        "contacts(email,do_not_contact,is_primary,first_name,full_name))"
                    ),
                    "status": "eq.NEW",
                    "order": "priority.desc,created_at.asc",
                    "limit": PAGINA_LEADS,
                    "offset": offset,
                },
            )
            if respuesta.status_code != 200:
                raise RuntimeError(
                    f"No se pudieron leer los leads NEW (HTTP {respuesta.status_code}): "
                    f"{respuesta.text[:300]}"
                )
            filas = respuesta.json()
            crudos.extend(filas)
            if len(filas) < PAGINA_LEADS:
                break
            offset += PAGINA_LEADS

        resultados: list[dict[str, Any]] = []
        for fila in crudos:
            email = None
            nombre = None
            via = None
            contacto = fila.get("contact")
            if isinstance(contacto, dict) and contacto.get("email") and not contacto.get("do_not_contact"):
                email = contacto["email"]
                nombre = contacto.get("first_name") or contacto.get("full_name")
                via = "contact_id"
            if email is None:
                company = fila.get("company")
                if isinstance(company, dict):
                    validos = [
                        c
                        for c in (company.get("contacts") or [])
                        if isinstance(c, dict) and c.get("email") and not c.get("do_not_contact")
                    ]
                    if validos:
                        primario = next((c for c in validos if c.get("is_primary")), validos[0])
                        email = primario["email"]
                        nombre = primario.get("first_name") or primario.get("full_name")
                        via = "company_contacts"
            if email is None:
                continue
            company = fila.get("company") if isinstance(fila.get("company"), dict) else {}
            resultados.append(
                {
                    "lead_id": fila["id"],
                    "company_id": fila.get("company_id"),
                    "owner_id": fila.get("owner_id"),
                    "priority": fila.get("priority") if fila.get("priority") is not None else 0,
                    "email": email.strip(),
                    "nombre": nombre,
                    "empresa": (company.get("name") or "").strip(),
                    "sector": (company.get("category") or "").strip() or None,
                    "via": via,
                }
            )
        return resultados

    def contactos_con_email(self, max_filas: int = 1500) -> list[dict[str, Any]]:
        """Contactos reales del CRM con email (fuente alternativa del warm-up).
        Los leads NEW casi no tienen contacto con email enlazado, así que el
        warm-up tomaría solo correos de prueba. Esta fuente usa los contactos
        reales (excluye do_not_contact) para que el dominio se caliente con
        correos que sí se entregan. El nombre de la empresa se resuelve con el
        embedding de companies.
        """
        offset = 0
        crudos: list[dict[str, Any]] = []
        while len(crudos) < max_filas:
            respuesta = self._request(
                "GET",
                "/rest/v1/contacts",
                params={
                    "select": "id,email,do_not_contact,first_name,full_name,company_id,"
                    "companies(name,category)",
                    "email": "not.is.null",
                    "do_not_contact": "eq.false",
                    "order": "created_at.desc",
                    "limit": PAGINA_LEADS,
                    "offset": offset,
                },
            )
            if respuesta.status_code != 200:
                raise RuntimeError(
                    f"No se pudieron leer los contactos (HTTP {respuesta.status_code}): "
                    f"{respuesta.text[:300]}"
                )
            filas = respuesta.json()
            crudos.extend(filas)
            if len(filas) < PAGINA_LEADS:
                break
            offset += PAGINA_LEADS

        resultados: list[dict[str, Any]] = []
        for fila in crudos:
            email = (fila.get("email") or "").strip()
            if not email or "@" not in email:
                continue
            company = fila.get("companies") if isinstance(fila.get("companies"), dict) else {}
            resultados.append(
                {
                    "lead_id": None,
                    "company_id": fila.get("company_id"),
                    "owner_id": fila.get("owner_id") if "owner_id" in fila else None,
                    "priority": 0,
                    "email": email,
                    "nombre": fila.get("first_name") or fila.get("full_name"),
                    "empresa": (company.get("name") or "").strip(),
                    "sector": (company.get("category") or "").strip() or None,
                    "via": "contacts",
                }
            )
        return resultados

    # ------------------------------------------------------------ escrituras

    def marcar_no_contactar(self, email: str) -> int:
        """PATCH contacts.do_not_contact=true para ese email (rebote/queja).

        Devuelve cuántas filas quedaron marcadas. Nunca lanza por 0 filas:
        el email puede no existir como contacto (solo en el estado del warm-up).
        """
        respuesta = self._request(
            "PATCH",
            "/rest/v1/contacts",
            params={"email": f"eq.{email}"},
            json_body={"do_not_contact": True},
            prefer="return=representation",
        )
        if respuesta.status_code not in (200, 204):
            raise RuntimeError(
                f"No se pudo marcar do_not_contact para {enmascarar_email(email)} "
                f"(HTTP {respuesta.status_code}): {respuesta.text[:200]}"
            )
        if not respuesta.text:
            return 0
        try:
            filas = respuesta.json()
        except ValueError:
            return 0
        return len(filas) if isinstance(filas, list) else 0

    def actualizar_lead_contactado(self, lead_id: str, ya_tenia_first_contact: bool) -> None:
        """status=CONTACTED + first_contact_at + last_activity_at."""
        payload: dict[str, Any] = {"status": "CONTACTED", "last_activity_at": ahora_utc_iso()}
        if not ya_tenia_first_contact:
            payload["first_contact_at"] = ahora_utc_iso()
        respuesta = self._request("PATCH", "/rest/v1/leads", params={"id": f"eq.{lead_id}"}, json_body=payload)
        if respuesta.status_code not in (200, 204):
            raise RuntimeError(
                f"No se pudo actualizar el lead {lead_id} a CONTACTED "
                f"(HTTP {respuesta.status_code}): {respuesta.text[:300]}"
            )

    def registrar_activity(
        self, lead_id: str, owner_id: str | None, dia: int, resend_id: str, email_enmascarado: str
    ) -> None:
        """Activity NOTE-visible en el timeline con {warmup_day, resend_id}."""
        payload = {
            "lead_id": lead_id,
            "owner_id": owner_id,
            "activity_type": "EMAIL_SENT",
            "actor_type": "SYSTEM",
            "subject": f"Warm-up día {dia}: email enviado a {email_enmascarado}",
            "body": (
                f"Email 1 de la secuencia Veyra MRI Outbound 30d "
                f"('¿Te escribo o prefieres que no?') enviado vía Resend desde "
                f"edwin@veyrasoluciones.com. Destinatario: {email_enmascarado}. "
                f"resend_id={resend_id}"
            ),
            "metadata": {
                "warmup_day": dia,
                "resend_id": resend_id,
                "canal": "warmup_resend",
                "plantilla": NOMBRE_PLANTILLA_ESPERADO,
            },
            "is_system_generated": True,
        }
        respuesta = self._request("POST", "/rest/v1/activities", json_body=[payload])
        if respuesta.status_code not in (200, 201):
            LOGGER.warning(
                "Activity del lead %s no se pudo registrar (HTTP %s): %s",
                lead_id,
                respuesta.status_code,
                respuesta.text[:200],
            )


# ==========================================================================
# Cliente Resend
# ==========================================================================


class ClienteResend:
    """Envios y monitoreo via api.resend.com. La key JAMAS se loguea."""

    def __init__(self, api_key: str) -> None:
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._cliente = httpx.Client(timeout=TIMEOUT_RESEND)

    def cerrar(self) -> None:
        self._cliente.close()

    def enviar_email(self, to_email: str, subject: str, text: str, html: str) -> str:
        """POST /emails. SIN reintentos: un retry tras timeout duplicaria el envio.

        Devuelve el id de Resend. Lanza excepcion con mensaje claro si falla.
        """
        body = {"from": FROM_EMAIL, "to": [to_email], "subject": subject, "text": text, "html": html}
        try:
            respuesta = self._cliente.post(f"{URL_RESEND}/emails", headers=self._headers, json=body)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Error de red contra Resend (no se sabe si se envio): {exc}") from exc

        if respuesta.status_code in (200, 201):
            data = respuesta.json()
            resend_id = data.get("id")
            if not resend_id:
                raise RuntimeError(f"Resend respondio {respuesta.status_code} sin id: {respuesta.text[:300]}")
            return str(resend_id)

        detalle = respuesta.text[:300]
        if respuesta.status_code in (401, 403):
            raise RuntimeError(f"Resend rechazo la API key (HTTP {respuesta.status_code}). Revisa RESEND_API_KEY.")
        raise RuntimeError(f"Resend devolvio HTTP {respuesta.status_code}: {detalle}")

    def listar_emails(self, limite: int = LIMITE_LISTADO_RESEND) -> list[dict[str, Any]]:
        """GET /emails para monitoreo (last_event: sent/delivered/opened/bounced...)."""
        try:
            respuesta = self._cliente.get(
                f"{URL_RESEND}/emails",
                headers=self._headers,
                params={"limit": limite},
            )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Error de red consultando Resend: {exc}") from exc
        if respuesta.status_code != 200:
            raise RuntimeError(f"Resend devolvio HTTP {respuesta.status_code}: {respuesta.text[:300]}")
        data = respuesta.json()
        return data.get("data", []) if isinstance(data, dict) else []

    def estado_email(self, resend_id: str) -> dict[str, Any] | None:
        """GET /emails/{id}: last_event y destinatario real (para supresión).

        Se usa para detectar rebotes/quejas después del envío. Devuelve None si
        Resend no conoce el id (p. ej. ya purgado).
        """
        try:
            respuesta = self._cliente.get(f"{URL_RESEND}/emails/{resend_id}", headers=self._headers)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Error de red consultando Resend: {exc}") from exc
        if respuesta.status_code == 404:
            return None
        if respuesta.status_code != 200:
            raise RuntimeError(f"Resend devolvio HTTP {respuesta.status_code}: {respuesta.text[:300]}")
        data = respuesta.json()
        return data if isinstance(data, dict) else None


# ==========================================================================
# Personalizacion de la plantilla dia 0
# ==========================================================================


def personalizar_dia_0(
    cuerpo: str, nombre: str | None, sector: str | None, empresa: str
) -> str:
    """Sustituye los placeholders reales del email 1 sin improvisar texto.

    - [Nombre] -> nombre del contact; si no hay, queda el saludo natural
      "Hola." (el corcheto crudo jamas se envia).
    - [sector de la empresa] -> sector (category) de la company; si no
      existe, el nombre de la empresa.
    """
    cuerpo_final = cuerpo
    if nombre and nombre.strip():
        cuerpo_final = cuerpo_final.replace("[Nombre]", nombre.strip())
    else:
        cuerpo_final = cuerpo_final.replace(", [Nombre].", ".").replace("[Nombre]", "")
    sector_sustituto = (sector or empresa or "negocios B2B").strip()
    cuerpo_final = cuerpo_final.replace("[sector de la empresa]", sector_sustituto)
    return cuerpo_final


# ==========================================================================
# Seleccion de destinatarios
# ==========================================================================


def seleccionar_destinatarios(
    cliente: ClienteSupabase,
    estado: EstadoWarmup,
    dia: int,
    cupo: int,
) -> tuple[list[dict[str, Any]], dict[str, Any], int]:
    """Elige los destinatarios del dia segun las reglas del warm-up.

    Devuelve (destinatarios, plantilla_dia_0, total_disponibles).
    Cada destinatario: {tipo: CONTROL|LEAD, lead_id, email, nombre, empresa,
    sector, priority, via, first_contact_at}.
    """
    plantilla = cliente.plantilla_dia_0()

    whatsapp_ids = cliente.lead_ids_whatsapp_usado()
    if whatsapp_ids:
        LOGGER.info(
            "Excluidos por whatsapp_usado=true (regla anti-ban): %s leads",
            len(whatsapp_ids),
        )

    bloqueados_hoy = estado.hashes_bloqueados_hoy(dia)
    sent_siempre = estado.hashes_sent()

    todos = cliente.leads_new_con_email()
    # Fuente adicional: contactos reales del CRM. Resend rechaza los dominios de
    # control (example.com) con HTTP 422, así que nunca deben ocupar cupo.
    try:
        todos = todos + cliente.contactos_con_email()
    except RuntimeError as exc:
        LOGGER.warning("No se pudieron leer los contactos del CRM: %s", exc)

    disponibles = []
    vistos: set[str] = set()
    suprimidos = estado.hashes_suprimidos()
    for candidato in todos:
        if candidato["lead_id"] in whatsapp_ids:
            continue
        if dominio_email(candidato["email"]) in DOMINIOS_CONTROL:
            continue
        h = hash_email(candidato["email"])
        if h in sent_siempre or h in bloqueados_hoy or h in vistos or h in suprimidos:
            continue
        vistos.add(h)
        disponibles.append(candidato)

    total_disponibles = len(disponibles)

    # Dias 1-2: primero control (auto-envio + leads de prueba), luego NEW.
    destinatarios: list[dict[str, Any]] = []

    if dia == 1 and hash_email(EMAIL_CONTROL) not in sent_siempre:
        destinatarios.append(
            {
                "tipo": "CONTROL",
                "lead_id": None,
                "email": EMAIL_CONTROL,
                "nombre": NOMBRE_CONTROL,
                "empresa": "Veyra Soluciones",
                "sector": SECTOR_CONTROL,
                "priority": None,
                "via": "autoenvio",
                "first_contact_at": None,
                "owner_id": None,
            }
        )

    if dia <= 2:
        # Los dominios de control ya se excluyeron en `disponibles`: aquí solo
        # quedan correos que Resend sí entrega.
        for candidato in disponibles:
            if len(destinatarios) >= cupo:
                break
            destinatarios.append(
                {
                    "tipo": "LEAD",
                    "lead_id": candidato["lead_id"],
                    "email": candidato["email"],
                    "nombre": candidato["nombre"],
                    "empresa": candidato["empresa"],
                    "sector": candidato["sector"],
                    "priority": candidato["priority"],
                    "via": candidato["via"],
                    "first_contact_at": None,
                    "owner_id": candidato["owner_id"],
                }
            )
    else:
        for candidato in disponibles:
            if len(destinatarios) >= cupo:
                break
            destinatarios.append(
                {
                    "tipo": "LEAD",
                    "lead_id": candidato["lead_id"],
                    "email": candidato["email"],
                    "nombre": candidato["nombre"],
                    "empresa": candidato["empresa"],
                    "sector": candidato["sector"],
                    "priority": candidato["priority"],
                    "via": candidato["via"],
                    "first_contact_at": None,
                    "owner_id": candidato["owner_id"],
                }
            )

    # Necesitamos first_contact_at real para no sobreescribirlo en Supabase.
    ids = {d["lead_id"] for d in destinatarios if d["lead_id"]}
    if ids:
        respuesta = cliente._request(
            "GET",
            "/rest/v1/leads",
            params={"select": "id,first_contact_at", "id": f"in.({','.join(sorted(ids))})", "limit": len(ids)},
        )
        if respuesta.status_code == 200:
            por_first = {fila["id"]: fila.get("first_contact_at") for fila in respuesta.json()}
            for d in destinatarios:
                if d["lead_id"] in por_first:
                    d["first_contact_at"] = por_first[d["lead_id"]]

    return destinatarios, plantilla, total_disponibles


# ==========================================================================
# Modos de operacion
# ==========================================================================


def modo_plan(cliente: ClienteSupabase, estado: EstadoWarmup, total_dias: int) -> None:
    """Muestra la rampa completa y los destinatarios que saldrian, sin enviar."""
    hoy = date.today()
    dia = estado.dia_actual(hoy)
    rampa = rampa_completa(total_dias)

    LOGGER.info("=" * 74)
    LOGGER.info("PLAN DEL WARM-UP (nada se envia) — edwin@veyrasoluciones.com via Resend")
    LOGGER.info("=" * 74)
    if total_dias != RAMP_DIAS_BASE:
        LOGGER.info("Rampa re-escalada: %s dias (base %s, mapeo proporcional)", total_dias, RAMP_DIAS_BASE)
    LOGGER.info("Plantilla dia 0: '%s' (subject '%s')", NOMBRE_PLANTILLA_ESPERADO, SUBJECT_DIA_0)
    LOGGER.info("Remitente     : %s", FROM_EMAIL)
    LOGGER.info("")
    LOGGER.info("Rampa de envios (dia -> cupo/dia):")
    for d, cupo in rampa:
        enviados = len(estado.enviados_del_dia(d))
        marca = "  <- HOY" if d == dia else ""
        LOGGER.info("  Dia %2s: %2s envios  [llevamos %2s]%s", d, cupo, enviados, marca)
    total_rampa = sum(c for _, c in rampa)
    LOGGER.info("  Total del plan: %s envios en %s dias", total_rampa, total_dias)

    if estado.fecha_inicio:
        LOGGER.info("")
        LOGGER.info("Warm-up iniciado el %s -> hoy es el DIA %s de %s", estado.fecha_inicio, dia, total_dias)
        if cupo_para_dia(dia, total_dias) is None:
            LOGGER.info("La rampa ya termino (dia %s > %s). No queda nada por enviar.", dia, total_dias)

    LOGGER.info("")
    LOGGER.info("Destinatarios del DIA %s (seleccion real, sin enviar):", dia)
    cupo = cupo_para_dia(dia, total_dias)
    if cupo is None:
        LOGGER.info("  (dia fuera de rampa: no hay envios pendientes)")
        LOGGER.info("")
        return

    destinatarios, plantilla, total_disponibles = seleccionar_destinatarios(cliente, estado, dia, cupo)
    LOGGER.info("  Cupo del dia %s: %s | ya enviados hoy: %s | disponibles: %s",
                dia, cupo, len(estado.enviados_del_dia(dia)), total_disponibles)
    LOGGER.info("")
    LOGGER.info("  Primeros %s destinatarios seleccionados:", min(10, len(destinatarios)))
    for i, d in enumerate(destinatarios[:10], 1):
        LOGGER.info(
            "  %2s. [%s] prio=%s empresa=%.45s email=%s via=%s",
            i,
            d["tipo"],
            d["priority"] if d["priority"] is not None else "-",
            d["empresa"],
            enmascarar_email(d["email"]),
            d["via"],
        )
    if len(destinatarios) > 10:
        LOGGER.info("      ... y %s mas", len(destinatarios) - 10)
    if not destinatarios:
        LOGGER.info("  (sin destinatarios: cupo cubierto o no quedan elegibles)")

    LOGGER.info("")
    LOGGER.info("Vista previa del cuerpo (personalizado con el 1er destinatario):")
    if destinatarios:
        d = destinatarios[0]
        cuerpo = plantilla.get("body_text") or ""
        personalizado = personalizar_dia_0(cuerpo, d["nombre"], d["sector"], d["empresa"])
        for linea in personalizado.splitlines():
            LOGGER.info("    | %s", linea)
    LOGGER.info("")


def modo_status(estado: EstadoWarmup, total_dias: int, resend_key: str | None) -> None:
    """Progreso local por dia + monitoreo de Resend (opens/bounces) si hay key."""
    hoy = date.today()
    dia = estado.dia_actual(hoy)
    rampa = rampa_completa(total_dias)

    LOGGER.info("=" * 74)
    LOGGER.info("STATUS DEL WARM-UP — edwin@veyrasoluciones.com")
    LOGGER.info("=" * 74)

    if not estado.fecha_inicio:
        LOGGER.info("El warm-up NO ha iniciado (primer --send fijara el dia 1).")
    else:
        LOGGER.info(
            "Iniciado el %s -> hoy es el DIA %s de %s", estado.fecha_inicio, dia, total_dias
        )

    LOGGER.info("")
    LOGGER.info("Progreso por dia (del estado local %s):", RUTA_ESTADO.name)
    LOGGER.info("  Dia | Cupo | Enviados | Destinatarios")
    for d, cupo in rampa:
        enviados_dia = estado.enviados_del_dia(d)
        nombres = ", ".join(e["email_enmascarado"] for e in enviados_dia[:8])
        if len(enviados_dia) > 8:
            nombres += f" (+{len(enviados_dia) - 8})"
        LOGGER.info("  %4s | %4s | %8s | %s%s", d, cupo, len(enviados_dia), nombres or "-", "  <- HOY" if d == dia else "")
    fuera_rampa = [e for e in estado.envios if e.get("dia") not in {d for d, _ in rampa}]
    if fuera_rampa:
        LOGGER.info("  (ademas: %s envios registrados fuera de los dias de la rampa)", len(fuera_rampa))

    todos_envios = estado.envios
    sent = [e for e in todos_envios if e.get("status") == STATUS_SENT]
    fallidos = [e for e in todos_envios if e.get("status") == STATUS_FAILED]
    LOGGER.info("")
    LOGGER.info(
        "Total: %s registros | %s SENT | %s FAILED | %s INTENDED sin confirmar",
        len(todos_envios), len(sent), len(fallidos),
        len(todos_envios) - len(sent) - len(fallidos),
    )
    if sent:
        LOGGER.info("Ultimos 10 envios confirmados:")
        for e in sent[-10:]:
            LOGGER.info(
                "  dia %2s | %s | resend_id=%s | %s",
                e.get("dia"), e.get("email_enmascarado"), (e.get("resend_id") or "?")[:12], e.get("tipo", "LEAD"),
            )

    # -------- Monitoreo via Resend (opens/bounces) si la key esta --------
    LOGGER.info("")
    if not resend_key:
        LOGGER.info("Monitoreo Resend: NO disponible (RESEND_API_KEY no configurada en backend/.env).")
        LOGGER.info("Solo se muestra el estado local.")
        return

    LOGGER.info("Monitoreo Resend (GET /emails, ultimos %s):", LIMITE_LISTADO_RESEND)
    try:
        cliente_resend = ClienteResend(resend_key)
        emails = cliente_resend.listar_emails()
        cliente_resend.cerrar()
    except RuntimeError as exc:
        LOGGER.warning("No se pudo consultar Resend: %s", exc)
        LOGGER.info("Solo se muestra el estado local.")
        return

    nuestros_ids = {e.get("resend_id") for e in sent if e.get("resend_id")}
    nuestros = [e for e in emails if e.get("id") in nuestros_ids or e.get("from") == FROM_EMAIL]
    if not nuestros:
        LOGGER.info("  Resend no devolvio emails de este remitente todavia.")
        return

    conteo_eventos: dict[str, int] = {}
    for e in nuestros:
        evento = (e.get("last_event") or "desconocido").lower()
        conteo_eventos[evento] = conteo_eventos.get(evento, 0) + 1
    LOGGER.info("  Emails de %s en los ultimos %s de Resend:", FROM_EMAIL, LIMITE_LISTADO_RESEND)
    for evento, cantidad in sorted(conteo_eventos.items(), key=lambda x: -x[1]):
        LOGGER.info("    last_event=%-12s %s", evento, cantidad)


def modo_revisar_rebotes(cliente: ClienteSupabase, estado: EstadoWarmup, resend_key: str) -> int:
    """Revisa en Resend los envios SENT y suprime rebotes duros/quejas.

    Para cada resend_id consulta GET /emails/{id}. Si `last_event` es
    `bounced` o `complained`, el email entra a la lista de supresión y el
    contacto queda con do_not_contact=true en el CRM. Idempotente: re-ejecutar
    no vuelve a marcar lo ya suprimido.
    """
    if not resend_key:
        LOGGER.error("RESEND_API_KEY no configurada: no se pueden revisar rebotes.")
        return 1

    resend = ClienteResend(resend_key)
    revisados = 0
    suprimidos = 0
    fallidos = 0
    try:
        for envio in estado.envios:
            if envio.get("status") != STATUS_SENT or not envio.get("resend_id"):
                continue
            h = envio.get("email_hash")
            if h and h in estado.hashes_suprimidos():
                continue
            revisados += 1
            try:
                info = resend.estado_email(str(envio["resend_id"]))
            except RuntimeError as exc:
                LOGGER.warning("No se pudo consultar %s: %s", str(envio["resend_id"])[:12], exc)
                fallidos += 1
                continue
            if not info:
                continue
            evento = str(info.get("last_event") or "").lower()
            if evento not in ("bounced", "complained"):
                continue
            destinatario = ""
            to = info.get("to")
            if isinstance(to, list) and to:
                destinatario = str(to[0]).strip()
            if not destinatario:
                LOGGER.warning(
                    "Rebote %s sin destinatario en Resend; no se puede suprimir.",
                    str(envio["resend_id"])[:12],
                )
                continue
            nuevo = estado.suprimir(destinatario, evento, str(envio["resend_id"]))
            if not nuevo:
                continue
            suprimidos += 1
            LOGGER.info("SUPRIMIDO por %s: %s", evento, enmascarar_email(destinatario))
            try:
                filas = cliente.marcar_no_contactar(destinatario)
                LOGGER.info("  do_not_contact=true en %s contacto(s) del CRM.", filas)
            except RuntimeError as exc:
                LOGGER.warning("  no se pudo marcar do_not_contact: %s", exc)
    finally:
        resend.cerrar()

    LOGGER.info(
        "Revision de rebotes: %s envios revisados, %s suprimidos, %s sin consultar.",
        revisados, suprimidos, fallidos,
    )
    return 0


def modo_send(cliente: ClienteSupabase, estado: EstadoWarmup, total_dias: int, resend_key: str) -> int:
    """Envia los destinatarios del dia actual segun la rampa. Idempotente."""
    hoy = date.today()
    dia = estado.dia_actual(hoy)

    cupo = cupo_para_dia(dia, total_dias)
    if cupo is None:
        LOGGER.info(
            "Dia %s fuera de la rampa de %s dias: el warm-up ya termino. Nada que enviar.",
            dia, total_dias,
        )
        return 0

    ya_enviados = estado.enviados_del_dia(dia)
    faltan = cupo - len(ya_enviados)
    if faltan <= 0:
        LOGGER.info(
            "Dia %s: cupo de %s ya cubierto (%s enviados). Nada que enviar (idempotencia).",
            dia, cupo, len(ya_enviados),
        )
        return 0

    LOGGER.info("=" * 74)
    LOGGER.info("ENVIO DEL DIA %s/%s — cupo %s, ya enviados hoy %s, faltan %s",
                dia, total_dias, cupo, len(ya_enviados), faltan)
    LOGGER.info("=" * 74)

    # Fijar el inicio del warm-up en el primer envio real.
    estado.fijar_fecha_inicio(hoy)

    destinatarios, plantilla, total_disponibles = seleccionar_destinatarios(cliente, estado, dia, faltan)
    if not destinatarios:
        LOGGER.info("No quedan destinatarios elegibles (disponibles: %s). Fin del envio de hoy.", total_disponibles)
        return 0

    cuerpo_base = plantilla.get("body_text") or ""
    asunto_base = plantilla.get("subject") or SUBJECT_DIA_0
    LOGGER.info("Plantilla dia 0: '%s' (id %s)", plantilla.get("name"), str(plantilla.get("id"))[:8])
    LOGGER.info("")

    cliente_resend = ClienteResend(resend_key)
    enviados_ok = 0
    fallas = 0
    try:
        for d in destinatarios:
            email_masc = enmascarar_email(d["email"])
            cuerpo = personalizar_dia_0(cuerpo_base, d["nombre"], d["sector"], d["empresa"])
            # Sanity: jamas salir a produccion con un placeholder sin sustituir.
            if "[Nombre]" in cuerpo or "[sector de la empresa]" in cuerpo:
                LOGGER.error("Cuerpo con placeholder sin sustituir para %s: se omite el envio.", email_masc)
                fallas += 1
                continue

            LOGGER.info("Enviando a %s [%s] (prio=%s, empresa=%.40s)",
                        email_masc, d["tipo"], d["priority"], d["empresa"])

            registro = estado.registrar_intencion(d["lead_id"], d["email"], dia, d["tipo"])
            try:
                resend_id = cliente_resend.enviar_email(
                    d["email"], asunto_base, cuerpo, cuerpo_a_html(cuerpo)
                )
            except RuntimeError as exc:
                estado.marcar_fallido(registro, str(exc))
                fallas += 1
                LOGGER.error("  FALLO el envio a %s: %s", email_masc, exc)
                # 401/403 = la key no sirve: abortar el resto del run.
                if "rechazo la API key" in str(exc):
                    LOGGER.error("Se aborta el run: la API key no es valida.")
                    return 1
                continue

            estado.confirmar(registro, resend_id)
            enviados_ok += 1
            LOGGER.info("  OK resend_id=%s", resend_id)

            # Espejo en Supabase para la Torre de Control.
            if d["lead_id"]:
                try:
                    cliente.actualizar_lead_contactado(d["lead_id"], bool(d.get("first_contact_at")))
                    cliente.registrar_activity(
                        d["lead_id"], d.get("owner_id"), dia, resend_id, email_masc
                    )
                except RuntimeError as exc:
                    LOGGER.warning(
                        "Enviado a %s pero el espejo en Supabase fallo (%s). "
                        "El envio YA esta confirmado en el estado local.",
                        email_masc, exc,
                    )
    finally:
        cliente_resend.cerrar()

    LOGGER.info("")
    LOGGER.info("Resumen del dia %s: %s enviados OK, %s fallos (cupo %s, disponibles %s).",
                dia, enviados_ok, fallas, cupo, total_disponibles)
    return 0 if fallas == 0 else 1


# ==========================================================================
# Logging
# ==========================================================================


def configurar_logging() -> None:
    RUTA_LOG.parent.mkdir(parents=True, exist_ok=True)
    LOGGER.setLevel(logging.INFO)
    formato = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    consola = logging.StreamHandler(sys.stdout)
    consola.setFormatter(formato)
    LOGGER.addHandler(consola)

    archivo = logging.FileHandler(RUTA_LOG, encoding="utf-8")
    archivo.setFormatter(formato)
    LOGGER.addHandler(archivo)


# ==========================================================================
# Main
# ==========================================================================


def main() -> int:
    try:
        if sys.stdout and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - defensivo
        pass

    parser = argparse.ArgumentParser(
        description=(
            "Warm-up del dominio edwin@veyrasoluciones.com via Resend: envia el "
            "email 1 de la secuencia Veyra MRI Outbound 30d con una rampa de "
            "14 dias a leads NEW del CRM Mapache."
        ),
        epilog=(
            "Ejemplos:\n"
            "  python warmup_resend.py --plan\n"
            "  python warmup_resend.py --plan --days 21\n"
            "  python warmup_resend.py --status\n"
            "  python warmup_resend.py --send\n"
            "\n"
            "Tarea de Windows: VeyraWarmupDaily (09:05, deshabilitada hasta "
            "poner RESEND_API_KEY en backend/.env; habilitar con "
            "schtasks /Change /TN VeyraWarmupDaily /ENABLE)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--send", action="store_true", help="Envia los del dia actual segun la rampa.")
    parser.add_argument("--status", action="store_true", help="Muestra el progreso por dia y el monitoreo de Resend.")
    parser.add_argument("--plan", action="store_true", help="Muestra la rampa y los destinatarios SIN enviar nada.")
    parser.add_argument("--check-bounces", action="store_true",
                        help="Revisa los envios en Resend y suprime (do_not_contact) los rebotes duros y quejas.")
    parser.add_argument("--days", type=int, default=DIAS_PREDETERMINADOS,
                        help=f"Duracion de la rampa en dias (default {DIAS_PREDETERMINADOS}).")
    parser.add_argument("--env", default=str(RUTA_ENV_PREDETERMINADA),
                        help="Ruta del .env con SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY y RESEND_API_KEY.")
    args = parser.parse_args()

    modos = [m for m in (args.send, args.status, args.plan, args.check_bounces) if m]
    if len(modos) != 1:
        parser.error("Elije exactamente un modo: --send, --status, --plan o --check-bounces.")
    if args.days < 1:
        parser.error("--days debe ser >= 1.")

    configurar_logging()

    # ---- Credenciales: la API key SOLO del .env, jamas impresa ----------
    ruta_env = Path(args.env)
    if not ruta_env.exists():
        LOGGER.error("No existe el archivo .env en %s", ruta_env)
        return 1
    env = leer_env(ruta_env)

    faltantes_supabase = [k for k in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY") if not env.get(k)]
    if faltantes_supabase:
        LOGGER.error(
            "Faltan credenciales de Supabase en %s: %s. No se puede continuar.",
            ruta_env, ", ".join(faltantes_supabase),
        )
        return 1
    resend_key = env.get("RESEND_API_KEY", "").strip() or None

    estado = EstadoWarmup(RUTA_ESTADO)

    # ---- --send / --check-bounces exigen API key -------------------------
    if args.send or args.check_bounces:
        if not resend_key:
            LOGGER.error("RESEND_API_KEY no configurada en backend/.env")
            LOGGER.error(
                "El modo --send envia correos reales y no puede ejecutarse sin la key. "
                "Añade RESEND_API_KEY al .env (la key esta hoy solo en Vercel) y "
                "habilita la tarea VeyraWarmupDaily cuando quieras automatizarlo."
            )
            return 1

    cliente = ClienteSupabase(env["SUPABASE_URL"], env["SUPABASE_SERVICE_ROLE_KEY"])
    try:
        if args.plan:
            modo_plan(cliente, estado, args.days)
            return 0
        if args.status:
            modo_status(estado, args.days, resend_key)
            return 0
        if args.check_bounces:
            return modo_revisar_rebotes(cliente, estado, resend_key or "")
        return modo_send(cliente, estado, args.days, resend_key or "")
    finally:
        cliente.cerrar()


if __name__ == "__main__":
    sys.exit(main())
