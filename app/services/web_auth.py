"""Service helpers for web auth routes."""

import contextlib
import logging
from urllib.parse import quote, urlparse, urlunparse

from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.services import auth_flow as auth_flow_service
from app.services.auth_flow import AuthFlow
from app.services.email import send_password_reset_email

logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory="templates")


_UNSAFE_REFRESH_SEGMENTS = {
    "/email-connector",
    "/whatsapp-connector",
    "/convert",
    "/poll",
    "/reset",
    "/delete",
    "/activate",
    "/teams",
    "/agents",
    "/agent-teams",
}


def _sanitize_refresh_next(next_url: str | None, fallback: str) -> str:
    if not next_url or not next_url.startswith("/"):
        return fallback
    parsed = urlparse(next_url)
    for segment in _UNSAFE_REFRESH_SEGMENTS:
        if segment in parsed.path:
            if parsed.path.startswith("/admin/crm/inbox"):
                return urlunparse(("", "", "/admin/crm/inbox", "", parsed.query, ""))
            if parsed.path.startswith("/admin/crm/contacts"):
                return urlunparse(("", "", "/admin/crm/contacts", "", "", ""))
            return fallback
    return next_url


def _safe_next(next_url: str | None, fallback: str = "/admin/dashboard") -> str:
    return _sanitize_refresh_next(next_url, fallback)


def _set_refresh_cookie(response, db: Session, refresh_token: str):
    settings = AuthFlow.refresh_cookie_settings(db)
    response.set_cookie(
        key=settings["key"],
        value=refresh_token,
        httponly=settings["httponly"],
        secure=settings["secure"],
        samesite=settings["samesite"],
        domain=settings["domain"],
        path=settings["path"],
        max_age=settings["max_age"],
    )


def login_page(request: Request, error: str | None = None, next_url: str | None = None):
    return templates.TemplateResponse(
        "auth/login.html",
        {"request": request, "error": error, "next": next_url or ""},
    )


def login_submit(
    request: Request,
    db: Session,
    username: str,
    password: str,
    remember: bool,
    next_url: str,
):
    redirect_url = _safe_next(next_url)
    try:
        result = auth_flow_service.auth_flow.login(
            db=db,
            username=username,
            password=password,
            request=request,
            provider=None,
        )
        if result.get("requires_mfa"):
            mfa_url = f"/auth/mfa?next={next_url}" if next_url else "/auth/mfa"
            response = RedirectResponse(url=mfa_url, status_code=303)
            response.set_cookie(
                key="mfa_pending",
                value=result.get("mfa_token", ""),
                httponly=True,
                secure=False,  # Set to True in production with HTTPS
                samesite="lax",
                max_age=300,
            )
            return response

        response = RedirectResponse(url=redirect_url, status_code=303)
        max_age = 30 * 24 * 60 * 60 if remember else None
        response.set_cookie(
            key="session_token",
            value=result.get("access_token", ""),
            httponly=True,
            secure=False,  # Set to True in production with HTTPS
            samesite="lax",
            max_age=max_age,
        )
        refresh_token = result.get("refresh_token")
        if refresh_token:
            _set_refresh_cookie(response, db, refresh_token)
        logger.info(
            "web_login_redirect_success username=%s remember=%s next=%s target=%s ip=%s",
            username,
            remember,
            next_url or "",
            redirect_url,
            request.client.host if request.client else None,
        )
        return response
    except Exception as exc:
        error_msg = "Invalid credentials"
        if hasattr(exc, "detail"):
            detail = exc.detail
            if isinstance(detail, dict) and detail.get("code") == "PASSWORD_RESET_REQUIRED":
                reset = auth_flow_service.request_password_reset(db=db, email=username)
                if reset and reset.get("token"):
                    return RedirectResponse(
                        url=f"/auth/reset-password?token={reset['token']}",
                        status_code=303,
                    )
            error_msg = detail if isinstance(detail, str) else str(detail)
        elif str(exc):
            error_msg = str(exc)
        logger.warning(
            "web_login_submit_failed username=%s next=%s ip=%s error=%s",
            username,
            next_url or "",
            request.client.host if request.client else None,
            error_msg,
        )
        return templates.TemplateResponse(
            "auth/login.html",
            {"request": request, "error": error_msg, "next": next_url},
            status_code=401,
        )


def mfa_page(request: Request, next_url: str | None = None, error: str | None = None):
    mfa_pending = request.cookies.get("mfa_pending")
    if not mfa_pending:
        return RedirectResponse(url="/auth/login", status_code=303)
    return templates.TemplateResponse(
        "auth/mfa.html",
        {"request": request, "error": error, "next": next_url or ""},
    )


