"""Enriquecimiento de una empresa: web -> emails, teléfonos, redes y señales.

Este servicio es el que hace que el embudo funcione: ningún proveedor de
mapas devuelve email, así que sin esto no hay a quién escribirle.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import SourceType, VerificationStatus
from app.core.logging import get_logger
from app.enrichment.email_verifier import verify_email
from app.enrichment.extractors import is_role_email
from app.enrichment.signal_detector import CompanyFacts, detect_signals
from app.enrichment.website_crawler import CrawlResult, DomainRateLimiter, WebsiteCrawler
from app.models.company import Company, CompanySocial
from app.repositories.company import CompanyRepository
from app.utils.phone import whatsapp_from_url
from app.utils.url import detect_social_platform, is_own_website, social_handle

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class EnrichmentOutcome:
    company_id: uuid.UUID
    emails_found: int
    phones_found: int
    socials_found: int
    signals: list[str]
    crawled: bool
    error: str | None = None


class EnrichmentService:
    def __init__(
        self,
        session: AsyncSession,
        crawler: WebsiteCrawler | None = None,
    ) -> None:
        self.session = session
        self.repo = CompanyRepository(session)
        # Un único limitador por servicio: si dos empresas comparten dominio
        # (franquicias, agregadores), el ritmo se respeta igualmente.
        self.crawler = crawler or WebsiteCrawler(rate_limiter=DomainRateLimiter())

    async def enrich(self, company: Company) -> EnrichmentOutcome:
        crawl = None
        emails: list[str] = []
        phones: list[str] = []
        socials: dict[str, str] = {}

        has_own_site = is_own_website(company.website)

        # Muchos negocios no tienen web: su "sitio" en Google Maps es el perfil
        # de Instagram o la página de Facebook. Antes esa URL se descartaba por
        # no ser un sitio propio y la red social se perdía, que es justo el dato
        # que hay que guardar cuando no hay web.
        from_listing: set[str] = set()
        if company.website and not has_own_site:
            platform = detect_social_platform(company.website)
            if platform:
                socials[platform] = company.website
                from_listing.add(platform)

        if has_own_site:
            crawl = await self.crawler.crawl(company.website)  # type: ignore[arg-type]
            if crawl.reachable:
                emails = crawl.contact.emails
                phones = crawl.contact.phones
                socials.update(crawl.contact.socials)

        best_email = await self._persist_emails(company, emails, crawl)
        await self._persist_phones(company, phones, crawl)
        await self._persist_socials(company, socials, from_listing=from_listing)

        facts = CompanyFacts(
            website=company.website,
            phone=company.phone,
            email=company.email,
            rating=float(company.rating) if company.rating is not None else None,
            reviews_count=company.reviews_count,
            # Las de esta pasada, o las que ya estuvieran guardadas. Se
            # consultan explícitamente: `company.socials` es perezoso si la
            # empresa se construyó en memoria en vez de leerse, y una carga
            # perezosa aquí dentro rompe con MissingGreenlet.
            socials=socials or await self._known_socials(company.id),
        )
        signals = detect_signals(facts, crawl)
        await self.repo.replace_signals(
            company.id,
            [(s.key, s.value) for s in signals],
            source=SourceType.WEBSITE.value if crawl else SourceType.GOOGLE_MAPS.value,
        )

        company.last_enriched_at = datetime.now(UTC)
        company.data_quality_score = self._quality_score(company)
        await self.session.flush()

        outcome = EnrichmentOutcome(
            company_id=company.id,
            emails_found=len(emails),
            phones_found=len(phones),
            socials_found=len(socials),
            signals=[s.key for s in signals],
            crawled=bool(crawl and crawl.reachable),
            error=crawl.error if crawl else None,
        )
        logger.info(
            "company_enriched",
            company_id=str(company.id),
            emails=outcome.emails_found,
            best_email=best_email,
            signals=len(outcome.signals),
        )
        return outcome

    async def _persist_emails(
        self, company: Company, emails: list[str], crawl: CrawlResult | None
    ) -> str | None:
        """Guarda cada email con su procedencia y elige el principal.

        Gana el de mayor confianza: un email nominal del dominio propio vale
        más que `info@gmail.com`, y mucho más que el de la agencia que hizo
        la web.
        """
        best_email: str | None = None
        best_confidence = -1

        for email in emails:
            status, confidence = await verify_email(email)
            if status is VerificationStatus.INVALID:
                continue

            # Un email del dominio de la propia empresa es casi con seguridad
            # suyo; uno de otro dominio puede ser de su proveedor web.
            if company.website_domain and email.endswith(f"@{company.website_domain}"):
                confidence += 15
            if is_role_email(email):
                confidence -= 5

            await self.repo.upsert_source(
                company.id,
                field_name="email",
                value=email,
                source=SourceType.WEBSITE.value,
                source_url=crawl.final_url if crawl else None,
                confidence=min(confidence, 100),
                verification=status.value,
            )

            if confidence > best_confidence:
                best_confidence = confidence
                best_email = email

        if best_email and not company.email:
            company.email = best_email
        return best_email

    async def _persist_phones(
        self, company: Company, phones: list[str], crawl: CrawlResult | None
    ) -> None:
        for phone in phones:
            await self.repo.upsert_source(
                company.id,
                field_name="phone",
                value=phone,
                source=SourceType.WEBSITE.value,
                source_url=crawl.final_url if crawl else None,
                confidence=65,
            )
        if phones and not company.phone:
            company.phone = phones[0]

    async def _known_socials(self, company_id: uuid.UUID) -> dict[str, str]:
        rows = await self.session.execute(
            select(CompanySocial.platform, CompanySocial.url).where(
                CompanySocial.company_id == company_id
            )
        )
        return dict(rows.all())  # type: ignore[arg-type]

    async def _persist_socials(
        self,
        company: Company,
        socials: dict[str, str],
        *,
        from_listing: set[str] | None = None,
    ) -> None:
        """Guarda los perfiles con el `handle` y de dónde salió cada uno.

        La procedencia importa para el mismo motivo que en los emails: un
        perfil sacado de la ficha de Google es tan bueno como esa ficha, y uno
        enlazado desde la propia web del negocio lo puso el negocio.
        """
        listing = from_listing or set()
        for platform, url in socials.items():
            await self.repo.upsert_social(
                company.id,
                platform=platform,
                url=url,
                handle=social_handle(url),
                source=(
                    SourceType.GOOGLE_MAPS.value
                    if platform in listing
                    else SourceType.WEBSITE.value
                ),
            )

        # El número de WhatsApp detectado en el sitio propio (o en la ficha de
        # Google) va también a la columna dedicada, en E.164.
        wa_url = socials.get("whatsapp")
        if wa_url:
            wa_e164 = whatsapp_from_url(wa_url)
            if wa_e164 and not company.whatsapp:
                company.whatsapp = wa_e164

    @staticmethod
    def _quality_score(company: Company) -> int:
        fields = (
            "name",
            "category",
            "address",
            "city",
            "phone",
            "website",
            "email",
            "latitude",
            "opening_hours",
        )
        present = sum(1 for f in fields if getattr(company, f, None))
        return round(100 * present / len(fields))
