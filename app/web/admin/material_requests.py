"""Admin material request management web routes."""

import csv
import io
from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, selectinload

from app.csrf import get_csrf_token
from app.db import SessionLocal
from app.models.auth import UserCredential
from app.models.inventory import InventoryLocation
from app.models.material_request import (
    MaterialRequest,
    MaterialRequestItem,
    MaterialRequestPriority,
    MaterialRequestStatus,
)
from app.models.person import Person
from app.schemas.material_request import (
    MaterialRequestCreate,
    MaterialRequestItemCreate,
    MaterialRequestUpdate,
)
from app.services.audit_helpers import log_audit_event
from app.services.auth_dependencies import require_any_permission, require_permission
from app.services.common import coerce_uuid
from app.services.material_requests import (
    ResolveError,
    material_requests,
    resolve_project_id,
    resolve_ticket_id,
    resolve_warehouse_id,
)
from app.web.templates import Jinja2Templates

templates = Jinja2Templates(directory="templates")
router = APIRouter(prefix="/operations/material-requests", tags=["web-admin-material-requests"])
MATERIAL_REQUEST_STATUS_CHOICES = [
    ("draft", "Draft"),
    ("submitted", "Submitted"),
    ("issued", "Issued"),
    ("approved", "Approved"),
    ("fulfilled", "Fulfilled"),
    ("rejected", "Rejected"),
    ("canceled", "Cancelled"),
]


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
        "active_page": "material-requests",
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


def _export_query_string(
    status: str | None,
    date_from: str | None,
    date_to: str | None,
    ticket_id: str | None,
    project_id: str | None,
) -> str:
    params = {}
    if status:
        params["status"] = status
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to
    if ticket_id:
        params["ticket_id"] = ticket_id
    if project_id:
        params["project_id"] = project_id
    return urlencode(params)


def _warehouse_choices(db: Session) -> list[InventoryLocation]:
    return (
        db.query(InventoryLocation)
        .filter(InventoryLocation.is_active.is_(True))
        .order_by(InventoryLocation.name.asc())
        .all()
    )


def _collector_choices(db: Session) -> list[Person]:
    has_active_credential = (
        db.query(UserCredential.id)
        .filter(
            UserCredential.person_id == Person.id,
            UserCredential.is_active.is_(True),
        )
        .exists()
    )
    return (
        db.query(Person)
        .filter(Person.is_active.is_(True))
        .filter(has_active_credential)
        .order_by(Person.first_name.asc(), Person.last_name.asc(), Person.email.asc())
        .limit(500)
        .all()
    )


def _parse_serial_number_form(form) -> dict[str, list[str]]:
    serials_by_item: dict[str, list[str]] = {}
    for key in form:
        if not key.startswith("serial_numbers_"):
            continue
        item_id = key.removeprefix("serial_numbers_")
        serials_by_item[item_id] = [str(value) for value in form.getlist(key)]
    return serials_by_item


def _load_available_serials_from_erp_db(
    db: Session,
    *,
    item_code: str,
    warehouse_code: str,
    limit: int,
    offset: int,
) -> dict:
    """Temporary ERP DB fallback while the ERP serial API is not exposed."""
    bind = db.get_bind()
    source_url = bind.url if isinstance(bind, Engine) else bind.engine.url
    erp_url = source_url.set(database="son_erp")
    engine = create_engine(erp_url, pool_pre_ping=True)

    item_query = text(
        """
        select item_id, item_code, item_name, track_serial_numbers
        from inv.item
        where item_code = :item_code or item_name = :item_code
        order by case when item_code = :item_code then 0 else 1 end, item_name
        limit 1
        """
    )
    warehouse_query = text(
        """
        select warehouse_id, warehouse_code, warehouse_name
        from inv.warehouse
        where warehouse_code = :warehouse_code or warehouse_name = :warehouse_code
        order by case when warehouse_code = :warehouse_code then 0 else 1 end, warehouse_name
        limit 1
        """
    )
    serial_query = text(
        """
        select serial_number, status, updated_at
        from inv.inventory_serial
        where item_id = :item_id
          and warehouse_id = :warehouse_id
          and is_active is true
          and upper(status) = 'AVAILABLE'
        order by serial_number
        limit :limit_plus_one offset :offset
        """
    )

    try:
        with engine.connect() as conn:
            item = conn.execute(item_query, {"item_code": item_code}).mappings().first()
            warehouse = conn.execute(warehouse_query, {"warehouse_code": warehouse_code}).mappings().first()
            if item is None or warehouse is None:
                return {
                    "item_code": item_code,
                    "warehouse_code": warehouse_code,
                    "track_serial_numbers": bool(item["track_serial_numbers"]) if item else False,
                    "serials": [],
                    "limit": limit,
                    "offset": offset,
                    "has_more": False,
                }

            rows = (
                conn.execute(
                    serial_query,
                    {
                        "item_id": item["item_id"],
                        "warehouse_id": warehouse["warehouse_id"],
                        "limit_plus_one": limit + 1,
                        "offset": offset,
                    },
                )
                .mappings()
                .all()
            )
    finally:
        engine.dispose()

    visible_rows = rows[:limit]
    return {
        "item_code": item["item_code"],
        "warehouse_code": warehouse["warehouse_code"],
        "track_serial_numbers": bool(item["track_serial_numbers"]),
        "serials": [
            {
                "serial_number": row["serial_number"],
                "status": row["status"],
                "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
            }
            for row in visible_rows
        ],
        "limit": limit,
        "offset": offset,
        "has_more": len(rows) > limit,
    }


