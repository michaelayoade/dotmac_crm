"""Admin web pages for the referral program (list + reward actions)."""

import json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.crm.referral import Referral, ReferralStatus
from app.models.person import Person
from app.services.auth_dependencies import require_permission
from app.services.crm.referrals import referrals as referrals_service
from app.web.admin.crm_support import _can_write_sales, _crm_base_context
from app.web.templates import Jinja2Templates

router = APIRouter(tags=["web-admin-crm"])
templates = Jinja2Templates(directory="templates")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _name(person: Person | None) -> str:
    if person is None:
        return "—"
    return (person.display_name or f"{person.first_name} {person.last_name}".strip() or person.email or "—").strip()


def _referral_rows(db: Session, items: list[Referral]) -> list[dict]:
    person_ids: set = set()
    for r in items:
        person_ids.add(r.referrer_person_id)
        if r.referred_person_id:
            person_ids.add(r.referred_person_id)
    persons = {}
    if person_ids:
        persons = {p.id: p for p in db.query(Person).filter(Person.id.in_(person_ids)).all()}

    rows = []
    for r in items:
        amount = r.reward_amount
        rows.append(
            {
                "id": str(r.id),
                "referrer": _name(persons.get(r.referrer_person_id)),
                "referred": _name(persons.get(r.referred_person_id)),
                "status": r.status.value,
                "reward_status": r.reward_status.value,
                "reward": f"{r.reward_currency} {amount:,.0f}" if amount is not None else "—",
                "source": r.source or "—",
                "created_at": r.created_at,
                "can_issue": r.status == ReferralStatus.qualified and r.reward_status.value in ("pending", "approved"),
                "can_reject": r.status in (ReferralStatus.pending, ReferralStatus.qualified),
            }
        )
    return rows


def _stats(db: Session) -> dict:
    base = db.query(Referral).filter(Referral.is_active.is_(True))
    counts = {s.value: 0 for s in ReferralStatus}
    for r in base.all():
        counts[r.status.value] = counts.get(r.status.value, 0) + 1
    return {
        "total": sum(counts.values()),
        "pending": counts.get("pending", 0),
        "qualified": counts.get("qualified", 0),
        "rewarded": counts.get("rewarded", 0),
    }


def _toast(url: str, message: str, kind: str = "success") -> RedirectResponse:
    headers = {"HX-Trigger": json.dumps({"showToast": {"message": message, "type": kind}})}
    return RedirectResponse(url=url, status_code=303, headers=headers)


@router.get(
    "/referrals",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("crm:lead:read"))],
)
def crm_referrals(request: Request, status: str | None = None, db: Session = Depends(get_db)):
    valid = {s.value for s in ReferralStatus}
    status = status if status in valid else None
    items = referrals_service.list(db, status=status, limit=200)
    context = _crm_base_context(request, db, "referrals")
    context.update(
        {
            "referrals": _referral_rows(db, items),
            "status": status or "",
            "referral_statuses": [s.value for s in ReferralStatus],
            "stats": _stats(db),
            "can_write": _can_write_sales(request),
        }
    )
    return templates.TemplateResponse("admin/crm/referrals.html", context)


@router.post(
    "/referrals/{referral_id}/issue-reward",
    dependencies=[Depends(require_permission("crm:lead:write"))],
)
def crm_referral_issue_reward(referral_id: str, request: Request, db: Session = Depends(get_db)):
    referrals_service.issue_reward(db, referral_id)
    return _toast("/admin/crm/referrals", "Reward marked as issued.")


@router.post(
    "/referrals/{referral_id}/reject",
    dependencies=[Depends(require_permission("crm:lead:write"))],
)
def crm_referral_reject(
    referral_id: str,
    request: Request,
    reason: str = Form(default="Rejected by admin"),
    db: Session = Depends(get_db),
):
    referrals_service.reject(db, referral_id, reason)
    return _toast("/admin/crm/referrals", "Referral rejected.", kind="info")
