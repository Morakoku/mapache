"""Selectores en cascada (§4.8.4 del diseño).

El punto débil de cualquier scraper es el selector. Google rota las clases
CSS ofuscadas cada pocas semanas, así que aquí nunca son la primera opción:
se prioriza lo semántico (`data-item-id`, `role`, `aria-label`), que cambia
mucho menos porque Google lo necesita para su propia accesibilidad.

Cada cascada registra qué nivel respondió. Si el nivel 1 deja de funcionar y
contesta el 3, la métrica lo delata antes de que los datos se degraden en
silencio.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

SelectorKind = Literal["css", "regex"]


@dataclass(frozen=True, slots=True)
class Selector:
    """Un intento concreto de localizar un dato."""

    kind: SelectorKind
    expression: str
    # De dónde sale el valor: texto del nodo o uno de sus atributos.
    attribute: str | None = None
    # Grupo de captura para los regex.
    group: int = 1


@dataclass(slots=True)
class SelectorChain:
    """Cascada ordenada de la opción más robusta a la más frágil."""

    field_name: str
    selectors: list[Selector]
    # Contadores de uso por nivel. Los lee `health.py` para detectar deriva.
    hits: list[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.hits:
            self.hits = [0] * len(self.selectors)

    def record_hit(self, level: int) -> None:
        if 0 <= level < len(self.hits):
            self.hits[level] += 1

    @property
    def total_hits(self) -> int:
        return sum(self.hits)

    @property
    def fallback_ratio(self) -> float:
        """Fracción de extracciones que necesitaron algo que no era el nivel 1.

        Por encima de 0.30 hay que revisar los selectores: significa que el
        selector semántico dejó de servir y estamos tirando de heurísticas.
        """
        if self.total_hits == 0:
            return 0.0
        return 1 - (self.hits[0] / self.total_hits)


def _css(expression: str, attribute: str | None = None) -> Selector:
    return Selector(kind="css", expression=expression, attribute=attribute)


def _regex(expression: str, group: int = 1) -> Selector:
    return Selector(kind="regex", expression=expression, group=group)


def build_selector_chains() -> dict[str, SelectorChain]:
    """Cascadas de la ficha de detalle.

    Se construyen por llamada (no como constante de módulo) para que los
    contadores de un run no contaminen al siguiente.
    """
    return {
        "name": SelectorChain(
            "name",
            [
                _css("h1.DUwDvf"),
                _css("div[role='main'] h1"),
                _css("h1"),
            ],
        ),
        "category": SelectorChain(
            "category",
            [
                _css("button[jsaction*='category']"),
                _css("button.DkEaL"),
                _css("[jsaction*='pane.rating.category']"),
            ],
        ),
        "address": SelectorChain(
            "address",
            [
                # `data-item-id` es la opción estable: Google lo usa para su
                # propio manejo de acciones, no es una clase de estilo.
                _css("button[data-item-id='address']", attribute="aria-label"),
                _css("[data-item-id='address'] .Io6YTe"),
                _css("button[aria-label^='Dirección']", attribute="aria-label"),
            ],
        ),
        "phone": SelectorChain(
            "phone",
            [
                # El propio atributo lleva el número: `phone:tel:+576044441234`.
                _css("button[data-item-id^='phone:tel:']", attribute="data-item-id"),
                _css("button[aria-label^='Teléfono']", attribute="aria-label"),
                _regex(r"(?:\+57\s?)?(?:\(?\d{1,4}\)?[\s.\-]?)?\d{3}[\s.\-]?\d{4}"),
            ],
        ),
        "website": SelectorChain(
            "website",
            [
                _css("a[data-item-id='authority']", attribute="href"),
                _css("a[aria-label^='Sitio web']", attribute="href"),
                _css("a[data-tooltip='Abrir sitio web']", attribute="href"),
            ],
        ),
        "rating": SelectorChain(
            "rating",
            [
                _css("div.F7nice span[aria-hidden='true']"),
                _css("span[role='img'][aria-label*='estrella']", attribute="aria-label"),
                _regex(r"\b([0-5][.,]\d)\b"),
            ],
        ),
        "reviews": SelectorChain(
            "reviews",
            [
                _css("div.F7nice span[aria-label*='reseña']", attribute="aria-label"),
                _css("button[jsaction*='reviewChart']", attribute="aria-label"),
                _regex(r"([\d.,]+)\s*(?:reseñas|opiniones|reviews)"),
            ],
        ),
        "hours": SelectorChain(
            "hours",
            [
                _css("div[jsaction*='openhours'] table"),
                _css("table.eK4R0e"),
                _css("[aria-label*='Horario']", attribute="aria-label"),
            ],
        ),
        "description": SelectorChain(
            "description",
            [
                _css("div.PYvSYb"),
                _css("div[data-attrid='description']"),
                _css("div.WeS02d"),
            ],
        ),
    }


# --------------------------------------------------------------- listado

# Tarjetas del panel de resultados. El `feed` con role semántico es lo más
# estable que expone la página.
RESULT_FEED = "div[role='feed']"
RESULT_CARD_LINK = "a[href*='/maps/place/']"

# Texto de fin de listado, para saber cuándo parar de hacer scroll.
END_OF_LIST_PATTERNS = (
    "Has llegado al final de la lista",
    "You've reached the end of the list",
    "Llegaste al final de la lista",
)

# Señales de que Google presentó un challenge. Ante esto se para el job y se
# avisa al usuario; no se intenta resolver.
CHALLENGE_PATTERNS = (
    "/sorry/index",
    "unusual traffic",
    "tráfico inusual",
    "systems have detected",
    "recaptcha",
)

# Botones del diálogo de consentimiento de cookies, que aparece antes de
# cualquier resultado en la UE y a veces en LATAM.
CONSENT_BUTTONS = (
    "button[aria-label='Aceptar todo']",
    "button[aria-label='Accept all']",
    "form[action*='consent'] button",
    "button:has-text('Aceptar todo')",
)
