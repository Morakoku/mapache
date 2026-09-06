"""Etapas del Kanban: CRUD, reordenamiento y borrado seguro."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import StageType
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.lead import Lead
from app.models.pipeline import PipelineStage
from app.repositories.base import BaseRepository
from app.utils.text import slugify


class PipelineRepository(BaseRepository[PipelineStage]):
    model = PipelineStage


class PipelineService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = PipelineRepository(session)

    async def list_stages(self) -> list[PipelineStage]:
        result = await self.session.execute(select(PipelineStage).order_by(PipelineStage.position))
        return list(result.scalars().all())

    async def get_or_404(self, stage_id: uuid.UUID) -> PipelineStage:
        stage = await self.repo.get(stage_id)
        if stage is None:
            raise NotFoundError.for_entity("stage", stage_id)
        return stage

    async def get_by_key(self, stage_key: str) -> PipelineStage | None:
        return await self.repo.get_by(stage_key=stage_key)

    async def get_by_type(self, stage_type: StageType) -> PipelineStage | None:
        """Primera etapa con ese tipo, por posición.

        Los automatismos referencian tipos, no etapas concretas: así siguen
        funcionando si el usuario renombra o añade columnas.
        """
        result = await self.session.execute(
            select(PipelineStage)
            .where(PipelineStage.stage_type == stage_type)
            .order_by(PipelineStage.position)
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_default(self) -> PipelineStage:
        """Etapa de entrada de los leads nuevos."""
        result = await self.session.execute(
            select(PipelineStage)
            .where(PipelineStage.is_default.is_(True))
            .order_by(PipelineStage.position)
            .limit(1)
        )
        stage = result.scalar_one_or_none()
        if stage is not None:
            return stage

        # Sin etapa marcada por defecto, la primera del tablero sirve. Es
        # preferible a fallar: el usuario pudo desmarcar la casilla.
        result = await self.session.execute(
            select(PipelineStage).order_by(PipelineStage.position).limit(1)
        )
        stage = result.scalar_one_or_none()
        if stage is None:
            raise ValidationError(
                "No hay etapas configuradas en el pipeline.",
                code="PIPELINE_NOT_CONFIGURED",
            )
        return stage

    async def create(self, data: dict) -> PipelineStage:
        stage_key = data.get("stage_key") or slugify(data["name"])
        if await self.get_by_key(stage_key) is not None:
            raise ConflictError(
                f"Ya existe una etapa con la clave '{stage_key}'.",
                code="STAGE_KEY_TAKEN",
            )

        if "position" not in data or data["position"] is None:
            max_pos = await self.session.execute(select(func.max(PipelineStage.position)))
            data["position"] = (max_pos.scalar_one() or 0) + 1

        stage = PipelineStage(**{**data, "stage_key": stage_key, "is_system": False})
        try:
            await self.repo.add(stage)
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError("No se pudo crear la etapa.", code="STAGE_CREATE_FAILED") from exc
        return stage

    async def update(self, stage_id: uuid.UUID, data: dict) -> PipelineStage:
        stage = await self.get_or_404(stage_id)

        # `stage_type` es inmutable: las métricas del embudo se calculan sobre
        # él, así que cambiarlo reescribiría la historia. Renombrar es libre.
        if "stage_type" in data and data["stage_type"] != stage.stage_type:
            raise ValidationError(
                "El tipo de etapa no se puede cambiar: las métricas del embudo "
                "dependen de él. Crea una etapa nueva y mueve los prospectos.",
                code="STAGE_TYPE_IMMUTABLE",
            )
        data.pop("stage_type", None)

        for key, value in data.items():
            setattr(stage, key, value)
        await self.session.flush()
        return stage

    async def delete(self, stage_id: uuid.UUID, *, move_to_stage_id: uuid.UUID | None) -> int:
        """Borra una etapa y reubica sus leads.

        Nunca borra leads en cascada: perder prospectos por reorganizar el
        tablero sería inaceptable. Si la etapa tiene leads, exige destino.
        """
        stage = await self.get_or_404(stage_id)

        if stage.is_system:
            raise ValidationError(
                f"La etapa '{stage.name}' es del sistema y no se puede eliminar. "
                "Puedes renombrarla o cambiarle el color.",
                code="STAGE_IS_SYSTEM",
            )

        count = await self.session.execute(
            select(func.count()).select_from(Lead).where(Lead.stage_id == stage_id)
        )
        lead_count = count.scalar_one() or 0

        if lead_count > 0:
            if move_to_stage_id is None:
                raise ConflictError(
                    f"La etapa '{stage.name}' tiene {lead_count} prospectos. "
                    "Indica a qué etapa moverlos antes de eliminarla.",
                    code="STAGE_HAS_LEADS",
                    details={"lead_count": lead_count},
                )
            target = await self.get_or_404(move_to_stage_id)
            await self.session.execute(
                update(Lead).where(Lead.stage_id == stage_id).values(stage_id=target.id)
            )

        await self.repo.delete(stage)
        return lead_count

    async def reorder(self, ordered_ids: list[uuid.UUID]) -> list[PipelineStage]:
        stages = await self.list_stages()
        known = {s.id for s in stages}

        if set(ordered_ids) != known:
            raise ValidationError(
                "El reordenamiento debe incluir todas las etapas exactamente una vez.",
                code="REORDER_INCOMPLETE",
                details={"expected": len(known), "received": len(set(ordered_ids))},
            )

        by_id = {s.id: s for s in stages}
        # Desplazamiento temporal para no chocar con el índice de posición
        # mientras se reasignan.
        for offset, stage_id in enumerate(ordered_ids, start=1):
            by_id[stage_id].position = offset + 1000
        await self.session.flush()

        for offset, stage_id in enumerate(ordered_ids, start=1):
            by_id[stage_id].position = offset
        await self.session.flush()

        return await self.list_stages()