def _csv_response(data: list[dict[str, str]], filename: str) -> Response:
    output = io.StringIO()
    if data:
        writer = csv.DictWriter(output, fieldnames=list(data[0].keys()))
        writer.writeheader()
        writer.writerows(data)
    else:
        output.write("No data available\n")
    output.seek(0)
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _format_dt(value: datetime | None) -> str:
    if not value:
        return ""
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _enum_label(value: object | None) -> str:
    if value is None:
        return ""
    raw = str(value.value if hasattr(value, "value") else value)
    if raw == "canceled":
        return "Cancelled"
    return raw.replace("_", " ").title()


def _person_name(person: Person | None) -> str:
    if not person:
        return ""
    return person.display_name or f"{person.first_name or ''} {person.last_name or ''}".strip() or person.email or ""


def _warehouse_label(location: InventoryLocation | None) -> str:
    if not location:
        return ""
    return f"{location.name} ({location.code})" if location.code else location.name


def _ticket_label(mr: MaterialRequest) -> str:
    if not mr.ticket:
        return ""
    return str(mr.ticket.number or mr.ticket.title or mr.ticket_id or "")


def _project_label(mr: MaterialRequest) -> str:
    if not mr.project:
        return ""
    return str(mr.project.number or mr.project.code or mr.project.name or mr.project_id or "")


def _filtered_export_requests(
    db: Session,
    status: str | None,
    date_from: str | None,
    date_to: str | None,
    ticket_id: str | None,
    project_id: str | None,
) -> list[MaterialRequest]:
    selected_status = _selected_status(status)
    selected_date_from, selected_date_to = _selected_date_range(date_from, date_to)
    query = (
        db.query(MaterialRequest)
        .options(
            selectinload(MaterialRequest.items).selectinload(MaterialRequestItem.item),
            selectinload(MaterialRequest.requested_by),
            selectinload(MaterialRequest.approved_by),
            selectinload(MaterialRequest.collected_by),
            selectinload(MaterialRequest.ticket),
            selectinload(MaterialRequest.project),
            selectinload(MaterialRequest.source_location),
            selectinload(MaterialRequest.destination_location),
        )
        .filter(MaterialRequest.is_active.is_(True))
    )
    if selected_status:
        try:
            validated_status = MaterialRequestStatus(selected_status)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid material request status") from exc
        query = query.filter(MaterialRequest.status == validated_status)
    if selected_date_from and selected_date_to:
        range_start = datetime.combine(selected_date_from, time.min, tzinfo=UTC)
        range_end = datetime.combine(selected_date_to + timedelta(days=1), time.min, tzinfo=UTC)
        query = query.filter(MaterialRequest.created_at >= range_start)
        query = query.filter(MaterialRequest.created_at < range_end)
    if ticket_id:
        query = query.filter(MaterialRequest.ticket_id == coerce_uuid(ticket_id))
    if project_id:
        query = query.filter(MaterialRequest.project_id == coerce_uuid(project_id))
    return query.order_by(MaterialRequest.created_at.desc()).all()


