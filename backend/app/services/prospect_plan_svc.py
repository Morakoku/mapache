"""Del servicio al plan de búsquedas: a quién buscar y dónde.

El usuario describe **su** negocio —qué vende, a quién, qué problemas
resuelve— y este servicio lo traduce en búsquedas concretas: "panadería en
Medellín, sin sitio web propio, mínimo 4.0 de calificación".

Con clave de IA el plan se afina: `ideal_customer` es texto libre y un modelo
lo convierte en términos que Google Maps entiende. Sin clave se arma por
reglas a partir de `target_industries`, que ya es una lista concreta. Los dos
caminos producen lo mismo — el plan es una propuesta que el usuario revisa
antes de gastar seis minutos de scraping por búsqueda.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import client, prompts
from app.core.exceptions import ValidationError
from app.core.logging import get_logger
from app.enrichment.signal_detector import SIGNAL_LABELS
from app.models.company import Company
from app.models.search import Search
from app.models.service import Service
from app.models.settings import AppSettings

logger = get_logger(__name__)


def _primera_mayuscula(texto: str) -> str:
    return texto[:1].upper() + texto[1:] if texto else texto

# Señales que dicen algo sobre la presencia digital del negocio. Si el servicio
# apunta a alguna de ellas, la búsqueda no debe exigir sitio web propio: el
# prospecto ideal es justamente el que no lo tiene.
_SIGNALS_SIN_WEB = frozenset({"no_website", "site_unreachable", "social_only"})

# Cuántas búsquedas propone como máximo. Más de esto es una tarde entera de
# scraping y una factura de proveedor que el usuario no vio venir.
_MAX_SEARCHES = 12
_DEFAULT_TARGET = 100


@dataclass(slots=True)
class PlannedSearch:
    """Una búsqueda propuesta, con el porqué."""

    name: str
    business_type: str
    city: str
    keywords: list[str] = field(default_factory=list)
    target_count: int = _DEFAULT_TARGET
    min_rating: float | None = None
    require_phone: bool = False
    require_website: bool = False
    require_email: bool = False
    reason: str = ""
    already_exists: bool = False


@dataclass(slots=True)
class ProspectPlan:
    service_id: uuid.UUID
    service_name: str
    searches: list[PlannedSearch]
    source: str  # "ai" | "rules"
    notes: list[str] = field(default_factory=list)

    @property
    def total_target(self) -> int:
        return sum(s.target_count for s in self.searches if not s.already_exists)


class ProspectPlanService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def build(
        self,
        service: Service,
        *,
        cities: list[str],
        target_per_search: int = _DEFAULT_TARGET,
    ) -> ProspectPlan:
        """Propone las búsquedas para este servicio en estas ciudades."""
        ciudades = [c.strip() for c in cities if c and c.strip()]
        if not ciudades:
            raise ValidationError(
                "Hace falta al menos una ciudad para buscar.",
                code="CITIES_REQUIRED",
            )

        settings = await self._settings()
        industrias, source, notes = await self._industries(service, settings)

        if not industrias:
            raise ValidationError(
                f"El servicio «{service.name}» no dice a qué industrias apunta. "
                "Añade las industrias objetivo o el cliente ideal y vuelve a intentarlo.",
                code="SERVICE_HAS_NO_TARGET",
            )

        existentes = await self._existing_pairs()
        searches: list[PlannedSearch] = []

        for ciudad in ciudades:
            for industria in industrias:
                if len(searches) >= _MAX_SEARCHES:
                    notes.append(
                        f"El plan se corta en {_MAX_SEARCHES} búsquedas: cada una tarda "
                        "varios minutos. Lanza estas y vuelve a planificar."
                    )
                    return ProspectPlan(
                        service_id=service.id,
                        service_name=service.name,
                        searches=searches,
                        source=source,
                        notes=notes,
                    )

                searches.append(
                    self._plan_one(
                        service,
                        industria=industria,
                        ciudad=ciudad,
                        target=target_per_search,
                        existe=(industria.lower(), ciudad.lower()) in existentes,
                    )
                )

        return ProspectPlan(
            service_id=service.id,
            service_name=service.name,
            searches=searches,
            source=source,
            notes=notes,
        )

    def _plan_one(
        self,
        service: Service,
        *,
        industria: str,
        ciudad: str,
        target: int,
        existe: bool,
    ) -> PlannedSearch:
        """Una búsqueda, con los filtros que se deducen del servicio."""
        senales = set(service.opportunity_signals or ())

        # Si el servicio vive de que al prospecto le falte web, exigir web
        # sería descartar justo a los buenos.
        busca_sin_web = bool(senales & _SIGNALS_SIN_WEB)

        motivos = [f"{industria} en {ciudad}"]
        if busca_sin_web:
            etiquetas = [
                SIGNAL_LABELS.get(s, s).lower() for s in sorted(senales & _SIGNALS_SIN_WEB)
            ]
            motivos.append(f"buscas negocios donde {' o '.join(etiquetas)}")

        # Un negocio sin teléfono ni web no se puede contactar de ninguna
        # forma, así que al menos el teléfono se exige cuando no se pide web.
        require_phone = busca_sin_web

        min_rating: float | None = None
        if "low_rating" not in senales:
            # Sin señal de calificación baja, se filtra la morralla: un negocio
            # con 2.1 estrellas rara vez tiene presupuesto ni ganas.
            min_rating = 3.5
            motivos.append("descartando los de calificación muy baja")

        return PlannedSearch(
            name=f"{industria.capitalize()} · {ciudad}",
            business_type=industria,
            city=ciudad,
            keywords=[],
            target_count=target,
            min_rating=min_rating,
            require_phone=require_phone,
            require_website=False,
            require_email=False,
            # Solo la inicial: `capitalize()` baja el resto y dejaba
            # "Panadería en medellín".
            reason=_primera_mayuscula(". ".join(motivos)) + ".",
            already_exists=existe,
        )

    async def _industries(
        self, service: Service, settings: AppSettings | None
    ) -> tuple[list[str], str, list[str]]:
        """Las industrias a buscar, y de dónde salieron.

        Las que el usuario escribió mandan siempre. La IA solo entra cuando no
        hay ninguna y hay que deducirlas del texto libre del cliente ideal.
        """
        declaradas = [i.strip() for i in (service.target_industries or []) if i and i.strip()]
        if declaradas:
            return declaradas, "rules", []

        credentials = client.credentials_from(settings)
        if credentials is None or not (settings and settings.ai_enabled):
            return (
                [],
                "rules",
                [
                    "Sin industrias objetivo y sin IA configurada no se puede deducir "
                    "a quién buscar."
                ],
            )

        deducidas = await self._ask_ai(service, credentials)
        if not deducidas:
            return [], "rules", ["La IA no pudo deducir industrias del cliente ideal."]

        return (
            deducidas,
            "ai",
            [
                "Las industrias las dedujo la IA de tu descripción del cliente ideal. "
                "Revísalas: son una propuesta, no un dato."
            ],
        )

    async def _ask_ai(
        self, service: Service, credentials: client.AICredentials
    ) -> list[str]:
        try:
            result = await client.complete_json(
                system=prompts.PLANNER_SYSTEM,
                user=prompts.build_planner_input(
                    service_name=service.name,
                    ideal_customer=service.ideal_customer,
                    value_proposition=service.value_proposition,
                    problems_solved=list(service.problems_solved or []),
                ),
                schema=prompts.PLANNER_SCHEMA,
                credentials=credentials,
                effort="low",
            )
        except client.AIUnavailableError as exc:
            logger.info("planner_unavailable", service=str(service.id), reason=exc.code)
            return []

        crudas = result.data.get("business_types") or []
        # Se limpia y se corta: una lista de veinte términos es una tarde de
        # scraping que nadie pidió.
        return [str(t).strip() for t in crudas if str(t).strip()][:6]

    async def _existing_pairs(self) -> set[tuple[str, str]]:
        """Qué combinaciones tipo+ciudad ya existen, para no duplicarlas."""
        filas = await self.session.execute(select(Search.business_type, Search.city))
        return {(b.lower(), c.lower()) for b, c in filas}

    async def _settings(self) -> AppSettings | None:
        result = await self.session.execute(select(AppSettings).limit(1))
        return result.scalar_one_or_none()

    async def suggest_cities(self, limit: int = 8) -> list[str]:
        """Ciudades donde ya hay empresas, de más a menos.

        Es la mejor pista de dónde trabaja el usuario: si el 80% de su base
        está en Medellín, proponerle Bogotá de primeras sería inventar.
        """
        filas = await self.session.execute(
            select(Company.city, func.count())
            .where(Company.city.is_not(None))
            .group_by(Company.city)
            .order_by(func.count().desc())
            .limit(limit)
        )
        return [ciudad for ciudad, _ in filas if ciudad]

    async def create_searches(
        self, service: Service, plan: ProspectPlan
    ) -> list[Search]:
        """Crea las búsquedas del plan que aún no existían."""
        creadas: list[Search] = []
        for propuesta in plan.searches:
            if propuesta.already_exists:
                continue

            search = Search(
                service_id=service.id,
                name=propuesta.name,
                business_type=propuesta.business_type,
                city=propuesta.city,
                keywords=propuesta.keywords,
                country="Colombia",
                target_count=propuesta.target_count,
                min_rating=propuesta.min_rating,
                require_phone=propuesta.require_phone,
                require_website=propuesta.require_website,
                require_email=propuesta.require_email,
                owner_id=service.owner_id,
            )
            self.session.add(search)
            creadas.append(search)

        await self.session.flush()
        logger.info("prospect_plan_created", service=str(service.id), searches=len(creadas))
        return creadas
