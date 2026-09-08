"""Lista de no contactar (§14 del diseño).

Se puede añadir y quitar a mano, pero también se llena sola: cada baja y cada
rebote duro entran aquí desde el tracking y desde el sincronizador de entrada.
"""

from __future__ import annotations

import csv
import io
import uuid

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import ValidationError
from app.schemas.common import Page
from app.schemas.email import SuppressionIn, SuppressionOut
from app.services.mail_admin_svc import SuppressionRepository, SuppressionService

router = APIRouter()

_MAX_CSV_BYTES = 2 * 1024 * 1024


@router.get("", response_model=Page[SuppressionOut])
async def list_entries(
    q: str | None = None,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession | None = Depends(get_db),
) -> Page[SuppressionOut]:
    service = SuppressionService(db)
    items, total = await SuppressionRepository(db).paginate(
        service.build_list_query(q=q), page=page, size=size
    )
    return Page.build([SuppressionOut.model_validate(e) for e in items], total, page, size)


@router.post("", response_model=SuppressionOut, status_code=status.HTTP_201_CREATED)
async def add_entry(
    payload: SuppressionIn,
    db: AsyncSession | None = Depends(get_db),
) -> SuppressionOut:
    entry = await SuppressionService(db).add(
        email=payload.email,
        domain=payload.domain,
        reason=payload.reason,
        notes=payload.notes,
    )
    await db.commit()
    return SuppressionOut.model_validate(entry)


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_entry(entry_id: uuid.UUID, db: AsyncSession | None = Depends(get_db)) -> None:
    """Quita una entrada.

    Existe porque un rebote temporal mal clasificado no debe condenar a un
    contacto para siempre. Las bajas voluntarias no deberían quitarse nunca.
    """
    await SuppressionService(db).remove(entry_id)
    await db.commit()


@router.post("/import", response_model=dict, status_code=status.HTTP_201_CREATED)
async def import_csv(
    file: UploadFile = File(...),
    db: AsyncSession | None = Depends(get_db),
) -> dict[str, int]:
    """Importa un CSV con columnas `email` y/o `domain`.

    Se usa para cargar de golpe clientes actuales, competencia y bajas
    heredadas de otra herramienta.
    """
    raw = await file.read()
    if len(raw) > _MAX_CSV_BYTES:
        raise ValidationError("El archivo supera los 2 MB.", code="CSV_TOO_LARGE")

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValidationError("El archivo no está en UTF-8.", code="CSV_ENCODING") from exc

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or not {"email", "domain"} & set(reader.fieldnames):
        raise ValidationError(
            "El CSV necesita una columna 'email' o 'domain'.",
            code="CSV_MISSING_COLUMNS",
            details={"found": reader.fieldnames or []},
        )

    service = SuppressionService(db)
    imported = skipped = 0
    for row in reader:
        email = (row.get("email") or "").strip() or None
        domain = (row.get("domain") or "").strip() or None
        if not email and not domain:
            skipped += 1
            continue
        await service.add(
            email=email,
            domain=domain,
            reason=(row.get("reason") or "manual").strip() or "manual",
            notes=(row.get("notes") or "").strip() or None,
        )
        imported += 1

    await db.commit()
    return {"imported": imported, "skipped": skipped}
