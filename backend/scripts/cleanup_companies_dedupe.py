#!/usr/bin/env python3
"""Limpieza de duplicados en companies del CRM Mapache (Supabase PostgREST).

Standalone: no importa nada del paquete `app` (se rige por el schema en vivo).
Solo necesita httpx y las credenciales de backend/.env.

Qué hace:
  1. Descarga TODAS las companies paginando (PostgREST limita a 1000 por
     request; offset + order=id.asc).
  2. Agrupa por (owner_id, dedupe_key) — la constraint única de la tabla —
     y TAMBIEN por nombre normalizado (lower + trim + espacios internos
     colapsados) CON CONCIENCIA DE CIUDAD: la dedupe_key del sistema es
     slug(nombre)[:31] + "-" + slug(municipio)[:8], así que el mismo nombre
     en ciudades distintas es una sede distinta POR DISEÑO y no se fusiona.
     Solo se fusionan filas con la misma ciudad no-nula; si el grupo tiene
     2+ ciudades distintas se sub-agrupa por ciudad, y las filas con ciudad
     nula quedan vivas (ambiguas, reportadas).
  3. En cada grupo con >1 fila elige sobreviviente: la fila con más campos
     no-nulos de (phone, website, address, rating, google_maps_url);
     tiebreak: first_extracted_at más antiguo (luego created_at, luego id).
  4. ANTES de borrar, fusiona al sobreviviente los campos (phone, website,
     address, city) que tenga en NULL y algún duplicado lleno (PATCH).
  5. Verifica las FKs hijas (contacts, leads, activities, tasks,
     company_sources, company_signals, company_socials, search_results):
     si referencian un duplicado, las re-apunta al sobreviviente (PATCH de
     la tabla hija) ANTES de borrar la company. Si una fila hija choca con
     una constraint única del sobreviviente (uq_contacts_company_email,
     uq_leads_company_service, PK de search_results, etc.), el duplicado NO
     se borra: queda vivo y se reporta (nunca se borra un duplicado con
     referencias que no se pueden re-apuntar).
  6. Borra los duplicados con DELETE por id y re-cuenta el estado final.

Uso:
    python cleanup_companies_dedupe.py --dry-run     # plan completo, 0 escrituras
    python cleanup_companies_dedupe.py               # ejecuta la limpieza real
    python cleanup_companies_dedupe.py --env ruta/.env

Seguridad:
  - Nunca borra el sobreviviente ni companies ajenas al plan.
  - No toca leads.status / first_contact_at (solo lead.company_id si hace
    falta re-apuntar).
  - Los emails de contactos jamás se imprimen (solo conteos e ids).
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

# ==========================================================================
# Configuración
# ==========================================================================

RUTA_BACKEND = Path(__file__).resolve().parent.parent
RUTA_ENV_PREDETERMINADA = RUTA_BACKEND / ".env"

TIMEOUT_HTTP = 60.0
PAGINA = 1000          # máximo por defecto de PostgREST
CHUNK_IN = 100         # ids por filtro in.() (URL segura)
REINTENTOS = 3

# Columnas que se descargan de companies (lo justo para puntuar, fusionar
# y reportar).
COLUMNAS_COMPANIES = (
    "id,name,dedupe_key,owner_id,phone,website,address,city,email,"
    "rating,google_maps_url,google_place_id,website_domain,"
    "first_extracted_at,last_extracted_at,last_enriched_at,created_at"
)

# Tablas hijas con FK a companies. 'unicas' lista las columnas que forman la
# constraint única (además de company_id) para detectar colisiones al
# re-apuntar; None = sin unique que impida el re-apunte.
# NOTA schema en vivo (verificado via OpenAPI 2026-09-09):
#  - leads NO tiene service_id (la uq_leads_company_service original no existe).
#  - company_sources/company_signals no tienen field_name/signal_key: son
#    tablas vacias (0 filas) y no hay unique que impida re-apuntar.
#  - contacts conserva uq (company_id, lower(email)); search_results conserva
#    su PK (search_run_id, company_id); company_socials uq (company_id, platform, url).
#  - tasks en vivo NO tiene company_id (solo lead_id/contact_id): no es hija.
TABLAS_HIJAS: tuple[tuple[str, str, tuple[str, ...] | None], ...] = (
    # tabla, columnas a descargar, únicas (sin company_id)
    ("contacts", "id,company_id,email", ("email",)),
    ("leads", "id,company_id,contact_id", None),
    ("company_sources", "id,company_id,source_type", None),
    ("company_signals", "id,company_id,signal_type", None),
    ("company_socials", "id,company_id,platform,url", ("platform", "url")),
    ("search_results", "search_run_id,company_id", ("search_run_id",)),
    ("activities", "id,company_id", None),
)

# Campos que se fusionan del duplicado al sobreviviente cuando el
# sobreviviente los tiene vacíos.
CAMPOS_FUSION = ("phone", "website", "address", "city")

# Campos que puntúan al sobreviviente (más no-nulos = mejor).
CAMPOS_PUNTAJE = ("phone", "website", "address", "rating", "google_maps_url")

OWNER_NULL = "__NULL__"

LOGGER = logging.getLogger("cleanup_dedupe")


# ==========================================================================
# Utilidades
# ==========================================================================


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


def normalizar_nombre(nombre: str) -> str:
    """lower + trim + espacios internos colapsados (base de agrupar)."""
    return " ".join((nombre or "").split()).strip().lower()


def normalizar_ciudad(ciudad: Any) -> str | None:
    """lower + trim de la ciudad; None si viene vacia."""
    if es_vacio(ciudad):
        return None
    return " ".join(str(ciudad).split()).strip().lower()


def es_vacio(valor: Any) -> bool:
    """Null o string vacía cuentan como ausentes."""
    return valor is None or (isinstance(valor, str) and not valor.strip())


def parsear_ts(valor: Any) -> tuple[int, str]:
    """Clave ordenable de timestamp: (epoch, iso). Los None van al final."""
    if es_vacio(valor):
        return (9_999_999_999_999, "")
    texto = str(valor)
    try:
        dt = datetime.fromisoformat(texto.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (int(dt.timestamp()), texto)
    except ValueError:
        return (9_999_999_999_998, texto)


def id_corto(company_id: str | None) -> str:
    return (company_id or "?")[:8]


# ==========================================================================
# Cliente PostgREST con reintentos
# ==========================================================================


class ClientePostgREST:
    """Cliente HTTP minimo contra PostgREST (SELECT/PATCH/DELETE)."""

    def __init__(self, url_base: str, api_key: str) -> None:
        self.url_base = url_base.rstrip("/") + "/rest/v1/"
        self._headers = {
            "apikey": api_key,
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._cliente = httpx.Client(timeout=TIMEOUT_HTTP)

    def cerrar(self) -> None:
        self._cliente.close()

    def _request(
        self,
        metodo: str,
        ruta: str,
        *,
        params: dict[str, str] | None = None,
        json_body: Any = None,
    ) -> httpx.Response:
        url = self.url_base + ruta
        ultimo_error = "desconocido"
        for intento in range(1, REINTENTOS + 1):
            try:
                respuesta = self._cliente.request(
                    metodo, url, headers=self._headers, params=params, json=json_body
                )
            except httpx.HTTPError as exc:
                ultimo_error = f"error de transporte: {exc}"
                LOGGER.warning("Intento %s/%s fallo en %s %s: %s", intento, REINTENTOS, metodo, ruta, exc)
            else:
                if respuesta.status_code < 400:
                    return respuesta
                if respuesta.status_code == 429 or respuesta.status_code >= 500:
                    ultimo_error = f"HTTP {respuesta.status_code}: {respuesta.text[:300]}"
                    LOGGER.warning("Intento %s/%s fallo en %s %s: %s", intento, REINTENTOS, metodo, ruta, ultimo_error)
                else:
                    return respuesta  # 4xx definitivo: lo maneja el llamador
            if intento < REINTENTOS:
                time.sleep(2 * intento)
        raise RuntimeError(f"{metodo} {ruta} fallo tras {REINTENTOS} intentos: {ultimo_error}")

    # ------------------------------------------------------------- lectura

    def select_todos(self, tabla: str, *, columns: str, filters: dict[str, str] | None = None, order: str = "id") -> list[dict[str, Any]]:
        """SELECT completo paginando por offset (PostgREST satura a 1000)."""
        filas: list[dict[str, Any]] = []
        offset = 0
        while True:
            params: dict[str, str] = {
                "select": columns,
                "order": f"{order}.asc",
                "limit": str(PAGINA),
                "offset": str(offset),
            }
            if filters:
                params.update(filters)
            respuesta = self._request("GET", tabla, params=params)
            if respuesta.status_code != 200:
                raise RuntimeError(f"GET {tabla} devolvio HTTP {respuesta.status_code}: {respuesta.text[:300]}")
            lote = respuesta.json()
            if not isinstance(lote, list):
                raise RuntimeError(f"GET {tabla} devolvio un cuerpo inesperado: {str(respuesta.text)[:200]}")
            filas.extend(lote)
            if len(lote) < PAGINA:
                return filas
            offset += PAGINA

    def select_in_chunks(
        self, tabla: str, *, columns: str, ids: list[str], campo: str = "company_id", order: str = "id"
    ) -> list[dict[str, Any]]:
        """SELECT filtrando company_id=in.(...) por chunks de ids."""
        filas: list[dict[str, Any]] = []
        ids_unicos = sorted(set(ids))
        for inicio in range(0, len(ids_unicos), CHUNK_IN):
            chunk = ids_unicos[inicio : inicio + CHUNK_IN]
            lista = ",".join(chunk)
            filas.extend(
                self.select_todos(tabla, columns=columns, filters={campo: f"in.({lista})"}, order=order)
            )
        return filas

    # ------------------------------------------------------------ escritura

    def patch(self, tabla: str, filters: dict[str, str], data: dict[str, Any]) -> tuple[bool, int]:
        """PATCH; devuelve (ok, filas_afectadas_aprox)."""
        params: dict[str, str] = {k: f"eq.{v}" for k, v in filters.items()}
        respuesta = self._request("PATCH", tabla, params=params, json_body=data)
        if respuesta.status_code in (200, 204):
            filas = 0
            if respuesta.status_code == 200:
                try:
                    cuerpo = respuesta.json()
                    filas = len(cuerpo) if isinstance(cuerpo, list) else (1 if cuerpo else 0)
                except ValueError:
                    filas = 0
            return True, filas
        LOGGER.error("PATCH %s fallo (HTTP %s): %s", tabla, respuesta.status_code, respuesta.text[:300])
        return False, 0

    def delete(self, tabla: str, filters: dict[str, str]) -> bool:
        """DELETE; devuelve True si el servidor acepto."""
        params: dict[str, str] = {k: f"eq.{v}" for k, v in filters.items()}
        respuesta = self._request("DELETE", tabla, params=params)
        if respuesta.status_code in (200, 204):
            return True
        LOGGER.error("DELETE %s fallo (HTTP %s): %s", tabla, respuesta.status_code, respuesta.text[:300])
        return False


# ==========================================================================
# Plan de deduplicación
# ==========================================================================


def puntaje_fila(fila: dict[str, Any]) -> int:
    """Cantidad de campos no-nulos entre CAMPOS_PUNTAJE."""
    return sum(1 for campo in CAMPOS_PUNTAJE if not es_vacio(fila.get(campo)))


def elegir_sobreviviente(filas: list[dict[str, Any]]) -> dict[str, Any]:
    """Más campos no-nulos; tiebreak first_extracted_at antigua, luego created_at, luego id."""
    return sorted(
        filas,
        key=lambda f: (
            -puntaje_fila(f),
            parsear_ts(f.get("first_extracted_at")),
            parsear_ts(f.get("created_at")),
            str(f.get("id", "")),
        ),
    )[0]


def valor_fusion(campos: str, dups: list[dict[str, Any]]) -> Any:
    """Valor no-vacío de CAMPOS_FUSION tomado del duplicado mejor puntuado."""
    for dup in sorted(
        dups,
        key=lambda f: (
            -puntaje_fila(f),
            parsear_ts(f.get("first_extracted_at")),
            str(f.get("id", "")),
        ),
    ):
        if not es_vacio(dup.get(campos)):
            return dup[campos]
    return None


def construir_plan(companies: list[dict[str, Any]]) -> dict[str, Any]:
    """Construye el plan completo de deduplicación (sin tocar la base).

    Devuelve:
      grupos_g1, grupos_g2: detalle por grupo.
      sobrevivientes: {id_company: {campos_a_fusionar: valor}}
      por_borrar: {id_dup: {"grupo": etiqueta, "id_grupo": ..., "motivo_bloqueo": None|str}}
    """
    # ---- Grupo 1: (owner_id, dedupe_key) — la constraint única de la tabla
    g1: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for fila in companies:
        owner = fila.get("owner_id") or OWNER_NULL
        g1.setdefault((owner, fila.get("dedupe_key") or ""), []).append(fila)

    por_borrar: dict[str, dict[str, Any]] = {}
    grupos_g1: list[dict[str, Any]] = []
    for (owner, dedupe), filas in sorted(g1.items()):
        if len(filas) <= 1:
            continue
        sobreviviente = elegir_sobreviviente(filas)
        dups = [f for f in filas if f["id"] != sobreviviente["id"]]
        grupos_g1.append(
            {"tipo": "owner_dedupe_key", "owner_id": owner, "dedupe_key": dedupe,
             "sobreviviente": sobreviviente, "dups": dups, "nombres": sorted({normalizar_nombre(f["name"]) for f in filas})}
        )
        for dup in dups:
            por_borrar[dup["id"]] = {"grupo": "owner_dedupe_key", "id_grupo": f"{owner}|{dedupe}"}

    # ---- Grupo 2: nombre normalizado, con conciencia de ciudad.
    # La dedupe_key del sistema es slug(nombre)[:31] + "-" + slug(municipio)[:8]:
    # el MISMO nombre en ciudades distintas es una sede distinta por diseño
    # (ej. "Instituto Codesarrollo" presente en 12 municipios). Por eso:
    #   - grupo con <=1 ciudad no-nula (o todas nulas): mismo negocio, se fusiona.
    #   - grupo con >=2 ciudades distintas: son sedes legítimas -> solo se
    #     fusiona DENTRO de cada ciudad. Las filas con ciudad nula son
    #     ambiguas (no se sabe a qué sede pertenecen): quedan vivas, reportadas.
    vivas = [f for f in companies if f["id"] not in por_borrar]
    g2_por_nombre: dict[str, list[dict[str, Any]]] = {}
    for fila in vivas:
        g2_por_nombre.setdefault(normalizar_nombre(fila["name"]), []).append(fila)

    grupos_g2: list[dict[str, Any]] = []
    ids_ambiguos: list[str] = []
    for nombre, filas in sorted(g2_por_nombre.items()):
        if len(filas) <= 1:
            continue
        por_ciudad: dict[str | None, list[dict[str, Any]]] = {}
        for fila in filas:
            por_ciudad.setdefault(normalizar_ciudad(fila.get("city")), []).append(fila)
        ciudades_reales = [c for c in por_ciudad if c is not None]

        if len(ciudades_reales) <= 1:
            # Ciudad única (o solo nulas): todo el grupo es el mismo negocio.
            subgrupos: list[tuple[str, list[dict[str, Any]]]] = [
                (ciudades_reales[0] if ciudades_reales else "(nula)", filas)
            ]
        else:
            # Sedes legítimas: sub-agrupar por ciudad; ciudad nula = ambigua.
            subgrupos = [
                (ciudad, filas_ciudad)
                for ciudad, filas_ciudad in sorted(por_ciudad.items())
                if ciudad is not None and len(filas_ciudad) > 1
            ]
            ids_ambiguos.extend(f["id"] for f in por_ciudad.get(None, []))

        for ciudad, filas_sub in subgrupos:
            owners = {str(f.get("owner_id")) for f in filas_sub}
            sobreviviente = elegir_sobreviviente(filas_sub)
            dups = [f for f in filas_sub if f["id"] != sobreviviente["id"]]
            etiqueta = "nombre+ciudad_entre_owners" if len(owners) > 1 else "nombre+ciudad_mismo_owner_otra_key"
            grupos_g2.append(
                {"tipo": etiqueta, "nombre_norm": nombre, "ciudad": ciudad, "owners": sorted(owners),
                 "sobreviviente": sobreviviente, "dups": dups}
            )
            for dup in dups:
                por_borrar[dup["id"]] = {"grupo": "nombre+ciudad", "id_grupo": f"{nombre}|{ciudad}"}

    return {"grupos_g1": grupos_g1, "grupos_g2": grupos_g2, "por_borrar": por_borrar, "ambiguas": ids_ambiguos}


def fusiones_del_grupo(sobreviviente: dict[str, Any], dups: list[dict[str, Any]]) -> dict[str, Any]:
    """Campos (phone, website, address, city) que el sobreviviente puede heredar."""
    patch: dict[str, Any] = {}
    for campo in CAMPOS_FUSION:
        if es_vacio(sobreviviente.get(campo)):
            valor = valor_fusion(campo, dups)
            if valor is not None:
                patch[campo] = valor
    return patch


def _clave_unica(fila: dict[str, Any], unicas: tuple[str, ...]) -> tuple[Any, ...]:
    """Clave única de una fila hija (email en lower). None se conserva."""
    partes: list[Any] = []
    for u in unicas:
        valor = fila.get(u)
        partes.append(valor.lower() if (u == "email" and isinstance(valor, str)) else valor)
    return tuple(partes)


def detectar_colisiones(
    plan: dict[str, Any], hijos: dict[str, dict[str, list[dict[str, Any]]]]
) -> None:
    """Marca motivo_bloqueo en cada duplicado que no puede re-apuntarse.

    Modelo SECUENCIAL y fiel a la base:
      - Cada sobreviviente acumula las claves únicas de las filas hijas que
        recibirá (las propias + las de los duplicados re-apuntables antes
        que él). Un duplicado colisiona si alguna de sus filas hijas tiene
        una clave única que el acumulado del sobreviviente ya contiene.
      - Las únicas de leads/contacts son NULLS DISTINCT (solo la de
        companies se recreó con NULLS NOT DISTINCT): una fila hija con
        componente NULL jamás colisiona en la base real, así que se ignora
        para el chequeo.
    Un duplicado bloqueado queda vivo (no se re-apunta ni se borra).
    """
    por_borrar = plan["por_borrar"]
    grupos_todos = plan["grupos_g1"] + plan["grupos_g2"]

    # Claves únicas iniciales de cada sobreviviente, por tabla.
    claves_sobrev: dict[tuple[str, str], set[tuple[Any, ...]]] = {}
    ids_sobrev = {g["sobreviviente"]["id"] for g in grupos_todos}
    for tabla, _, unicas in TABLAS_HIJAS:
        if unicas is None:
            continue
        for fila in hijos.get(tabla, []):
            cid = fila.get("company_id")
            if cid in ids_sobrev:
                claves_sobrev.setdefault((tabla, cid), set()).add(_clave_unica(fila, unicas))

    for grupo in grupos_todos:
        sobrev_id = grupo["sobreviviente"]["id"]
        for dup in grupo["dups"]:
            dup_id = dup["id"]
            motivo = None
            for tabla, _, unicas in TABLAS_HIJAS:
                if unicas is None:
                    continue
                claves = claves_sobrev.get((tabla, sobrev_id), set())
                for fila in hijos.get(tabla, []):
                    if fila.get("company_id") != dup_id:
                        continue
                    clave = _clave_unica(fila, unicas)
                    if any(componente is not None for componente in clave) and clave in claves:
                        detalle = ", ".join(f"{u}={fila.get(u)}" for u in unicas)
                        motivo = (
                            f"colision uq en {tabla} ({detalle}) con el sobreviviente {id_corto(sobrev_id)}"
                        )
                        break
                if motivo:
                    break
            por_borrar[dup_id]["motivo_bloqueo"] = motivo
            if motivo is None:
                # El duplicado se fusiona: sus claves pasan al acumulado del
                # sobreviviente (las filas hijas aterrizaran alli).
                for tabla, _, unicas in TABLAS_HIJAS:
                    if unicas is None:
                        continue
                    for fila in hijos.get(tabla, []):
                        if fila.get("company_id") == dup_id:
                            claves_sobrev.setdefault((tabla, sobrev_id), set()).add(_clave_unica(fila, unicas))


# ==========================================================================
# Ejecución
# ==========================================================================


def aplicar_limpieza(cliente: ClientePostgREST, plan: dict[str, Any]) -> dict[str, Any]:
    """Ejecuta fusiones, re-apuntes y borrados del plan. Devuelve contadores."""
    por_borrar = plan["por_borrar"]
    grupos = plan["grupos_g1"] + plan["grupos_g2"]
    contadores: dict[str, int] = {
        "sobrevivientes_fusionados": 0,
        "duplicados_borrados": 0,
        "borrados_fallidos": 0,
        "referencias_reapuntadas": 0,
        "reapuntes_fallidos": 0,
    }
    reapuntes_por_tabla: dict[str, int] = {}

    for grupo in grupos:
        sobreviviente = grupo["sobreviviente"]
        dups_borrables = [d for d in grupo["dups"] if not plan["por_borrar"][d["id"]].get("motivo_bloqueo")]

        # 1) Fusionar campos nulos del sobreviviente con datos de los dups
        patch = fusiones_del_grupo(sobreviviente, grupo["dups"])
        if patch:
            ok, _ = cliente.patch("companies", {"id": sobreviviente["id"]}, patch)
            if ok:
                contadores["sobrevivientes_fusionados"] += 1
                LOGGER.info("Fusion -> %s (%.50s): %s", id_corto(sobreviviente["id"]), sobreviviente.get("name", ""), patch)
            else:
                LOGGER.error("Fusion fallida para %s: sigue sin fusionar", id_corto(sobreviviente["id"]))

        # 2) Re-apuntar referencias y borrar cada duplicado
        for dup in dups_borrables:
            dup_id = dup["id"]
            bloqueado = False
            for tabla, _, _unicas in TABLAS_HIJAS:
                filas = [f for f in grupo.get("hijos", {}).get(tabla, []) if f.get("company_id") == dup_id]
                if not filas:
                    continue
                ok, afectadas = cliente.patch(tabla, {"company_id": dup_id}, {"company_id": sobreviviente["id"]})
                if ok:
                    contadores["referencias_reapuntadas"] += max(afectadas, len(filas))
                    reapuntes_por_tabla[tabla] = reapuntes_por_tabla.get(tabla, 0) + max(afectadas, len(filas))
                    LOGGER.info("Re-apuntadas %s filas de %s: %s -> %s", max(afectadas, len(filas)), tabla, id_corto(dup_id), id_corto(sobreviviente["id"]))
                else:
                    bloqueado = True
                    contadores["reapuntes_fallidos"] += 1
                    LOGGER.error("Re-apunte fallido en %s para %s: se conserva el duplicado", tabla, id_corto(dup_id))
                    break
            if bloqueado:
                continue
            if cliente.delete("companies", {"id": dup_id}):
                contadores["duplicados_borrados"] += 1
                LOGGER.info("Borrada duplicada %s (%.50s)", id_corto(dup_id), dup.get("name", ""))
            else:
                contadores["borrados_fallidos"] += 1

    contadores["reapuntes_por_tabla"] = reapuntes_por_tabla
    return contadores


def contar_duplicados_reales(companies: list[dict[str, Any]]) -> tuple[int, int]:
    """(total_filas, duplicados) reconstruyendo el plan con las mismas reglas."""
    plan = construir_plan(companies)
    return len(companies), len(plan["por_borrar"])


# ==========================================================================
# Main
# ==========================================================================


def configurar_logging() -> None:
    LOGGER.setLevel(logging.INFO)
    formato = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    consola = logging.StreamHandler(sys.stdout)
    consola.setFormatter(formato)
    LOGGER.addHandler(consola)


def main() -> int:
    try:
        if sys.stdout and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - defensivo
        pass

    parser = argparse.ArgumentParser(
        description=(
            "Limpieza de duplicados en companies del CRM Mapache: agrupa por "
            "(owner_id, dedupe_key) y por nombre normalizado respetando la "
            "ciudad (sedes distintas por diseno), fusiona datos utiles al "
            "sobreviviente, re-apunta referencias hijas y borra los "
            "duplicados. Usa --dry-run para ver el plan sin escribir."
        )
    )
    parser.add_argument("--dry-run", action="store_true", help="Calcula y muestra el plan sin escribir nada.")
    parser.add_argument("--env", default=str(RUTA_ENV_PREDETERMINADA), help="Ruta del .env con SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY.")
    args = parser.parse_args()

    configurar_logging()
    ruta_env = Path(args.env)
    if not ruta_env.exists():
        LOGGER.error("No existe el archivo .env en %s", ruta_env)
        return 1
    env = leer_env(ruta_env)
    faltantes = [k for k in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY") if not env.get(k)]
    if faltantes:
        LOGGER.error("Faltan credenciales de Supabase en %s: %s", ruta_env, ", ".join(faltantes))
        return 1

    cliente = ClientePostgREST(env["SUPABASE_URL"], env["SUPABASE_SERVICE_ROLE_KEY"])
    try:
        LOGGER.info("Descargando companies paginadas de %s...", env["SUPABASE_URL"].rstrip("/"))
        companies = cliente.select_todos("companies", columns=COLUMNAS_COMPANIES)
        LOGGER.info("Companies descargadas: %s", len(companies))

        plan = construir_plan(companies)
        por_borrar = plan["por_borrar"]
        grupos = plan["grupos_g1"] + plan["grupos_g2"]

        n_g1 = len(plan["grupos_g1"])
        n_g2 = len(plan["grupos_g2"])
        n_borrar = len(por_borrar)
        LOGGER.info("")
        LOGGER.info("=" * 78)
        LOGGER.info("PLAN DE LIMPIEZA %s", "(DRY-RUN: nada se escribe)" if args.dry_run else "(EJECUCION REAL)")
        LOGGER.info("=" * 78)
        LOGGER.info("Companies total: %s", len(companies))
        LOGGER.info("Grupos (owner_id, dedupe_key) con >1 fila : %s", n_g1)
        LOGGER.info("Grupos por nombre+ciudad con >1 fila      : %s", n_g2)
        LOGGER.info("Filas ambiguas (ciudad nula, sedes multiples): %s (se conservan)", len(plan.get("ambiguas", [])))
        LOGGER.info("Filas duplicadas a borrar                 : %s", n_borrar)
        LOGGER.info("Companies que quedarian                   : %s", len(companies) - n_borrar)

        # ---- Detalle de cada grupo (siempre, para auditoria) ----
        LOGGER.info("")
        for i, grupo in enumerate(plan["grupos_g1"], 1):
            sob = grupo["sobreviviente"]
            LOGGER.info(
                "G1-%03d [%s] owner=%s dedupe='%s' sobrev=%s name='%.50s' puntaje=%s",
                i, grupo["tipo"], id_corto(grupo["owner_id"]), grupo["dedupe_key"][:40],
                id_corto(sob["id"]), sob.get("name", ""), puntaje_fila(sob),
            )
            for dup in grupo["dups"]:
                LOGGER.info(
                    "       dup=%s name='%.50s' puntaje=%s first_extracted=%s",
                    id_corto(dup["id"]), dup.get("name", ""), puntaje_fila(dup), dup.get("first_extracted_at"),
                )
        for i, grupo in enumerate(plan["grupos_g2"], 1):
            sob = grupo["sobreviviente"]
            LOGGER.info(
                "G2-%03d [%s] nombre='%s' ciudad=%s owners=%s sobrev=%s name='%.50s' puntaje=%s",
                i, grupo["tipo"], grupo["nombre_norm"][:50], grupo["ciudad"], len(grupo["owners"]),
                id_corto(sob["id"]), sob.get("name", ""), puntaje_fila(sob),
            )
            for dup in grupo["dups"]:
                LOGGER.info(
                    "       dup=%s owner=%s name='%.50s' puntaje=%s first_extracted=%s",
                    id_corto(dup["id"]), id_corto(dup.get("owner_id")), dup.get("name", ""),
                    puntaje_fila(dup), dup.get("first_extracted_at"),
                )

        if n_borrar == 0:
            LOGGER.info("")
            LOGGER.info("No hay duplicados que limpiar. Fin.")
            return 0

        # ---- Descargar referencias hijas de dups y sobrevivientes ----
        ids_dups = list(por_borrar.keys())
        ids_sobrev = [g["sobreviviente"]["id"] for g in grupos]
        LOGGER.info("")
        LOGGER.info("Consultando referencias hijas (dups: %s, sobrevivientes: %s)...", len(ids_dups), len(ids_sobrev))
        hijos: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for tabla, columnas, _unicas in TABLAS_HIJAS:
            # search_results no tiene columna id (PK compuesta): se ordena por created_at.
            orden = "created_at" if tabla == "search_results" else "id"
            filas_dup = cliente.select_in_chunks(tabla, columns=columnas, ids=ids_dups, order=orden) if ids_dups else []
            filas_sob = cliente.select_in_chunks(tabla, columns=columnas, ids=ids_sobrev, order=orden) if ids_sobrev else []
            hijos[tabla] = filas_dup + filas_sob
            if filas_dup or filas_sob:
                LOGGER.info("  %-16s dups=%s sobrev=%s", tabla, len(filas_dup), len(filas_sob))
        for grupo in grupos:
            grupo["hijos"] = {
                tabla: [f for f in hijos[tabla] if f.get("company_id") in {d["id"] for d in grupo["dups"]} | {grupo["sobreviviente"]["id"]}]
                for tabla, _, _ in TABLAS_HIJAS
            }

        detectar_colisiones(plan, hijos)

        bloqueados = [(dup_id, info["motivo_bloqueo"]) for dup_id, info in por_borrar.items() if info.get("motivo_bloqueo")]
        LOGGER.info("")
        LOGGER.info("Duplicados BLOQUEADOS (no se borran): %s", len(bloqueados))
        for dup_id, motivo in bloqueados:
            LOGGER.info("  %s: %s", id_corto(dup_id), motivo)

        plan_borrables = n_borrar - len(bloqueados)
        total_reapuntes = sum(
            len([f for f in hijos[tabla] if f.get("company_id") in por_borrar])
            for tabla, _, _ in TABLAS_HIJAS
        )
        LOGGER.info("")
        LOGGER.info("RESUMEN ESPERADO:")
        LOGGER.info("  a borrar             : %s de %s duplicados", plan_borrables, n_borrar)
        LOGGER.info("  bloqueados           : %s", len(bloqueados))
        LOGGER.info("  referencias a apuntar: %s filas hijas en total", total_reapuntes)
        LOGGER.info("  companies final      : %s", len(companies) - plan_borrables)

        if args.dry_run:
            LOGGER.info("")
            LOGGER.info("DRY-RUN terminado: no se escribio nada en Supabase.")
            return 0

        LOGGER.info("")
        LOGGER.info("Ejecutando limpieza real...")
        contadores = aplicar_limpieza(cliente, plan)
        LOGGER.info("")
        LOGGER.info("RESULTADO DE LA LIMPIEZA:")
        LOGGER.info("  sobrevivientes fusionados : %s", contadores["sobrevivientes_fusionados"])
        LOGGER.info("  duplicados borrados       : %s", contadores["duplicados_borrados"])
        LOGGER.info("  borrados fallidos         : %s", contadores["borrados_fallidos"])
        LOGGER.info("  referencias re-apuntadas  : %s", contadores["referencias_reapuntadas"])
        LOGGER.info("  reapuntes fallidos        : %s", contadores["reapuntes_fallidos"])
        for tabla, cantidad in sorted(contadores["reapuntes_por_tabla"].items()):
            LOGGER.info("    %-16s %s", tabla, cantidad)

        # ---- Verificación final: re-contar duplicados reales ----
        LOGGER.info("")
        LOGGER.info("Verificacion final: re-descargando companies...")
        companies_final = cliente.select_todos("companies", columns=COLUMNAS_COMPANIES)
        LOGGER.info("Companies total final: %s (antes: %s)", len(companies_final), len(companies))
        _, quedan = contar_duplicados_reales(companies_final)
        LOGGER.info("Filas duplicadas que QUEDAN (grupo1+nombre): %s", quedan)
        LOGGER.info("")
        LOGGER.info("Limpieza terminada.")
        return 0
    finally:
        cliente.cerrar()


if __name__ == "__main__":
    sys.exit(main())
