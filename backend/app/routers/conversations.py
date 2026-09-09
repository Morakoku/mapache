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

# ------------------------------------------------------------------ helpers


def _pg_table() -> str:
    return "conversations"


def _msg_table() -> str:
    return "conversation_messages"


def _to_out(conversation: Conversation) -> ConversationOut:
    """Aplana el prospecto sobre la fila de la bandeja."""
    lead = conversation.lead
    return ConversationOut(
        **ConversationOut.model_validate(conversation).model_dump(
            exclude={"company_name", "contact_name", "stage_name", "stage_type", "reply_intent"}
        ),
        company_name=lead.company.name if lead.company else "",
        contact_name=lead.contact.display_name if lead.contact else None,
        stage_name=lead.stage.name if lead.stage else "",
        stage_type=lead.stage.stage_type if lead.stage else None,
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
        is_confident=confidence >= CONFIDENCE_THRESHOLD,
    )


# ------------------------------------------------------------------ list


@router.get("", response_model=Page[ConversationOut])
async def list_conversations(
    filter_: str = Query(default="all", alias="filter"),
    channel: Channel | None = None,
    q: str | None = None,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession | None = Depends(get_db),
) -> Page[ConversationOut]:
    from app.core.config import get_settings
    from app.core.supabase_http import select as pg_select

    settings = get_settings()
    if db is None or settings.use_postgrest:
        filters: dict[str, str] = {}
        if channel is not None:
            filters["channel"] = channel.value
        if filter_ != "all":
            filters["status"] = filter_
        items = await pg_select(_pg_table(), filters=filters if filters else None, limit=size)
        return Page.build([_to_out(Conversation.model_validate(c)) for c in items], len(items), page, size)

    service = ConversationService(db)
    stmt = service.build_list_query(filter_=filter_, channel=channel, q=q)
    items, total = await ConversationRepository(db).paginate(stmt, page=page, size=size)
    return Page.build([_to_out(c) for c in items], total, page, size)


# ------------------------------------------------------------------ get


@router.get("/{conversation_id}", response_model=ConversationDetailOut)
async def get_conversation(
    conversation_id: uuid.UUID, db: AsyncSession | None = Depends(get_db)
) -> ConversationDetailOut:
    from app.core.config import get_settings
    from app.core.supabase_http import select as pg_select

    settings = get_settings()
    if db is None or settings.use_postgrest:
        items = await pg_select(_pg_table(), filters={"id": str(conversation_id)}, limit=1)
        if not items:
            from app.core.exceptions import NotFoundError
            raise NotFoundError.for_entity("conversation", conversation_id)
        conv = items[0]
        msgs = await pg_select(_msg_table(), filters={"conversation_id": str(conversation_id)}, limit=200)
        return ConversationDetailOut(
            **ConversationOut.model_validate(conv).model_dump(
                exclude={"messages", "company_name", "contact_name", "stage_name", "stage_type", "reply_intent"}
            ),
            company_name="",
            contact_name=None,
            stage_name=None,
            stage_type=None,
            reply_intent=None,
            messages=[ConversationMessageOut.model_validate(m) for m in msgs],
            intent=_intent_of(Conversation.model_validate(conv)),
        )

    conversation = await ConversationService(db).get_or_404(conversation_id)
    detail = ConversationDetailOut.model_validate(_to_out(conversation).model_dump())
    detail.messages = [ConversationMessageOut.model_validate(m) for m in conversation.messages]
    detail.intent = _intent_of(conversation)
    return detail


# ------------------------------------------------------------------ set intent


@router.patch("/{conversation_id}/intent", response_model=IntentOut)
async def set_intent(
    conversation_id: uuid.UUID,
    payload: IntentIn,
    db: AsyncSession | None = Depends(get_db),
) -> IntentOut:
    from app.core.config import get_settings
    from app.core.supabase_http import select as pg_select
    from app.core.supabase_http import update as pg_update

    settings = get_settings()
    if db is None or settings.use_postgrest:
        items = await pg_select(_pg_table(), filters={"id": str(conversation_id)}, limit=1)
        if not items:
            from app.core.exceptions import NotFoundError
            raise NotFoundError.for_entity("conversation", conversation_id)
        conv = items[0]

        stage_type = SUGGESTED_STAGE.get(payload.reply_intent)

        data: dict[str, str | bool | Decimal | None] = {
            "reply_intent": payload.reply_intent.value if payload.reply_intent else None,
            "intent_suggested_stage": stage_type.value if stage_type else None,
            "intent_source": "user",
            "intent_confidence": Decimal("1.00"),
            "intent_reviewed": True,
        }
        result = await pg_update(_pg_table(), {"id": str(conversation_id)}, data)
        conv = result[0]

        if payload.apply_suggested_stage and stage_type is not None:
            from app.schemas.crm import StageType
            leads_items = await pg_select("leads", filters={"id": str(conv.get("lead_id"))}, limit=1)
            if leads_items:
                lead_data = leads_items[0].copy()
                lead_data["stage_type"] = stage_type.value
                await pg_update("leads", {"id": str(lead_data["id"])}, {"stage_type": stage_type.value})

        updated = Conversation.model_validate(conv)
        updated.intent_source = "user"
        updated.intent_confidence = Decimal("1.00")
        updated.intent_reviewed = True
        return _intent_of(updated)

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


