"""Importacion de leads unificados (CSV en D:) al CRM Mapache en Supabase.

Fuente : LEADS_UNIFICADOS_20260831.csv (8.738 filas de datos, 28 columnas).
Destino: tabla companies (+ contacts para los emails) via PostgREST.

Mapeo aplicado:
    nombre        -> companies.name            (NOT NULL; filas sin nombre se saltan)
    sector        -> companies.category + categories
    municipio     -> companies.city
    departamento  -> companies.state           (columna real del schema)
    pais          -> companies.country        (default "Colombia" si viene vacio)
    direccion     -> companies.address
    telefono      -> companies.phone en E.164 (to_e164 con region CO; fallback
                    basico +57 para moviles de 10 digitos empezando en 3) y
                    companies.phone_raw con el valor original
    email         -> companies.email (el primero valido) y una fila en
                    contacts por cada email valido (source=IMPORT)
    data_quality_score = 50 (valor neutro del pipeline)

dedupe_key: slug(nombre)[:31] + "-" + slug(municipio)[:8] (max 40 chars, el
limite de la columna). Se anexa la ciudad porque 172 nombres colisionan
entre municipios distintos ("INSTITUTO CODESARROLLO" existe en 12 pueblos de
Antioquia) y el slug del nombre a secas los fusionaria por error.

owner_id: namespace fijo de importacion 00000000-0000-4000-8000-000000000002
(distinto del ...0001 que usa el scheduler de scraping, para poder distinguir
el origen de los datos).

Contadores honestos: el resumen cuadra insertadas + duplicadas + ya_existentes
+ fallidas + sin_nombre == filas procesadas del CSV.

Uso:
    python import_leads_unificados.py --dry-run             # valida, no escribe
    python import_leads_unificados.py --dry-run --limit 50  # valida 50 filas
    python import_leads_unificados.py --limit 50            # importa 50 filas
    python import_leads_unificados.py                       # import completo

Requisitos: httpx y phonenumbers (presentes en el venv de Hermes).
No importa nada del paquete `app` del backend: es standalone para poder
correr con cualquier interprete que tenga las dos dependencias.
"""

from __future__ import annotations

import argparse
import csv
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
import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat

# --------------------------------------------------------------------------
# Constantes
# --------------------------------------------------------------------------

RUTA_CSV_PREDETERMINADA = r"D:\Proyectos IA\03_DATOS\CRM_DATASETS\LEADS_UNIFICADOS_20260831.csv"

# .env del backend: dos niveles arriba del script (backend/scripts/ -> backend/)
RUTA_ENV_PREDETERMINADA = Path(__file__).resolve().parent.parent / ".env"

# Namespace de owner para esta importacion (import-unificados). El pipeline de
# scraping usa ...0001; este ...0002 distingue el origen de los leads.
OWNER_ID_IMPORTACION = "00000000-0000-4000-8000-000000000002"

TAMANO_LOTE_PREDETERMINADO = 500
MAX_ERRORES_DETALLADOS = 20  # cuantos errores concretos conservar en el resumen

# Validacion de email: suficiente para filtrar concatenados y basura del CSV.
RE_EMAIL_VALIDO = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

# Para el slug (replica app/utils/text.py del backend).
RE_PUNTUACION = re.compile(r"[^\w\s]", flags=re.UNICODE)
RE_ESPACIOS = re.compile(r"\s+")

LOGGER = logging.getLogger("import_leads_unificados")


# --------------------------------------------------------------------------
# Utilidades de texto y normalizacion
# --------------------------------------------------------------------------


def quitar_tildes(valor: str) -> str:
    """Quita tildes y diacriticos ('Medellin' desde 'Medellín')."""
    descompuesto = unicodedata.normalize("NFD", valor)
    return "".join(ch for ch in descompuesto if unicodedata.category(ch) != "Mn")


def slugificar(valor: str) -> str:
    """Slug ASCII en minusculas separado por guiones (como el backend)."""
    limpio = RE_PUNTUACION.sub(" ", quitar_tildes(valor).lower())
    return RE_ESPACIOS.sub("-", limpio.strip()).strip("-")


def limpiar(valor: str | None) -> str:
    """Strip defensivo de cualquier celda del CSV."""
    return (valor or "").strip()


