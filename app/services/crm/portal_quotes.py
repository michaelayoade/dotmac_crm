"""Self-serve installation quotes (RFC #73, Sales/Quotes vertical).

The customer (or a reseller, on a customer's behalf) drops a map pin for the
install address; this service:
  1. computes **feasibility** — distance from the pin to the nearest fiber access
     point (PostGIS), classifying coverage as covered / survey_required / out_of_area;
  2. builds an **estimate** from configurable settings (base fee + distance
     surcharge) and the required **deposit**;
  3. creates a draft Lead + Quote with the pin, feasibility, and estimate, so the
     existing quote → sales-order → project chain takes over on acceptance.

On ``accept_with_deposit`` (called after the sub verifies the deposit payment)
the quote is accepted — which auto-creates the SalesOrder and the install
Project — and the deposit is recorded on the SalesOrder. Idempotent.

All pricing lives in settings (``SettingDomain.projects``, ``selfserve_quote_*``)
so the numbers are tunable per market without code changes.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException
from geoalchemy2.functions import ST_MakePoint, ST_SetSRID
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.crm.sales import Quote, QuoteStatus
from app.models.domain_settings import SettingDomain
from app.models.network import FiberAccessPoint
from app.models.projects import ProjectType
from app.models.sales_order import SalesOrder, SalesOrderPaymentStatus, SalesOrderStatus
from app.schemas.crm.sales import LeadCreate, QuoteCreate, QuoteLineItemCreate, QuoteUpdate
from app.services import settings_spec
from app.services.common import coerce_uuid
from app.services.crm import portal_scope
from app.services.crm.sales.service import (
    CrmQuoteLineItems,
    Leads,
    Quotes,
    _find_existing_project_for_quote,
)
from app.services.portal_auth import PortalPrincipal

_TWOPLACES = Decimal("0.01")
_PROJECT_TYPE = ProjectType.fiber_optics_installation.value


def _money(value) -> Decimal:
    return Decimal(str(value or "0")).quantize(_TWOPLACES, rounding=ROUND_HALF_UP)


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _settings(db: Session) -> dict:
    """Resolved, typed self-serve quote settings (with placeholder defaults)."""

    def _dec(key: str) -> Decimal:
        return _money(settings_spec.resolve_value(db, SettingDomain.projects, key))

    def _int(key: str, fallback: int) -> int:
        raw = settings_spec.resolve_value(db, SettingDomain.projects, key)
        try:
            return int(str(raw))
        except (TypeError, ValueError):
            return fallback

    return {
        "enabled": bool(settings_spec.resolve_value(db, SettingDomain.projects, "selfserve_quote_enabled")),
        "base_fee": _dec("selfserve_quote_base_fee"),
        "free_radius_m": _int("selfserve_quote_free_radius_meters", 300),
        "fee_per_km": _dec("selfserve_quote_fee_per_km"),
        "deposit_percent": max(0, min(100, _int("selfserve_quote_deposit_percent", 50))),
        "feasibility_radius_m": _int("selfserve_quote_feasibility_radius_meters", 2000),
    }


def _nearest_fiber_access_point(db: Session, latitude: float, longitude: float):
    """Nearest active fiber access point and its distance in metres (PostGIS).

    Mirrors ``app.services.gis`` — projected to EPSG:3857 for a metre distance.
    Returns ``(FiberAccessPoint | None, float | None)``. Isolated so the pricing
    logic can be unit-tested without a spatial database.
    """
    point = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
    distance = func.ST_Distance(
        func.ST_Transform(FiberAccessPoint.geom, 3857),
        func.ST_Transform(point, 3857),
    ).label("distance_m")
    row = (
        db.query(FiberAccessPoint, distance)
        .filter(FiberAccessPoint.is_active.is_(True))
        .filter(FiberAccessPoint.geom.isnot(None))
        .order_by(distance)
        .first()
    )
    if row is None:
        return None, None
    fap, dist = row
    return fap, (float(dist) if dist is not None else None)


def compute_feasibility(db: Session, latitude: float, longitude: float) -> dict:
    """Classify install feasibility from proximity to the nearest fiber plant."""
    cfg = _settings(db)
    fap, distance = _nearest_fiber_access_point(db, latitude, longitude)
    if fap is None or distance is None:
        return {
            "feasible": False,
            "coverage": "out_of_area",
            "nearest_fap_id": None,
            "nearest_fap_name": None,
            "distance_meters": None,
        }
    coverage = "covered" if distance <= cfg["feasibility_radius_m"] else "survey_required"
    return {
        "feasible": True,
        "coverage": coverage,
        "nearest_fap_id": str(fap.id),
        "nearest_fap_name": fap.name,
        "distance_meters": round(distance, 1),
    }


def compute_estimate(db: Session, feasibility: dict, currency: str) -> dict:
    """Build the estimate + deposit + quote line items from settings.

    For ``out_of_area`` / ``survey_required`` the estimate is **provisional** — a
    site survey confirms the true cost; only the base fee is quoted up front.
    """
    cfg = _settings(db)
    base_fee = _money(cfg["base_fee"])
    coverage = feasibility.get("coverage")
    free_radius_m = float(cfg["free_radius_m"])

    distance_m: float | None = None
    raw_distance = feasibility.get("distance_meters")
    if raw_distance is not None:
        try:
            distance_m = float(str(raw_distance))
        except (TypeError, ValueError):
            distance_m = None

    distance_fee = Decimal("0.00")
    billable_m = 0.0
    provisional = coverage != "covered"
    if coverage == "covered" and distance_m is not None:
        billable_m = max(0.0, distance_m - free_radius_m)
        if billable_m > 0:
            km = Decimal(str(billable_m)) / Decimal("1000")
            distance_fee = _money(km * cfg["fee_per_km"])

    line_items = [{"description": "Fiber installation (base)", "unit_price": base_fee}]
    if distance_fee > 0:
        over_km = round(billable_m / 1000, 2)
        line_items.append(
            {"description": f"Distance surcharge ({over_km} km beyond free radius)", "unit_price": distance_fee}
        )

    subtotal = _money(base_fee + distance_fee)
    deposit_percent = cfg["deposit_percent"]
    deposit_amount = _money(subtotal * Decimal(deposit_percent) / Decimal("100"))
    return {
        "currency": currency,
        "base_fee": base_fee,
        "distance_fee": distance_fee,
        "subtotal": subtotal,
        "deposit_percent": deposit_percent,
        "deposit_amount": deposit_amount,
        "provisional": provisional,
        "line_items": line_items,
    }


def _resolve_currency(db: Session) -> str:
    currency = settings_spec.resolve_value(db, SettingDomain.billing, "default_currency")
    return str(currency) if currency else "NGN"


class PortalQuotes:
    """Service for the self-serve quote portal surface (subscriber + reseller)."""

    @staticmethod
    def request(
        db: Session,
        principal: PortalPrincipal,
        *,
        latitude: float,
        longitude: float,
        address: str | None,
        region: str | None = None,
        note: str | None = None,
        for_subscriber_id: str | None = None,
    ) -> Quote:
        cfg = _settings(db)
        if not cfg["enabled"]:
            raise HTTPException(status_code=403, detail="Self-serve quotes are not available")

        subscriber = portal_scope.resolve_target_subscriber(db, principal, for_subscriber_id)
        if subscriber.person_id is None:
            raise HTTPException(status_code=404, detail="Subscriber has no associated contact")

        feasibility = compute_feasibility(db, latitude, longitude)
        currency = _resolve_currency(db)
        estimate = compute_estimate(db, feasibility, currency)

        install = {
            "latitude": latitude,
            "longitude": longitude,
            "address": address,
            "region": region,
        }
        lead = Leads.create(
            db,
            LeadCreate(
                person_id=subscriber.person_id,
                title="Self-serve installation request",
                address=address,
                region=region,
                notes=note,
                lead_source="portal",
                metadata_={"source": "portal_self_serve", "install": install},
            ),
        )
        quote = Quotes.create(
            db,
            QuoteCreate(
                person_id=subscriber.person_id,
                lead_id=lead.id,
                status=QuoteStatus.draft,
                currency=currency,
                metadata_={
                    "source": "portal_self_serve",
                    "project_type": _PROJECT_TYPE,
                    "subscriber_id": str(subscriber.id),
                    "subscriber_external_id": subscriber.external_id,
                    "install": install,
                    "feasibility": feasibility,
                    "deposit_percent": estimate["deposit_percent"],
                    "estimate_provisional": estimate["provisional"],
                },
            ),
        )
        for item in estimate["line_items"]:
            CrmQuoteLineItems.create(
                db,
                QuoteLineItemCreate(
                    quote_id=quote.id,
                    description=item["description"],
                    quantity=Decimal("1.000"),
                    unit_price=item["unit_price"],
                ),
            )
        db.refresh(quote)
        return quote

    @staticmethod
    def portal_list(db: Session, principal: PortalPrincipal) -> list[dict]:
        """Self-serve quotes visible to the principal (own, or reseller subtree)."""
        person_ids = portal_scope.resolve_person_ids(db, principal)
        uuids = [coerce_uuid(str(p)) for p in person_ids]
        uuids = [u for u in uuids if u is not None]
        if not uuids:
            return []
        quotes = (
            db.query(Quote)
            .filter(Quote.person_id.in_(uuids))
            .filter(Quote.is_active.is_(True))
            .order_by(Quote.created_at.desc())
            .all()
        )
        # Only surface portal-originated quotes (not internal sales quotes).
        return [
            build_portal_quote_payload(db, q)
            for q in quotes
            if isinstance(q.metadata_, dict) and q.metadata_.get("source") == "portal_self_serve"
        ]

    @staticmethod
    def accept_with_deposit(
        db: Session,
        principal: PortalPrincipal,
        quote_id: str,
        *,
        deposit_reference: str,
        deposit_amount: str | Decimal,
        provider: str | None = None,
    ) -> dict:
        """Accept a quote after the deposit is verified; record it; return payload.

        Idempotent on the quote's accepted state — a repeat call (e.g. a webhook
        retry) returns the same already-created sales order / project.
        """
        quote = Quotes.get(db, quote_id)
        allowed = set(portal_scope.resolve_person_ids(db, principal))
        if str(quote.person_id) not in allowed:
            raise HTTPException(status_code=403, detail="Quote is outside your scope")

        amount = _money(deposit_amount)
        already_accepted = quote.status == QuoteStatus.accepted

        if not already_accepted:
            meta = dict(quote.metadata_ or {})
            meta["deposit"] = {
                "reference": deposit_reference,
                "amount": str(amount),
                "provider": provider,
                "paid": True,
            }
            Quotes.update(db, str(quote.id), QuoteUpdate(status=QuoteStatus.accepted, metadata_=meta))
            db.refresh(quote)

        _record_deposit_on_sales_order(db, quote, amount)
        return build_portal_quote_payload(db, quote, already_accepted=already_accepted)


def _record_deposit_on_sales_order(db: Session, quote: Quote, deposit_amount: Decimal) -> SalesOrder | None:
    sales_order = db.query(SalesOrder).filter(SalesOrder.quote_id == quote.id).first()
    if sales_order is None:
        return None
    total = _money(sales_order.total)
    paid = _money(deposit_amount)
    sales_order.deposit_required = True
    sales_order.deposit_paid = True
    sales_order.amount_paid = paid
    sales_order.balance_due = _money(max(Decimal("0.00"), total - paid))
    sales_order.payment_status = (
        SalesOrderPaymentStatus.paid if paid >= total and total > 0 else SalesOrderPaymentStatus.partial
    )
    if sales_order.payment_status == SalesOrderPaymentStatus.paid:
        sales_order.status = SalesOrderStatus.paid
    db.commit()
    db.refresh(sales_order)
    return sales_order


def build_portal_quote_payload(db: Session, quote: Quote, *, already_accepted: bool = False) -> dict:
    """Serialize a quote for the portal API / sub mirror."""
    meta = _as_dict(quote.metadata_)
    install = _as_dict(meta.get("install"))
    feasibility = _as_dict(meta.get("feasibility"))
    deposit_meta = _as_dict(meta.get("deposit"))

    total = _money(quote.total)
    deposit_percent = int(meta.get("deposit_percent") or 0)
    deposit_amount = (
        _money(deposit_meta["amount"]) if deposit_meta.get("amount") else _money(total * Decimal(deposit_percent) / 100)
    )

    sales_order = db.query(SalesOrder).filter(SalesOrder.quote_id == quote.id).first()
    project = _find_existing_project_for_quote(db, quote.id)

    line_items = [
        {
            "description": li.description,
            "quantity": str(li.quantity),
            "unit_price": str(_money(li.unit_price)),
            "amount": str(_money(li.amount)),
        }
        for li in sorted(quote.line_items, key=lambda x: x.created_at or x.id)
    ]

    return {
        "id": str(quote.id),
        "status": quote.status.value,
        "currency": quote.currency,
        "subtotal": str(_money(quote.subtotal)),
        "tax_total": str(_money(quote.tax_total)),
        "total": str(total),
        "project_type": meta.get("project_type"),
        "subscriber_id": meta.get("subscriber_id"),
        "subscriber_external_id": meta.get("subscriber_external_id"),
        "latitude": install.get("latitude"),
        "longitude": install.get("longitude"),
        "address": install.get("address"),
        "region": install.get("region"),
        "feasibility": {
            "coverage": feasibility.get("coverage"),
            "feasible": feasibility.get("feasible"),
            "distance_meters": feasibility.get("distance_meters"),
            "nearest_fap_name": feasibility.get("nearest_fap_name"),
        },
        "estimate_provisional": bool(meta.get("estimate_provisional")),
        "deposit_percent": deposit_percent,
        "deposit_amount": str(deposit_amount),
        "deposit_paid": bool(deposit_meta.get("paid")),
        "deposit_reference": deposit_meta.get("reference"),
        "line_items": line_items,
        "sales_order_id": str(sales_order.id) if sales_order else None,
        "project_id": str(project.id) if project else None,
        "already_accepted": already_accepted,
        "created_at": quote.created_at.isoformat() if quote.created_at else None,
        "expires_at": quote.expires_at.isoformat() if quote.expires_at else None,
    }


quotes = PortalQuotes()