# ------------------------------------------------------------------ reply


@router.post("/{conversation_id}/reply", response_model=ConversationMessageOut)
async def reply(
    conversation_id: uuid.UUID,
    payload: ReplyIn,
    db: AsyncSession | None = Depends(get_db),
) -> ConversationMessageOut:
    from app.core.config import get_settings
    from app.core.supabase_http import insert as pg_insert
    from app.core.supabase_http import select as pg_select
    from app.core.supabase_http import update as pg_update
    import uuid as _uuid
    from datetime import UTC, datetime

    settings = get_settings()
    if db is None or settings.use_postgrest:
        items = await pg_select(_pg_table(), filters={"id": str(conversation_id)}, limit=1)
        if not items:
            from app.core.exceptions import NotFoundError
            raise NotFoundError.for_entity("conversation", conversation_id)
        conv = items[0]

        now = datetime.now(UTC).isoformat()
        msg_id = str(_uuid.uuid4())
        data = {
            "id": msg_id,
            "conversation_id": str(conversation_id),
            "channel": conv.get("channel"),
            "direction": "OUTBOUND",
            "author_name": None,
            "body_text": payload.body_text,
            "subject": payload.subject,
            "occurred_at": now,
            "is_unread": True,
            "is_automated": False,
            "created_at": now,
        }
        result = await pg_insert(_msg_table(), data)
        if not result:
            from app.core.exceptions import ValidationError
            raise ValidationError("No se pudo registrar la respuesta", code="REPLY_FAILED")

        await pg_update(_pg_table(), {"id": str(conversation_id)}, {
            "is_unread": False,
            "status": "AWAITING_REPLY",
            "last_message_at": now,
            "last_direction": "OUTBOUND",
            "message_count": int(conv.get("message_count", 0)) + 1,
        })
        return ConversationMessageOut.model_validate(result[0])

    service = ConversationService(db)
    conversation = await service.get_or_404(conversation_id)
    message = await service.reply(conversation, subject=payload.subject, body_text=payload.body_text)
    await service.mark_read(conversation)
    await db.commit()
    return ConversationMessageOut.model_validate(message)


# ------------------------------------------------------------------ mark read


@router.post("/{conversation_id}/read", response_model=ConversationOut)
async def mark_read(
    conversation_id: uuid.UUID, db: AsyncSession | None = Depends(get_db)
) -> ConversationOut:
    from app.core.config import get_settings
    from app.core.supabase_http import select as pg_select
    from app.core.supabase_http import update as pg_update

    settings = get_settings()
    if db is None or settings.use_postgrest:
        items = await pg_select(_pg_table(), filters={"id": str(conversation_id)}, limit=1)
        if not items:
            from app.core.exceptions import NotFoundError
            raise NotFoundError.for_entity("conversation", conversation_id)
        conv = items[0]
        now = Conversation.model_validate(conv).updated_at.isoformat() if conv.get("updated_at") else None
        result = await pg_update(_pg_table(), {"id": str(conversation_id)}, {
            "is_unread": False,
            "status": "AWAITING_REPLY",
            "updated_at": now,
        })
        return _to_out(Conversation.model_validate(result[0]))

    service = ConversationService(db)
    conversation = await service.mark_read(await service.get_or_404(conversation_id))
    await db.commit()
    return _to_out(conversation)


# ------------------------------------------------------------------ close


@router.post("/{conversation_id}/close", response_model=ConversationOut)
async def close(
    conversation_id: uuid.UUID, db: AsyncSession | None = Depends(get_db)
) -> ConversationOut:
    from app.core.config import get_settings
    from app.core.supabase_http import select as pg_select
    from app.core.supabase_http import update as pg_update
    from datetime import UTC, datetime

    settings = get_settings()
    if db is None or settings.use_postgrest:
        items = await pg_select(_pg_table(), filters={"id": str(conversation_id)}, limit=1)
        if not items:
            from app.core.exceptions import NotFoundError
            raise NotFoundError.for_entity("conversation", conversation_id)
        now = datetime.now(UTC).isoformat()
        result = await pg_update(_pg_table(), {"id": str(conversation_id)}, {
            "status": "CLOSED",
            "updated_at": now,
        })
        return _to_out(Conversation.model_validate(result[0]))

    service = ConversationService(db)
    conversation = await service.close(await service.get_or_404(conversation_id))
    await db.commit()
    return _to_out(conversation)