def clave_dedupe(nombre: str, municipio: str) -> str:
    """slug(nombre)[:31] + '-' + slug(municipio)[:8], maximo 40 caracteres.

    El limite de la columna es VARCHAR(40): 31 + 1 + 8 = 40 exactos.
    """
    parte_nombre = slugificar(nombre)[:31]
    parte_ciudad = slugificar(municipio)[:8] if municipio else ""
    if parte_ciudad:
        return f"{parte_nombre}-{parte_ciudad}"
    return parte_nombre


def email_valido(valor: str) -> str | None:
    """Devuelve el email si pasa la validacion sintactica, None si no."""
    email = limpiar(valor)
    if not email or len(email) > 254:
        return None
    return email if RE_EMAIL_VALIDO.fullmatch(email) else None


def normalizar_telefono(crudo: str | None) -> tuple[str | None, str | None, str]:
    """Normaliza un telefono del CSV.

    Devuelve (e164, raw, nota):
      - e164: telefono valido en formato E.164 (phonenumbers, region CO) o
        fallback basico +57 para moviles colombianos de 10 digitos empezando
        en 3. None si no se puede garantizar nada marcable.
      - raw: valor original recortado a 60 (limite de phone_raw), solo si
        parece un telefono (>= 5 digitos).
      - nota: 'e164' | 'fallback_57' | 'invalido' | 'vacio'.
    """
    original = limpiar(crudo)
    if not original:
        return None, None, "vacio"
    digitos = re.sub(r"\D", "", original)
    raw = original[:60] if len(digitos) >= 5 else None

    # 1) Ruta principal: phonenumbers con region CO (iguales que to_e164).
    try:
        parsed = phonenumbers.parse(original, "CO")
        if phonenumbers.is_valid_number(parsed):
            e164 = phonenumbers.format_number(parsed, PhoneNumberFormat.E164)
            return (e164[:20], raw, "e164")
    except NumberParseException:
        pass

    # 2) Fallback basico: movil colombiano de 10 digitos empezando en 3.
    if len(digitos) == 10 and digitos.startswith("3"):
        return ("+57" + digitos, raw, "fallback_57")

    # 3) Fijos de 7 digitos sin indicativo y numeracion extranjera: no se
    #    puede reconstruir un E.164 fiable; se conserva solo phone_raw.
    return (None, raw, "invalido")


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


# --------------------------------------------------------------------------
# Modelo interno de la importacion
# --------------------------------------------------------------------------


