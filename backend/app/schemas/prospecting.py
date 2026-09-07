"""Schemas de entrada y salida de la Fase 2."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import Field, field_validator

from app.core.enums import JobStatus, SourceType, VerificationStatus
from app.schemas.common import APIModel

# ------------------------------------------------------------------ servicios


class ServiceIn(APIModel):
    name: str = Field(min_length=2, max_length=160)
    description: str | None = None
    ideal_customer: str | None = Field(default=None, max_length=255)
    target_industries: list[str] = Field(default_factory=list)
    problems_solved: list[str] = Field(default_factory=list)
    opportunity_signals: list[str] = Field(default_factory=list)
    value_proposition: str | None = None
    price_from: Decimal | None = Field(default=None, ge=0)
    currency: str = Field(default="COP", min_length=3, max_length=3)
    is_active: bool = True


class ServiceUpdate(APIModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    description: str | None = None
    ideal_customer: str | None = None
    target_industries: list[str] | None = None
    problems_solved: list[str] | None = None
    opportunity_signals: list[str] | None = None
    value_proposition: str | None = None
    price_from: Decimal | None = None
    currency: str | None = None
    is_active: bool | None = None


class ServiceOut(APIModel):
    id: uuid.UUID
    name: str
    description: str | None
    ideal_customer: str | None
    target_industries: list[str]
    problems_solved: list[str]
    opportunity_signals: list[str]
    value_proposition: str | None
    price_from: Decimal | None
    currency: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class SignalOption(APIModel):
    """Catálogo de señales para que la UI ofrezca opciones y no texto libre."""

    key: str
    label: str


# ------------------------------------------------------------------ búsquedas


class SearchIn(APIModel):
    name: str = Field(min_length=2, max_length=160)
    service_id: uuid.UUID | None = None
    business_type: str = Field(min_length=2, max_length=160)
    keywords: list[str] = Field(default_factory=list)
    country: str | None = "Colombia"
    region: str | None = None
    city: str = Field(min_length=2, max_length=120)
    zone: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    radius_km: Decimal = Field(default=Decimal("10"), gt=0, le=200)
    target_count: int = Field(default=100, ge=1, le=1000)
    source: SourceType = SourceType.GOOGLE_MAPS
    min_rating: Decimal | None = Field(default=None, ge=0, le=5)
    max_reviews: int | None = Field(default=None, ge=0)
    exclude_chains: bool = False
    # Requisitos de contacto. Sin al menos uno, la búsqueda trae negocios a los
    # que no se puede escribir ni llamar.
    require_phone: bool = False
    require_website: bool = False
    require_email: bool = False
    strict_match: bool = True
    auto_enrich: bool = True
    auto_score: bool = True

    @field_validator("keywords")
    @classmethod
    def _clean_keywords(cls, v: list[str]) -> list[str]:
        return [k.strip() for k in v if k and k.strip()]


class SearchUpdate(APIModel):
    name: str | None = None
    service_id: uuid.UUID | None = None
    business_type: str | None = None
    keywords: list[str] | None = None
    city: str | None = None
    zone: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    radius_km: Decimal | None = None
    target_count: int | None = Field(default=None, ge=1, le=1000)
    min_rating: Decimal | None = None
    max_reviews: int | None = None
    require_phone: bool | None = None
    require_website: bool | None = None
    require_email: bool | None = None
    strict_match: bool | None = None
    auto_enrich: bool | None = None
    auto_score: bool | None = None
    is_active: bool | None = None


class SearchOut(APIModel):
    id: uuid.UUID
    name: str
    service_id: uuid.UUID | None
    business_type: str
    keywords: list[str]
    country: str | None
    region: str | None
    city: str
    zone: str | None
    latitude: float | None
    longitude: float | None
    radius_km: Decimal
    target_count: int
    source: SourceType
    min_rating: Decimal | None
    max_reviews: int | None
    require_phone: bool
    require_website: bool
    require_email: bool
    strict_match: bool
    auto_enrich: bool
    auto_score: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


class SearchRunOut(APIModel):
    id: uuid.UUID
    search_id: uuid.UUID
    job_id: uuid.UUID | None
    status: JobStatus
    provider: SourceType
    results_found: int
    results_new: int
    results_duplicate: int
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


# --------------------------------------------------------- plan de prospección


class ProspectPlanIn(APIModel):
    cities: list[str] = Field(min_length=1, max_length=10)
    target_per_search: int = Field(default=100, ge=10, le=500)


class PlannedSearchOut(APIModel):
    """Una búsqueda propuesta, con el porqué de sus filtros."""

    name: str
    business_type: str
    city: str
    keywords: list[str]
    target_count: int
    min_rating: float | None
    require_phone: bool
    require_website: bool
    require_email: bool
    reason: str
    # Ya existe una búsqueda igual: se enseña, pero no se vuelve a crear.
    already_exists: bool


class ProspectPlanOut(APIModel):
    service_id: uuid.UUID
    service_name: str
    searches: list[PlannedSearchOut]
    # "ai" si las industrias las dedujo un modelo; "rules" si las escribió el
    # usuario. Se publica para que la interfaz pueda decir de dónde salen.
    source: str
    notes: list[str]
    total_target: int


class ProspectPlanRunOut(APIModel):
    created: int
    skipped_existing: int
    job_ids: list[uuid.UUID]
    message: str


class TopProspectOut(APIModel):
    """Un prospecto de los buenos, con lo que lo justifica."""

    lead_id: uuid.UUID
    company_id: uuid.UUID
    company_name: str
    city: str | None
    score: int
    email: str | None
    phone: str | None
    # La ficha de Google: es por donde se comprueba el negocio antes de llamar.
    google_maps_url: str | None
    stage_name: str
    # Las razones que dio el motor de score, ya en lenguaje llano.
    reasons: list[str]


# ------------------------------------------------------------------ empresas


class CompanySourceOut(APIModel):
    id: uuid.UUID
    field_name: str
    value: str
    source: SourceType
    source_url: str | None
    confidence: int
    verification: VerificationStatus
    extracted_at: datetime


class CompanySocialOut(APIModel):
    """Perfil social de la empresa.

    Es el mismo schema en el listado y en la ficha: cuando el negocio no tiene
    web, su Instagram **es** el canal de contacto, y esconderlo tras un clic
    obliga a abrir empresa por empresa para saber a quién se le puede escribir.
    """

    id: uuid.UUID
    platform: str
    url: str
    handle: str | None
    followers_count: int | None


class CompanySignalOut(APIModel):
    id: uuid.UUID
    signal_key: str
    value: dict[str, Any] | None
    detected_at: datetime


class CompanySummaryOut(APIModel):
    """Versión ligera para listados.

    Las redes van aquí y no solo en el detalle: cuando el negocio no tiene web,
    su Instagram **es** el canal, y no verlo en la lista obliga a abrir empresa
    por empresa para saber a quién se le puede escribir.
    """

    id: uuid.UUID
    name: str
    category: str | None = None
    city: str | None = None
    phone: str | None = None
    whatsapp: str | None = None
    email: str | None = None
    website: str | None = None
    website_domain: str | None = None
    google_maps_url: str | None = None
    rating: Decimal | None = None
    reviews_count: int | None = None
    data_quality_score: int = 0
    last_extracted_at: datetime | None = None
    last_enriched_at: datetime | None = None
    # Verificado = tiene ficha real en Google. Se publica para que la interfaz
    # pueda distinguir un negocio comprobado de uno dado de alta a mano.
    is_verified: bool = False
    is_permanently_closed: bool = False
    socials: list[CompanySocialOut] = Field(default_factory=list)


class CompanyDetailOut(CompanySummaryOut):
    description: str | None = None
    categories: list[str] = Field(default_factory=list)
    address: str | None = None
    state: str | None = None
    country: str | None = None
    postal_code: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    opening_hours: dict[str, Any] | None = None
    google_ftid: str | None = None
    google_place_id: str | None = None
    first_extracted_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    sources: list[CompanySourceOut] = Field(default_factory=list)
    socials: list[CompanySocialOut] = Field(default_factory=list)
    signals: list[CompanySignalOut] = Field(default_factory=list)


class WebFindingOut(APIModel):
    """Algo que el buscador indexó sobre esta empresa."""

    title: str
    url: str
    snippet: str | None
    source_domain: str


class DossierContactOut(APIModel):
    display_name: str
    job_title: str | None
    email: str | None
    phone: str | None
    is_contactable: bool


class CompanyDossierOut(APIModel):
    """Todo lo que se sabe de una empresa, para leer antes de contactarla."""

    company: CompanyDetailOut
    lead_id: uuid.UUID | None
    lead_score: int | None
    stage_name: str | None
    contacts: list[DossierContactOut]
    signal_labels: list[str]
    web_findings: list[WebFindingOut]
    web_findings_at: datetime | None
    # Si el buscador está configurado. En falso, la interfaz explica cómo.
    web_available: bool
    notes: list[str]


class PlatformCountOut(APIModel):
    platform: str
    count: int


class CompanyFacetsOut(APIModel):
    """Conteos para que los filtros digan cuánto hay antes de pulsarlos."""

    total: int
    with_email: int
    without_email: int
    with_website: int
    without_website: int
    with_phone: int
    without_phone: int
    with_whatsapp: int
    without_whatsapp: int
    with_social: int
    without_social: int
    verified: int
    unverified: int
    permanently_closed: int
    platforms: list[PlatformCountOut]
    cities: list[str]


class CompanyManualIn(APIModel):
    """Alta manual, para empresas que llegan por fuera de una búsqueda."""

    name: str = Field(min_length=2, max_length=255)
    category: str | None = None
    address: str | None = None
    city: str | None = None
    phone: str | None = None
    whatsapp: str | None = None
    email: str | None = None
    website: str | None = None


class DuplicateCandidateOut(APIModel):
    company: CompanySummaryOut
    similarity: float
    distance_m: float | None
    reason: str


# ------------------------------------------------------------------ jobs


class JobOut(APIModel):
    id: uuid.UUID
    job_type: str
    status: JobStatus
    progress_current: int
    progress_total: int | None
    progress_message: str | None
    error_message: str | None
    result: dict[str, Any] | None
    attempts: int
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime

    @property
    def progress_pct(self) -> int | None:
        if not self.progress_total:
            return None
        return min(100, round(100 * self.progress_current / self.progress_total))


class ProviderHealthOut(APIModel):
    provider: str
    healthy: bool
    checked_fields: dict[str, bool]
    degraded_fields: list[str]
    message: str | None
