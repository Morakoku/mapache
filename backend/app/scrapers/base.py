"""Contrato de los proveedores de descubrimiento (decisión D4).

Todos los proveedores —el scraper propio, la Places API, Apify— hablan este
mismo protocolo. El `DiscoveryWorker` no sabe cuál está usando, y por eso
cambiar de uno a otro es editar una línea de configuración en vez de
reescribir el flujo.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.core.enums import SourceType
from app.core.exceptions import ExternalServiceError
from app.utils.text import strip_accents

# Palabras que no discriminan nada: aparecen en cualquier categoría.
_STOPWORDS = frozenset(
    {"de", "del", "la", "el", "los", "las", "y", "en", "para", "con", "por", "un", "una"}
)


def _normalize(value: str | None) -> str:
    """Minúsculas y sin tildes, para comparar 'Panadería' con 'panaderia'."""
    return strip_accents(value or "").lower()


def _significant_words(value: str | None) -> set[str]:
    """Palabras del término de búsqueda que sirven para discriminar.

    Se quedan las de cuatro letras o más: 'bar' y 'spa' son legítimas, pero
    como subcadena aparecen en demasiados sitios —'barbería' contiene 'bar'— y
    aceptarlas devolvería el mismo ruido que no filtrar.
    """
    return {w for w in _normalize(value).split() if len(w) >= 4 and w not in _STOPWORDS}


@dataclass(frozen=True, slots=True)
class SearchQuery:
    """Lo que el usuario pidió, normalizado y agnóstico de proveedor."""

    business_type: str
    city: str
    keywords: list[str] = field(default_factory=list)
    zone: str | None = None
    country: str | None = None
    region: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    radius_km: float = 10.0
    limit: int = 100
    min_rating: float | None = None
    max_reviews: int | None = None
    language: str = "es"
    # Exigir que el resultado sea del tipo de negocio pedido y esté en la
    # ciudad pedida. Activado por defecto: sin esto, "panadería en Medellín"
    # devuelve cafeterías de Envigado.
    strict_match: bool = True

    @property
    def text_query(self) -> str:
        """Consulta en lenguaje natural: `restaurantes italianos en El Poblado`."""
        parts = [self.business_type, *self.keywords]
        term = " ".join(p.strip() for p in parts if p and p.strip())
        location = self.zone or self.city
        return f"{term} en {location}".strip()

    def matches_filters(self, place: RawPlace) -> bool:
        """Filtros que se aplican en cliente porque ningún proveedor los ofrece."""
        return self.rejection_reason(place) is None

    def rejection_reason(self, place: RawPlace) -> str | None:
        """Por qué se descarta esta ficha, o None si vale.

        Devuelve el motivo en vez de un booleano para poder decirle al usuario
        *cuántas* se cayeron y por qué: "trajo 80 y descartó 45 porque no eran
        panaderías" es accionable; "trajo 35" no.
        """
        if self.min_rating is not None and (place.rating is None or place.rating < self.min_rating):
            return "rating"
        if self.max_reviews is not None and (place.reviews_count or 0) > self.max_reviews:
            return "reviews"

        if self.strict_match:
            if not self._matches_business_type(place):
                return "tipo_de_negocio"
            if not self._matches_city(place):
                return "ciudad"
        return None

    def _matches_business_type(self, place: RawPlace) -> bool:
        """¿Es de verdad el tipo de negocio que se pidió?

        Google devuelve de todo alrededor del término: buscando "panadería"
        entran cafeterías, supermercados y hasta ferreterías que alguien
        etiquetó mal. Sin esta comprobación, la base se llena de empresas que
        hay que descartar a mano una por una.

        Se compara sin tildes y por palabras sueltas: "panaderia" tiene que
        aparecer en la categoría o en el nombre. Basta con que encaje **una**
        de las palabras buscadas —"restaurante italiano" acepta un sitio
        categorizado solo como "Restaurante"—.
        """
        terminos = _significant_words(self.business_type) | _significant_words(
            " ".join(self.keywords)
        )
        if not terminos:
            return True

        heno = _normalize(" ".join(filter(None, [place.category, *place.categories, place.name])))
        return any(t in heno for t in terminos)

    def _matches_city(self, place: RawPlace) -> bool:
        """¿Está en la ciudad pedida?

        El radio de Google se salta los límites municipales: una búsqueda en
        Medellín trae Envigado e Itagüí, que son otros municipios.

        Se busca el nombre de la ciudad pedida en la ciudad, la región y la
        dirección juntas. Los barrios pasan porque la dirección de Google en
        Colombia nombra el municipio: "Cra 43 #10, El Poblado, Medellín".

        Si no hay ni dirección ni ciudad, se acepta: el dato **falta**, no
        contradice, y descartar por falta de dato tira buenos prospectos.
        """
        if not self.city:
            return True

        donde = _normalize(" ".join(filter(None, [place.city, place.region, place.address])))
        if not donde.strip():
            return True
        return _normalize(self.city) in donde


@dataclass(slots=True)
class RawPlace:
    """Una empresa tal como la devuelve el proveedor, antes de normalizar.

    Deliberadamente permisivo: todo es opcional menos el nombre. Un resultado
    con solo nombre y dirección sigue siendo un prospecto válido — el
    enriquecimiento se encarga del resto.
    """

    name: str
    source: SourceType

    external_id: str | None = None  # place_id (API) o FTID (scraper)
    place_id: str | None = None  # ChIJ... — solo la Places API
    ftid: str | None = None  # 0x...:0x... — solo el scraper

    description: str | None = None
    category: str | None = None
    categories: list[str] = field(default_factory=list)

    address: str | None = None
    city: str | None = None
    region: str | None = None
    country: str | None = None
    postal_code: str | None = None

    phone: str | None = None
    website: str | None = None
    maps_url: str | None = None
    # Perfiles sociales enlazados desde la propia ficha, por plataforma. Muchos
    # negocios pequeños no tienen web y su ficha enlaza el Instagram o el
    # WhatsApp: quedarse solo con el campo "sitio web" tira el resto.
    socials: dict[str, str] = field(default_factory=dict)

    latitude: float | None = None
    longitude: float | None = None

    rating: float | None = None
    reviews_count: int | None = None
    price_level: int | None = None

    opening_hours: dict[str, Any] | None = None
    is_permanently_closed: bool = False

    position: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_usable(self) -> bool:
        """Descarta resultados vacíos.

        Un nombre suelto sin ninguna forma de ubicar ni contactar al negocio no
        sirve para prospectar; ocupa sitio y ensucia las métricas.
        """
        if not self.name or not self.name.strip():
            return False
        # Un negocio cuyo único rastro es su Instagram sigue siendo
        # contactable, así que las redes también cuentan como forma de llegar.
        return any([self.address, self.phone, self.website, self.latitude, self.socials])


@runtime_checkable
class DiscoveryProvider(Protocol):
    """Fuente de descubrimiento de empresas."""

    name: str
    source_type: SourceType

    # Declarado como `def` que devuelve un AsyncIterator, no como `async def`:
    # las implementaciones son generadores asíncronos, y con `async def` en el
    # Protocol el tipo esperado sería Coroutine[..., AsyncIterator], que no
    # casa. Es la forma que documenta mypy para iteradores asíncronos.
    def search(self, query: SearchQuery) -> AsyncIterator[RawPlace]:
        """Emite empresas una a una.

        Es un iterador y no una lista a propósito: el worker persiste cada
        empresa según llega, así que si el proveedor falla en el resultado 73
        de 100, los 72 anteriores ya están guardados.
        """
        ...

    async def healthcheck(self) -> ProviderHealth:
        """¿Sigue funcionando este proveedor?"""
        ...


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    provider: str
    healthy: bool
    checked_fields: dict[str, bool] = field(default_factory=dict)
    message: str | None = None

    @property
    def degraded_fields(self) -> list[str]:
        return [name for name, ok in self.checked_fields.items() if not ok]


class ProviderError(ExternalServiceError):
    """Fallo del proveedor de descubrimiento."""

    code = "DISCOVERY_PROVIDER_ERROR"


class ProviderBlockedError(ProviderError):
    """El proveedor presentó un challenge o bloqueó la petición.

    Se distingue del fallo genérico porque la reacción es distinta: aquí no se
    reintenta, se para el job y se avisa al usuario.
    """

    code = "DISCOVERY_PROVIDER_BLOCKED"