@dataclass
class RegistroEmpresa:
    """Una empresa unica lista para insertar, ya deduplicada en memoria."""

    clave: str
    nombre: str
    categoria: str | None = None
    ciudad: str | None = None
    departamento: str | None = None
    pais: str | None = None
    direccion: str | None = None
    telefono_e164: str | None = None
    telefono_raw: str | None = None
    emails: list[str] = field(default_factory=list)
    filas_colapsadas: int = 0  # filas CSV extra fusionadas en este registro
    numero_fila: int = 0  # primera fila del CSV donde aparecio

    def a_fila_companies(self) -> dict[str, Any]:
        """Payload para POST /rest/v1/companies (solo columnas reales)."""
        categoria = self.categoria or None
        return {
            "name": self.nombre,
            "category": categoria,
            "categories": [categoria] if categoria else [],
            "address": self.direccion or None,
            "city": self.ciudad or None,
            "state": self.departamento or None,
            "country": self.pais or "Colombia",
            "phone": self.telefono_e164,
            "phone_raw": self.telefono_raw,
            "email": self.emails[0] if self.emails else None,
            "data_quality_score": 50,
            "dedupe_key": self.clave,
            "owner_id": OWNER_ID_IMPORTACION,
            "is_permanently_closed": False,
            "first_extracted_at": ahora_iso(),
            "last_extracted_at": ahora_iso(),
        }

    def filas_de_contactos(self, company_id: str) -> list[dict[str, Any]]:
        """Payloads para POST /rest/v1/contacts (uno por email valido)."""
        contactos: list[dict[str, Any]] = []
        for indice, email in enumerate(self.emails):
            contactos.append(
                {
                    "company_id": company_id,
                    "email": email,
                    "phone": self.telefono_e164,
                    "source": "IMPORT",
                    "is_primary": indice == 0,
                    "owner_id": OWNER_ID_IMPORTACION,
                }
            )
        return contactos


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
                LOGGER.warning("Intento %s/%s fallo (%s) en %s", intento, reintentos, exc, ruta)
            else:
                if respuesta.status_code < 400:
                    return respuesta
                if respuesta.status_code == 429 or respuesta.status_code >= 500:
                    ultimo_error = f"HTTP {respuesta.status_code}: {respuesta.text[:300]}"
                    LOGGER.warning(
                        "Intento %s/%s fallo (%s) en %s", intento, reintentos, ultimo_error, ruta
                    )
                else:
                    # 4xx: error definitivo del payload, no se reintenta
                    return respuesta
            if intento < reintentos:
                time.sleep(2 * intento)

        raise RuntimeError(f"Request a {ruta} fallo tras {reintentos} intentos: {ultimo_error}")

    # ------------------------------------------------------------- lecturas

    def claves_existentes(self, tabla: str, owner_id: str) -> set[str]:
        """Pagina SELECT dedupe_key WHERE owner_id=... para precargar llaves."""
        claves: set[str] = set()
        offset = 0
        por_pagina = 1000
        while True:
            respuesta = self._request(
                "GET",
                f"/rest/v1/{tabla}",
                params={
                    "select": "dedupe_key",
                    "owner_id": f"eq.{owner_id}",
                    "limit": por_pagina,
                    "offset": offset,
                },
                prefer="count=exact",
            )
            # 206 = Partial Content: lo normal con limit + count=exact
            if respuesta.status_code not in (200, 206):
                raise RuntimeError(
                    f"No se pudieron leer dedupe_keys existentes: HTTP {respuesta.status_code}: "
                    f"{respuesta.text[:300]}"
                )
            filas = respuesta.json()
            claves.update(f["dedupe_key"] for f in filas if f.get("dedupe_key"))
            if len(filas) < por_pagina:
                break
            offset += por_pagina
        LOGGER.info("Claves existentes en %s para el owner de importacion: %s", tabla, len(claves))
        return claves

    def ids_por_claves(self, claves: list[str]) -> dict[str, str]:
        """Mapa dedupe_key -> id consultando en trozos de 100 (URLs cortas)."""
        ids: dict[str, str] = {}
        for inicio in range(0, len(claves), 100):
            trozo = claves[inicio : inicio + 100]
            respuesta = self._request(
                "GET",
                "/rest/v1/companies",
                params={
                    "select": "id,dedupe_key",
                    "owner_id": f"eq.{OWNER_ID_IMPORTACION}",
                    "dedupe_key": f"in.({','.join(trozo)})",
                    "limit": len(trozo),
                },
            )
            if respuesta.status_code not in (200, 206):
                LOGGER.warning(
                    "Lookup de ids fallo (HTTP %s): %s", respuesta.status_code, respuesta.text[:200]
                )
                continue
            for fila in respuesta.json():
                ids[fila["dedupe_key"]] = fila["id"]
        return ids

    def contar(self, tabla: str, owner_id: str) -> int | None:
        """COUNT exacto via cabecera content-range (Prefer: count=exact).

        Con filas y limit, PostgREST responde 206 (Partial Content): la
        cabecera content-range trae el total tras '/' (p.ej. "0-0/50").
        """
        respuesta = self._request(
            "GET",
            f"/rest/v1/{tabla}",
            params={"select": "id", "owner_id": f"eq.{owner_id}", "limit": 1},
            prefer="count=exact",
        )
        if respuesta.status_code not in (200, 206):
            return None
        rango = respuesta.headers.get("content-range", "")
        # Formato: "0-0/366" o "*/0"
        try:
            return int(rango.split("/")[1])
        except (IndexError, ValueError):
            return None

    def ejemplos(self, tabla: str, owner_id: str, cantidad: int = 5) -> list[dict[str, Any]]:
        respuesta = self._request(
            "GET",
            f"/rest/v1/{tabla}",
            params={
                "select": "name,city,state,category,phone,phone_raw,email,dedupe_key,created_at",
                "owner_id": f"eq.{owner_id}",
                "order": "created_at.desc",
                "limit": cantidad,
            },
        )
        return respuesta.json() if respuesta.status_code in (200, 206) else []

    # ------------------------------------------------------------- escrituras

    def insertar_lote(
        self, tabla: str, filas: list[dict[str, Any]], *, upsert: bool = False
    ) -> httpx.Response:
        """INSERT de un array. Para companies se usa merge-duplicates."""
        params: dict[str, str] = {}
        prefer = "return=representation"
        if upsert:
            prefer += ",resolution=merge-duplicates"
            if tabla == "companies":
                params["on_conflict"] = "owner_id,dedupe_key"
        return self._request(
            "POST", f"/rest/v1/{tabla}", params=params, json_body=filas, prefer=prefer
        )


