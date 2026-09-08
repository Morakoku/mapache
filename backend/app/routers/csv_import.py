"""CSV import router for companies."""
from __future__ import annotations

import csv
import hashlib
import io
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.company import Company
from app.models.lead import Lead
from app.models.service import Service
from app.models.pipeline import PipelineStage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/csv", tags=["csv-import"])

COLUMN_MAP = {
    "name": "name",
    "company_name": "name",
    "description": "description",
    "category": "category",
    "city": "city",
    "address": "address",
    "phone": "phone",
    "email": "email",
    "website": "website",
}


def _normalize(row: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for raw_key, value in row.items():
        key = raw_key.strip().lower()
        if key in COLUMN_MAP and value is not None:
            stripped = value.strip()
            if stripped:
                out[COLUMN_MAP[key]] = stripped
    return out


def _dedupe_key(row: dict[str, Any]) -> str:
    """Build a dedupe key (max 40 chars for the column)."""
    raw = f"{row.get('name','')}|{row.get('phone','')}|{row.get('email','')}"
    return hashlib.md5(raw.encode()).hexdigest()[:32]


@router.post("/import", summary="Import companies from CSV")
async def import_csv(
    file: UploadFile = File(...),
    db: AsyncSession | None = Depends(get_db),
) -> dict[str, Any]:
    """Import companies from CSV, create leads, and score them."""
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a CSV")

    content = await file.read()
    text = content.decode("utf-8-sig")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV is empty")

    # Get default service and first pipeline stage
    service = await db.execute(select(Service).limit(1))
    service = service.scalar_one_or_none()
    if not service:
        raise HTTPException(status_code=400, detail="No service found. Create one first.")

    stage = await db.execute(
        select(PipelineStage).order_by(PipelineStage.created_at).limit(1)
    )
    stage = stage.scalar_one_or_none()
    if not stage:
        raise HTTPException(status_code=400, detail="No pipeline stage found.")

    seen_keys: set[str] = set()
    created = 0
    duplicates = 0
    errors: list[dict[str, str]] = []
    now = datetime.now(timezone.utc)

    for idx, raw_row in enumerate(reader, start=2):
        row = _normalize(raw_row)
        name = row.get("name", "")

        if not name:
            errors.append({"row": str(idx), "error": "missing name"})
            continue

        dk = _dedupe_key(row)
        if dk in seen_keys:
            duplicates += 1
            continue
        seen_keys.add(dk)

        existing = await db.execute(
            select(Company).where(Company.name == name).limit(1)
        )
        if existing.scalar_one_or_none():
            duplicates += 1
            continue

        company = Company(
            id=uuid4(),
            name=name,
            description=row.get("description"),
            category=row.get("category"),
            categories=[row["category"]] if row.get("category") else [],
            city=row.get("city"),
            address=row.get("address"),
            phone=row.get("phone"),
            email=row.get("email"),
            website=row.get("website"),
            dedupe_key=dk,
            first_extracted_at=now,
            last_extracted_at=now,
        )
        db.add(company)
        await db.flush()  # get company.id

        # Create lead for this company
        lead = Lead(
            id=uuid4(),
            company_id=company.id,
            service_id=service.id,
            stage_id=stage.id,
        )
        db.add(lead)
        created += 1

    await db.commit()

    return {
        "success": True,
        "created": created,
        "duplicates": duplicates,
        "errors": errors,
        "total_rows": created + duplicates + len(errors),
    }
