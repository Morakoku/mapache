"""Todo lo que se sabe de una empresa, en un solo sitio.

Junta lo que el CRM ya tiene —ficha de Google, redes, señales, contactos,
historial— y le añade lo que el buscador indexó públicamente de ese negocio:
notas de prensa, directorios, su propia web. Es lo que se lee de arriba abajo
justo antes de escribir o llamar.

Los hallazgos web se guardan en la empresa. Cada consulta al buscador cuesta
cuota y la información pública de un negocio no cambia de un día para otro:
volver a preguntar en cada apertura del panel sería tirar el presupuesto.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConfigurationError, ExternalServiceError
from app.core.logging import get_logger
from app.core.security import decrypt
from app.enrichment.signal_detector import SIGNAL_LABELS
from app.models.company import Company, CompanySignal
from app.models.contact import Contact
from app.models.lead import Lead
from app.models.settings import AppSettings
from app.scrapers.web_search import WebSearchClient, build_web_search

logger = get_logger(__name__)

_MAX_FINDINGS = 8

# Cuánto vale un hallazgo antes de volver a preguntar. Un mes: lo que tarda en
# cambiar algo relevante de un negocio pequeño.
_FRESH_FOR = timedelta(days=30)

# Dominios que no aportan nada sobre el negocio: agregadores de cupones,
# directorios de spam y los propios resultados de Google.
_NOISE_DOMAINS = frozenset(
    {
        "google.com",
        "maps.google.com",
        "translate.google.com",
        "webcache.googleusercontent.com",
    }
)


@dataclass(slots=True)
class WebFinding:
    """Un resultado del buscador sobre esta empresa."""

    title: str
    url: str
    snippet: str | None
    source_domain: str


@dataclass(slots=True)
class CompanyDossier:
    company: Company
    lead_id: uuid.UUID | None
    lead_score: int | None
    stage_name: str | None
    contacts: list[Contact]
    signal_labels: list[str]
    web_findings: list[WebFinding] = field(default_factory=list)
    web_findings_at: datetime | None = None
    web_available: bool = False
    notes: list[str] = field(default_factory=list)


class DossierService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def build(self, company: Company, *, refresh_web: bool = False) -> CompanyDossier:
        settings = await self._settings()
        credenciales = self._serp_credentials(settings)

        lead = await self._lead_of(company.id)
        contacts = await self._contacts_of(company.id)

        dossier = CompanyDossier(
            company=company,
            lead_id=lead.id if lead else None,
            lead_score=lead.score if lead else None,
            stage_name=lead.stage.name if lead else None,
            contacts=contacts,
            signal_labels=await self._signal_labels(company.id),
            web_findings=[WebFinding(**f) for f in (company.web_findings or [])],
            web_findings_at=company.web_findings_at,
            web_available=credenciales is not None,
        )

        if credenciales is None:
            dossier.notes.append(
                "Para buscar información pública de la empresa en la web, configura el "
                "buscador en Configuración."
            )
            return dossier

        if refresh_web or self._is_stale(company):
            try:
                dossier.web_findings = await self._search_web(company, credenciales)
            except ExternalServiceError as exc:
                # Un fallo del buscador no puede dejar sin panel: lo demás ya
                # está y es lo que de verdad se usa para decidir.
                dossier.notes.append(exc.message)
                return dossier

            company.web_findings = [asdict(f) for f in dossier.web_findings]
            company.web_findings_at = datetime.now(UTC)
            dossier.web_findings_at = company.web_findings_at
            await self.session.flush()

        return dossier

    def _is_stale(self, company: Company) -> bool:
        """Nunca se buscó, o lo que hay ya tiene un mes."""
        if company.web_findings_at is None:
            return company.web_findings is None
        return datetime.now(UTC) - company.web_findings_at > _FRESH_FOR

    async def _search_web(
        self, company: Company, buscador: WebSearchClient
    ) -> list[WebFinding]:
        """Busca la empresa en el índice del buscador.

        La consulta lleva nombre y ciudad entre comillas: sin ellas, "Panadería
        Ana" devuelve todas las panaderías del país.
        """
        partes = [f'"{company.name}"']
        if company.city:
            partes.append(f'"{company.city}"')
        consulta = " ".join(partes)

        async with httpx.AsyncClient(timeout=20.0) as client:
            resultados = await buscador.search(client, consulta, page=0)

        hallazgos: list[WebFinding] = []
        vistos: set[str] = set()

        for item in resultados:
            dominio = (item.domain or "").lower()
            if not item.url or not dominio or dominio in _NOISE_DOMAINS or dominio in vistos:
                continue
            vistos.add(dominio)

            hallazgos.append(
                WebFinding(
                    title=item.title or dominio,
                    url=item.url,
                    snippet=item.snippet,
                    source_domain=dominio,
                )
            )
            if len(hallazgos) >= _MAX_FINDINGS:
                break

        logger.info(
            "dossier_web_search",
            company=str(company.id),
            buscador=buscador.name,
            findings=len(hallazgos),
        )
        return hallazgos

    @staticmethod
    def _serp_credentials(settings: AppSettings | None) -> WebSearchClient | None:
        """El buscador configurado, o None si no hay con qué buscar."""
        if settings is None or not settings.serp_api_key_enc:
            return None
        try:
            return build_web_search(
                provider=settings.serp_provider,
                api_key=decrypt(settings.serp_api_key_enc),
                engine_id=settings.serp_engine_id,
            )
        except ConfigurationError:
            # Falta el motor de Google, o la clave de cifrado no descifra lo
            # guardado. En los dos casos el panel se dibuja sin la parte web.
            return None

    async def _signal_labels(self, company_id: uuid.UUID) -> list[str]:
        """Las señales, consultadas en vez de leídas de la relación.

        `company.signals` es perezoso si la empresa se construyó en memoria en
        vez de leerse, y una carga perezosa aquí revienta con MissingGreenlet.
        """
        filas = await self.session.execute(
            select(CompanySignal.signal_key).where(CompanySignal.company_id == company_id)
        )
        return [SIGNAL_LABELS.get(k, k) for k in filas.scalars()]

    async def _lead_of(self, company_id: uuid.UUID) -> Lead | None:
        """El prospecto de esta empresa, si ya se creó."""
        result = await self.session.execute(
            select(Lead)
            .where(Lead.company_id == company_id)
            .order_by(Lead.score.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def _contacts_of(self, company_id: uuid.UUID) -> list[Contact]:
        result = await self.session.execute(
            select(Contact)
            .where(Contact.company_id == company_id)
            .order_by(Contact.is_primary.desc(), Contact.created_at)
        )
        return list(result.scalars())

    async def _settings(self) -> AppSettings | None:
        result = await self.session.execute(select(AppSettings).limit(1))
        return result.scalar_one_or_none()
