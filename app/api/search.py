from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.common import ListResponse
from app.schemas.typeahead import CatalogOfferItem, TypeaheadItem
from app.services import customer_search as customer_search_service
from app.services import typeahead as typeahead_service
from app.services.response import list_response

router = APIRouter(prefix="/search", tags=["search"])


def _empty_typeahead(limit: int) -> dict:
    # Compatibility shim for removed domains (billing/catalog/etc).
    return list_response([], limit, 0)


@router.get("/people", response_model=ListResponse[TypeaheadItem])
def search_people(
    q: str = Query(default=""),
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    return typeahead_service.people_response(db, q, limit)


@router.get("/ticket-customers", response_model=ListResponse[TypeaheadItem])
def search_ticket_customers(
    q: str = Query(default=""),
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    return typeahead_service.ticket_people_response(db, q, limit)


@router.get("/ticket-subscribers", response_model=ListResponse[TypeaheadItem])
def search_ticket_subscribers(
    q: str = Query(default=""),
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    return typeahead_service.ticket_subscribers_response(db, q, limit)


@router.get("/technicians", response_model=ListResponse[TypeaheadItem])
def search_technicians(
    q: str = Query(default=""),
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    return typeahead_service.technicians_response(db, q, limit)


@router.get("/customers", response_model=ListResponse[TypeaheadItem])
def search_customers(
    q: str = Query(min_length=2),
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    return customer_search_service.search_response(db, q, limit)


@router.get("/network-devices", response_model=ListResponse[TypeaheadItem])
def search_network_devices(
    q: str = Query(min_length=2),
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    return typeahead_service.network_devices_response(db, q, limit)


@router.get("/pop-sites", response_model=ListResponse[TypeaheadItem])
def search_pop_sites(
    q: str = Query(min_length=2),
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    return typeahead_service.pop_sites_response(db, q, limit)


@router.get("/vendors", response_model=ListResponse[TypeaheadItem])
def search_vendors(
    # Allow empty queries so typeahead fields can fetch on focus/click.
    q: str = Query(default=""),
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    return typeahead_service.vendors_response(db, q, limit)


@router.get("/inventory-items", response_model=ListResponse[TypeaheadItem])
def search_inventory_items(
    # Allow empty queries so fields can fetch on focus/click.
    q: str = Query(default=""),
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    return typeahead_service.inventory_items_response(db, q, limit)


@router.get("/organizations", response_model=ListResponse[TypeaheadItem])
def search_organizations(
    q: str = Query(min_length=2),
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    return typeahead_service.organizations_response(db, q, limit)


@router.get("/global")
def global_search(
    q: str = Query(min_length=2),
    limit: int = Query(default=3, ge=1, le=10),
    db: Session = Depends(get_db),
):
    """
    Global search across multiple entity types.
    Returns categorized results with navigation URLs.
    """
    return typeahead_service.global_search(db, q, limit)


@router.get("/accounts", response_model=ListResponse[TypeaheadItem])
def search_accounts(
    q: str = Query(min_length=2),
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    return _empty_typeahead(limit)


@router.get("/subscribers", response_model=ListResponse[TypeaheadItem])
def search_subscribers(
    q: str = Query(default=""),
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    return typeahead_service.subscribers_response(db, q, limit)


@router.get("/subscriptions", response_model=ListResponse[TypeaheadItem])
def search_subscriptions(
    q: str = Query(min_length=2),
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    return _empty_typeahead(limit)


@router.get("/contacts", response_model=ListResponse[TypeaheadItem])
def search_contacts(
    q: str = Query(min_length=2),
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    return typeahead_service.people_response(db, q, limit)


@router.get("/invoices", response_model=ListResponse[TypeaheadItem])
def search_invoices(
    q: str = Query(min_length=2),
    limit: int = Query(default=20, ge=1, le=50),
    account_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    return _empty_typeahead(limit)


@router.get("/nas-devices", response_model=ListResponse[TypeaheadItem])
def search_nas_devices(
    q: str = Query(min_length=2),
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    return _empty_typeahead(limit)


@router.get("/catalog-offers", response_model=ListResponse[CatalogOfferItem])
def search_catalog_offers(
    # Allow empty queries so the plan picker can fetch on focus/click.
    q: str = Query(default=""),
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """The dotmac_sub subscription-plan catalog (offers), for picking a plan on a
    quote/sale. dotmac_sub is the source of truth — the CRM keeps no parallel
    plan list. Returns [] when the integration is off."""
    from app.services import selfcare

    if not selfcare.is_customer_sync_enabled(db):
        return _empty_typeahead(limit)

    try:
        offers = selfcare.fetch_offers(db, q=q or None, active_only=True)
    except Exception:
        return _empty_typeahead(limit)

    items = [_offer_to_item(row) for row in offers[:limit] if row.get("id")]
    return list_response(items, limit, 0)


def _offer_to_item(row: dict) -> CatalogOfferItem:
    code = str(row.get("code") or "").strip() or None
    name = str(row.get("name") or "").strip()
    price = str(row.get("recurring_price") or "").strip() or None
    cycle = str(row.get("billing_cycle") or "").strip() or None
    label = " — ".join(part for part in (code, name) if part) or name or str(row.get("id"))
    return CatalogOfferItem(
        id=str(row.get("id")),
        code=code,
        label=label,
        recurring_price=price,
        currency=str(row.get("currency") or "").strip() or None,
        billing_cycle=cycle,
        speed_download_mbps=row.get("speed_download_mbps"),
        speed_upload_mbps=row.get("speed_upload_mbps"),
    )