# --------------------------------------------------------------------------
# Lectura y deduplicacion del CSV
# --------------------------------------------------------------------------


@dataclass
class EstadisticasCSV:
    filas_procesadas: int = 0
    sin_nombre: int = 0
    emails_validos: int = 0
    emails_invalidos: int = 0
    telefonos_e164: int = 0
    telefonos_fallback: int = 0
    telefonos_invalidos: int = 0


def _fusionar(registro: RegistroEmpresa, fila: dict[str, str]) -> None:
    """Fusiona una fila duplicada (misma clave) sobre el registro existente.

    Rellena solo campos vacios y agrega emails nuevos: asi ninguna fila
    duplicada pierde informacion util (telefono, direccion, email extra).
    """
    registro.filas_colapsadas += 1
    if not registro.categoria:
        registro.categoria = limpiar(fila.get("sector")) or None
    if not registro.ciudad:
        registro.ciudad = limpiar(fila.get("municipio")) or None
    if not registro.departamento:
        registro.departamento = limpiar(fila.get("departamento")) or None
    if not registro.direccion:
        registro.direccion = limpiar(fila.get("direccion")) or None

    e164, raw, _ = normalizar_telefono(fila.get("telefono"))
    if not registro.telefono_e164 and e164:
        registro.telefono_e164, registro.telefono_raw = e164, raw
    elif not registro.telefono_raw and raw:
        registro.telefono_raw = raw

    email = email_valido(fila.get("email"))
    if email and email.lower() not in {e.lower() for e in registro.emails}:
        registro.emails.append(email)


def leer_registros(
    ruta_csv: str, limite: int | None
) -> tuple[dict[str, RegistroEmpresa], EstadisticasCSV, list[str]]:
    """Lee el CSV y devuelve registros unicos en memoria (clave -> registro).

    Deduplica por clave_dedupe fusionando la informacion de las filas
    repetidas, para que los inserts lleguen sin conflictos internos.
    """
    registros: dict[str, RegistroEmpresa] = {}
    stats = EstadisticasCSV()

    with open(ruta_csv, encoding="utf-8-sig", newline="") as f:
        lector = csv.DictReader(f)
        if not lector.fieldnames:
            raise RuntimeError("El CSV no tiene cabecera")
        cabecera = [limpiar(c) for c in lector.fieldnames]
        obligatorias = {"nombre", "municipio", "sector", "telefono", "email"}
        faltantes = obligatorias - set(cabecera)
        if faltantes:
            raise RuntimeError(f"Columnas esperadas ausentes en el CSV: {sorted(faltantes)}")

        for numero_fila, fila in enumerate(lector, start=2):
            if limite is not None and stats.filas_procesadas >= limite:
                break
            stats.filas_procesadas += 1

            nombre = limpiar(fila.get("nombre"))
            if not nombre:
                stats.sin_nombre += 1
                continue

            # Estadisticas por fila (pre-colapso) para el reporte honesto
            if email_valido(fila.get("email")):
                stats.emails_validos += 1
            elif limpiar(fila.get("email")):
                stats.emails_invalidos += 1
            _, _, nota_tel = normalizar_telefono(fila.get("telefono"))
            if nota_tel == "e164":
                stats.telefonos_e164 += 1
            elif nota_tel == "fallback_57":
                stats.telefonos_fallback += 1
            elif nota_tel == "invalido":
                stats.telefonos_invalidos += 1

            municipio = limpiar(fila.get("municipio"))
            clave = clave_dedupe(nombre, municipio)
            existente = registros.get(clave)
            if existente is not None:
                _fusionar(existente, dict(fila))
                continue

            email = email_valido(fila.get("email"))
            e164, raw, _ = normalizar_telefono(fila.get("telefono"))
            registros[clave] = RegistroEmpresa(
                clave=clave,
                nombre=nombre,
                categoria=limpiar(fila.get("sector")) or None,
                ciudad=municipio or None,
                departamento=limpiar(fila.get("departamento")) or None,
                pais=limpiar(fila.get("pais")) or "Colombia",
                direccion=limpiar(fila.get("direccion")) or None,
                telefono_e164=e164,
                telefono_raw=raw,
                emails=[email] if email else [],
                numero_fila=numero_fila,
            )

    return registros, stats, cabecera


