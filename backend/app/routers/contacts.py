"""Módulos 6 y 19 — contactos, actividades y tareas."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.enums import ActivityType
from app.schemas.common import Page
from app.schemas.crm import (
    ActivityOut,
    ContactIn,
    ContactOut,
    ContactUpdate,
    TaskIn,
    TaskOut,
    TaskUpdate,
)
from app.services.activity_svc import ActivityService
from app.services.contact_svc import ContactRepository, ContactService, TaskRepository, TaskService

contacts_router = APIRouter()
activities_router = APIRouter()
tasks_router = APIRouter()


# ------------------------------------------------------------------ contactos


@contacts_router.get("", response_model=Page[ContactOut])
async def list_contacts(
    company_id: uuid.UUID | None = None,
    q: str | None = None,
    has_email: bool | None = None,
    contactable: bool | None = None,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> Page[ContactOut]:
    service = ContactService(db)
    stmt = service.build_list_query(
        company_id=company_id, q=q, has_email=has_email, contactable=contactable
    )
    items, total = await ContactRepository(db).paginate(stmt, page=page, size=size)
    return Page.build([ContactOut.model_validate(c) for c in items], total, page, size)


@contacts_router.post("", response_model=ContactOut, status_code=status.HTTP_201_CREATED)
async def create_contact(payload: ContactIn, db: AsyncSession = Depends(get_db)) -> ContactOut:
    contact = await ContactService(db).create(payload.model_dump())
    await db.commit()
    return ContactOut.model_validate(contact)


@contacts_router.get("/{contact_id}", response_model=ContactOut)
async def get_contact(contact_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> ContactOut:
    return ContactOut.model_validate(await ContactService(db).get_or_404(contact_id))


@contacts_router.patch("/{contact_id}", response_model=ContactOut)
async def update_contact(
    contact_id: uuid.UUID,
    payload: ContactUpdate,
    db: AsyncSession = Depends(get_db),
) -> ContactOut:
    contact = await ContactService(db).update(contact_id, payload.model_dump(exclude_unset=True))
    await db.commit()
    # Ver nota en `services.update_service`: `updated_at` queda expirado tras
    # el UPDATE y hay que releerlo antes de serializar.
    await db.refresh(contact)
    return ContactOut.model_validate(contact)


@contacts_router.delete("/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_contact(contact_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> None:
    await ContactService(db).delete(contact_id)
    await db.commit()


@contacts_router.post("/{contact_id}/primary", response_model=ContactOut)
async def set_primary_contact(
    contact_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ContactOut:
    contact = await ContactService(db).set_primary(contact_id)
    await db.commit()
    await db.refresh(contact)
    return ContactOut.model_validate(contact)


@contacts_router.post("/{contact_id}/verify-email", response_model=ContactOut)
async def verify_contact_email(
    contact_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ContactOut:
    contact = await ContactService(db).reverify(contact_id)
    await db.commit()
    await db.refresh(contact)
    return ContactOut.model_validate(contact)


# ------------------------------------------------------------------ actividades


@activities_router.get("", response_model=Page[ActivityOut])
async def list_activities(
    lead_id: uuid.UUID | None = None,
    company_id: uuid.UUID | None = None,
    activity_type: ActivityType | None = None,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> Page[ActivityOut]:
    service = ActivityService(db)
    stmt = service.build_timeline_query(
        lead_id=lead_id, company_id=company_id, activity_type=activity_type
    )
    items, total = await service.repo.paginate(stmt, page=page, size=size)
    return Page.build([ActivityOut.model_validate(a) for a in items], total, page, size)


# ------------------------------------------------------------------ tareas


@tasks_router.get("", response_model=Page[TaskOut])
async def list_tasks(
    completed: bool | None = None,
    lead_id: uuid.UUID | None = None,
    due_before: datetime | None = None,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> Page[TaskOut]:
    service = TaskService(db)
    stmt = service.build_list_query(completed=completed, lead_id=lead_id, due_before=due_before)
    items, total = await TaskRepository(db).paginate(stmt, page=page, size=size)
    return Page.build([TaskOut.model_validate(t) for t in items], total, page, size)


@tasks_router.post("", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
async def create_task(payload: TaskIn, db: AsyncSession = Depends(get_db)) -> TaskOut:
    task = await TaskService(db).create(payload.model_dump())
    await db.commit()
    return TaskOut.model_validate(task)


@tasks_router.patch("/{task_id}", response_model=TaskOut)
async def update_task(
    task_id: uuid.UUID,
    payload: TaskUpdate,
    db: AsyncSession = Depends(get_db),
) -> TaskOut:
    task = await TaskService(db).update(task_id, payload.model_dump(exclude_unset=True))
    await db.commit()
    return TaskOut.model_validate(task)


@tasks_router.post("/{task_id}/complete", response_model=TaskOut)
async def complete_task(task_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> TaskOut:
    task = await TaskService(db).complete(task_id)
    await db.commit()
    return TaskOut.model_validate(task)


@tasks_router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(task_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> None:
    await TaskService(db).delete(task_id)
    await db.commit()
