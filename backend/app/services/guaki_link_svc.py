"""Vínculo Prospecto (Mapache) ↔ Negocio (Guaki) — LOOP-24.

El motor de matching es una función pura y determinista: no relaciona registros
solo por el nombre. Usa señales (dominio, email, teléfono, identificador externo,
nombre+ubicación) y devuelve un estado UNMATCHED / POSSIBLE_MATCH / MATCHED /
REJECTED con confianza y las señales usadas.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.company import CompanySignal
from app.models.guaki_link import MATCH_STATES, GuakiLink
from app.models.lead import Lead
from app.schemas.guaki import GuakiLinkIn

# ------------------------------------------------------------ normalización

def norm_domain(value: str | None) -> str | None:
    """Dominio comparable: sin esquema, www, ruta ni parámetros."""
    if not value:
        return None
    host = value.strip().lower()
    if "://" in host:
        host = urlparse(host).netloc
    elif "/" in host:
        host = host.split("/", 1)[0]
    host = host.split("@")[-1].split(":")[0].strip(".")
    if host.startswith("www."):
        host = host[4:]
    return host or None


def norm_email(value: str | None) -> str | None:
    return value.strip().lower() if value else None


def norm_phone(value: str | None) -> str | None:
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    if digits.startswith("00"):
        digits = digits[2:]
    return digits or None


def norm_name(value: str | None) -> str | None:
    if not value:
        return None
    return re.sub(r"\s+", " ", value.strip().lower()).strip(" .")


# ------------------------------------------------------------ matching puro

@dataclass(frozen=True, slots=True)
class GuakiMatch:
    state: str  # UNMATCHED | POSSIBLE_MATCH | MATCHED | REJECTED
    confidence: int
    signals: list[str] = field(default_factory=list)


_STRONG = ("dominio", "email", "telefono", "identificador_externo")


def match_business(prospect: dict[str, object], business: dict[str, object]) -> GuakiMatch:
    """Compara un prospecto de Mapache con un negocio de Guaki.

    - Señal fuerte (dominio/email/teléfono/identificador externo) → MATCHED.
    - Solo nombre+ubicación → POSSIBLE_MATCH.
    - Mismo nombre pero ciudad distinta y sin señal fuerte → REJECTED (son
      negocios distintos con el mismo nombre).
    - Sin señales → UNMATCHED.
    """
    signals: list[str] = []

    p_domain = norm_domain(str(prospect.get("domain") or ""))
    b_domain = norm_domain(str(business.get("domain") or ""))
    if p_domain and b_domain and p_domain == b_domain:
        signals.append("dominio")

    p_email = norm_email(str(prospect.get("email") or ""))
    b_email = norm_email(str(business.get("email") or ""))
    if p_email and b_email and p_email == b_email:
        signals.append("email")

    p_phone = norm_phone(str(prospect.get("phone") or ""))
    b_phone = norm_phone(str(business.get("phone") or ""))
    if p_phone and b_phone and p_phone == b_phone:
        signals.append("telefono")

    p_ext = str(prospect.get("external_id") or "").strip()
    b_ext = str(business.get("external_id") or "").strip()
    if p_ext and b_ext and p_ext == b_ext:
        signals.append("identificador_externo")

    p_name = norm_name(str(prospect.get("name") or ""))
    b_name = norm_name(str(business.get("name") or ""))
    same_name = bool(p_name and b_name and p_name == b_name)
    p_city = norm_name(str(prospect.get("city") or ""))
    b_city = norm_name(str(business.get("city") or ""))
    same_city = bool(p_city and b_city and p_city == b_city)
    if same_name and same_city:
        signals.append("nombre+ubicacion")

    strong = [s for s in signals if s in _STRONG]
    if strong:
        return GuakiMatch(
            state="MATCHED",
            confidence=min(100, 90 + 3 * len(strong)),
            signals=signals,
        )
    if same_name and not same_city and (p_city or b_city):
        # Mismo nombre, ciudad distinta y sin señal fuerte → no es el mismo negocio.
        return GuakiMatch(state="REJECTED", confidence=70, signals=["nombre_sin_ubicacion"])
    if same_name and same_city:
        return GuakiMatch(state="POSSIBLE_MATCH", confidence=70, signals=signals)
    return GuakiMatch(state="UNMATCHED", confidence=0, signals=signals)


# ------------------------------------------------------------ servicio

class GuakiLinkService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def by_lead(self, lead_id: uuid.UUID) -> GuakiLink | None:
        return await self.session.scalar(
            select(GuakiLink).where(GuakiLink.lead_id == lead_id)
        )

    async def links_by_leads(self, lead_ids: list[uuid.UUID]) -> dict[uuid.UUID, GuakiLink]:
        if not lead_ids:
            return {}
        rows = (
            await self.session.execute(
                select(GuakiLink).where(GuakiLink.lead_id.in_(lead_ids))
            )
        ).scalars().all()
        return {row.lead_id: row for row in rows}

    async def upsert(self, lead_id: uuid.UUID, payload: GuakiLinkIn) -> GuakiLink:
        existing = await self.by_lead(lead_id)
        if existing is None:
            existing = GuakiLink(lead_id=lead_id, guaki_business_id="")
            self.session.add(existing)

        state = payload.match_state.upper()
        if state not in MATCH_STATES:
            state = "POSSIBLE_MATCH"
        existing.match_state = state
        existing.confidence = max(0, min(100, payload.confidence))
        existing.guaki_business_id = payload.guaki_business_id.strip()
        existing.signals = list(payload.signals)
        existing.matched_at = payload.matched_at
        existing.registered_at = payload.registered_at
        existing.plan = payload.plan
        existing.status = payload.status
        if existing.matched_at is None and state == "MATCHED":
            existing.matched_at = datetime.now(UTC)

        await self.session.flush()
        return existing

    @staticmethod
    def to_dict(link: GuakiLink) -> dict[str, object]:
        return {
            "state": link.match_state,
            "confidence": link.confidence,
            "signals": link.signals or [],
            "guaki_business_id": link.guaki_business_id,
            "matched_at": link.matched_at.isoformat() if link.matched_at else None,
            "registered_at": link.registered_at.isoformat() if link.registered_at else None,
            "plan": link.plan,
            "status": link.status,
        }

    async def find_candidates(self, business: dict[str, object]) -> list[dict[str, object]]:
        """Busca, entre los prospectos existentes, los que podrían ser este negocio."""
        stmt = (
            select(Lead)
            .options(selectinload(Lead.company), selectinload(Lead.contact))
            .limit(500)
        )
        leads = list((await self.session.execute(stmt)).scalars().all())
        signals_map = await self._signals_by_company(
            [lead.company_id for lead in leads]
        )
        candidates: list[dict[str, object]] = []
        for lead in leads:
            company = lead.company
            if company is None:
                continue
            prospect = self._prospect_fields(lead, signals_map.get(company.id, frozenset()))
            match = match_business(prospect, business)
            if match.state in {"MATCHED", "POSSIBLE_MATCH"}:
                candidates.append(
                    {
                        "lead_id": str(lead.id),
                        "negocio": company.name,
                        "ciudad": company.city,
                        "match_state": match.state,
                        "confidence": match.confidence,
                        "signals": match.signals,
                    }
                )
        candidates.sort(
            key=lambda x: (x["match_state"] == "MATCHED", x["confidence"]),
            reverse=True,
        )
        return candidates

    @staticmethod
    def _prospect_fields(lead: Lead, signals: frozenset[str]) -> dict[str, object]:
        company = lead.company
        return {
            "name": company.name,
            "domain": company.website,
            "email": (lead.contact.email if lead.contact else None) or company.email,
            "phone": (lead.contact.phone if lead.contact else None) or company.phone,
            "city": company.city,
            "external_id": None,
            "signals": sorted(signals),
        }

    async def _signals_by_company(
        self, company_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, frozenset[str]]:
        if not company_ids:
            return {}
        rows = (
            await self.session.execute(
                select(CompanySignal.company_id, CompanySignal.signal_key).where(
                    CompanySignal.company_id.in_(company_ids)
                )
            )
        ).all()
        grouped: dict[uuid.UUID, set[str]] = {}
        for company_id, key in rows:
            grouped.setdefault(company_id, set()).add(key)
        return {cid: frozenset(keys) for cid, keys in grouped.items()}