# --------------------------------------------------------------------------
# Importacion
# --------------------------------------------------------------------------


def _insertar_lote_empresas(
    cliente: ClienteSupabase, lote: list[RegistroEmpresa], resumen: dict[str, Any]
) -> list[RegistroEmpresa]:
    """Inserta un lote de companies; si el lote falla, reintenta fila a fila.

    Devuelve los registros que quedaron persistidos (insertadas o duplicadas
    por 409): los que tienen id garantizado en la base y pueden recibir
    contactos.
    """
    persistidos: list[RegistroEmpresa] = []
    payload = [r.a_fila_companies() for r in lote]
    try:
        respuesta = cliente.insertar_lote("companies", payload, upsert=True)
    except RuntimeError as exc:
        LOGGER.error("Lote de empresas agoto reintentos: %s", exc)
        respuesta = None

    if respuesta is not None and respuesta.status_code in (200, 201):
        for registro in lote:
            resumen["empresas_insertadas"] += 1
            resumen["filas_insertadas"] += 1 + registro.filas_colapsadas
            persistidos.append(registro)
        return persistidos

    detalle = "" if respuesta is None else respuesta.text[:300]
    LOGGER.error("Lote de empresas fallo (%s). Reintentando fila a fila.", detalle)

    for registro in lote:
        try:
            respuesta_unica = cliente.insertar_lote(
                "companies", [registro.a_fila_companies()], upsert=True
            )
        except RuntimeError as exc:
            LOGGER.error("Empresa fallida (%s): %s", registro.nombre, exc)
            resumen["empresas_fallidas"] += 1
            resumen["filas_fallidas"] += 1 + registro.filas_colapsadas
            continue
        if respuesta_unica.status_code in (200, 201):
            resumen["empresas_insertadas"] += 1
            resumen["filas_insertadas"] += 1 + registro.filas_colapsadas
            persistidos.append(registro)
        elif respuesta_unica.status_code == 409:
            # Duplicado no previsto (carrera con otra escritura): cuenta como
            # duplicada, no como fallida, porque la empresa ya existe.
            resumen["empresas_duplicadas_409"] += 1
            resumen["filas_duplicadas_409"] += 1 + registro.filas_colapsadas
            persistidos.append(registro)
        else:
            LOGGER.error(
                "Empresa fallida (fila %s, '%s'): HTTP %s %s",
                registro.numero_fila,
                registro.nombre,
                respuesta_unica.status_code,
                respuesta_unica.text[:200],
            )
            resumen["empresas_fallidas"] += 1
            resumen["filas_fallidas"] += 1 + registro.filas_colapsadas
            if len(resumen["errores"]) < MAX_ERRORES_DETALLADOS:
                resumen["errores"].append(
                    {
                        "fila": registro.numero_fila,
                        "nombre": registro.nombre,
                        "http": respuesta_unica.status_code,
                        "detalle": respuesta_unica.text[:200],
                    }
                )
    return persistidos


def _insertar_contactos(
    cliente: ClienteSupabase, contactos: list[dict[str, Any]], resumen: dict[str, Any]
) -> None:
    """Inserta contactos en lotes; los 409 cuentan como duplicados."""
    if not contactos:
        return
    try:
        respuesta = cliente.insertar_lote("contacts", contactos)
    except RuntimeError as exc:
        LOGGER.error("Lote de contactos agoto reintentos: %s", exc)
        respuesta = None

    if respuesta is not None and respuesta.status_code in (200, 201):
        resumen["contactos_creados"] += len(contactos)
        return

    LOGGER.warning("Lote de contactos fallo; reintentando uno a uno.")
    for contacto in contactos:
        try:
            resp = cliente.insertar_lote("contacts", [contacto])
        except RuntimeError:
            resumen["contactos_fallidos"] += 1
            continue
        if resp.status_code in (200, 201):
            resumen["contactos_creados"] += 1
        elif resp.status_code == 409:
            resumen["contactos_duplicados"] += 1
        else:
            resumen["contactos_fallidos"] += 1
            LOGGER.error(
                "Contacto fallido (company %s, email %s): HTTP %s",
                contacto.get("company_id"),
                contacto.get("email"),
                resp.status_code,
            )


