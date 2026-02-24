from datetime import UTC, datetime

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.db import get_db as _get_db
from app.models.auth import ApiKey, SessionStatus
from app.models.auth import Session as AuthSession
from app.models.rbac import Permission, PersonPermission, PersonRole, Role, RolePermission
from app.services.auth import hash_api_key
from app.services.auth_cache import get_cached_session, set_cached_session
from app.services.auth_flow import _load_rbac_claims, decode_access_token, hash_session_token


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _is_jwt(token: str) -> bool:
    return token.count(".") == 2


def _has_audit_scope(payload: dict) -> bool:
    scopes: set[str] = set()
    scope_value = payload.get("scope")
    if isinstance(scope_value, str):
        scopes.update(scope_value.split())
    scopes_value = payload.get("scopes")
    if isinstance(scopes_value, list):
        scopes.update(str(item) for item in scopes_value)
    role_value = payload.get("role")
    roles_value = payload.get("roles")
    roles: set[str] = set()
    if isinstance(role_value, str):
        roles.add(role_value)
    if isinstance(roles_value, list):
        roles.update(str(item) for item in roles_value)
    return "audit:read" in scopes or "audit:*" in scopes or "admin" in roles or "auditor" in roles


def require_audit_auth(
    request: Request = None,  # type: ignore[assignment]
    authorization: str | None = Header(default=None),
    x_session_token: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    db: Session = Depends(_get_db),
):
    token = _extract_bearer_token(authorization) or x_session_token
    now = datetime.now(UTC)
    if token:
        if _is_jwt(token):
            payload = decode_access_token(db, token)
            if not _has_audit_scope(payload):
                raise HTTPException(status_code=403, detail="Insufficient scope")
            session_id = payload.get("session_id")
            if session_id:
                session = db.get(AuthSession, session_id)
                if not session:
                    raise HTTPException(status_code=401, detail="Invalid session")
                if session.status != SessionStatus.active or session.revoked_at:
                    raise HTTPException(status_code=401, detail="Invalid session")
                expires_at = _as_utc(session.expires_at)
                if expires_at and expires_at <= now:
                    raise HTTPException(status_code=401, detail="Session expired")
            actor_id = str(payload.get("sub"))
            if request is not None:
                request.state.actor_id = actor_id
                request.state.actor_type = "user"
            return {"actor_type": "user", "actor_id": actor_id}
        session = (
            db.query(AuthSession)
            .filter(AuthSession.token_hash == hash_session_token(token))
            .filter(AuthSession.status == SessionStatus.active)
            .filter(AuthSession.revoked_at.is_(None))
            .filter(AuthSession.expires_at > now)
            .first()
        )
        if session:
            if request is not None:
                request.state.actor_id = str(session.person_id)
                request.state.actor_type = "user"
            return {"actor_type": "user", "actor_id": str(session.person_id)}
    if x_api_key:
        api_key = (
            db.query(ApiKey)
            .filter(ApiKey.key_hash == hash_api_key(x_api_key))
            .filter(ApiKey.is_active.is_(True))
            .filter(ApiKey.revoked_at.is_(None))
            .filter((ApiKey.expires_at.is_(None)) | (ApiKey.expires_at > now))
            .first()
        )
        if api_key:
            if request is not None:
                request.state.actor_id = str(api_key.id)
                request.state.actor_type = "api_key"
            return {"actor_type": "api_key", "actor_id": str(api_key.id)}
    raise HTTPException(status_code=401, detail="Unauthorized")


def require_user_auth(
    request: Request = None,  # type: ignore[assignment]
    authorization: str | None = Header(default=None),
    db: Session = Depends(_get_db),
):
    token = _extract_bearer_token(authorization)
    if not token and request is not None:
        token = request.cookies.get("session_token")
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    payload = decode_access_token(db, token)
    person_id = payload.get("sub")
    session_id = payload.get("session_id")
    if not person_id or not session_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    roles_claim = payload.get("roles")
    scopes_claim = payload.get("scopes")
    roles_from_claim = list(roles_claim) if isinstance(roles_claim, (list, tuple, set)) else []
    scopes_from_claim = list(scopes_claim) if isinstance(scopes_claim, (list, tuple, set)) else []

    now = datetime.now(UTC)

    # Check cache first
    cached = get_cached_session(str(session_id))
    if cached:
        cached_person_id = cached.get("person_id")
        cached_expires_at = cached.get("expires_at")
        if cached_person_id == str(person_id):
            if cached_expires_at:
                expires_dt = datetime.fromisoformat(cached_expires_at)
                if expires_dt <= now:
                    raise HTTPException(status_code=401, detail="Unauthorized")
            roles = cached.get("roles")
            scopes = cached.get("scopes")
            # Only reload if roles/scopes were never cached (None), not if empty lists
            if roles is None or scopes is None:
                roles, scopes = _load_rbac_claims(db, str(person_id))
                set_cached_session(
                    str(session_id),
                    {
                        "person_id": str(person_id),
                        "roles": roles,
                        "scopes": scopes,
                        "expires_at": cached_expires_at,
                    },
                )
            roles = roles or []
            scopes = scopes or []
            actor_id = str(person_id)
            if request is not None:
                request.state.actor_id = actor_id
                request.state.actor_type = "user"
            return {
                "person_id": str(person_id),
                "session_id": str(session_id),
                "roles": roles,
                "scopes": scopes,
            }

    # Cache miss - query database
    session = (
        db.query(AuthSession)
        .filter(AuthSession.id == session_id)
        .filter(AuthSession.person_id == person_id)
        .filter(AuthSession.status == SessionStatus.active)
        .filter(AuthSession.revoked_at.is_(None))
        .filter(AuthSession.expires_at > now)
        .first()
    )
    if not session:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if roles_from_claim or scopes_from_claim:
        roles = roles_from_claim
        scopes = scopes_from_claim
    else:
        roles, scopes = _load_rbac_claims(db, str(person_id))

    # Cache the session for future requests
    set_cached_session(
        str(session_id),
        {
            "person_id": str(person_id),
            "roles": roles,
            "scopes": scopes,
            "expires_at": session.expires_at.isoformat() if session.expires_at else None,
        },
    )

    actor_id = str(person_id)
    if request is not None:
        request.state.actor_id = actor_id
        request.state.actor_type = "user"
    return {
        "person_id": str(person_id),
        "session_id": str(session_id),
        "roles": roles,
        "scopes": scopes,
    }


