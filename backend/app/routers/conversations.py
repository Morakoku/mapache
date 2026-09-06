"""Módulo 16 — bandeja de conversaciones."""

from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.classifier import CONFIDENCE_THRESHOLD, SUGGESTED_STAGE
from app.core.database import get_db
from app.core.enums import ActorType, Channel
from app.models.email import Conversation
from app.schemas.common import Page
from app.schemas.email import (
    ConversationDetailOut,
    ConversationMessageOut,
    ConversationOut,
    IntentIn,
    IntentOut,
    ReplyIn,
)
from app.services.conversation_svc import ConversationRepository, ConversationService
from app.services.lead_svc import LeadService
from app.services.pipeline_svc import PipelineService

router = APIRouter()


@router.get("", response_model=Page[ConversationOut])
async def list_conversations(
    filter_: str = Query(default="all", alias="filter"),
    channel: Channel | None = None,
    q: str | None = None,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> Page[ConversationOut]:
    service = ConversationService(db)
    stmt = service.build_list_query(filter_=filter_, channel=channel, q=q)
    items, total = await ConversationRepository(db).paginate(stmt, page=page, size=size)
    return Page.build([_to_out(c) for c in items], total, page, size)


def _to_out(conversation: Conversation) -> ConversationOut:
    """Aplana el prospecto sobre la fila de la bandeja."""
    lead = conversation.lead
    return ConversationOut(
        **ConversationOut.model_validate(conversation).model_dump(
            exclude={"company_name", "contact_name", "stage_name", "stage_type", "reply_intent"}
        ),
        company_name=lead.company.name,
        contact_name=lead.contact.display_name if lead.contact else None,
        stage_name=lead.stage.name,
        stage_type=lead.stage.stage_type,
        reply_intent=conversation.reply_intent,
    )


def _intent_of(conversation: Conversation) -> IntentOut | None:
    if conversation.reply_intent is None:
        return None
    confidence = float(conversation.intent_confidence or 0)
    return IntentOut(
        reply_intent=conversation.reply_intent,
        confidence=confidence,
        summary=conversation.intent_summary,
        suggested_stage=conversation.intent_suggested_stage,
        reply_points=list(conversation.intent_reply_points or []),
        source=conversation.intent_source,
        reviewed=conversation.intent_reviewed,
        # Por debajo del umbral la UI la pinta atenuada y no ofrece aplicarla.
        is_confident=confidence >= CONFIDENCE_THRESHOLD,
    )


@router.get("/{conversation_id}", response_model=ConversationDetailOut)
async def get_conversation(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ConversationDetailOut:
    conversation = await ConversationService(db).get_or_404(conversation_id)
    detail = ConversationDetailOut.model_validate(_to_out(conversation).model_dump())
    detail.messages = [ConversationMessageOut.model_validate(m) for m in conversation.messages]
    detail.intent = _intent_of(conversation)
    return detail


@router.patch("/{conversation_id}/intent", response_model=IntentOut)
async def set_intent(
    conversation_id: uuid.UUID,
    payload: IntentIn,
    db: AsyncSession = Depends(get_db),
) -> IntentOut:
    """Corrige la intención detectada.

    La IA sugiere, el usuario decide (§9). Al corregir se marca la
    conversación como revisada: la siguiente clasificación ya no pisa este
    valor. Mover la etapa es opcional y explícito — corregir una etiqueta no
    debería mover un prospecto de columna por sorpresa.
    """
    service = ConversationService(db)
    conversation = await service.get_or_404(conversation_id)

    conversation.reply_intent = payload.reply_intent
    conversation.intent_suggested_stage = SUGGESTED_STAGE.get(payload.reply_intent)
    conversation.intent_source = "user"
    conversation.intent_confidence = Decimal("1.00")
    conversation.intent_reviewed = True

    lead = await LeadService(db).get_or_404(conversation.lead_id)
    lead.reply_intent = payload.reply_intent

    if payload.apply_suggested_stage and conversation.intent_suggested_stage is not None:
        stage = await PipelineService(db).get_by_type(conversation.intent_suggested_stage)
        if stage is not None:
            await LeadService(db).move_stage(
                lead,
                stage.id,
                actor=ActorType.USER,
                reason=f"Intención confirmada: {payload.reply_intent.value}",
            )

    await db.commit()
    await db.refresh(conversation)
    intent = _intent_of(conversation)
    assert intent is not None  # se acaba de asignar
    return intent


@router.post("/{conversation_id}/reply", response_model=ConversationMessageOut)
async def reply(
    conversation_id: uuid.UUID,
    payload: ReplyIn,
    db: AsyncSession = Depends(get_db),
) -> ConversationMessageOut:
    """Responde por el canal del hilo.

    El transporte lo decide el `ChannelAdapter`: email hoy, WhatsApp en la
    Fase 10, sin cambiar este endpoint.
    """
    service = ConversationService(db)
    conversation = await service.get_or_404(conversation_id)
    message = await service.reply(
        conversation, subject=payload.subject, body_text=payload.body_text
    )
    await service.mark_read(conversation)
    await db.commit()
    return ConversationMessageOut.model_validate(message)


@router.post("/{conversation_id}/read", response_model=ConversationOut)
async def mark_read(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ConversationOut:
    service = ConversationService(db)
    conversation = await service.mark_read(await service.get_or_404(conversation_id))
    await db.commit()
    return _to_out(conversation)


@router.post("/{conversation_id}/close", response_model=ConversationOut)
async def close(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ConversationOut:
    service = ConversationService(db)
    conversation = await service.close(await service.get_or_404(conversation_id))
    await db.commit()
    return _to_out(conversation)
