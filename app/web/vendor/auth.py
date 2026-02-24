from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.services import web_vendor_auth as web_vendor_auth_service

router = APIRouter(prefix="/vendor/auth", tags=["web-vendor-auth"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/login", response_class=HTMLResponse)
def vendor_login_page(request: Request, error: str | None = None):
    return web_vendor_auth_service.vendor_login_page(request, error)


@router.post("/login", response_class=HTMLResponse)
async def vendor_login_submit(
    request: Request,
    db: Session = Depends(get_db),
):
    username, password, remember = await web_vendor_auth_service.parse_vendor_login_payload(request)
    return web_vendor_auth_service.vendor_login_submit(request, db, username, password, remember)


@router.get("/mfa", response_class=HTMLResponse)
def vendor_mfa_page(request: Request, error: str | None = None):
    return web_vendor_auth_service.vendor_mfa_page(request, error)


@router.post("/mfa", response_class=HTMLResponse)
def vendor_mfa_submit(
    request: Request,
    code: str = Form(...),
    db: Session = Depends(get_db),
):
    return web_vendor_auth_service.vendor_mfa_submit(request, db, code)


@router.get("/logout")
def vendor_logout(request: Request):
    return web_vendor_auth_service.vendor_logout(request)


@router.get("/refresh")
def vendor_refresh(request: Request):
    return web_vendor_auth_service.vendor_refresh(request)


@router.get("/forgot-password", response_class=HTMLResponse)
def vendor_forgot_password_page(request: Request, success: bool = False):
    return web_vendor_auth_service.vendor_forgot_password_page(request, success)


@router.post("/forgot-password", response_class=HTMLResponse)
def vendor_forgot_password_submit(
    request: Request,
    email: str = Form(...),
    db: Session = Depends(get_db),
):
    return web_vendor_auth_service.vendor_forgot_password_submit(request, db, email)


@router.get("/reset-password", response_class=HTMLResponse)
def vendor_reset_password_page(request: Request, token: str, error: str | None = None):
    return web_vendor_auth_service.vendor_reset_password_page(request, token, error)


@router.post("/reset-password", response_class=HTMLResponse)
async def vendor_reset_password_submit(
    request: Request,
    db: Session = Depends(get_db),
):
    token, password, password_confirm = await web_vendor_auth_service.parse_vendor_reset_payload(request)
    return web_vendor_auth_service.vendor_reset_password_submit(request, db, token, password, password_confirm)
