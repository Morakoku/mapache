"""Puente Mapache → Guaki (LOOP-23): prospectos comerciales de Guaki.

Convierte la información que Mapache ya posee (empresas + leads + señales +
clasificación del Guaki Filter) en prospectos útiles para Guaki. No crea una
base de datos paralela: es una vista de lectura sobre los datos existentes.

Cada prospecto incluye: negocio, categoría, ciudad, web, redes, email, teléfono,
nivel de oportunidad (HIGH/MEDIUM/LOW/NOT_RELEVANT), razones del filtro, fecha de
detección, estado comercial, contacto realizado, respuesta, y los campos de lado
Guaki (registro, plan, upgrade) que quedan `null` hasta que exista el vínculo
Guaki ↔ Mapache.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import EmailStatus, ReplyIntent
from app.models.company import Company, CompanySignal
from app.models.email import EmailMessage
from app.models.guaki_link import GuakiLink
from app.models.lead import Lead
from app.scoring.dimensions import ScoreInput
from app.scoring.guaki import GUAKI_SOCIAL_SIGNALS, GuakiOpportunity, guaki_opportunity
from app.services.guaki_link_svc import GuakiLinkService

_SENT_STATUSES = frozenset({EmailStatus.SENT, EmailStatus.DELIVERED})


class GuakiProspectService:
    """Vista de prospectos comerciales de Guaki sobre los datos de Mapache."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def prospects(self, limit: int = 200) -> tuple[list[dict[str, object]], dict[str, int]]:
        """Devuelve (prospectos, resumen de embudo) ordenados por actividad/score."""
        stmt = (
            select(Lead)
            .order_by(Lead.last_activity_at.desc().nullslast(), Lead.score.desc())
            .limit(limit)
        )
        leads = list((await self.session.execute(stmt)).scalars().all())

        signals = await self._signals_by_company([lead.company_id for lead in leads])
        contacted = await self._contacted_leads([lead.id for lead in leads])
        links = await GuakiLinkService(self.session).links_by_leads([lead.id for lead in leads])

        items: list[dict[str, object]] = []
        for lead in leads:
            company = lead.company
            if company is None:
                continue
            items.append(
                self._to_prospect(
                    lead,
                    signals.get(company.id, frozenset()),
                    lead.id in contacted,
                    links.get(lead.id),
                )
            )

        return items, self._summary(items)

    async def funnel(self, limit: int = 500) -> dict[str, object]:
        """Embudo comercial de Guaki con métricas reales (LOOP-26).

        MRR = 0 hasta que existan pagos confirmados (LOOP-27); upgrades requieren
        historial de plan (futuro). Las tasas llevan numerador/denominador.
        """
        items, _ = await self.prospects(limit)

        def cnt(predicate: Callable[[dict[str, object]], object]) -> int:
            return sum(1 for x in items if bool(predicate(x)))

        detectadas = len(items)
        registrados = cnt(lambda x: x["registro_en_guaki"] is not None)
        pagos = cnt(
            lambda x: (
                x["registro_en_guaki"] is not None and x["plan_actual"] not in (None, "GRATIS")
            )
        )
        gratuitos = cnt(
            lambda x: x["registro_en_guaki"] is not None and x["plan_actual"] in (None, "GRATIS")
        )

        return {
            "detectadas": detectadas,
            "high": cnt(lambda x: x["oportunidad"] == "HIGH"),
            "medium": cnt(lambda x: x["oportunidad"] == "MEDIUM"),
            "contactados": cnt(lambda x: x["contacto_realizado"]),
            "interesados": cnt(lambda x: x["respuesta"]),
            "registrados": registrados,
            "gratuitos": gratuitos,
            "pagos": pagos,
            "upgrades": 0,
            "conversion_registro": {
                "value": round(registrados / detectadas * 100, 1) if detectadas else 0,
                "numerator": registrados,
                "denominator": detectadas,
            },
            "mrr": 0,
            "mrr_status": "PENDING_PAYMENTS",
        }

    # ------------------------------------------------------------------ helpers

    async def _signals_by_company(
        self, company_ids: Sequence[uuid.UUID]
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

    async def _contacted_leads(self, lead_ids: Sequence[uuid.UUID]) -> set[uuid.UUID]:
        if not lead_ids:
            return set()
        rows = (
            await self.session.execute(
                select(EmailMessage.lead_id)
                .where(
                    EmailMessage.lead_id.in_(lead_ids),
                    EmailMessage.status.in_(_SENT_STATUSES),
                )
                .distinct()
            )
        ).all()
        return {row[0] for row in rows if row[0] is not None}

    def _classification(
        self, lead: Lead, company: Company, signals: frozenset[str]
    ) -> dict[str, object]:
        """Usa el bloque `guaki` del scoring si existe; si no, lo calcula en vivo."""
        breakdown = lead.score_breakdown or {}
        stored = breakdown.get("guaki")
        if isinstance(stored, dict) and stored.get("opportunity"):
            return {
                "opportunity": stored["opportunity"],
                "reasons": list(stored.get("reasons") or []),
            }

        data = ScoreInput(
            company_category=company.category,
            company_categories=tuple(company.categories or ()),
            company_city=company.city,
            company_email=company.email,
            company_phone=company.phone,
            company_website=company.website,
            rating=company.rating,
            reviews_count=company.reviews_count,
            signals=signals,
            data_quality_score=company.data_quality_score or 0,
            extracted_at=company.first_extracted_at,
            contact_email=lead.contact.email if lead.contact else None,
            contact_phone=lead.contact.phone if lead.contact else None,
        )
        classification: GuakiOpportunity = guaki_opportunity(data)
        return {"opportunity": classification.opportunity, "reasons": classification.reasons}

    def _to_prospect(
        self,
        lead: Lead,
        signals: frozenset[str],
        contacted: bool,
        link: GuakiLink | None,
    ) -> dict[str, object]:
        company = lead.company
        social = sorted(signals & GUAKI_SOCIAL_SIGNALS)
        classification = self._classification(lead, company, signals)

        replied = bool(
            (
                lead.reply_intent is not None
                and lead.reply_intent not in {ReplyIntent.UNKNOWN, ReplyIntent.OUT_OF_OFFICE}
            )
            or lead.replied_at is not None
        )

        return {
            "id": str(lead.id),
            "company_id": str(company.id),
            "negocio": company.name,
            "categoria": company.category,
            "ciudad": company.city,
            "web": company.website,
            "redes": social or None,
            "email": ((lead.contact.email if lead.contact else None) or company.email),
            "telefono": ((lead.contact.phone if lead.contact else None) or company.phone),
            "oportunidad": classification["opportunity"],
            "razones": classification["reasons"],
            "fecha_deteccion": (
                company.first_extracted_at.isoformat() if company.first_extracted_at else None
            ),
            "estado_comercial": (lead.stage.stage_type if lead.stage else lead.status.value),
            "contacto_realizado": contacted,
            "respuesta": replied,
            # Lado Guaki: datos del vínculo Prospecto↔Negocio (LOOP-24).
            "vinculo": GuakiLinkService.to_dict(link) if link else None,
            "registro_en_guaki": (
                link.registered_at.isoformat() if link and link.registered_at else None
            ),
            "plan_actual": link.plan if link else None,
            "posibilidad_upgrade": None,
        }

    @staticmethod
    def _summary(items: list[dict[str, object]]) -> dict[str, int]:
        def count(predicate: Callable[[dict[str, object]], object]) -> int:
            return sum(1 for x in items if bool(predicate(x)))

        return {
            "detectadas": len(items),
            "alta": count(lambda x: x["oportunidad"] == "HIGH"),
            "media": count(lambda x: x["oportunidad"] == "MEDIUM"),
            "baja": count(lambda x: x["oportunidad"] == "LOW"),
            "no_relevantes": count(lambda x: x["oportunidad"] == "NOT_RELEVANT"),
            "con_email": count(lambda x: x["email"]),
            "redes_sin_web": count(lambda x: x["redes"] and not x["web"]),
            "contactados": count(lambda x: x["contacto_realizado"]),
            "respondieron": count(lambda x: x["respuesta"]),
            "registrados": count(lambda x: x["registro_en_guaki"] is not None),
            "con_plan": count(lambda x: x["plan_actual"] is not None),
            "conversiones": 0,
        }
