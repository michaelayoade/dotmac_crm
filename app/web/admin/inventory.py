"""Admin inventory management web routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.services import inventory as inventory_service
from app.services import audit as audit_service
from app.services.audit_helpers import build_changes_metadata, extract_changes, format_changes, log_audit_event
from app.models.person import Person
from app.schemas.inventory import (
    InventoryItemCreate,
    InventoryItemUpdate,
    InventoryLocationCreate,
    InventoryLocationUpdate,
)

templates = Jinja2Templates(directory="templates")
router = APIRouter(prefix="/inventory", tags=["web-admin-inventory"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("", response_class=HTMLResponse)
def inventory_index(request: Request, tab: str = "items", db: Session = Depends(get_db)):
    from app.web.admin import get_sidebar_stats, get_current_user

    # Get inventory data
    items = inventory_service.inventory_items.list(
        db=db, is_active=None, order_by="created_at", order_dir="desc", limit=100, offset=0
    )
    locations = inventory_service.inventory_locations.list(
        db=db, is_active=None, order_by="created_at", order_dir="desc", limit=100, offset=0
    )
    stocks = inventory_service.inventory_stocks.list(
        db=db, item_id=None, location_id=None, is_active=None,
        order_by="created_at", order_dir="desc", limit=100, offset=0
    )

    # Calculate totals
    total_on_hand = sum(s.quantity_on_hand for s in stocks)
    total_reserved = sum(s.reserved_quantity for s in stocks)

    context = {
        "request": request,
        "active_page": "inventory",
        "current_user": get_current_user(request),
        "sidebar_stats": get_sidebar_stats(db),
        "active_tab": tab,
        "items": items,
        "locations": locations,
        "stocks": stocks,
        "total_on_hand": total_on_hand,
        "total_reserved": total_reserved,
    }
    return templates.TemplateResponse("admin/inventory/index.html", context)


@router.get("/items/new", response_class=HTMLResponse)
def inventory_item_new(request: Request, db: Session = Depends(get_db)):
    from app.web.admin import get_sidebar_stats, get_current_user

    context = {
        "request": request,
        "active_page": "inventory",
        "current_user": get_current_user(request),
        "sidebar_stats": get_sidebar_stats(db),
        "item": None,
        "action_url": "/admin/inventory/items/new",
        "error": None,
    }
    return templates.TemplateResponse("admin/inventory/item_form.html", context)


@router.post("/items/new", response_class=HTMLResponse)
def inventory_item_create(
    request: Request,
    name: str = Form(...),
    sku: str = Form(None),
    unit: str = Form(None),
    description: str = Form(None),
    is_active: str = Form(None),
    db: Session = Depends(get_db),
):
    from app.web.admin import get_sidebar_stats, get_current_user

    try:
        payload = InventoryItemCreate(
            name=name,
            sku=sku or None,
            unit=unit or None,
            description=description or None,
            is_active=is_active == "true",
        )
        item = inventory_service.inventory_items.create(db=db, payload=payload)
        current_user = get_current_user(request)
        log_audit_event(
            db=db,
            request=request,
            action="create",
            entity_type="inventory_item",
            entity_id=str(item.id),
            actor_id=str(current_user.get("person_id")) if current_user else None,
            metadata={"name": item.name, "sku": item.sku},
        )
        return RedirectResponse(url="/admin/inventory", status_code=303)
    except Exception as e:
        context = {
            "request": request,
            "active_page": "inventory",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
            "item": None,
            "action_url": "/admin/inventory/items/new",
            "error": str(e),
        }
        return templates.TemplateResponse("admin/inventory/item_form.html", context)


@router.get("/items/{item_id}", response_class=HTMLResponse)
def inventory_item_detail(request: Request, item_id: str, db: Session = Depends(get_db)):
    from app.web.admin import get_sidebar_stats, get_current_user

    item = inventory_service.inventory_items.get(db=db, item_id=item_id)
    stocks = inventory_service.inventory_stocks.list(
        db=db, item_id=item_id, location_id=None, is_active=None,
        order_by="created_at", order_dir="desc", limit=100, offset=0
    )

    total_on_hand = sum(s.quantity_on_hand for s in stocks)
    total_reserved = sum(s.reserved_quantity for s in stocks)

    audit_events = audit_service.audit_events.list(
        db=db,
        actor_id=None,
        actor_type=None,
        action=None,
        entity_type="inventory_item",
        entity_id=str(item_id),
        request_id=None,
        is_success=None,
        status_code=None,
        is_active=None,
        order_by="occurred_at",
        order_dir="desc",
        limit=10,
        offset=0,
    )
    actor_ids = {str(event.actor_id) for event in audit_events if getattr(event, "actor_id", None)}
    people = {}
    if actor_ids:
        people = {
            str(person.id): person
            for person in db.query(Person).filter(Person.id.in_(actor_ids)).all()
        }
    activities = []
    for event in audit_events:
        actor = people.get(str(event.actor_id)) if getattr(event, "actor_id", None) else None
        actor_name = f"{actor.first_name} {actor.last_name}".strip() if actor else "System"
        metadata = getattr(event, "metadata_", None) or {}
        changes = extract_changes(metadata, getattr(event, "action", None))
        change_summary = format_changes(changes, max_items=2)
        activities.append(
            {
                "title": (event.action or "Activity").replace("_", " ").title(),
                "description": f"{actor_name}" + (f" · {change_summary}" if change_summary else ""),
                "occurred_at": event.occurred_at,
            }
        )

    context = {
        "request": request,
        "active_page": "inventory",
        "current_user": get_current_user(request),
        "sidebar_stats": get_sidebar_stats(db),
        "item": item,
        "stocks": stocks,
        "total_on_hand": total_on_hand,
        "total_reserved": total_reserved,
        "activities": activities,
    }
    return templates.TemplateResponse("admin/inventory/item_detail.html", context)


@router.get("/items/{item_id}/edit", response_class=HTMLResponse)
def inventory_item_edit(request: Request, item_id: str, db: Session = Depends(get_db)):
    from app.web.admin import get_sidebar_stats, get_current_user

    item = inventory_service.inventory_items.get(db=db, item_id=item_id)

    context = {
        "request": request,
        "active_page": "inventory",
        "current_user": get_current_user(request),
        "sidebar_stats": get_sidebar_stats(db),
        "item": item,
        "action_url": f"/admin/inventory/items/{item_id}/edit",
        "error": None,
    }
    return templates.TemplateResponse("admin/inventory/item_form.html", context)


@router.post("/items/{item_id}/edit", response_class=HTMLResponse)
def inventory_item_update(
    request: Request,
    item_id: str,
    name: str = Form(...),
    sku: str = Form(None),
    unit: str = Form(None),
    description: str = Form(None),
    is_active: str = Form(None),
    db: Session = Depends(get_db),
):
    from app.web.admin import get_sidebar_stats, get_current_user

    try:
        before = inventory_service.inventory_items.get(db=db, item_id=item_id)
        payload = InventoryItemUpdate(
            name=name,
            sku=sku or None,
            unit=unit or None,
            description=description or None,
            is_active=is_active == "true",
        )
        inventory_service.inventory_items.update(db=db, item_id=item_id, payload=payload)
        after = inventory_service.inventory_items.get(db=db, item_id=item_id)
        metadata_payload = build_changes_metadata(before, after)
        current_user = get_current_user(request)
        log_audit_event(
            db=db,
            request=request,
            action="update",
            entity_type="inventory_item",
            entity_id=str(item_id),
            actor_id=str(current_user.get("person_id")) if current_user else None,
            metadata=metadata_payload,
        )
        return RedirectResponse(url=f"/admin/inventory/items/{item_id}", status_code=303)
    except Exception as e:
        item = inventory_service.inventory_items.get(db=db, item_id=item_id)
        context = {
            "request": request,
            "active_page": "inventory",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
            "item": item,
            "action_url": f"/admin/inventory/items/{item_id}/edit",
            "error": str(e),
        }
        return templates.TemplateResponse("admin/inventory/item_form.html", context)


@router.get("/locations/new", response_class=HTMLResponse)
def inventory_location_new(request: Request, db: Session = Depends(get_db)):
    from app.web.admin import get_sidebar_stats, get_current_user

    context = {
        "request": request,
        "active_page": "inventory",
        "current_user": get_current_user(request),
        "sidebar_stats": get_sidebar_stats(db),
        "location": None,
        "action_url": "/admin/inventory/locations/new",
        "error": None,
    }
    return templates.TemplateResponse("admin/inventory/location_form.html", context)


@router.post("/locations/new", response_class=HTMLResponse)
def inventory_location_create(
    request: Request,
    name: str = Form(...),
    code: str = Form(None),
    address_id: str = Form(None),
    is_active: str = Form(None),
    db: Session = Depends(get_db),
):
    from app.web.admin import get_sidebar_stats, get_current_user

    try:
        payload = InventoryLocationCreate(
            name=name,
            code=code or None,
            address_id=UUID(address_id) if address_id else None,
            is_active=is_active == "true",
        )
        location = inventory_service.inventory_locations.create(db=db, payload=payload)
        current_user = get_current_user(request)
        log_audit_event(
            db=db,
            request=request,
            action="create",
            entity_type="inventory_location",
            entity_id=str(location.id),
            actor_id=str(current_user.get("person_id")) if current_user else None,
            metadata={"name": location.name, "code": location.code},
        )
        return RedirectResponse(url="/admin/inventory?tab=locations", status_code=303)
    except Exception as e:
        context = {
            "request": request,
            "active_page": "inventory",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
            "location": None,
            "action_url": "/admin/inventory/locations/new",
            "error": str(e),
        }
        return templates.TemplateResponse("admin/inventory/location_form.html", context)


@router.get("/locations/{location_id}", response_class=HTMLResponse)
def inventory_location_detail(
    request: Request, location_id: str, db: Session = Depends(get_db)
):
    from app.web.admin import get_sidebar_stats, get_current_user

    location = inventory_service.inventory_locations.get(db=db, location_id=location_id)
    stocks = inventory_service.inventory_stocks.list(
        db=db,
        item_id=None,
        location_id=location_id,
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=100,
        offset=0,
    )

    total_on_hand = sum(s.quantity_on_hand for s in stocks)
    total_reserved = sum(s.reserved_quantity for s in stocks)

    audit_events = audit_service.audit_events.list(
        db=db,
        actor_id=None,
        actor_type=None,
        action=None,
        entity_type="inventory_location",
        entity_id=str(location_id),
        request_id=None,
        is_success=None,
        status_code=None,
        is_active=None,
        order_by="occurred_at",
        order_dir="desc",
        limit=10,
        offset=0,
    )
    actor_ids = {str(event.actor_id) for event in audit_events if getattr(event, "actor_id", None)}
    people = {}
    if actor_ids:
        people = {
            str(person.id): person
            for person in db.query(Person).filter(Person.id.in_(actor_ids)).all()
        }
    activities = []
    for event in audit_events:
        actor = people.get(str(event.actor_id)) if getattr(event, "actor_id", None) else None
        actor_name = f"{actor.first_name} {actor.last_name}".strip() if actor else "System"
        metadata = getattr(event, "metadata_", None) or {}
        changes = extract_changes(metadata, getattr(event, "action", None))
        change_summary = format_changes(changes, max_items=2)
        activities.append(
            {
                "title": (event.action or "Activity").replace("_", " ").title(),
                "description": f"{actor_name}" + (f" · {change_summary}" if change_summary else ""),
                "occurred_at": event.occurred_at,
            }
        )

    context = {
        "request": request,
        "active_page": "inventory",
        "current_user": get_current_user(request),
        "sidebar_stats": get_sidebar_stats(db),
        "location": location,
        "stocks": stocks,
        "total_on_hand": total_on_hand,
        "total_reserved": total_reserved,
        "activities": activities,
    }
    return templates.TemplateResponse("admin/inventory/location_detail.html", context)


@router.get("/locations/{location_id}/edit", response_class=HTMLResponse)
def inventory_location_edit(
    request: Request, location_id: str, db: Session = Depends(get_db)
):
    from app.web.admin import get_sidebar_stats, get_current_user

    location = inventory_service.inventory_locations.get(db=db, location_id=location_id)

    context = {
        "request": request,
        "active_page": "inventory",
        "current_user": get_current_user(request),
        "sidebar_stats": get_sidebar_stats(db),
        "location": location,
        "action_url": f"/admin/inventory/locations/{location_id}/edit",
        "error": None,
    }
    return templates.TemplateResponse("admin/inventory/location_form.html", context)


@router.post("/locations/{location_id}/edit", response_class=HTMLResponse)
def inventory_location_update(
    request: Request,
    location_id: str,
    name: str = Form(...),
    code: str = Form(None),
    address_id: str = Form(None),
    is_active: str = Form(None),
    db: Session = Depends(get_db),
):
    from app.web.admin import get_sidebar_stats, get_current_user

    try:
        before = inventory_service.inventory_locations.get(db=db, location_id=location_id)
        payload = InventoryLocationUpdate(
            name=name,
            code=code or None,
            address_id=UUID(address_id) if address_id else None,
            is_active=is_active == "true",
        )
        inventory_service.inventory_locations.update(
            db=db, location_id=location_id, payload=payload
        )
        after = inventory_service.inventory_locations.get(db=db, location_id=location_id)
        metadata_payload = build_changes_metadata(before, after)
        current_user = get_current_user(request)
        log_audit_event(
            db=db,
            request=request,
            action="update",
            entity_type="inventory_location",
            entity_id=str(location_id),
            actor_id=str(current_user.get("person_id")) if current_user else None,
            metadata=metadata_payload,
        )
        return RedirectResponse(
            url=f"/admin/inventory/locations/{location_id}", status_code=303
        )
    except Exception as e:
        location = inventory_service.inventory_locations.get(
            db=db, location_id=location_id
        )
        context = {
            "request": request,
            "active_page": "inventory",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
            "location": location,
            "action_url": f"/admin/inventory/locations/{location_id}/edit",
            "error": str(e),
        }
        return templates.TemplateResponse("admin/inventory/location_form.html", context)