def mfa_submit(
    request: Request,
    db: Session,
    code: str,
    next_url: str,
):
    mfa_token = request.cookies.get("mfa_pending")
    if not mfa_token:
        return RedirectResponse(url="/auth/login", status_code=303)
    redirect_url = _safe_next(next_url)
    try:
        result = auth_flow_service.auth_flow.mfa_verify(
            db=db,
            mfa_token=mfa_token,
            code=code,
            request=request,
        )
        response = RedirectResponse(url=redirect_url, status_code=303)
        response.delete_cookie("mfa_pending")
        response.set_cookie(
            key="session_token",
            value=result.get("access_token", ""),
            httponly=True,
            secure=False,  # Set to True in production with HTTPS
            samesite="lax",
        )
        refresh_token = result.get("refresh_token")
        if refresh_token:
            _set_refresh_cookie(response, db, refresh_token)
        return response
    except Exception:
        return templates.TemplateResponse(
            "auth/mfa.html",
            {"request": request, "error": "Invalid verification code", "next": next_url},
            status_code=401,
        )


def forgot_password_page(request: Request, success: bool = False):
    return templates.TemplateResponse(
        "auth/forgot-password.html",
        {"request": request, "success": success},
    )


def forgot_password_submit(request: Request, db: Session, email: str):
    try:
        reset_payload = auth_flow_service.request_password_reset(db=db, email=email)
        if reset_payload and reset_payload.get("token"):
            send_password_reset_email(
                db=db,
                to_email=reset_payload.get("email", email),
                reset_token=reset_payload["token"],
                person_name=reset_payload.get("person_name"),
            )
    except Exception:
        logger.debug("Password reset email send failed.", exc_info=True)
    return templates.TemplateResponse(
        "auth/forgot-password.html",
        {"request": request, "success": True},
    )


def reset_password_page(request: Request, token: str, error: str | None = None):
    return templates.TemplateResponse(
        "auth/reset-password.html",
        {"request": request, "token": token, "error": error},
    )


def reset_password_submit(
    request: Request,
    db: Session,
    token: str,
    password: str,
    password_confirm: str,
):
    if password != password_confirm:
        return templates.TemplateResponse(
            "auth/reset-password.html",
            {"request": request, "token": token, "error": "Passwords do not match"},
            status_code=400,
        )
    try:
        auth_flow_service.reset_password(db=db, token=token, new_password=password)
        return RedirectResponse(url="/auth/login?reset=success", status_code=303)
    except Exception:
        return templates.TemplateResponse(
            "auth/reset-password.html",
            {"request": request, "token": token, "error": "Invalid or expired reset link"},
            status_code=400,
        )


def refresh(request: Request, db: Session, next_url: str | None = None):
    redirect_url = _safe_next(next_url)
    refresh_token = AuthFlow.resolve_refresh_token(request, None, db)
    if not refresh_token:
        login_url = "/auth/login"
        if next_url and next_url.startswith("/"):
            login_url = f"/auth/login?next={quote(next_url)}"
        logger.warning(
            "web_refresh_redirect_login reason=missing_refresh_cookie next=%s ip=%s",
            next_url or "",
            request.client.host if request.client else None,
        )
        return RedirectResponse(url=login_url, status_code=303)
    try:
        result = auth_flow_service.auth_flow.refresh(db, refresh_token, request)
    except Exception as exc:
        login_url = "/auth/login"
        if next_url and next_url.startswith("/"):
            login_url = f"/auth/login?next={quote(next_url)}"
        logger.warning(
            "web_refresh_redirect_login reason=refresh_failed next=%s ip=%s error=%s",
            next_url or "",
            request.client.host if request.client else None,
            str(exc),
        )
        return RedirectResponse(url=login_url, status_code=303)

    response = RedirectResponse(url=redirect_url, status_code=303)
    response.set_cookie(
        key="session_token",
        value=result.get("access_token", ""),
        httponly=True,
        secure=False,  # Set to True in production with HTTPS
        samesite="lax",
    )
    refresh_token = result.get("refresh_token")
    if refresh_token:
        _set_refresh_cookie(response, db, refresh_token)
    logger.info(
        "web_refresh_redirect_success next=%s target=%s ip=%s",
        next_url or "",
        redirect_url,
        request.client.host if request.client else None,
    )
    return response


def logout(request: Request, db: Session):
    refresh_token = AuthFlow.resolve_refresh_token(request, None, db)
    if refresh_token:
        with contextlib.suppress(Exception):
            auth_flow_service.auth_flow.logout(db, refresh_token)
    response = RedirectResponse(url="/auth/login", status_code=303)
    response.delete_cookie("session_token")
    response.delete_cookie("mfa_pending")
    settings = AuthFlow.refresh_cookie_settings(db)
    response.delete_cookie(
        key=settings["key"],
        domain=settings["domain"],
        path=settings["path"],
    )
    return response
