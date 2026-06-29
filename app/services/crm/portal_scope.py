"""Actor-aware scoping for the customer/reseller Portal API (RFC #73).

A portal token is either ``subscriber``-scoped (one subscriber) or ``reseller``-
scoped (a reseller organization). The operational verticals — projects, work
orders, quotes — must serve both: a subscriber sees only their own records,
while a reseller sees every record belonging to a subscriber in its organization
subtree. These helpers centralize that resolution so every data route enforces
the same boundary (a client can never widen its own scope).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.subscriber import Organization, Subscriber
from app.services.common import coerce_uuid
from app.services.portal_auth import PortalPrincipal

# Depth guard for the org hierarchy walk — enterprise/reseller trees are shallow;
# this only bounds a pathological/cyclic parent chain.
_MAX_ORG_DEPTH = 20


def org_subtree_ids(db: Session, root_org_id) -> list:
    """The reseller org plus every descendant org id (bounded BFS over parent_id)."""
    root = coerce_uuid(str(root_org_id))
    if root is None:
        return []
    collected = {root}
    frontier = [root]
    for _ in range(_MAX_ORG_DEPTH):
        rows = db.query(Organization.id).filter(Organization.parent_id.in_(frontier)).all()
        children = [r[0] for r in rows if r[0] not in collected]
        if not children:
            break
        collected.update(children)
        frontier = children
    return list(collected)


def resolve_subscriber_ids(db: Session, principal: PortalPrincipal) -> list[str]:
    """Subscriber ids visible to a portal principal.

    - ``subscriber`` → just its own subscriber id.
    - ``reseller`` → every subscriber under the reseller's organization subtree.
    """
    if principal.actor == "reseller":
        org_ids = org_subtree_ids(db, principal.subject_id)
        if not org_ids:
            return []
        rows = db.query(Subscriber.id).filter(Subscriber.organization_id.in_(org_ids)).all()
        return [str(r[0]) for r in rows]
    sub_uuid = coerce_uuid(str(principal.subject_id))
    return [str(sub_uuid)] if sub_uuid else []


def resolve_person_ids(db: Session, principal: PortalPrincipal) -> list[str]:
    """Person ids owning records for the subscribers in scope.

    Quotes key on ``person_id`` (not ``subscriber_id``), so reseller-scoped quote
    listing maps the visible subscribers to their owning people.
    """
    subscriber_ids = resolve_subscriber_ids(db, principal)
    uuids = [coerce_uuid(str(s)) for s in subscriber_ids]
    uuids = [u for u in uuids if u is not None]
    if not uuids:
        return []
    rows = (
        db.query(Subscriber.person_id).filter(Subscriber.id.in_(uuids)).filter(Subscriber.person_id.isnot(None)).all()
    )
    return [str(r[0]) for r in rows]


def resolve_target_subscriber(
    db: Session, principal: PortalPrincipal, requested_subscriber_id: str | None
) -> Subscriber:
    """Resolve the subscriber a write action targets, enforcing the actor's scope.

    - ``subscriber`` → always its own subscriber (any requested id is ignored).
    - ``reseller`` → ``requested_subscriber_id`` is required and must resolve to a
      subscriber inside the reseller's org subtree.
    """
    from fastapi import HTTPException

    if principal.actor == "reseller":
        if not requested_subscriber_id:
            raise HTTPException(status_code=422, detail="for_subscriber_id is required for reseller actors")
        target = db.get(Subscriber, coerce_uuid(str(requested_subscriber_id)))
        if target is None:
            raise HTTPException(status_code=404, detail="Subscriber not found")
        allowed_orgs = {str(o) for o in org_subtree_ids(db, principal.subject_id)}
        if str(target.organization_id) not in allowed_orgs:
            raise HTTPException(status_code=403, detail="Subscriber is outside your reseller scope")
        return target
    target = db.get(Subscriber, coerce_uuid(str(principal.subject_id)))
    if target is None:
        raise HTTPException(status_code=404, detail="Subscriber not found")
    return target