def _material_request_export_rows(requests: list[MaterialRequest]) -> list[dict[str, str]]:
    rows = []
    for mr in requests:
        request_fields = {
            "Request ID": str(mr.number or mr.id),
            "Request UUID": str(mr.id),
            "Status": _enum_label(mr.status),
            "Priority": _enum_label(mr.priority),
            "Requested By": _person_name(mr.requested_by),
            "Approved/Issued By": _person_name(mr.approved_by),
            "Collected By": _person_name(mr.collected_by),
            "Number of Items": str(len(mr.items or [])),
            "Ticket": _ticket_label(mr),
            "Project": _project_label(mr),
            "Source Warehouse": _warehouse_label(mr.source_location),
            "Destination Warehouse": _warehouse_label(mr.destination_location),
            "ERP Material Request ID": mr.erp_material_request_id or "",
            "Request Notes": mr.notes or "",
            "Created Date": _format_dt(mr.created_at),
            "Submitted Date": _format_dt(mr.submitted_at),
            "Approved/Issued Date": _format_dt(mr.approved_at),
            "Rejected Date": _format_dt(mr.rejected_at),
            "Fulfilled Date": _format_dt(mr.fulfilled_at),
            "Updated Date": _format_dt(mr.updated_at),
        }
        if mr.items:
            for index, request_item in enumerate(mr.items, start=1):
                item = request_item.item
                rows.append(
                    {
                        **request_fields,
                        "Line Number": str(index),
                        "Item Name": item.name if item else "",
                        "Item SKU": item.sku if item else "",
                        "Item Description": item.description if item else "",
                        "Item Unit": item.unit if item else "",
                        "Quantity": str(request_item.quantity),
                        "Item Notes": request_item.notes or "",
                    }
                )
        else:
            rows.append(
                {
                    **request_fields,
                    "Line Number": "",
                    "Item Name": "",
                    "Item SKU": "",
                    "Item Description": "",
                    "Item Unit": "",
                    "Quantity": "",
                    "Item Notes": "",
                }
            )
    return rows


@router.get("", response_class=HTMLResponse)
def material_request_list(
    request: Request,
    status: str | None = None,
    erp_status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    ticket_id: str | None = None,
    project_id: str | None = None,
    db: Session = Depends(get_db),
):
    selected_status = _selected_status(status)
    selected_erp_status = (erp_status or "").strip().lower().replace("-", "_").replace(" ", "_") or None
    if selected_erp_status == "all":
        selected_erp_status = None
    selected_date_from, selected_date_to = _selected_date_range(date_from, date_to)

    items = material_requests.list(
        db,
        status=selected_status,
        erp_status=selected_erp_status,
        created_from=selected_date_from,
        created_to=selected_date_to,
        ticket_id=ticket_id,
        project_id=project_id,
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
        status_choices=MATERIAL_REQUEST_STATUS_CHOICES,
        export_query=_export_query_string(selected_status, date_from, date_to, ticket_id, project_id),
    )
    return templates.TemplateResponse("admin/material_requests/index.html", context)


@router.get("/export.csv")
def material_request_export_csv(
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    ticket_id: str | None = None,
    project_id: str | None = None,
    db: Session = Depends(get_db),
):
    requests = _filtered_export_requests(db, status, date_from, date_to, ticket_id, project_id)
    rows = _material_request_export_rows(requests)
    filename = f"material_requests_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.csv"
    return _csv_response(rows, filename)


@router.get("/new", response_class=HTMLResponse)
def material_request_new(
    request: Request,
    ticket_id: str | None = None,
    project_id: str | None = None,
    db: Session = Depends(get_db),
):
    context = _base_ctx(
        request,
        db,
        mr=None,
        ticket_id=ticket_id,
        project_id=project_id,
        priorities=[p.value for p in MaterialRequestPriority],
        warehouses=_warehouse_choices(db),
    )
    return templates.TemplateResponse("admin/material_requests/form.html", context)


