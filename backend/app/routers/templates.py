"""Módulo 10 — plantillas de correo."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.mail.renderer import AVAILABLE_VARIABLES
from app.schemas.email import (
    DraftOut,
    PreviewIn,
    TemplateIn,
    TemplateOut,
    TemplateUpdate,
    TemplateVariableOut,
)
from app.services.email_svc import EmailService
from app.services.lead_svc import LeadService
from app.services.mail_admin_svc import TemplateService

router = APIRouter()


@router.get("", response_model=list[TemplateOut])
async def list_templates(
    category: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[TemplateOut]:
    service = TemplateService(db)
    result = await db.execute(service.build_list_query(category=category))
    return [TemplateOut.model_validate(t) for t in result.scalars().all()]


@router.get("/variables", response_model=list[TemplateVariableOut])
async def list_variables() -> list[TemplateVariableOut]:
    """Catálogo de variables que el editor puede insertar."""
    return [TemplateVariableOut(key=key, label=label) for key, label in AVAILABLE_VARIABLES.items()]


@router.post("", response_model=TemplateOut, status_code=status.HTTP_201_CREATED)
async def create_template(payload: TemplateIn, db: AsyncSession = Depends(get_db)) -> TemplateOut:
    template = await TemplateService(db).create(payload.model_dump())
    await db.commit()
    return TemplateOut.model_validate(template)


@router.get("/{template_id}", response_model=TemplateOut)
async def get_template(template_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> TemplateOut:
    return TemplateOut.model_validate(await TemplateService(db).get_or_404(template_id))


@router.patch("/{template_id}", response_model=TemplateOut)
async def update_template(
    template_id: uuid.UUID,
    payload: TemplateUpdate,
    db: AsyncSession = Depends(get_db),
) -> TemplateOut:
    template = await TemplateService(db).update(template_id, payload.model_dump(exclude_unset=True))
    await db.commit()
    # `updated_at` lo calcula Postgres en el UPDATE, así que queda expirado:
    # serializar sin releer dispararía un lazy load fuera del greenlet.
    await db.refresh(template)
    return TemplateOut.model_validate(template)


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(template_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> None:
    await TemplateService(db).delete(template_id)
    await db.commit()


@router.post("/{template_id}/preview", response_model=DraftOut)
async def preview_template(
    template_id: uuid.UUID,
    payload: PreviewIn,
    db: AsyncSession = Depends(get_db),
) -> DraftOut:
    """Renderiza la plantilla con los datos reales de un prospecto.

    Es la comprobación honesta de una plantilla: enseña qué variables se
    quedan vacías con ese lead concreto antes de mandarla a 50 empresas.
    """
    templates = TemplateService(db)
    emails = EmailService(db)

    template = await templates.get_or_404(template_id)
    lead = await LeadService(db).get_or_404(payload.lead_ids[0])
    settings = await emails.get_settings_row()
    account = await emails.resolve_account(payload.account_id)

    draft = await emails.build_draft(lead, template=template, settings=settings, account=account)
    return DraftOut.from_draft(draft)