def ejecutar_importacion(
    cliente: ClienteSupabase,
    registros: list[RegistroEmpresa],
    claves_existentes: set[str],
    tamano_lote: int,
    resumen: dict[str, Any],
) -> None:
    """Inserta los registros nuevos por lotes y crea sus contactos."""
    nuevos = [r for r in registros if r.clave not in claves_existentes]
    ya_existentes = [r for r in registros if r.clave in claves_existentes]
    resumen["registros_ya_existentes"] = len(ya_existentes)
    resumen["filas_ya_existentes"] = sum(1 + r.filas_colapsadas for r in ya_existentes)
    resumen["empresas_por_insertar"] = len(nuevos)

    # Los ya existentes tambien reciben contactos (idempotente: la unique
    # (company_id, lower(email)) hace que reinsertar de 409, contado como
    # duplicado en vez de falla).
    if ya_existentes:
        LOGGER.info("Procesando contactos de %s empresas ya existentes", len(ya_existentes))
        for inicio in range(0, len(ya_existentes), tamano_lote):
            sublote = ya_existentes[inicio : inicio + tamano_lote]
            ids_previos = cliente.ids_por_claves([r.clave for r in sublote])
            contactos: list[dict[str, Any]] = []
            for registro in sublote:
                company_id = ids_previos.get(registro.clave)
                if company_id is None:
                    continue
                contactos.extend(registro.filas_de_contactos(company_id))
            _insertar_contactos(cliente, contactos, resumen)

    total_lotes = (len(nuevos) + tamano_lote - 1) // tamano_lote
    LOGGER.info(
        "Empresas a insertar: %s (de %s registros unicos; %s ya existian)",
        len(nuevos),
        len(registros),
        len(ya_existentes),
    )

    for indice_lote in range(total_lotes):
        lote = nuevos[indice_lote * tamano_lote : (indice_lote + 1) * tamano_lote]
        LOGGER.info(
            "Lote %s/%s: %s empresas", indice_lote + 1, total_lotes, len(lote)
        )

        persistidos = _insertar_lote_empresas(cliente, lote, resumen)

        # Recuperar los ids reales (el upsert puede fusionar sobre filas
        # previas y el id generado localmente no sirve para contacts).
        ids = cliente.ids_por_claves([r.clave for r in persistidos])

        contactos: list[dict[str, Any]] = []
        for registro in persistidos:
            company_id = ids.get(registro.clave)
            if company_id is None:
                resumen["contactos_omitidos_sin_id"] += len(registro.emails)
                LOGGER.warning(
                    "Sin id para dedupe_key=%s (fila %s); se omiten %s contactos",
                    registro.clave,
                    registro.numero_fila,
                    len(registro.emails),
                )
                continue
            contactos.extend(registro.filas_de_contactos(company_id))

        _insertar_contactos(cliente, contactos, resumen)
        time.sleep(0.2)  # cortesia con el rate limit de Supabase


# --------------------------------------------------------------------------
# Reporte
# --------------------------------------------------------------------------