@router.post("/new")
def material_request_create(
    request: Request,
    ticket_id: str | None = Form(None),
    project_id: str | None = Form(None),
    notes: str | None = Form(None),
    priority: str = Form("medium"),
    source_location_id: str | None = Form(None),
    destination_location_id: str | None = Form(None),
    item_id: list[str] = Form(default=[]),
    quantity: list[int] = Form(default=[]),
    item_notes: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
):
    from app.web.admin._auth_helpers import get_current_user

    current_user = get_current_user(request)
    person_id = current_user.get("person_id") if current_user else None

    if not person_id:
        return RedirectResponse(url="/admin/operations/material-requests", status_code=303)

    items: list[MaterialRequestItemCreate] = []
    if item_id:
        for idx, item_value in enumerate(item_id):
            if not item_value:
                continue
            qty = quantity[idx] if idx < len(quantity) else 1
            note = item_notes[idx] if idx < len(item_notes) else None
            if qty is None or qty < 1:
                continue
            items.append(
                MaterialRequestItemCreate(
                    item_id=coerce_uuid(item_value),
                    quantity=qty,
                    notes=note,
                )
            )

    try:
        resolved_ticket_id = resolve_ticket_id(db, ticket_id)
        resolved_project_id = resolve_project_id(db, project_id)
        resolved_source = resolve_warehouse_id(source_location_id)
        resolved_dest = resolve_warehouse_id(destination_location_id)
    except ResolveError as exc:
        context = _base_ctx(
            request,
            db,
            mr=None,
            ticket_id=ticket_id,
            project_id=project_id,
            priorities=[p.value for p in MaterialRequestPriority],
            warehouses=_warehouse_choices(db),
            error=str(exc),
        )
        return templates.TemplateResponse("admin/material_requests/form.html", context)

    if not (resolved_ticket_id or resolved_project_id):
        context = _base_ctx(
            request,
            db,
            mr=None,
            ticket_id=ticket_id,
            project_id=project_id,
            priorities=[p.value for p in MaterialRequestPriority],
            warehouses=_warehouse_choices(db),
            error="Link a ticket or project before creating a material request.",
        )
        return templates.TemplateResponse("admin/material_requests/form.html", context, status_code=400)

    payload = MaterialRequestCreate(
        ticket_id=resolved_ticket_id,
        project_id=resolved_project_id,
        requested_by_person_id=coerce_uuid(person_id),
        priority=MaterialRequestPriority(priority) if priority else MaterialRequestPriority.medium,
        notes=notes,
        source_location_id=resolved_source,
        destination_location_id=resolved_dest,
        items=items or None,
    )
    try:
        mr = material_requests.create(db, payload)
    except HTTPException as exc:
        context = _base_ctx(
            request,
            db,
            mr=None,
            ticket_id=ticket_id,
            project_id=project_id,
            priorities=[p.value for p in MaterialRequestPriority],
            warehouses=_warehouse_choices(db),
            error=str(exc.detail or "Unable to create material request."),
        )
        return templates.TemplateResponse("admin/material_requests/form.html", context, status_code=400)

    log_audit_event(
        db=db,
        request=request,
        action="create",
        entity_type="material_request",
        entity_id=str(mr.id),
        actor_id=str(person_id),
        metadata={"number": mr.number, "status": mr.status.value},
    )

    return RedirectResponse(url=f"/admin/operations/material-requests/{mr.id}", status_code=303)


