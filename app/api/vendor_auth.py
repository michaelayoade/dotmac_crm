from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.vendor_auth import (
    VendorLoginRequest,
    VendorMeResponse,
    VendorMfaVerifyRequest,
    VendorRefreshRequest,
    VendorTokenResponse,
)
from app.services.vendor_auth_tokens import require_vendor_token, vendor_auth_tokens

router = APIRouter(prefix="/vendor/auth", tags=["vendor-auth"])


@router.post("/login", response_model=VendorTokenResponse, status_code=status.HTTP_200_OK)
def vendor_login(payload: VendorLoginRequest, request: Request, db: Session = Depends(get_db)):
    return vendor_auth_tokens.login(db, payload.username, payload.password, request)


@router.post("/mfa", response_model=VendorTokenResponse, status_code=status.HTTP_200_OK)
def vendor_mfa_verify(payload: VendorMfaVerifyRequest, request: Request, db: Session = Depends(get_db)):
    return vendor_auth_tokens.mfa_verify(db, payload.mfa_token, payload.code, request)


@router.post("/refresh", response_model=VendorTokenResponse, status_code=status.HTTP_200_OK)
def vendor_refresh(payload: VendorRefreshRequest, request: Request, db: Session = Depends(get_db)):
    return vendor_auth_tokens.refresh(db, payload.refresh_token, request)


@router.get("/me", response_model=VendorMeResponse, status_code=status.HTTP_200_OK)
def vendor_me(context=Depends(require_vendor_token)):
    vendor_user = context["vendor_user"]
    person = vendor_user.person
    vendor = vendor_user.vendor
    display_name = person.display_name or f"{person.first_name} {person.last_name}".strip()
    return VendorMeResponse(
        person_id=str(person.id),
        vendor_id=context["vendor_id"],
        vendor_user_id=context["vendor_user_id"],
        name=display_name,
        email=person.email,
        vendor_name=vendor.name,
        vendor_role=context.get("vendor_role"),
    )
