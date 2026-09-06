"""Módulo 6 — contactos, y tareas del usuario."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Select, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ActivityType, ActorType, VerificationStatus
from app.core.exceptions import ConflictError, NotFoundError
from app.enrichment.email_verifier import verify_email
from app.enrichment.extractors import is_role_email
from app.models.activity import Task
from app.models.company import Company
from app.models.contact import Contact
from app.repositories.base import BaseRepository
from app.services.activity_svc import ActivityService
from app.utils.phone import to_e164


class ContactRepository(BaseRepository[Contact]):
    model = Contact


class TaskRepository(BaseRepository[Task]):
    model = Task


class ContactService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ContactRepository(session)
        self.activities = ActivityService(session)

    async def get_or_404(self, contact_id: uuid.UUID) -> Contact:
        contact = await self.repo.get(contact_id)
        if contact is None:
            raise NotFoundError.for_entity("contact", contact_id)
        return contact

    async def create(self, data: dict, *, owner_id: uuid.UUID | None = None) -> Contact:
        company = await self.session.get(Company, data["company_id"])
        if company is None:
            raise NotFoundError.for_entity("company", data["company_id"])

        data = dict(data)
        if data.get("phone"):
            data["phone"] = to_e164(data["phone"]) or data["phone"]
        if data.get("whatsapp"):
            data["whatsapp"] = to_e164(data["whatsapp"]) or data["whatsapp"]

        email = data.get("email")
        if email:
            email = email.strip().lower()
            data["email"] = email
            data["is_role_email"] = is_role_email(email)
            status, _confidence = await verify_email(email)
            data["email_verified"] = status

        contact = Contact(owner_id=owner_id, **data)
        try:
            await self.repo.add(contact)
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError(
                f"Ya existe un contacto con el email '{email}' en esta empresa.",
                code="CONTACT_EMAIL_TAKEN",
            ) from exc

        await self.activities.record(
            ActivityType.EMAIL_FOUND if email else ActivityType.NOTE,
            title=f"Contacto añadido: {contact.display_name}",
            company_id=company.id,
            contact_id=contact.id,
            actor=ActorType.USER,
            owner_id=owner_id,
        )
        return contact

    async def update(self, contact_id: uuid.UUID, data: dict) -> Contact:
        contact = await self.get_or_404(contact_id)

        if data.get("phone"):
            data["phone"] = to_e164(data["phone"]) or data["phone"]
        if data.get("email"):
            email = data["email"].strip().lower()
            data["email"] = email
            data["is_role_email"] = is_role_email(email)
            status, _ = await verify_email(email)
            data["email_verified"] = status

        for key, value in data.items():
            setattr(contact, key, value)
        await self.session.flush()
        return contact

    async def delete(self, contact_id: uuid.UUID) -> None:
        await self.repo.delete(await self.get_or_404(contact_id))

    async def set_primary(self, contact_id: uuid.UUID) -> Contact:
        """Marca el contacto como principal y desmarca los demás de la empresa."""
        contact = await self.get_or_404(contact_id)
        others = await self.session.execute(
            select(Contact).where(
                Contact.company_id == contact.company_id, Contact.id != contact.id
            )
        )
        for other in others.scalars().all():
            other.is_primary = False
        contact.is_primary = True
        await self.session.flush()
        return contact

    async def reverify(self, contact_id: uuid.UUID) -> Contact:
        contact = await self.get_or_404(contact_id)
        if contact.email:
            status, _ = await verify_email(contact.email)
            contact.email_verified = status
            await self.session.flush()
        return contact

    def build_list_query(
        self,
        *,
        company_id: uuid.UUID | None = None,
        q: str | None = None,
        has_email: bool | None = None,
        contactable: bool | None = None,
    ) -> Select[tuple[Contact]]:
        stmt = select(Contact).order_by(Contact.is_primary.desc(), Contact.created_at.desc())

        if company_id is not None:
            stmt = stmt.where(Contact.company_id == company_id)
        if q:
            pattern = f"%{q}%"
            stmt = stmt.where(
                or_(
                    Contact.full_name.ilike(pattern),
                    Contact.first_name.ilike(pattern),
                    Contact.last_name.ilike(pattern),
                    Contact.email.ilike(pattern),
                )
            )
        if has_email is True:
            stmt = stmt.where(Contact.email.is_not(None))
        elif has_email is False:
            stmt = stmt.where(Contact.email.is_(None))
        if contactable is True:
            stmt = stmt.where(
                Contact.email.is_not(None),
                Contact.do_not_contact.is_(False),
                Contact.email_verified.notin_(
                    [VerificationStatus.INVALID, VerificationStatus.BOUNCED]
                ),
            )
        return stmt


class TaskService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = TaskRepository(session)
        self.activities = ActivityService(session)

    async def get_or_404(self, task_id: uuid.UUID) -> Task:
        task = await self.repo.get(task_id)
        if task is None:
            raise NotFoundError.for_entity("task", task_id)
        return task

    async def create(self, data: dict, *, owner_id: uuid.UUID | None = None) -> Task:
        task = Task(owner_id=owner_id, **data)
        await self.repo.add(task)
        await self.activities.record(
            ActivityType.TASK_CREATED,
            title=f"Tarea: {task.title}",
            lead_id=task.lead_id,
            company_id=task.company_id,
            actor=ActorType.USER,
            owner_id=owner_id,
        )
        return task

    async def update(self, task_id: uuid.UUID, data: dict) -> Task:
        task = await self.get_or_404(task_id)
        for key, value in data.items():
            setattr(task, key, value)
        await self.session.flush()
        return task

    async def complete(self, task_id: uuid.UUID) -> Task:
        task = await self.get_or_404(task_id)
        if task.completed_at is None:
            task.completed_at = datetime.now(UTC)
            await self.activities.record(
                ActivityType.TASK_COMPLETED,
                title=f"Tarea completada: {task.title}",
                lead_id=task.lead_id,
                company_id=task.company_id,
                actor=ActorType.USER,
                owner_id=task.owner_id,
            )
            await self.session.flush()
        return task

    async def delete(self, task_id: uuid.UUID) -> None:
        await self.repo.delete(await self.get_or_404(task_id))

    def build_list_query(
        self,
        *,
        completed: bool | None = None,
        lead_id: uuid.UUID | None = None,
        due_before: datetime | None = None,
    ) -> Select[tuple[Task]]:
        stmt = select(Task).order_by(Task.due_at.asc().nullslast(), Task.priority)
        if completed is True:
            stmt = stmt.where(Task.completed_at.is_not(None))
        elif completed is False:
            stmt = stmt.where(Task.completed_at.is_(None))
        if lead_id is not None:
            stmt = stmt.where(Task.lead_id == lead_id)
        if due_before is not None:
            stmt = stmt.where(Task.due_at.is_not(None), Task.due_at <= due_before)
        return stmt
