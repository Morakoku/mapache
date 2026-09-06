"""Timeline de actividad (Módulo 19).

Todo lo que le pasa a un prospecto se registra aquí. Es lo que responde
"¿por qué está este lead donde está?" seis semanas después, cuando ya nadie
se acuerda.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ActivityType, ActorType
from app.models.activity import Activity
from app.repositories.base import BaseRepository


class ActivityRepository(BaseRepository[Activity]):
    model = Activity


class ActivityService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ActivityRepository(session)

    async def record(
        self,
        activity_type: ActivityType,
        *,
        title: str,
        lead_id: uuid.UUID | None = None,
        company_id: uuid.UUID | None = None,
        contact_id: uuid.UUID | None = None,
        description: str | None = None,
        actor: ActorType = ActorType.SYSTEM,
        metadata: dict[str, Any] | None = None,
        owner_id: uuid.UUID | None = None,
    ) -> Activity:
        """Registra un evento. No hace commit: lo hace quien posee la transacción."""
        activity = Activity(
            activity_type=activity_type,
            title=title,
            description=description,
            actor=actor,
            lead_id=lead_id,
            company_id=company_id,
            contact_id=contact_id,
            activity_metadata=metadata,
            owner_id=owner_id,
        )
        self.session.add(activity)
        return activity

    def build_timeline_query(
        self,
        *,
        lead_id: uuid.UUID | None = None,
        company_id: uuid.UUID | None = None,
        activity_type: ActivityType | None = None,
    ) -> Select[tuple[Activity]]:
        stmt = select(Activity).order_by(Activity.occurred_at.desc(), Activity.id.desc())
        if lead_id is not None:
            stmt = stmt.where(Activity.lead_id == lead_id)
        if company_id is not None:
            stmt = stmt.where(Activity.company_id == company_id)
        if activity_type is not None:
            stmt = stmt.where(Activity.activity_type == activity_type)
        return stmt