def require_role(role_name: str):
    def _require_role(
        auth=Depends(require_user_auth),
        db: Session = Depends(_get_db),
    ):
        person_id = auth["person_id"]
        roles = set(auth.get("roles") or [])
        if role_name in roles:
            return auth
        role = db.query(Role).filter(Role.name == role_name).filter(Role.is_active.is_(True)).first()
        if not role:
            raise HTTPException(status_code=403, detail="Role not found")
        link = (
            db.query(PersonRole).filter(PersonRole.person_id == person_id).filter(PersonRole.role_id == role.id).first()
        )
        if not link:
            raise HTTPException(status_code=403, detail="Forbidden")
        return auth

    return _require_role


def _expand_permission_keys(permission_key: str) -> list[str]:
    """
    Expand a permission key to include hierarchical matches.

    For granular permissions like 'billing:invoice:create', this returns:
    - 'billing:invoice:create' (exact match)
    - 'billing:write' (domain:write implies domain:*:create/update/delete)
    - 'billing:read' (if the action is 'read')

    This allows both granular and broad permissions to work together.
    """
    keys = [permission_key]
    parts = permission_key.split(":")

    if len(parts) >= 2:
        domain = parts[0]
        # For 3-part permissions like billing:invoice:create
        if len(parts) == 3:
            action = parts[2]
            # billing:invoice:read -> also accept billing:read
            if action == "read":
                keys.append(f"{domain}:read")
            # billing:invoice:create/update/delete -> also accept billing:write
            elif action in ("create", "update", "delete", "write"):
                keys.append(f"{domain}:write")
        # For 2-part permissions like customer:read
        elif len(parts) == 2:
            action = parts[1]
            # customer:read is already a broad permission
            # customer:create/update/delete -> also accept customer:write (if it exists)
            if action in ("create", "update", "delete"):
                keys.append(f"{domain}:write")

    return keys


def require_permission(permission_key: str):
    def _require_permission(
        auth=Depends(require_user_auth),
        db: Session = Depends(_get_db),
    ):
        person_id = auth["person_id"]
        roles = set(auth.get("roles") or [])
        if "admin" in roles:
            return auth

        # Expand the permission key to include hierarchical matches
        possible_keys = _expand_permission_keys(permission_key)

        # Check if permission is granted via JWT scopes
        scopes = set(auth.get("scopes") or [])
        if scopes & set(possible_keys):
            return auth

        # Find all matching permissions (exact or hierarchical)
        permissions = (
            db.query(Permission).filter(Permission.key.in_(possible_keys)).filter(Permission.is_active.is_(True)).all()
        )
        if not permissions:
            raise HTTPException(status_code=403, detail="Permission not found")

        permission_ids = [p.id for p in permissions]

        # Check if user has any of the matching permissions via roles
        has_role_permission = (
            db.query(RolePermission)
            .join(Role, RolePermission.role_id == Role.id)
            .join(PersonRole, PersonRole.role_id == Role.id)
            .filter(PersonRole.person_id == person_id)
            .filter(RolePermission.permission_id.in_(permission_ids))
            .filter(Role.is_active.is_(True))
            .first()
        )

        # Check if user has any direct permission grants
        has_direct_permission = (
            db.query(PersonPermission)
            .filter(PersonPermission.person_id == person_id)
            .filter(PersonPermission.permission_id.in_(permission_ids))
            .first()
        )

        if not has_role_permission and not has_direct_permission:
            raise HTTPException(status_code=403, detail="Forbidden")
        return auth

    return _require_permission


def require_any_permission(*permission_keys: str):
    """Require user to have at least one of the specified permissions."""

    def _require_any_permission(
        auth=Depends(require_user_auth),
        db: Session = Depends(_get_db),
    ):
        person_id = auth["person_id"]
        roles = set(auth.get("roles") or [])
        if "admin" in roles:
            return auth

        # Expand all permission keys
        all_possible_keys = set()
        for key in permission_keys:
            all_possible_keys.update(_expand_permission_keys(key))

        permissions = (
            db.query(Permission)
            .filter(Permission.key.in_(all_possible_keys))
            .filter(Permission.is_active.is_(True))
            .all()
        )
        if not permissions:
            raise HTTPException(status_code=403, detail="Permission not found")

        permission_ids = [p.id for p in permissions]

        # Check if user has any of the matching permissions via roles
        has_role_permission = (
            db.query(RolePermission)
            .join(Role, RolePermission.role_id == Role.id)
            .join(PersonRole, PersonRole.role_id == Role.id)
            .filter(PersonRole.person_id == person_id)
            .filter(RolePermission.permission_id.in_(permission_ids))
            .filter(Role.is_active.is_(True))
            .first()
        )

        # Check if user has any direct permission grants
        has_direct_permission = (
            db.query(PersonPermission)
            .filter(PersonPermission.person_id == person_id)
            .filter(PersonPermission.permission_id.in_(permission_ids))
            .first()
        )

        if not has_role_permission and not has_direct_permission:
            raise HTTPException(status_code=403, detail="Forbidden")
        return auth

    return _require_any_permission
