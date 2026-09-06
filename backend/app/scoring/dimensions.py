"""Las seis dimensiones del prospect score (§8 del diseño).

Cada dimensión devuelve un valor de 0 a 100 **y las razones que lo componen**.
Las razones no son decoración: el número tiene que poder discutirse. Si un
prospecto sale con 34, el comercial debe poder ver que fue porque el email
rebotó, no porque "el sistema lo dijo".

Todo lo de aquí son funciones puras sobre un `ScoreInput`: no tocan la base de
datos, así que se prueban con casos concretos en vez de con fixtures.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from app.core.enums import VerificationStatus
from app.utils.text import normalize_city, strip_accents

# Señal que indica que la empresa no tiene web. Vale doble cuando el servicio
# que se vende es precisamente hacer webs: es la oportunidad más directa que
# existe, y diluirla entre las demás señales la escondería.
NO_WEBSITE = "no_website"

# Palabras que delatan un servicio de desarrollo web en el nombre o en los
# problemas que dice resolver. Se comparan normalizadas (sin tildes).
_WEB_SERVICE_MARKERS = ("web", "sitio", "pagina", "landing", "ecommerce", "tienda online")

# Umbral a partir del cual una calificación de Google se considera buena.
GOOD_RATING = Decimal("4.0")

# Suelo del denominador de FIT. Con menos criterios evaluables que esto, el
# encaje no puede salir perfecto: acertar el único dato disponible no es
# evidencia suficiente.
_FIT_FLOOR = 60

# Vida media del dato extraído, en días: a los 30 días una extracción vale
# ~37% de lo que valía recién hecha.
TIMING_HALF_LIFE_DAYS = 30.0

# Una actividad en las últimas 48 h es la señal más fresca que hay.
RECENT_ACTIVITY_HOURS = 48


@dataclass(frozen=True, slots=True)
class DimensionScore:
    """Valor de una dimensión con el porqué."""

    value: int
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ScoreInput:
    """Todo lo que el motor necesita, ya leído de la base.

    Se pasa como un objeto plano para que las dimensiones no puedan disparar
    consultas perezosas sin querer: con SQLAlchemy async eso reventaría fuera
    del greenlet, y aquí además haría N consultas por prospecto.
    """

    # --- empresa
    company_category: str | None = None
    company_categories: tuple[str, ...] = ()
    company_city: str | None = None
    company_email: str | None = None
    company_phone: str | None = None
    company_website: str | None = None
    rating: Decimal | None = None
    reviews_count: int | None = None
    employee_range: str | None = None
    signals: frozenset[str] = frozenset()
    data_quality_score: int = 0
    extracted_at: datetime | None = None

    # --- servicio
    service_name: str = ""
    target_industries: tuple[str, ...] = ()
    opportunity_signals: tuple[str, ...] = ()
    problems_solved: tuple[str, ...] = ()
    target_cities: frozenset[str] = frozenset()

    # --- contacto
    contact_email: str | None = None
    contact_email_status: VerificationStatus = VerificationStatus.UNVERIFIED
    contact_is_role_email: bool = False
    contact_phone: str | None = None
    contact_linkedin: str | None = None

    # --- prospecto
    engagement_score: int = 0
    last_activity_at: datetime | None = None
    has_bounced: bool = False


def _norm(value: str | None) -> str:
    """Forma comparable: sin tildes, en minúsculas. `Panadería` == `panaderia`."""
    return strip_accents(value or "").lower().strip()


def fit(data: ScoreInput) -> DimensionScore:
    """¿Es el cliente ideal? Categoría, ciudad, tamaño y reputación.

    Solo se puntúa sobre lo que se puede evaluar. Un servicio recién creado no
    tiene aún ciudades donde prospectar, y una empresa puede no tener
    calificación en Maps: si esos puntos contaran igual como fallo, todos los
    prospectos de un servicio nuevo saldrían mediocres por un dato que nadie ha
    tenido ocasión de rellenar. Lo desconocido no puntúa ni penaliza; se saca
    del reparto.

    El denominador tiene un suelo (`_FIT_FLOOR`) para que no ocurra lo
    contrario: con un único criterio evaluable, acertarlo daría un encaje
    perfecto sobre casi ninguna evidencia.
    """
    earned = 0
    possible = 0
    reasons: list[str] = []

    targets = {_norm(x) for x in data.target_industries if x}
    categories = {_norm(x) for x in (data.company_category, *data.company_categories) if x}
    if targets:
        possible += 40
        matched = targets & categories
        if matched:
            earned += 40
            reasons.append(f"La categoría coincide con el servicio ({sorted(matched)[0]})")
        else:
            reasons.append("La categoría no está entre las que busca el servicio")
    else:
        reasons.append("El servicio no declara a qué industrias apunta")

    if data.target_cities:
        possible += 20
        if normalize_city(data.company_city) in data.target_cities:
            earned += 20
            reasons.append(f"Está en {data.company_city}, una ciudad donde prospectas")
        else:
            reasons.append("No está en las ciudades donde prospectas este servicio")

    possible += 20
    if _size_is_coherent(data):
        earned += 20
        reasons.append("El tamaño encaja con el cliente que buscas")
    elif data.reviews_count is None:
        reasons.append("No se sabe el tamaño del negocio: sin reseñas ni rango de empleados")
    else:
        reasons.append(f"Tamaño fuera de lo típico ({data.reviews_count} reseñas)")

    if data.rating is not None:
        possible += 20
        if data.rating >= GOOD_RATING:
            earned += 20
            reasons.append(f"Buena reputación en Google ({data.rating})")
        else:
            reasons.append(f"Calificación baja en Google ({data.rating})")

    value = round(100 * earned / max(_FIT_FLOOR, possible))
    return DimensionScore(min(100, value), reasons)


def _size_is_coherent(data: ScoreInput) -> bool:
    """Aproximación al tamaño del negocio.

    No hay un dato fiable de plantilla en Maps, así que se usa lo que sí hay:
    el rango de empleados cuando el enriquecimiento lo encontró, y si no, el
    número de reseñas como sustituto. Un negocio con 20-500 reseñas es el
    típico PyME establecido: ni recién abierto ni una cadena.
    """
    if data.employee_range:
        return True
    reviews = data.reviews_count
    return reviews is not None and 20 <= reviews <= 500


def opportunity(data: ScoreInput) -> DimensionScore:
    """¿Hay una necesidad detectada? Cruce de señales con el servicio."""
    wanted = {_norm(x) for x in data.opportunity_signals if x}
    found = {_norm(x) for x in data.signals}
    matched = wanted & found

    if not wanted:
        return DimensionScore(
            0,
            ["El servicio no declara qué señales busca: sin ellas no se puede medir"],
        )
    if not matched:
        return DimensionScore(0, ["No se detectó ninguna de las señales que busca el servicio"])

    points = 0
    reasons: list[str] = []
    for signal in sorted(matched):
        weight = 50 if signal == NO_WEBSITE and _sells_websites(data) else 25
        points += weight
        reasons.append(
            f"Señal «{signal}»" + (" (cuenta doble: vendes webs)" if weight == 50 else "")
        )

    return DimensionScore(min(100, points), reasons)


def _sells_websites(data: ScoreInput) -> bool:
    haystack = _norm(" ".join((data.service_name, *data.problems_solved)))
    return any(marker in haystack for marker in _WEB_SERVICE_MARKERS)


def contactability(data: ScoreInput) -> DimensionScore:
    """¿Puedo llegar a esta empresa sin quemar la reputación de envío?

    Un email inválido o que ya rebotó fuerza la dimensión a 0: da igual lo bien
    que encaje el prospecto, escribirle hace daño. Es la misma regla que aplica
    la supresión al enviar.
    """
    if data.has_bounced or data.contact_email_status is VerificationStatus.INVALID:
        motivo = "ya rebotó" if data.has_bounced else "es inválido"
        return DimensionScore(0, [f"El email {motivo}: no se le puede escribir"])

    email = data.contact_email or data.company_email
    if not email:
        return DimensionScore(0, ["Sin email: no hay por dónde escribirle"])

    points = 0
    reasons: list[str] = []

    if data.contact_email_status in (VerificationStatus.VERIFIED, VerificationStatus.MX_OK):
        points += 50
        reasons.append("El dominio del email acepta correo (MX verificado)")
    elif data.contact_email_status is VerificationStatus.SYNTAX_OK:
        points += 25
        reasons.append("El email tiene forma válida, sin verificar el dominio")
    else:
        reasons.append("Email sin verificar")

    if data.contact_email and not data.contact_is_role_email:
        points += 20
        reasons.append("Es el email de una persona, no un buzón genérico")
    elif data.contact_is_role_email:
        reasons.append("Es un buzón genérico (info@, contacto@): se responde menos")

    phone = data.contact_phone or data.company_phone
    if phone and phone.startswith("+"):
        points += 15
        reasons.append("Teléfono normalizado disponible")

    if data.contact_linkedin:
        points += 15
        reasons.append("Tiene LinkedIn: hay una segunda vía")

    return DimensionScore(min(100, points), reasons)


def data_quality(data: ScoreInput) -> DimensionScore:
    """Qué tan completa está la ficha de la empresa.

    Lo calcula el enriquecimiento sobre los campos clave; aquí solo se traduce
    a una razón legible.
    """
    value = max(0, min(100, data.data_quality_score))
    return DimensionScore(value, [f"Ficha completa al {value}%"])


def intent(data: ScoreInput) -> DimensionScore:
    """Interés demostrado, derivado del engagement acumulado."""
    if data.engagement_score <= 0:
        return DimensionScore(0, ["Todavía no ha interactuado con ningún correo"])
    value = min(100, round(data.engagement_score * 1.2))
    return DimensionScore(value, [f"Engagement acumulado de {data.engagement_score} puntos"])


def timing(data: ScoreInput, now: datetime | None = None) -> DimensionScore:
    """Frescura del dato. Un contacto extraído hace un año ya no es el mismo.

    Decae de forma exponencial en vez de por escalones para que un prospecto no
    pierda 30 puntos de golpe por cumplir un día más.
    """
    now = now or datetime.now(UTC)

    if data.extracted_at is None:
        base = 0
        reasons = ["Sin fecha de extracción"]
    else:
        days = max(0.0, (now - data.extracted_at).total_seconds() / 86400)
        base = round(100 * math.exp(-days / TIMING_HALF_LIFE_DAYS))
        reasons = [f"Extraído hace {round(days)} día(s)"]

    if data.last_activity_at is not None:
        hours = (now - data.last_activity_at).total_seconds() / 3600
        if 0 <= hours <= RECENT_ACTIVITY_HOURS:
            base += 20
            reasons.append("Tuvo actividad en las últimas 48 h")

    return DimensionScore(min(100, base), reasons)