def imprimir_resumen(resumen: dict[str, Any]) -> None:
    LOGGER.info("=" * 72)
    LOGGER.info("RESUMEN DE LA IMPORTACION")
    LOGGER.info("=" * 72)
    LOGGER.info("Modo                     : %s", resumen["modo"])
    LOGGER.info("Filas CSV procesadas     : %s", resumen["filas_procesadas"])
    LOGGER.info("Registros unicos         : %s", resumen["registros_unicos"])
    LOGGER.info("-- Empresas (companies)")
    LOGGER.info("  Insertadas              : %s (filas: %s)", resumen["empresas_insertadas"], resumen["filas_insertadas"])
    LOGGER.info("  Ya existentes (skip)    : %s (filas: %s)", resumen["registros_ya_existentes"], resumen["filas_ya_existentes"])
    LOGGER.info("  Duplicadas en el CSV    : %s filas extra fusionadas", resumen["filas_duplicadas_csv"])
    LOGGER.info("  Duplicadas imprevistas  : %s empresas (409)", resumen["empresas_duplicadas_409"])
    LOGGER.info("  Fallidas                : %s empresas (filas: %s)", resumen["empresas_fallidas"], resumen["filas_fallidas"])
    LOGGER.info("  Sin nombre (omitidas)   : %s", resumen["sin_nombre"])
    LOGGER.info("-- Contactos (contacts)")
    LOGGER.info("  Creados                 : %s", resumen["contactos_creados"])
    LOGGER.info("  Duplicados (409)        : %s", resumen["contactos_duplicados"])
    LOGGER.info("  Fallidos                : %s", resumen["contactos_fallidos"])
    LOGGER.info("  Omitidos sin company id : %s", resumen["contactos_omitidos_sin_id"])
    LOGGER.info("-- Calidad de datos del CSV (por fila)")
    LOGGER.info("  Emails validos / invalidos : %s / %s", resumen["emails_validos"], resumen["emails_invalidos"])
    LOGGER.info(
        "  Telefonos E.164 / fallback +57 / invalidos : %s / %s / %s",
        resumen["telefonos_e164"],
        resumen["telefonos_fallback"],
        resumen["telefonos_invalidos"],
    )
    # En dry-run nada se inserta: la cuadratura usa las filas que SE INSERTARIAN
    if resumen["modo"] == "dry-run":
        # filas_duplicadas_csv ya esta incluida dentro de
        # filas_insertar_dryrun y filas_ya_existentes: no se suma aparte.
        cuadratura = (
            resumen["filas_insertar_dryrun"]
            + resumen["filas_ya_existentes"]
            + resumen["sin_nombre"]
        )
    else:
        # filas_duplicadas_csv ya esta incluida dentro de filas_insertadas /
        # filas_fallidas / filas_ya_existentes (cada registro suma
        # 1 + filas_colapsadas): no se suma aparte o se contaria dos veces.
        cuadratura = (
            resumen["filas_insertadas"]
            + resumen["filas_fallidas"]
            + resumen["filas_ya_existentes"]
            + resumen["filas_duplicadas_409"]
            + resumen["sin_nombre"]
        )
    LOGGER.info(
        "Cuadratura (insertadas+fallidas+existentes+duplicadas+sin_nombre): %s == %s -> %s",
        cuadratura,
        resumen["filas_procesadas"],
        "OK" if cuadratura == resumen["filas_procesadas"] else "DESCUADRE",
    )
    for error in resumen["errores"]:
        LOGGER.error("  Error detallado: %s", error)


