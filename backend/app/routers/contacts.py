"""Módulos 6 y 19 — contactos, actividades y tareas."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.enums import ActivityType
from app.core.exceptions import NotFoundError
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
from app.utils.phone import to_e164

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
    db: AsyncSession | None = Depends(get_db),
) -> Page[ContactOut]:
    from app.core.config import get_settings

    settings = get_settings()
    if settings.use_postgrest or db is None:
        from app.core.supabase_http import select as pg_select

        filters: dict[str, str] = {}
        if company_id is not None:
            filters["company_id"] = str(company_id)
        items = await pg_select("contacts", filters=filters if filters else None, limit=size)
        total = len(items)
        # Si hay más páginas, ajustar - PostgREST no devuelve total, devolvemos lo que tenemos
        return Page.build([ContactOut.model_validate(c) for c in items], total, page, size)

    service = ContactService(db)
    stmt = service.build_list_query(
        company_id=company_id, q=q, has_email=has_email, contactable=contactable
    )
    items, total = await ContactRepository(db).paginate(stmt, page=page, size=size)
    return Page.build([ContactOut.model_validate(c) for c in items], total, page, size)


@contacts_router.post("", response_model=ContactOut, status_code=status.HTTP_201_CREATED)
async def create_contact(payload: ContactIn, db: AsyncSession | None = Depends(get_db)) -> ContactOut:
    from app.core.config import get_settings

    settings = get_settings()
    if settings.use_postgrest or db is None:
        from app.core.supabase_http import insert as pg_insert

        data = dict(payload.model_dump())
        data["id"] = str(uuid.uuid4())
        data["company_id"] = str(data["company_id"])
        if data.get("phone"):
            data["phone"] = to_e164(data["phone"]) or data["phone"]
        if data.get("whatsapp"):
            data["whatsapp"] = to_e164(data["whatsapp"]) or data["whatsapp"]
        if data.get("email"):
            data["email"] = data["email"].strip().lower()
        result = await pg_insert("contacts", data)
        if result:
            return ContactOut.model_validate(result[0])
        raise HTTPException(status_code=500, detail="No se pudo crear el contacto")

    contact = await ContactService(db).create(payload.model_dump())
    await db.commit()
    return ContactOut.model_validate(contact)


@contacts_router.get("/{contact_id}", response_model=ContactOut)
async def get_contact(contact_id: uuid.UUID, db: AsyncSession | None = Depends(get_db)) -> ContactOut:
    from app.core.config import get_settings

    settings = get_settings()
    if settings.use_postgrest or db is None:
        from app.core.supabase_http import select as pg_select

        items = await pg_select("contacts", filters={"id": str(contact_id)}, limit=1)
        if not items:
            raise NotFoundError.for_entity("contact", contact_id)
        return ContactOut.model_validate(items[0])

    return ContactOut.model_validate(await ContactService(db).get_or_404(contact_id))


@contacts_router.patch("/{contact_id}", response_model=ContactOut)
async def update_contact(
    contact_id: uuid.UUID,
    payload: ContactUpdate,
    db: AsyncSession | None = Depends(get_db),
) -> ContactOut:
    from app.core.config import get_settings

    settings = get_settings()
    if settings.use_postgrest or db is None:
        from app.core.supabase_http import update as pg_update

        data = payload.model_dump(exclude_unset=True)
        if data.get("phone"):
            data["phone"] = to_e164(data["phone"]) or data["phone"]
        if data.get("email"):
            data["email"] = data["email"].strip().lower()
        result = await pg_update("contacts", {"id": str(contact_id)}, data)
        if not result:
            raise NotFoundError.for_entity("contact", contact_id)
        return ContactOut.model_validate(result[0])

    contact = await ContactService(db).update(contact_id, payload.model_dump(exclude_unset=True))
    await db.commit()
    await db.refresh(contact)
    return ContactOut.model_validate(contact)


@contacts_router.delete("/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_contact(contact_id: uuid.UUID, db: AsyncSession | None = Depends(get_db)) -> None:
    from app.core.config import get_settings

    settings = get_settings()
    if settings.use_postgrest or db is None:
        from app.core.supabase_http import delete as pg_delete

        await pg_delete("contacts", {"id": str(contact_id)})
        return

    await ContactService(db).delete(contact_id)
    await db.commit()


@contacts_router.post("/{contact_id}/primary", response_model=ContactOut)
async def set_primary_contact(
    contact_id: uuid.UUID,
    db: AsyncSession | None = Depends(get_db),
) -> ContactOut:
    from app.core.config import get_settings

    settings = get_settings()
    if settings.use_postgrest or db is None:
        from app.core.supabase_http import select as pg_select
        from app.core.supabase_http import update as pg_update

        # Obtener el contacto y su empresa
        items = await pg_select("contacts", filters={"id": str(contact_id)}, limit=1)
        if not items:
            raise NotFoundError.for_entity("contact", contact_id)
        contact = items[0]
        company_id = contact.get("company_id")
        if not company_id:
            raise NotFoundError.for_entity("contact", contact_id)

        # Desmarcar los demás de la misma empresa
        others = await pg_select("contacts", filters={"company_id": company_id}, limit=200)
        for other in others:
            if other["id"] != str(contact_id):
                await pg_update("contacts", {"id": other["id"]}, {"is_primary": False})

        # Marcar este como primary
        result = await pg_update("contacts", {"id": str(contact_id)}, {"is_primary": True})
        if result:
            return ContactOut.model_validate(result[0])
        raise NotFoundError.for_entity("contact", contact_id)

    contact = await ContactService(db).set_primary(contact_id)
    await db.commit()
    await db.refresh(contact)
    return ContactOut.model_validate(contact)


@contacts_router.post("/{contact_id}/verify-email", response_model=ContactOut)
async def verify_contact_email(
    contact_id: uuid.UUID,
    db: AsyncSession | None = Depends(get_db),
) -> ContactOut:
    from app.core.config import get_settings

    settings = get_settings()
    if settings.use_postgrest or db is None:
        from app.enrichment.email_verifier import verify_email
        from app.core.supabase_http import select as pg_select
        from app.core.supabase_http import update as pg_update

        items = await pg_select("contacts", filters={"id": str(contact_id)}, limit=1)
        if not items:
            raise NotFoundError.for_entity("contact", contact_id)
        contact = items[0]
        email = contact.get("email")
        if not email:
            return ContactOut.model_validate(contact)
        status, _ = await verify_email(email)
        result = await pg_update("contacts", {"id": str(contact_id)}, {"email_verified": status})
        if result:
            return ContactOut.model_validate(result[0])
        raise NotFoundError.for_entity("contact", contact_id)

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
    db: AsyncSession | None = Depends(get_db),
) -> Page[ActivityOut]:
    from app.core.config import get_settings

    settings = get_settings()
    if settings.use_postgrest or db is None:
        from app.core.supabase_http import select as pg_select

        filters: dict[str, str] = {}
        if lead_id is not None:
            filters["lead_id"] = str(lead_id)
        if company_id is not None:
            filters["company_id"] = str(company_id)
        items = await pg_select("activities", filters=filters if filters else None, limit=size)
        total = len(items)
        return Page.build([ActivityOut.model_validate(a) for a in items], total, page, size)

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
    db: AsyncSession | None = Depends(get_db),
) -> Page[TaskOut]:
    from app.core.config import get_settings

    settings = get_settings()
    if settings.use_postgrest or db is None:
        from app.core.supabase_http import select as pg_select

        filters: dict[str, str] = {}
        if lead_id is not None:
            filters["lead_id"] = str(lead_id)
        if completed is not None:
            filters["completed"] = str(completed).lower()
        items = await pg_select("tasks", filters=filters if filters else None, limit=size)
        total = len(items)
        return Page.build([TaskOut.model_validate(t) for t in items], total, page, size)

    service = TaskService(db)
    stmt = service.build_list_query(completed=completed, lead_id=lead_id, due_before=due_before)
    items, total = await TaskRepository(db).paginate(stmt, page=page, size=size)
    return Page.build([TaskOut.model_validate(t) for t in items], total, page, size)


@tasks_router.post("", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
async def create_task(payload: TaskIn, db: AsyncSession | None = Depends(get_db)) -> TaskOut:
    from app.core.config import get_settings

    settings = get_settings()
    if settings.use_postgrest or db is None:
        from app.core.supabase_http import insert as pg_insert

        data = dict(payload.model_dump())
        data["id"] = str(uuid.uuid4())
        if data.get("lead_id"):
            data["lead_id"] = str(data["lead_id"])
        if data.get("company_id"):
            data["company_id"] = str(data["company_id"])
        result = await pg_insert("tasks", data)
        if result:
            return TaskOut.model_validate(result[0])
        raise HTTPException(status_code=500, detail="No se pudo crear la tarea")

    task = await TaskService(db).create(payload.model_dump())
    await db.commit()
    return TaskOut.model_validate(task)


@tasks_router.patch("/{task_id}", response_model=TaskOut)
async def update_task(
    task_id: uuid.UUID,
    payload: TaskUpdate,
    db: AsyncSession | None = Depends(get_db),
) -> TaskOut:
    from app.core.config import get_settings

    settings = get_settings()
    if settings.use_postgrest or db is None:
        from app.core.supabase_http import update as pg_update

        data = payload.model_dump(exclude_unset=True)
        result = await pg_update("tasks", {"id": str(task_id)}, data)
        if not result:
            raise NotFoundError.for_entity("task", task_id)
        return TaskOut.model_validate(result[0])

    task = await TaskService(db).update(task_id, payload.model_dump(exclude_unset=True))
    await db.commit()
    return TaskOut.model_validate(task)


@tasks_router.post("/{task_id}/complete", response_model=TaskOut)
async def complete_task(task_id: uuid.UUID, db: AsyncSession | None = Depends(get_db)) -> TaskOut:
    from app.core.config import get_settings

    settings = get_settings()
    if settings.use_postgrest or db is None:
        from app.core.supabase_http import update as pg_update

        result = await pg_update("tasks", {"id": str(task_id)}, {"completed": True, "completed_at": datetime.now(UTC).isoformat()})
        if not result:
            raise NotFoundError.for_entity("task", task_id)
        return TaskOut.model_validate(result[0])

    task = await TaskService(db).complete(task_id)
    await db.commit()
    return TaskOut.model_validate(task)


@tasks_router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(task_id: uuid.UUID, db: AsyncSession | None = Depends(get_db)) -> None:
    await TaskService(db).delete(task_id)
    await db.commit()
