"""Bearer-token auth for vendor users (mobile field app).

The vendor web portal uses a JWT-in-cookie session; mobile clients need the
same access/refresh bearer-token flow staff use. This service reuses the
existing AuthFlow (credentials, TOTP MFA, refresh rotation, the
person-enabled checks) and layers the vendor membership checks on top.

A token issued here is a normal AuthFlow token — what makes it a "vendor
token" is that ``require_vendor_token`` resolves an active VendorUser for the
caller on every request. Vendor people typically hold no staff roles or
permissions, so staff endpoints reject them through the normal RBAC checks.
"""

from __future__ import annotations

import contextlib
import logging

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.vendor import Vendor, VendorUser
from app.services.auth_dependencies import require_user_auth
from app.services.auth_flow import AuthFlow, decode_access_token
from app.services.common import coerce_uuid
from app.services.vendor_portal import _person_is_active, _vendor_is_active, get_vendor_user

logger = logging.getLogger(__name__)


def _vendor_context_or_none(db: Session, person_id: str) -> dict | None:
    vendor_user = get_vendor_user(db, person_id)
    if not vendor_user:
        return None
    if not _person_is_active(vendor_user.person if vendor_user.person else None):
        return None
    vendor = db.get(Vendor, vendor_user.vendor_id)
    if not _vendor_is_active(vendor):
        return None
    return {
        "vendor_user_id": str(vendor_user.id),
        "vendor_id": str(vendor_user.vendor_id),
        "vendor_role": vendor_user.role,
    }


def _revoke_quietly(db: Session, refresh_token: str | None) -> None:
    if not refresh_token:
        return
    with contextlib.suppress(HTTPException):
        AuthFlow.logout(db, refresh_token)


class VendorAuthTokens:
    @staticmethod
    def login(db: Session, username: str, password: str, request: Request) -> dict:
        result = AuthFlow.login(db, username, password, request, None)
        if result.get("mfa_required"):
            # Vendor membership is verified after MFA completes.
            return {"mfa_required": True, "mfa_token": result["mfa_token"]}
        return VendorAuthTokens._with_vendor_context(db, result)

    @staticmethod
    def mfa_verify(db: Session, mfa_token: str, code: str, request: Request) -> dict:
        result = AuthFlow.mfa_verify(db, mfa_token, code, request)
        return VendorAuthTokens._with_vendor_context(db, result)

    @staticmethod
    def refresh(db: Session, refresh_token: str, request: Request) -> dict:
        result = AuthFlow.refresh(db, refresh_token, request)
        payload = decode_access_token(db, result["access_token"])
        context = _vendor_context_or_none(db, str(payload.get("sub")))
        if context is None:
            _revoke_quietly(db, result.get("refresh_token") or refresh_token)
            raise HTTPException(status_code=401, detail="Vendor access revoked")
        return {**result, **context}

    @staticmethod
    def _with_vendor_context(db: Session, tokens: dict) -> dict:
        payload = decode_access_token(db, tokens["access_token"])
        person_id = str(payload.get("sub"))
        context = _vendor_context_or_none(db, person_id)
        if context is None:
            # Credentials are valid but this person is not an active vendor
            # user — do not leave a usable session behind.
            _revoke_quietly(db, tokens.get("refresh_token"))
            logger.warning("vendor_token_login_rejected reason=no_vendor_context person_id=%s", person_id)
            raise HTTPException(status_code=403, detail="Vendor access required")
        return {**tokens, **context}


vendor_auth_tokens = VendorAuthTokens()


def require_vendor_token(
    auth: dict = Depends(require_user_auth),
    db: Session = Depends(get_db),
) -> dict:
    """Resolve the calling vendor user from a bearer token.

    Validates on every request (not from token claims) so revoking the
    VendorUser, the Vendor, or the Person takes effect immediately.
    """
    context = _vendor_context_or_none(db, auth["person_id"])
    if context is None:
        raise HTTPException(status_code=403, detail="Vendor access required")
    vendor_user = db.get(VendorUser, coerce_uuid(context["vendor_user_id"]))
    return {**auth, **context, "vendor_user": vendor_user}
