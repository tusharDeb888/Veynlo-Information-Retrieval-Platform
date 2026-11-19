"""
Authentication & authorisation layer.

Supports two modes controlled by ``AUTH_MODE``:

* **firebase** – verifies tokens with the Firebase Admin SDK
  (``auth.verify_id_token(token, check_revoked=True)``).
* **local_jwt** – verifies tokens using a symmetric HMAC secret so that
  integration tests can run offline inside Docker without Firebase.

After identity verification, the user's tenant, department, role, and
document-level permissions are resolved from PostgreSQL and cached in
Redis under ``acl:{tenant_id}:{user_id}`` with a configurable TTL
(default 300 s).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

import jwt as pyjwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.database import async_session_factory
from app.models.document import DocumentPermission
from app.models.tenant import User

# ---------------------------------------------------------------------------
# Firebase initialisation (lazy, once)
# ---------------------------------------------------------------------------

_firebase_app_initialised = False


def _ensure_firebase_initialised(settings: Settings) -> None:
    global _firebase_app_initialised  # noqa: PLW0603
    if _firebase_app_initialised:
        return
    import firebase_admin  # type: ignore[import-untyped]
    from firebase_admin import credentials as fb_creds  # type: ignore[import-untyped]

    if not firebase_admin._apps:  # noqa: SLF001
        firebase_admin.initialize_app(
            fb_creds.ApplicationDefault(),
            options={"projectId": settings.FIREBASE_PROJECT_ID},
        )
    _firebase_app_initialised = True


# ---------------------------------------------------------------------------
# Redis singleton
# ---------------------------------------------------------------------------

_redis_client: Redis | None = None


async def _get_redis(settings: Settings | None = None) -> Redis:
    global _redis_client  # noqa: PLW0603
    if _redis_client is None:
        s = settings or get_settings()
        _redis_client = Redis.from_url(s.REDIS_URL, decode_responses=True)
    return _redis_client


# ---------------------------------------------------------------------------
# CurrentUser data-class
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """Immutable value-object carrying the authenticated identity."""

    user_id: uuid.UUID
    firebase_uid: str
    tenant_id: uuid.UUID
    department_id: uuid.UUID
    role: str  # 'TENANT_ADMIN' | 'DEPT_ADMIN' | 'EMPLOYEE'
    email: str
    allowed_document_ids: list[uuid.UUID] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------

_bearer_scheme = HTTPBearer(auto_error=True)


async def _verify_token(
    credentials: HTTPAuthorizationCredentials,
    settings: Settings,
) -> dict[str, Any]:
    """Return the decoded JWT payload (Firebase or local)."""
    token = credentials.credentials

    if settings.AUTH_MODE == "firebase":
        _ensure_firebase_initialised(settings)
        from firebase_admin import auth as fb_auth  # type: ignore[import-untyped]

        try:
            decoded = fb_auth.verify_id_token(
                token,
                check_revoked=True,
                app=None,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Firebase token verification failed: {exc}",
            ) from exc
        return decoded

    # local_jwt mode — symmetric HMAC-SHA256 for integration tests
    try:
        decoded = pyjwt.decode(
            token,
            key=settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            options={"verify_aud": False},
        )
    except pyjwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired.",
        ) from exc
    except pyjwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
        ) from exc
    return decoded


# ---------------------------------------------------------------------------
# ACL cache helpers
# ---------------------------------------------------------------------------


def _acl_cache_key(tenant_id: uuid.UUID, user_id: uuid.UUID) -> str:
    return f"acl:{tenant_id}:{user_id}"


async def invalidate_acl_cache(tenant_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """Remove the cached permission set for a specific user.

    Call this whenever a Departmental Admin grants, updates, or revokes
    document access.
    """
    redis = await _get_redis()
    await redis.delete(_acl_cache_key(tenant_id, user_id))
    # The UID lookup is the hot-path cache read, so both keys must be purged.
    async for key in redis.scan_iter(match="acl:uid:*", count=200):
        cached = await redis.get(key)
        if cached:
            try:
                data = json.loads(cached)
            except json.JSONDecodeError:
                continue
            if data.get("tenant_id") == str(tenant_id) and data.get("user_id") == str(user_id):
                await redis.delete(key)


async def invalidate_acl_cache_for_document(
    tenant_id: uuid.UUID,
    document_id: uuid.UUID,
    session: AsyncSession,
) -> None:
    """Invalidate ACL caches for all users who have permissions on a document.

    This catches both explicit ``allowed_user_id`` grants and broader
    department / role grants (by invalidating *all* users in the tenant
    whose department or role may match).  The simple approach is to delete
    all ACL keys for the tenant; a more surgical approach would query the
    affected user set, but the 300 s TTL makes the broad invalidation safe
    and simple.
    """
    redis = await _get_redis()
    # Scan and delete all keys matching the tenant prefix.
    async for key in redis.scan_iter(match=f"acl:{tenant_id}:*", count=200):
        await redis.delete(key)
    # The secondary UID keys intentionally have no tenant in their Redis key.
    # Inspect their values so a grant/revoke is visible on the next request.
    async for key in redis.scan_iter(match="acl:uid:*", count=200):
        cached = await redis.get(key)
        if cached:
            try:
                data = json.loads(cached)
            except json.JSONDecodeError:
                continue
            if data.get("tenant_id") == str(tenant_id):
                await redis.delete(key)


async def _resolve_and_cache_user(
    firebase_uid: str,
    settings: Settings,
) -> CurrentUser:
    """Resolve user details from PostgreSQL and cache in Redis."""
    async with async_session_factory() as session, session.begin():
        result = await session.execute(select(User).where(User.firebase_uid == firebase_uid))
        user: User | None = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User not registered on this platform.",
            )

        # Fetch document-level permissions
        perm_result = await session.execute(
            select(DocumentPermission.document_id).where(
                DocumentPermission.tenant_id == user.tenant_id,
                (
                    (DocumentPermission.allowed_user_id == user.id)
                    | (DocumentPermission.allowed_department_id == user.department_id)
                    | (DocumentPermission.allowed_role == user.role.value)
                ),
            )
        )
        allowed_doc_ids = list({row[0] for row in perm_result.fetchall()})

        current_user = CurrentUser(
            user_id=user.id,
            firebase_uid=user.firebase_uid,
            tenant_id=user.tenant_id,
            department_id=user.department_id,
            role=user.role.value,
            email=user.email,
            allowed_document_ids=allowed_doc_ids,
        )

    # Cache in Redis
    redis = await _get_redis(settings)
    cache_key = _acl_cache_key(current_user.tenant_id, current_user.user_id)
    cache_payload = {
        "user_id": str(current_user.user_id),
        "firebase_uid": current_user.firebase_uid,
        "tenant_id": str(current_user.tenant_id),
        "department_id": str(current_user.department_id),
        "role": current_user.role,
        "email": current_user.email,
        "allowed_document_ids": [str(d) for d in current_user.allowed_document_ids],
    }
    await redis.set(
        cache_key,
        json.dumps(cache_payload),
        ex=settings.ACL_CACHE_TTL,
    )
    return current_user


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> CurrentUser:
    """Authenticate the caller and return a ``CurrentUser`` with resolved
    permissions.

    1. Verify the JWT (Firebase or local HMAC).
    2. Extract ``sub`` (Firebase UID).
    3. Check Redis ACL cache; on miss, resolve from PostgreSQL and cache.
    """
    decoded = await _verify_token(credentials, settings)

    # Firebase tokens use "sub" for the UID; local_jwt tokens should too.
    firebase_uid: str | None = decoded.get("sub") or decoded.get("uid")
    if not firebase_uid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token does not contain a subject / uid claim.",
        )

    # -- Try Redis cache first ------------------------------------------------
    redis = await _get_redis(settings)
    # We need the user_id & tenant_id to form the cache key, but we don't
    # have them yet.  Use a secondary index keyed by firebase_uid.
    uid_cache_key = f"acl:uid:{firebase_uid}"
    cached_raw = await redis.get(uid_cache_key)
    if cached_raw:
        data = json.loads(cached_raw)
        return CurrentUser(
            user_id=uuid.UUID(data["user_id"]),
            firebase_uid=data["firebase_uid"],
            tenant_id=uuid.UUID(data["tenant_id"]),
            department_id=uuid.UUID(data["department_id"]),
            role=data["role"],
            email=data["email"],
            allowed_document_ids=[uuid.UUID(d) for d in data.get("allowed_document_ids", [])],
        )

    # -- Cache miss: resolve from DB ------------------------------------------
    current_user = await _resolve_and_cache_user(firebase_uid, settings)

    # Also store under firebase_uid so the next lookup is O(1)
    cache_payload = {
        "user_id": str(current_user.user_id),
        "firebase_uid": current_user.firebase_uid,
        "tenant_id": str(current_user.tenant_id),
        "department_id": str(current_user.department_id),
        "role": current_user.role,
        "email": current_user.email,
        "allowed_document_ids": [str(d) for d in current_user.allowed_document_ids],
    }
    await redis.set(
        uid_cache_key,
        json.dumps(cache_payload),
        ex=settings.ACL_CACHE_TTL,
    )
    return current_user


async def require_dept_admin(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Guard: caller must hold ``TENANT_ADMIN`` or ``DEPT_ADMIN`` role."""
    if current_user.role not in ("TENANT_ADMIN", "DEPT_ADMIN"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Departmental-admin or tenant-admin role required.",
        )
    return current_user


async def require_authenticated_employee(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Guard: any authenticated user (EMPLOYEE, DEPT_ADMIN, TENANT_ADMIN).

    This is effectively a no-op beyond ``get_current_user``, but naming it
    explicitly makes the intent of each router dependency self-documenting.
    """
    return current_user
