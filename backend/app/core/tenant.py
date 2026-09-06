"""Tenant identity resolution for service contracts."""

from __future__ import annotations

import uuid
from typing import Any


def resolve_tenant_id(
    header_value: str | None,
    payload: dict[str, Any],
    *,
    enforced: bool = False,
) -> uuid.UUID | None:
    """Resolve one tenant UUID from the request header and JSON payload.

    Both channels are accepted for compatibility, but when both are present
    they must match. Strict production mode rejects a missing tenant.
    """
    header_id = _parse(header_value, "TENANT_ID_INVALID") if header_value else None
    raw_payload = payload.get("tenant_id")
    payload_id = _parse(str(raw_payload), "TENANT_ID_INVALID") if raw_payload else None
    if header_id and payload_id and header_id != payload_id:
        raise ValueError("TENANT_ID_MISMATCH")
    tenant_id = header_id or payload_id
    if enforced and tenant_id is None:
        raise ValueError("TENANT_ID_REQUIRED")
    return tenant_id


def _parse(value: str, code: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValueError(code) from exc
