"""Admin routes for CRM-local expense requests.

Historical ERP fields remain visible as audit evidence for requests processed
before the direct application integration was retired.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.csrf import get_csrf_token
from app.db import SessionLocal
from app.services.audit_helpers import log_audit_event
from app.services.auth_dependencies import require_any_permission, require_permission
from app.services.expense_requests import expense_requests
from app.web.templates import Jinja2Templates

templates = Jinja2Templates(directory="templates")
router = APIRouter(prefix="/operations/expense-requests", tags=["web-admin-expense-requests"])

EXPENSE_REQUEST_STATUS_CHOICES = [
    ("draft", "Draft"),
    ("submitted", "Submitted"),
    ("approved", "Approved"),
    ("paid", "Paid"),
    ("rejected", "Rejected"),
    ("canceled", "Cancelled"),
]

_READ = [Depends(require_any_permission("operations:expense_request:read", "operations:expense_request:write"))]
_WRITE = [Depends(require_permission("operations:expense_request:write"))]


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _base_ctx(request: Request, db: Session, **kwargs) -> dict:
    from app.web.admin._auth_helpers import get_current_user, get_sidebar_stats

    return {
        "request": request,
        "active_page": "expense-requests",
        "current_user": get_current_user(request),
        "sidebar_stats": get_sidebar_stats(db),
        "csrf_token": get_csrf_token(request),
        **kwargs,
    }


def _resolve_date(value: str | None) -> date | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _selected_status(value: str | None) -> str | None:
    selected_status = (value or "").strip().lower() or None
    if selected_status == "all":
        return None
    if selected_status == "cancelled":
        return "canceled"
    return selected_status


def _selected_date_range(date_from: str | None, date_to: str | None) -> tuple[date | None, date | None]:
    selected_date_from = _resolve_date(date_from)
    selected_date_to = _resolve_date(date_to)
    if not (selected_date_from and selected_date_to):
        return None, None
    if selected_date_from > selected_date_to:
        raise HTTPException(status_code=400, detail="From date must be before or equal to To date")
    return selected_date_from, selected_date_to


@router.get("", response_class=HTMLResponse, dependencies=_READ)
def expense_request_list(
    request: Request,
    status: str | None = None,
    erp_status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    ticket_id: str | None = None,
    project_id: str | None = None,
    work_order_id: str | None = None,
    db: Session = Depends(get_db),
):
    selected_status = _selected_status(status)
    selected_erp_status = (erp_status or "").strip().lower().replace("-", "_").replace(" ", "_") or None
    if selected_erp_status == "all":
        selected_erp_status = None
    selected_date_from, selected_date_to = _selected_date_range(date_from, date_to)

    items = expense_requests.list(
        db,
        status=selected_status,
        erp_status=selected_erp_status,
        created_from=selected_date_from,
        created_to=selected_date_to,
        ticket_id=ticket_id,
        project_id=project_id,
        work_order_id=work_order_id,
        order_by="created_at",
        order_dir="desc",
        limit=100,
        offset=0,
    )

    context = _base_ctx(
        request,
        db,
        items=items,
        filter_status=selected_status or "",
        filter_erp_status=selected_erp_status or "",
        filter_date_from=date_from or "",
        filter_date_to=date_to or "",
        status_choices=EXPENSE_REQUEST_STATUS_CHOICES,
    )
    return templates.TemplateResponse("admin/expense_requests/index.html", context)


@router.get("/{er_id}", response_class=HTMLResponse, dependencies=_READ)
def expense_request_detail(request: Request, er_id: str, db: Session = Depends(get_db)):
    er = expense_requests.get(db, er_id)
    context = _base_ctx(request, db, er=er)
    return templates.TemplateResponse("admin/expense_requests/detail.html", context)


@router.post("/{er_id}/cancel", dependencies=_WRITE)
def expense_request_cancel(request: Request, er_id: str, db: Session = Depends(get_db)):
    from app.web.admin._auth_helpers import get_current_user

    current_user = get_current_user(request)
    expense_requests.cancel(db, er_id)

    log_audit_event(
        db=db,
        request=request,
        action="cancel",
        entity_type="expense_request",
        entity_id=er_id,
        actor_id=str(current_user.get("person_id")) if current_user else None,
    )

    return RedirectResponse(url=f"/admin/operations/expense-requests/{er_id}", status_code=303)