def verificar_en_supabase(cliente: ClienteSupabase) -> None:
    """Verificacion POST-IMPORT con counts reales y filas de ejemplo."""
    LOGGER.info("=" * 72)
    LOGGER.info("VERIFICACION EN SUPABASE (lecturas reales)")
    LOGGER.info("=" * 72)

    total_companies = cliente.contar("companies", OWNER_ID_IMPORTACION)
    total_contactos = cliente.contar("contacts", OWNER_ID_IMPORTACION)
    LOGGER.info("COUNT companies (owner import-unificados): %s", total_companies)
    LOGGER.info("COUNT contacts  (owner import-unificados): %s", total_contactos)

    LOGGER.info("Ultimas 5 companies importadas:")
    for fila in cliente.ejemplos("companies", OWNER_ID_IMPORTACION, 5):
        LOGGER.info(
            "  - %s | ciudad=%s | dpto=%s | cat=%s | tel=%s | email=%s | key=%s",
            fila.get("name"),
            fila.get("city"),
            fila.get("state"),
            fila.get("category"),
            fila.get("phone"),
            fila.get("email"),
            fila.get("dedupe_key"),
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
        description="Importa los leads unificados del CSV al CRM Mapache (Supabase).",
        epilog=(
            "Ejemplos:\n"
            "  python import_leads_unificados.py --dry-run --limit 50\n"
            "  python import_leads_unificados.py --limit 50\n"
            "  python import_leads_unificados.py\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Valida y reporta sin escribir nada en Supabase (solo lecturas).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Procesa solo las primeras N filas de datos del CSV.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=TAMANO_LOTE_PREDETERMINADO,
        help=f"Registros por request de insert (default {TAMANO_LOTE_PREDETERMINADO}).",
    )
    parser.add_argument(
        "--csv",
        default=RUTA_CSV_PREDETERMINADA,
        help="Ruta del CSV de leads unificados.",
    )
    parser.add_argument(
        "--env",
        default=str(RUTA_ENV_PREDETERMINADA),
        help="Ruta del .env con SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY.",
    )
    args = parser.parse_args()

    ruta_log = Path(__file__).resolve().parent / "import_leads_unificados.log"
    configurar_logging(ruta_log)

    modo = "dry-run"
    if not args.dry_run:
        modo = f"limit={args.limit}" if args.limit else "completo"
    LOGGER.info("Modo: %s | CSV: %s | Lote: %s", modo, args.csv, args.batch_size)

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

    # ------------------------------------------------------------- lectura
    try:
        registros_por_clave, stats, cabecera = leer_registros(args.csv, args.limit)
    except FileNotFoundError:
        LOGGER.error("No existe el CSV: %s", args.csv)
        return 1
    except RuntimeError as exc:
        LOGGER.error("%s", exc)
        return 1

    LOGGER.info("Cabecera del CSV (%s columnas): %s", len(cabecera), cabecera)
    filas_duplicadas_csv = sum(r.filas_colapsadas for r in registros_por_clave.values())
    LOGGER.info(
        "Filas procesadas: %s | sin nombre: %s | registros unicos: %s | filas duplicadas fusionadas: %s",
        stats.filas_procesadas,
        stats.sin_nombre,
        len(registros_por_clave),
        filas_duplicadas_csv,
    )

    resumen: dict[str, Any] = {
        "modo": modo,
        "filas_procesadas": stats.filas_procesadas,
        "registros_unicos": len(registros_por_clave),
        "filas_duplicadas_csv": filas_duplicadas_csv,
        "sin_nombre": stats.sin_nombre,
        "emails_validos": stats.emails_validos,
        "emails_invalidos": stats.emails_invalidos,
        "telefonos_e164": stats.telefonos_e164,
        "telefonos_fallback": stats.telefonos_fallback,
        "telefonos_invalidos": stats.telefonos_invalidos,
        "empresas_insertadas": 0,
        "filas_insertadas": 0,
        "filas_insertar_dryrun": 0,
        "empresas_fallidas": 0,
        "filas_fallidas": 0,
        "empresas_duplicadas_409": 0,
        "filas_duplicadas_409": 0,
        "registros_ya_existentes": 0,
        "filas_ya_existentes": 0,
        "empresas_por_insertar": 0,
        "contactos_creados": 0,
        "contactos_duplicados": 0,
        "contactos_fallidos": 0,
        "contactos_omitidos_sin_id": 0,
        "errores": [],
    }

    cliente = ClienteSupabase(supabase_url, supabase_key)
    try:
        # Conexion + claves ya presentes para este owner (lectura, no escritura)
        try:
            claves_existentes = cliente.claves_existentes("companies", OWNER_ID_IMPORTACION)
        except RuntimeError as exc:
            LOGGER.error("No se pudo conectar con Supabase: %s", exc)
            return 1

        if args.dry_run:
            LOGGER.info("DRY-RUN: no se escribe nada en Supabase.")
            # Simular el efecto del skip para que los numeros sean honestos
            ya = sum(
                1 + r.filas_colapsadas
                for r in registros_por_clave.values()
                if r.clave in claves_existentes
            )
            resumen["registros_ya_existentes"] = sum(
                1 for r in registros_por_clave.values() if r.clave in claves_existentes
            )
            resumen["filas_ya_existentes"] = ya
            resumen["filas_insertar_dryrun"] = (
                stats.filas_procesadas - ya - stats.sin_nombre
            )
            resumen["empresas_por_insertar"] = sum(
                1 for r in registros_por_clave.values() if r.clave not in claves_existentes
            )
            contactos_estimados = sum(
                len(r.emails)
                for r in registros_por_clave.values()
                if r.clave not in claves_existentes
            )
            resumen["contactos_creados"] = contactos_estimados  # estimacion en dry-run
            LOGGER.info("Empresas que se insertarian: %s", resumen["empresas_por_insertar"])
            LOGGER.info("Contactos que se crearian (estimado): %s", contactos_estimados)
            imprimir_resumen(resumen)
            LOGGER.info("DRY-RUN: contactos_creados es una ESTIMACION, no escritura real.")
            return 0

        registros_ordenados = list(registros_por_clave.values())
        ejecutar_importacion(
            cliente, registros_ordenados, claves_existentes, args.batch_size, resumen
        )
        imprimir_resumen(resumen)
        verificar_en_supabase(cliente)
        return 0 if resumen["empresas_fallidas"] == 0 else 1
    finally:
        cliente.cerrar()


if __name__ == "__main__":
    sys.exit(main())
