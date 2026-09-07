"""Lógica de negocio de empresas: alta, dedupe y fusión de datos.

El corazón es `upsert_from_raw`: recibe lo que devolvió un proveedor y decide
si es una empresa nueva o una que ya conocíamos. Sin esto, relanzar una
búsqueda duplica todo el trabajo comercial hecho encima.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import SourceType
from app.core.logging import get_logger
from app.models.company import Company
from app.repositories.company import CompanyRepository, DuplicateCandidate
from app.scrapers.base import RawPlace
from app.utils.dedupe import build_dedupe_key
from app.utils.phone import is_mobile, to_e164, whatsapp_from_url
from app.utils.text import snippet
from app.utils.url import (
    detect_social_platform,
    extract_domain,
    is_own_website,
    is_social_url,
    normalize_url,
    social_handle,
)

logger = get_logger(__name__)

# Campos que cuentan para la calidad del dato. El score alimenta la dimensión
# DATA_QUALITY del scoring (Fase 8).
_QUALITY_FIELDS = (
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


@dataclass(frozen=True, slots=True)
class UpsertResult:
    company: Company
    is_new: bool
    duplicate_candidates: list[DuplicateCandidate]


class CompanyService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = CompanyRepository(session)

    async def upsert_from_raw(
        self, raw: RawPlace, *, owner_id: uuid.UUID | None = None
    ) -> UpsertResult:
        """Crea o actualiza una empresa a partir del resultado de un proveedor."""
        website = normalize_url(raw.website)
        # Un "sitio web" que apunta a Instagram o a una ficha de directorio no
        # es un sitio propio. Guardar el dominio igualmente rompería la señal
        # `no_website`, que es justamente la más valiosa para quien vende
        # desarrollo web.
        domain = extract_domain(website) if is_own_website(website) else None
        phone_e164 = to_e164(raw.phone)
        whatsapp = whatsapp_from_url(raw.socials.get("whatsapp")) if raw.socials else None
        # Fallback: casi todas las PYMEs de LatAm atienden por WhatsApp en su
        # propio número. Si el perfil no expuso enlace wa/*, promover el
        # teléfono móvil como WhatsApp. Nunca un fijo: no es WhatsApp.
        if whatsapp is None and is_mobile(phone_e164):
            whatsapp = phone_e164

        dedupe_key = build_dedupe_key(
            name=raw.name, website=website, phone_e164=phone_e164, city=raw.city
        )

        existing = await self.repo.find_by_google_ids(ftid=raw.ftid, place_id=raw.place_id)
        matched_by = "google_id"
        if existing is None:
            existing = await self.repo.find_by_dedupe_key(dedupe_key, owner_id)
            matched_by = "dedupe_key"

        now = datetime.now(UTC)

        if existing is not None:
            self._merge_into(existing, raw, website, domain, phone_e164, whatsapp, dedupe_key, now)
            await self._record_socials(existing, raw)
            await self.session.flush()
            logger.debug("company_updated", company_id=str(existing.id), matched_by=matched_by)
            return UpsertResult(company=existing, is_new=False, duplicate_candidates=[])

        # Nivel 3: antes de crear, buscar parecidos. No se fusiona
        # automáticamente — se deja el aviso para que decida el usuario.
        candidates = await self.repo.find_similar(
            name=raw.name, city=raw.city, latitude=raw.latitude, longitude=raw.longitude
        )

        company = Company(
            owner_id=owner_id,
            name=raw.name.strip(),
            description=snippet(raw.description, 500),
            category=raw.category,
            categories=raw.categories or [],
            address=raw.address,
            city=raw.city,
            state=raw.region,
            country=raw.country,
            postal_code=raw.postal_code,
            phone=phone_e164,
            phone_raw=raw.phone,
            whatsapp=whatsapp,
            website=website,
            website_domain=domain,
            google_maps_url=raw.maps_url,
            google_ftid=raw.ftid,
            google_place_id=raw.place_id,
            latitude=raw.latitude,
            longitude=raw.longitude,
            rating=Decimal(str(raw.rating)) if raw.rating is not None else None,
            reviews_count=raw.reviews_count,
            price_level=raw.price_level,
            opening_hours=raw.opening_hours,
            is_permanently_closed=raw.is_permanently_closed,
            dedupe_key=dedupe_key,
            first_extracted_at=now,
            last_extracted_at=now,
        )
        company.data_quality_score = self.quality_score(company)
        await self.repo.add(company)

        await self._record_discovery_sources(company, raw)
        await self._record_socials(company, raw)

        if candidates:
            logger.info(
                "company_possible_duplicate",
                company_id=str(company.id),
                name=company.name,
                candidates=len(candidates),
            )

        return UpsertResult(company=company, is_new=True, duplicate_candidates=candidates)

    def _merge_into(
        self,
        company: Company,
        raw: RawPlace,
        website: str | None,
        domain: str | None,
        phone_e164: str | None,
        whatsapp: str | None,
        dedupe_key: str,
        now: datetime,
    ) -> None:
        """Actualiza una empresa conocida sin pisar datos buenos con vacíos.

        Solo se escribe donde no había nada, o donde el proveedor trae algo
        nuevo. Un scraping que falló al leer el teléfono no debe borrar el que
        ya teníamos.
        """
        company.last_extracted_at = now

        if not company.description and raw.description:
            company.description = snippet(raw.description, 500)
        if not company.category and raw.category:
            company.category = raw.category
        if raw.categories:
            merged = list(dict.fromkeys([*company.categories, *raw.categories]))
            company.categories = merged
        if not company.address and raw.address:
            company.address = raw.address
        if not company.city and raw.city:
            company.city = raw.city
        if not company.state and raw.region:
            company.state = raw.region
        if not company.phone and phone_e164:
            company.phone = phone_e164
            company.phone_raw = raw.phone
        if not company.whatsapp and whatsapp:
            company.whatsapp = whatsapp
        if not company.website and website:
            company.website = website
            company.website_domain = domain
        if not company.google_maps_url and raw.maps_url:
            company.google_maps_url = raw.maps_url
        if not company.google_ftid and raw.ftid:
            company.google_ftid = raw.ftid
        if not company.google_place_id and raw.place_id:
            company.google_place_id = raw.place_id
        if company.latitude is None and raw.latitude is not None:
            company.latitude = raw.latitude
            company.longitude = raw.longitude

        # Rating y reseñas sí se sobrescriben: son datos vivos y el valor
        # nuevo siempre es más cierto que el viejo. La columna es NUMERIC, así
        # que se convierte explícitamente en vez de dejar que SQLAlchemy
        # coaccione un float y arrastre ruido de precisión binaria.
        if raw.rating is not None:
            company.rating = Decimal(str(raw.rating))
        if raw.reviews_count is not None:
            company.reviews_count = raw.reviews_count
        if raw.opening_hours:
            company.opening_hours = raw.opening_hours
        company.is_permanently_closed = raw.is_permanently_closed

        company.dedupe_key = dedupe_key
        company.data_quality_score = self.quality_score(company)

    async def _record_socials(self, company: Company, raw: RawPlace) -> None:
        """Guarda las redes que enlazaba la ficha del proveedor.

        Se hace ya en el descubrimiento y no se deja para el enriquecimiento
        porque el enriquecimiento solo entra cuando hay una web propia que
        rastrear: el negocio que únicamente tiene Instagram nunca llegaría.
        """
        socials = dict(raw.socials)

        # El campo "sitio web" de la ficha, cuando apunta a una red, es una
        # red más. Va aquí para que la regla viva en un solo sitio.
        if raw.website and is_social_url(raw.website):
            platform = detect_social_platform(raw.website)
            if platform:
                socials.setdefault(platform, raw.website)

        for platform, url in socials.items():
            await self.repo.upsert_social(
                company.id,
                platform=platform,
                url=url,
                handle=social_handle(url),
                source=raw.source.value,
            )

    async def _record_discovery_sources(self, company: Company, raw: RawPlace) -> None:
        """Deja constancia de qué trajo el proveedor y de dónde."""
        source = raw.source.value
        url = raw.maps_url

        for field_name, value in (
            ("phone", company.phone),
            ("whatsapp", company.whatsapp),
            ("website", company.website),
            ("address", company.address),
        ):
            if value:
                await self.repo.upsert_source(
                    company.id,
                    field_name=field_name,
                    value=value,
                    source=source,
                    source_url=url,
                    confidence=70 if raw.source == SourceType.GOOGLE_PLACES_API else 60,
                )

    @staticmethod
    def quality_score(company: Company) -> int:
        present = sum(1 for field in _QUALITY_FIELDS if getattr(company, field, None))
        return round(100 * present / len(_QUALITY_FIELDS))
