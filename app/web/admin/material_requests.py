"""Admin material request management web routes."""

import csv
import io
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy.orm import Session, selectinload

from app.csrf import get_csrf_token
from app.db import SessionLocal
from app.models.auth import UserCredential
from app.models.inventory import InventoryLocation
from app.models.material_request import (
    MaterialRequest,
    MaterialRequestERPSyncStatus,
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


def _parse_serial_number_form(form) -> dict[str, list[str] | str]:
    serials_by_item: dict[str, list[str] | str] = {}
    for key in form:
        if not key.startswith("serial_numbers_"):
            continue
        item_id = key.removeprefix("serial_numbers_")
        serials_by_item[item_id] = [str(value) for value in form.getlist(key)]
    return serials_by_item


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


def _export_value(value: object | None) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value).strip()


def _person_name(person: Person | None) -> str:
    if person is None:
        return ""
    name = " ".join(part for part in [person.first_name, person.last_name] if part).strip()
    return name or person.email or str(person.id)


def _normalize_material_request_status(value: str | None) -> str | None:
    normalized = (value or "").strip().lower().replace("cancelled", "canceled")
    if not normalized or normalized == "all":
        return None
    return normalized


def _material_request_status_label(status: MaterialRequestStatus | None) -> str:
    if status is None:
        return ""
    if status == MaterialRequestStatus.canceled:
        return "Cancelled"
    return status.value.title()


def _material_request_export_rows(requests: list[MaterialRequest]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for mr in requests:
        base_row = {
            "Request ID": _export_value(mr.id),
            "Request Number": _export_value(mr.number),
            "Status": _export_value(_material_request_status_label(mr.status)),
            "Priority": _export_value(mr.priority.value.title() if mr.priority else ""),
            "Requested By": _person_name(mr.requested_by),
            "Requested By ID": _export_value(mr.requested_by_person_id),
            "Approved By": _person_name(mr.approved_by),
            "Collected By": _person_name(mr.collected_by),
            "Number of Items": _export_value(len(mr.items or [])),
            "Created Date": _export_value(mr.created_at),
            "Submitted Date": _export_value(mr.submitted_at),
            "Approved Date": _export_value(mr.approved_at),
            "Rejected Date": _export_value(mr.rejected_at),
            "Fulfilled Date": _export_value(mr.fulfilled_at),
            "Ticket ID": _export_value(mr.ticket_id),
            "Ticket Number": _export_value(mr.ticket.number if mr.ticket else ""),
            "Ticket Title": _export_value(mr.ticket.title if mr.ticket else ""),
            "Project ID": _export_value(mr.project_id),
            "Project Number": _export_value(mr.project.number if mr.project else ""),
            "Project Name": _export_value(mr.project.name if mr.project else ""),
            "Work Order ID": _export_value(mr.work_order_id),
            "Work Order Title": _export_value(mr.work_order.title if mr.work_order else ""),
            "Source Warehouse": _export_value(mr.source_location.name if mr.source_location else ""),
            "Destination Warehouse": _export_value(mr.destination_location.name if mr.destination_location else ""),
            "Notes": _export_value(mr.notes),
        }
        if not mr.items:
            rows.append(
                {
                    **base_row,
                    "Line Item ID": "",
                    "Item ID": "",
                    "Item SKU": "",
                    "Item Name": "",
                    "Item Description": "",
                    "Unit": "",
                    "Quantity": "",
                    "Line Notes": "",
                    "Serial Numbers": "",
                }
            )
            continue
        for line in mr.items:
            item = line.item
            rows.append(
                {
                    **base_row,
                    "Line Item ID": _export_value(line.id),
                    "Item ID": _export_value(line.item_id),
                    "Item SKU": _export_value(item.sku if item else ""),
                    "Item Name": _export_value(item.name if item else ""),
                    "Item Description": _export_value(item.description if item else ""),
                    "Unit": _export_value(item.unit if item else ""),
                    "Quantity": _export_value(line.quantity),
                    "Line Notes": _export_value(line.notes),
                    "Serial Numbers": _export_value(line.serial_numbers),
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
    selected_status = (status or "").strip().lower() or None
    if selected_status == "all":
        selected_status = None
    selected_erp_status = (erp_status or "").strip().lower().replace("-", "_").replace(" ", "_") or None
    if selected_erp_status == "all":
        selected_erp_status = None

    selected_date_from = _resolve_date(date_from)
    selected_date_to = _resolve_date(date_to)
    if not (selected_date_from and selected_date_to):
        selected_date_from = None
        selected_date_to = None

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
    )
    return templates.TemplateResponse("admin/material_requests/index.html", context)


@router.get("/export.csv")
def material_request_export_csv(
    status: str | None = None,
    erp_status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    ticket_id: str | None = None,
    project_id: str | None = None,
    db: Session = Depends(get_db),
):
    selected_status = _normalize_material_request_status(status)
    selected_erp_status = (erp_status or "").strip().lower().replace("-", "_").replace(" ", "_") or None
    if selected_erp_status == "all":
        selected_erp_status = None

    selected_date_from = _resolve_date(date_from)
    selected_date_to = _resolve_date(date_to)
    if not (selected_date_from and selected_date_to):
        selected_date_from = None
        selected_date_to = None

    query = db.query(MaterialRequest).options(
        selectinload(MaterialRequest.items).selectinload(MaterialRequestItem.item),
        selectinload(MaterialRequest.requested_by),
        selectinload(MaterialRequest.approved_by),
        selectinload(MaterialRequest.collected_by),
        selectinload(MaterialRequest.ticket),
        selectinload(MaterialRequest.project),
        selectinload(MaterialRequest.work_order),
        selectinload(MaterialRequest.source_location),
        selectinload(MaterialRequest.destination_location),
    )
    query = query.filter(MaterialRequest.is_active.is_(True))
    if selected_status:
        try:
            query = query.filter(MaterialRequest.status == MaterialRequestStatus(selected_status))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid material request status") from exc
    if selected_erp_status:
        if selected_erp_status in {item.value for item in MaterialRequestERPSyncStatus}:
            query = query.filter(MaterialRequest.erp_sync_status == MaterialRequestERPSyncStatus(selected_erp_status))
        else:
            query = query.filter(MaterialRequest.erp_material_status == selected_erp_status)
    if ticket_id:
        query = query.filter(MaterialRequest.ticket_id == coerce_uuid(ticket_id))
    if project_id:
        query = query.filter(MaterialRequest.project_id == coerce_uuid(project_id))
    if selected_date_from and selected_date_to:
        if selected_date_from > selected_date_to:
            raise HTTPException(status_code=400, detail="From date must be before or equal to To date")
        start_dt = datetime.combine(selected_date_from, datetime.min.time(), tzinfo=UTC)
        end_dt = datetime.combine(selected_date_to, datetime.max.time(), tzinfo=UTC)
        query = query.filter(MaterialRequest.created_at >= start_dt)
        query = query.filter(MaterialRequest.created_at <= end_dt)

    requests = query.order_by(MaterialRequest.created_at.desc()).all()
    rows = _material_request_export_rows(requests)
    status_part = selected_status or "all_statuses"
    filename = f"material_requests_{status_part}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.csv"
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
    search: str | None = Query(None, max_length=120),
    db: Session = Depends(get_db),
):
    from app.services.dotmac_erp import DotMacERPError
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
            search=(search or "").strip() or None,
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
    mr = material_requests.get(db, mr_id)
    context = _base_ctx(
        request,
        db,
        mr=mr,
        warehouses=_warehouse_choices(db),
        collectors=_collector_choices(db),
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
