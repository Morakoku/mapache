"""Contact queue — leads preparados para contacto humano."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.enums import ActorType, ActivityType
from app.models.lead import Lead
from app.models.company import Company
from app.models.contact import Contact
from app.models.activity import Activity

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/contact-queue", tags=["contact-queue"])


class ContactQueueOut(BaseModel):
    """Lead preparado para contacto humano."""
    lead_id: str
    score: int
    company_name: str
    company_city: str | None
    company_phone: str | None
    company_email: str | None
    company_whatsapp: str | None
    category: str | None
    address: str | None
    description: str | None
    preferred_channel: str
    contact_name: str | None
    contact_phone: str | None
    contact_email: str | None
    contact_position: str | None
    last_contact_at: datetime | None
    contact_attempts: int
    next_follow_up_at: datetime | None
    suggested_message: str


def _generate_message(company_name: str, category: str | None, channel: str) -> str:
    if channel == "whatsapp":
        return f"👋 Hola, soy de {company_name}. ¿Hablamos sobre cómo podemos ayudarte?"
    elif channel == "phone":
        return f"📞 Hola, soy de {company_name}. Te llamo para conocer más sobre tu negocio."
    elif channel == "email":
        return f"📧 Hola, soy de {company_name}. Me gustaría contarte sobre nuestros servicios."
    return f"Hola, soy de {company_name}."


@router.get("/", response_model=list[ContactQueueOut])
async def get_contact_queue(
    min_score: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
) -> list[ContactQueueOut]:
    stmt = (
        select(Lead, Company, Contact)
        .join(Company, Lead.company_id == Company.id)
        .outerjoin(Contact, Lead.contact_id == Contact.id)
        .where(Lead.score >= min_score)
        .order_by(Lead.score.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    rows = result.all()

    items = []
    for lead, company, contact in rows:
        if company.whatsapp:
            preferred = "whatsapp"
        elif company.phone:
            preferred = "phone"
        elif company.email:
            preferred = "email"
        else:
            preferred = "none"

        suggested = _generate_message(company.name, company.category, preferred)

        activities_result = await db.execute(
            select(func.count(Activity.id)).where(Activity.lead_id == lead.id)
        )
        attempts = activities_result.scalar() or 0

        items.append(ContactQueueOut(
            lead_id=str(lead.id),
            score=lead.score or 0,
            company_name=company.name,
            company_city=company.city,
            company_phone=company.phone,
            company_email=company.email,
            company_whatsapp=company.whatsapp,
            category=company.category,
            address=company.address,
            description=company.description,
            preferred_channel=preferred,
            contact_name=contact.full_name if contact else None,
            contact_phone=contact.phone if contact else None,
            contact_email=contact.email if contact else None,
            contact_position=contact.job_title if contact else None,
            last_contact_at=lead.first_contact_at,
            contact_attempts=attempts,
            next_follow_up_at=lead.next_follow_up_at,
            suggested_message=suggested,
        ))

    return items


@router.post("/{lead_id}/attempt", response_model=dict[str, str])
async def record_contact_attempt(
    lead_id: str,
    channel: str = "phone",
    result: str = "no_response",
    notes: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    lead = await db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    now = datetime.now(timezone.utc)
    lead.first_contact_at = lead.first_contact_at or now

    activity = Activity(
        lead_id=lead.id,
        activity_type=ActivityType.EMAIL_SENT if channel == "email" else ActivityType.STAGE_CHANGED,
        actor=ActorType.USER,
        title=f"Contacto vía {channel}",
        description=notes or f"Intento de contacto por {channel}: {result}",
    )
    db.add(activity)
    await db.commit()

    return {"status": "recorded", "lead_id": lead_id, "channel": channel, "result": result}