@router.get(
    "/serials/available",
    response_class=JSONResponse,
    dependencies=[Depends(require_any_permission("inventory:read", "inventory:write"))],
)
def material_request_available_serials(
    item_code: str = Query(..., min_length=1),
    warehouse_code: str = Query(..., min_length=1),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    from app.services.dotmac_erp import DotMacERPError, DotMacERPNotFoundError
    from app.services.dotmac_erp.material_request_sync import dotmac_erp_material_request_sync

    try:
        sync_service = dotmac_erp_material_request_sync(db)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        data = sync_service.client.list_available_serials(
            item_code=item_code,
            warehouse_code=warehouse_code,
            limit=limit,
            offset=offset,
        )
        return JSONResponse(data)
    except DotMacERPNotFoundError:
        data = _load_available_serials_from_erp_db(
            db,
            item_code=item_code,
            warehouse_code=warehouse_code,
            limit=limit,
            offset=offset,
        )
        return JSONResponse(data)
    except DotMacERPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        sync_service.close()


@router.get("/{mr_id}/edit", response_class=HTMLResponse)
def material_request_edit(request: Request, mr_id: str, db: Session = Depends(get_db)):
    mr = material_requests.get(db, mr_id)
    if mr.status and mr.status.value != "draft":
        return RedirectResponse(url=f"/admin/operations/material-requests/{mr_id}", status_code=303)
    context = _base_ctx(
        request,
        db,
        mr=mr,
        priorities=[p.value for p in MaterialRequestPriority],
        ticket_id=mr.ticket.number if mr.ticket and mr.ticket.number else mr.ticket_id,
        project_id=mr.project.number if mr.project and mr.project.number else mr.project_id,
        warehouses=_warehouse_choices(db),
    )
    return templates.TemplateResponse("admin/material_requests/form.html", context)


@router.post("/{mr_id}/edit")
def material_request_update(
    request: Request,
    mr_id: str,
    ticket_id: str | None = Form(None),
    project_id: str | None = Form(None),
    notes: str | None = Form(None),
    priority: str | None = Form(None),
    source_location_id: str | None = Form(None),
    destination_location_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    from app.web.admin._auth_helpers import get_current_user

    current_user = get_current_user(request)
    mr = material_requests.get(db, mr_id)
    if mr.status and mr.status.value != "draft":
        return RedirectResponse(url=f"/admin/operations/material-requests/{mr_id}", status_code=303)

    payload = MaterialRequestUpdate(
        ticket_id=resolve_ticket_id(db, ticket_id),
        project_id=resolve_project_id(db, project_id),
        priority=MaterialRequestPriority(priority) if priority else None,
        notes=notes,
        source_location_id=resolve_warehouse_id(source_location_id),
        destination_location_id=resolve_warehouse_id(destination_location_id),
    )
    material_requests.update(db, mr_id, payload)

    log_audit_event(
        db=db,
        request=request,
        action="update",
        entity_type="material_request",
        entity_id=mr_id,
        actor_id=str(current_user.get("person_id")) if current_user else None,
        metadata={"status": mr.status.value if mr.status else "draft"},
    )

    return RedirectResponse(url=f"/admin/operations/material-requests/{mr_id}", status_code=303)


@router.get("/{mr_id}", response_class=HTMLResponse)
def material_request_detail(request: Request, mr_id: str, db: Session = Depends(get_db)):
    from app.models.inventory import InventoryItem

    mr = material_requests.get(db, mr_id)
    inventory_items = (
        db.query(InventoryItem).filter(InventoryItem.is_active.is_(True)).order_by(InventoryItem.name).all()
    )
    context = _base_ctx(
        request,
        db,
        mr=mr,
        warehouses=_warehouse_choices(db),
        collectors=_collector_choices(db),
        inventory_items=inventory_items,
    )
    return templates.TemplateResponse("admin/material_requests/detail.html", context)


@router.post("/{mr_id}/submit")
def material_request_submit(request: Request, mr_id: str, db: Session = Depends(get_db)):
    from app.web.admin._auth_helpers import get_current_user

    current_user = get_current_user(request)
    material_requests.submit(db, mr_id)

    log_audit_event(
        db=db,
        request=request,
        action="submit",
        entity_type="material_request",
        entity_id=mr_id,
        actor_id=str(current_user.get("person_id")) if current_user else None,
    )

    return RedirectResponse(url=f"/admin/operations/material-requests/{mr_id}", status_code=303)


@router.post("/{mr_id}/approve", dependencies=[Depends(require_permission("inventory:write"))])
async def material_request_approve(
    request: Request,
    mr_id: str,
    source_location_id: str | None = Form(None),
    destination_location_id: str | None = Form(None),
    collected_by_person_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    from app.web.admin._auth_helpers import get_current_user

    current_user = get_current_user(request)
    person_id = current_user.get("person_id") if current_user else None
    form = await request.form()

    material_requests.approve(
        db,
        mr_id,
        str(person_id) if person_id else "",
        source_location_id=source_location_id,
        destination_location_id=destination_location_id,
        collected_by_person_id=collected_by_person_id,
        serial_numbers_by_item=_parse_serial_number_form(form),
    )

    log_audit_event(
        db=db,
        request=request,
        action="approve",
        entity_type="material_request",
        entity_id=mr_id,
        actor_id=str(person_id) if person_id else None,
        metadata={"collected_by_person_id": collected_by_person_id} if collected_by_person_id else None,
    )

    return RedirectResponse(url=f"/admin/operations/material-requests/{mr_id}", status_code=303)


@router.post("/{mr_id}/retry-erp-sync", dependencies=[Depends(require_permission("inventory:write"))])
def material_request_retry_erp_sync(request: Request, mr_id: str, db: Session = Depends(get_db)):
    from app.web.admin._auth_helpers import get_current_user

    current_user = get_current_user(request)
    mr = material_requests.retry_erp_sync(db, mr_id)

    log_audit_event(
        db=db,
        request=request,
        action="retry_erp_sync",
        entity_type="material_request",
        entity_id=mr_id,
        actor_id=str(current_user.get("person_id")) if current_user else None,
        metadata={
            "erp_sync_status": mr.erp_sync_status.value if mr.erp_sync_status else None,
            "erp_material_status": mr.erp_material_status,
        },
    )

    return RedirectResponse(url=f"/admin/operations/material-requests/{mr_id}", status_code=303)


@router.post("/{mr_id}/refresh-erp-status", dependencies=[Depends(require_permission("inventory:write"))])
def material_request_refresh_erp_status(request: Request, mr_id: str, db: Session = Depends(get_db)):
    from app.models.material_request import MaterialRequestERPSyncStatus
    from app.tasks.integrations import refresh_material_request_erp_status
    from app.web.admin._auth_helpers import get_current_user

    current_user = get_current_user(request)
    mr = material_requests.get(db, mr_id)
    try:
        mr.erp_sync_status = MaterialRequestERPSyncStatus.pending
        mr.erp_sync_error = None
        db.commit()
        db.refresh(mr)
        refresh_material_request_erp_status.delay(str(mr.id))
    except Exception as exc:
        mr.erp_sync_status = MaterialRequestERPSyncStatus.failed
        mr.erp_sync_error = f"ERP status refresh enqueue failed: {exc}"[:500]
        db.commit()

    log_audit_event(
        db=db,
        request=request,
        action="refresh_erp_status",
        entity_type="material_request",
        entity_id=mr_id,
        actor_id=str(current_user.get("person_id")) if current_user else None,
        metadata={
            "erp_sync_status": mr.erp_sync_status.value if mr.erp_sync_status else None,
            "erp_material_status": mr.erp_material_status,
        },
    )

    return RedirectResponse(url=f"/admin/operations/material-requests/{mr_id}", status_code=303)


@router.post("/{mr_id}/reject")
def material_request_reject(request: Request, mr_id: str, reason: str = Form(""), db: Session = Depends(get_db)):
    from app.web.admin._auth_helpers import get_current_user

    current_user = get_current_user(request)
    person_id = current_user.get("person_id") if current_user else None

    material_requests.reject(db, mr_id, str(person_id) if person_id else "", reason)

    log_audit_event(
        db=db,
        request=request,
        action="reject",
        entity_type="material_request",
        entity_id=mr_id,
        actor_id=str(person_id) if person_id else None,
        metadata={"reason": reason},
    )

    return RedirectResponse(url=f"/admin/operations/material-requests/{mr_id}", status_code=303)


@router.post("/{mr_id}/cancel")
def material_request_cancel(request: Request, mr_id: str, db: Session = Depends(get_db)):
    from app.web.admin._auth_helpers import get_current_user

    current_user = get_current_user(request)
    material_requests.cancel(db, mr_id)

    log_audit_event(
        db=db,
        request=request,
        action="cancel",
        entity_type="material_request",
        entity_id=mr_id,
        actor_id=str(current_user.get("person_id")) if current_user else None,
    )

    return RedirectResponse(url=f"/admin/operations/material-requests/{mr_id}", status_code=303)


@router.post("/{mr_id}/items/add")
def material_request_add_item(
    request: Request,
    mr_id: str,
    item_id: str = Form(...),
    quantity: int = Form(...),
    notes: str | None = Form(None),
    db: Session = Depends(get_db),
):
    payload = MaterialRequestItemCreate(item_id=coerce_uuid(item_id), quantity=quantity, notes=notes)
    material_requests.add_item(db, mr_id, payload)
    return RedirectResponse(url=f"/admin/operations/material-requests/{mr_id}", status_code=303)


@router.post("/{mr_id}/items/{item_id}/remove")
def material_request_remove_item(request: Request, mr_id: str, item_id: str, db: Session = Depends(get_db)):
    material_requests.remove_item(db, mr_id, item_id)
    return RedirectResponse(url=f"/admin/operations/material-requests/{mr_id}", status_code=303)
