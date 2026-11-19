from __future__ import annotations

import uuid
from types import SimpleNamespace

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.api.v1.query import _build_acl_filter
from app.core.config import Settings
from app.core.security import _acl_cache_key, _verify_token


@pytest.mark.asyncio
async def test_local_jwt_verification_returns_subject() -> None:
    settings = Settings(AUTH_MODE="local_jwt", JWT_SECRET_KEY="test-secret-key-with-32-plus-characters")
    token = jwt.encode({"sub": "local-user"}, settings.JWT_SECRET_KEY, algorithm="HS256")
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    payload = await _verify_token(credentials, settings)

    assert payload["sub"] == "local-user"


@pytest.mark.asyncio
async def test_invalid_local_jwt_is_rejected() -> None:
    settings = Settings(AUTH_MODE="local_jwt", JWT_SECRET_KEY="test-secret-key-with-32-plus-characters")
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="not-a-jwt")

    with pytest.raises(HTTPException) as exc_info:
        await _verify_token(credentials, settings)

    assert exc_info.value.status_code == 401


def test_acl_filter_scopes_tenant_and_all_supported_grants() -> None:
    user = SimpleNamespace(
        tenant_id=uuid.uuid4(),
        department_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        role="EMPLOYEE",
        allowed_document_ids=[],
    )

    acl_filter = _build_acl_filter(user)

    assert acl_filter.must[0].key == "tenant_id"
    assert acl_filter.must[0].match.value == str(user.tenant_id)
    assert acl_filter.must[1].key == "file_id"
    assert acl_filter.must[1].match.any == []
    assert acl_filter.min_should is not None
    assert {item.key for item in acl_filter.min_should.conditions} == {
        "allowed_departments",
        "allowed_roles",
        "allowed_users",
    }
    allowed_user = next(item for item in acl_filter.min_should.conditions if item.key == "allowed_users")
    assert allowed_user.match.value == str(user.user_id)


def test_acl_cache_key_is_tenant_scoped() -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()

    assert _acl_cache_key(tenant_id, user_id) == f"acl:{tenant_id}:{user_id}"
