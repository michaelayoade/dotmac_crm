"""Admin API for reseller commissions + payouts.

Gated by `operations:reseller:*` permissions (admins bypass). The reseller-facing
read view lives in the reseller portal (app/web/reseller).
"""

from typing import Any

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.reseller_commission import ResellerCommission, ResellerPayout
from app.services.auth_dependencies import require_permission
from app.services.reseller_commissions import reseller_commissions as svc

router = APIRouter(prefix="/reseller-commissions", tags=["reseller-commissions"])
payout_router = APIRouter(prefix="/reseller-payouts", tags=["reseller-commissions"])

_READ = Depends(require_permission("operations:reseller:read"))
_WRITE = Depends(require_permission("operations:reseller:write"))


def _commission_dict(c: ResellerCommission) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "reseller_org_id": str(c.reseller_org_id),
        "sales_order_id": str(c.sales_order_id) if c.sales_order_id else None,
        "person_id": str(c.person_id) if c.person_id else None,
        "basis_amount": str(c.basis_amount),
        "rate": str(c.rate),
        "amount": str(c.amount),
        "currency": c.currency,
        "status": c.status.value,
        "payout_id": str(c.payout_id) if c.payout_id else None,
        "earned_at": c.earned_at.isoformat() if c.earned_at else None,
    }


def _payout_dict(p: ResellerPayout) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "reseller_org_id": str(p.reseller_org_id),
        "total_amount": str(p.total_amount),
        "currency": p.currency,
        "status": p.status.value,
        "method": p.method,
        "reference": p.reference,
        "paid_at": p.paid_at.isoformat() if p.paid_at else None,
    }


@router.get("", dependencies=[_READ])
def list_commissions(
    reseller_org_id: str | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items = svc.list_commissions(db, reseller_org_id=reseller_org_id, status=status, limit=limit, offset=offset)
    return {"items": [_commission_dict(c) for c in items], "count": len(items), "limit": limit, "offset": offset}


@router.get("/summary", dependencies=[_READ])
def commission_summary(reseller_org_id: str, db: Session = Depends(get_db)):
    summary = svc.reseller_summary(db, reseller_org_id)
    for key in ("pending_amount", "approved_amount", "paid_amount", "unpaid_amount"):
        summary[key] = str(summary[key])
    return summary


@router.post("/{commission_id}/approve", dependencies=[_WRITE])
def approve_commission(commission_id: str, db: Session = Depends(get_db)):
    return _commission_dict(svc.approve(db, commission_id))


@router.post("/{commission_id}/void", dependencies=[_WRITE])
def void_commission(
    commission_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    db: Session = Depends(get_db),
):
    reason = payload.get("reason")
    return _commission_dict(svc.void(db, commission_id, str(reason) if reason else None))


@payout_router.get("", dependencies=[_READ])
def list_payouts(
    reseller_org_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items = svc.list_payouts(db, reseller_org_id=reseller_org_id, limit=limit, offset=offset)
    return {"items": [_payout_dict(p) for p in items], "count": len(items), "limit": limit, "offset": offset}


@payout_router.post("", dependencies=[_WRITE])
def create_payout(payload: dict[str, Any] = Body(default_factory=dict), db: Session = Depends(get_db)):
    reseller_org_id = str(payload.get("reseller_org_id") or "")
    return _payout_dict(svc.create_payout(db, reseller_org_id))


@payout_router.post("/{payout_id}/mark-paid", dependencies=[_WRITE])
def mark_payout_paid(
    payout_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    db: Session = Depends(get_db),
):
    method = payload.get("method")
    reference = payload.get("reference")
    return _payout_dict(
        svc.mark_payout_paid(
            db,
            payout_id,
            method=str(method) if method else None,
            reference=str(reference) if reference else None,
        )
    )
