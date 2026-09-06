import uuid

import pytest

from app.core.tenant import resolve_tenant_id


def test_resolves_tenant_from_header():
    tenant_id = uuid.uuid4()
    assert resolve_tenant_id(str(tenant_id), {}) == tenant_id


def test_rejects_missing_tenant_when_enforced():
    with pytest.raises(ValueError, match="TENANT_ID_REQUIRED"):
        resolve_tenant_id(None, {}, enforced=True)


def test_rejects_mismatched_header_and_payload():
    header_id = uuid.uuid4()
    payload_id = uuid.uuid4()
    with pytest.raises(ValueError, match="TENANT_ID_MISMATCH"):
        resolve_tenant_id(str(header_id), {"tenant_id": str(payload_id)}, enforced=True)
