"""CRM inbox social comment reply routes."""

import contextlib
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.logging import get_logger

router = APIRouter(tags=["web-admin-crm"])
logger = get_logger(__name__)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _get_current_roles(request: Request) -> list[str]:
    auth = getattr(request.state, "auth", None)
    if isinstance(auth, dict):
        roles = auth.get("roles") or []
        if isinstance(roles, list):
            return [str(role) for role in roles]
    return []


def _get_current_scopes(request: Request) -> list[str]:
    auth = getattr(request.state, "auth", None)
    if isinstance(auth, dict):
        scopes = auth.get("scopes") or []
        if isinstance(scopes, list):
            return [str(scope) for scope in scopes]
    return []


@router.post("/inbox/comments/{comment_id}/reply", response_class=HTMLResponse)
async def reply_to_social_comment(
    request: Request,
    comment_id: str,
    message: str = Form(...),
    db: Session = Depends(get_db),
):
    next_url = request.query_params.get("next")
    if not next_url or not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/admin/crm/inbox"

    referer_query: dict[str, str] = {}
    referer = request.headers.get("referer")
    if referer:
        with contextlib.suppress(Exception):
            referer_query = dict(parse_qsl(urlparse(referer).query, keep_blank_values=True))

    target_id = request.query_params.get("target_id") or referer_query.get("target_id")
    search = request.query_params.get("search") or referer_query.get("search")

    def _build_reply_redirect(
        *,
        reply_sent: bool = False,
        reply_error: bool = False,
        reply_error_detail: str | None = None,
    ) -> str:
        parsed_next = urlparse(next_url)
        params = dict(parse_qsl(parsed_next.query, keep_blank_values=True))

        # Preserve main inbox context and remove legacy comments channel forcing.
        params.pop("channel", None)
        params["comment_id"] = comment_id
        if target_id:
            params["target_id"] = target_id
        if search:
            params["search"] = search

        if reply_sent:
            params["reply_sent"] = "1"
        if reply_error:
            params["reply_error"] = "1"
        if reply_error_detail:
            params["reply_error_detail"] = reply_error_detail

        return urlunparse(parsed_next._replace(query=urlencode(params, doseq=True)))

    from app.services.crm.inbox.comment_replies import reply_to_social_comment
    from app.web.admin import get_current_user

    current_user = get_current_user(request)
    actor_id = (current_user or {}).get("person_id")
    result = await reply_to_social_comment(
        db,
        comment_id=comment_id,
        message=message,
        actor_id=actor_id,
        roles=_get_current_roles(request),
        scopes=_get_current_scopes(request),
    )
    if result.kind == "forbidden":
        return RedirectResponse(
            url=_build_reply_redirect(
                reply_error=True,
                reply_error_detail="Forbidden",
            ),
            status_code=303,
        )
    if result.kind == "not_found":
        return RedirectResponse(
            url=_build_reply_redirect(reply_error=True),
            status_code=303,
        )
    if result.kind == "error":
        logger.exception(
            "social_comment_reply_failed comment_id=%s error=%s",
            comment_id,
            result.error_detail,
        )
        return RedirectResponse(
            url=_build_reply_redirect(
                reply_error=True,
                reply_error_detail=result.error_detail or "Reply failed",
            ),
            status_code=303,
        )

    return RedirectResponse(
        url=_build_reply_redirect(reply_sent=True),
        status_code=303,
    )


@router.get("/inbox/comments/{comment_id}/reply", response_class=HTMLResponse)
def reply_to_social_comment_get(
    request: Request,
    comment_id: str,
    next: str | None = None,
):
    _ = request
    next_url = next or "/admin/crm/inbox"
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/admin/crm/inbox"
    detail = quote("Session expired. Please re-submit your reply.", safe="")
    return RedirectResponse(
        url=f"{next_url}?channel=comments&comment_id={comment_id}&reply_error=1&reply_error_detail={detail}",
        status_code=303,
    )
