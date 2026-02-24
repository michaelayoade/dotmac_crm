from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.organization_membership import OrganizationMembership, OrganizationMembershipRole
from app.models.person import PartyStatus, Person
from app.models.rbac import PersonRole, Role
from app.models.subscriber import AccountType, Organization, Subscriber, SubscriberStatus
from app.services.common import coerce_uuid

RESELLER_ROLE_ADMIN = "reseller_admin"
RESELLER_ROLE_MEMBER = "reseller_member"
RESELLER_ROLES = {RESELLER_ROLE_ADMIN, RESELLER_ROLE_MEMBER}


def _role_id_by_name(db: Session, role_name: str) -> uuid.UUID | None:
    role = db.query(Role).filter(Role.name == role_name).filter(Role.is_active.is_(True)).first()
    return role.id if role else None


def person_has_any_reseller_role(db: Session, *, person_id: uuid.UUID) -> bool:
    role_ids = [rid for rid in (_role_id_by_name(db, name) for name in RESELLER_ROLES) if rid]
    if not role_ids:
        return False
    return (
        db.query(PersonRole).filter(PersonRole.person_id == person_id).filter(PersonRole.role_id.in_(role_ids)).first()
        is not None
    )


def person_has_reseller_admin_role(db: Session, *, person_id: uuid.UUID) -> bool:
    role_id = _role_id_by_name(db, RESELLER_ROLE_ADMIN)
    if not role_id:
        return False
    return (
        db.query(PersonRole).filter(PersonRole.person_id == person_id).filter(PersonRole.role_id == role_id).first()
        is not None
    )


def ensure_reseller_portal_access(db: Session, *, person_id: uuid.UUID) -> Organization:
    """Hard enforcement for reseller portal access (DB-backed)."""
    person = db.get(Person, person_id)
    if not person or not person.is_active:
        raise HTTPException(status_code=403, detail="Forbidden")

    if not person.organization_id:
        raise HTTPException(status_code=403, detail="Reseller access required")

    org = db.get(Organization, person.organization_id)
    if not org or not org.is_active or org.account_type != AccountType.reseller:
        raise HTTPException(status_code=403, detail="Reseller access required")

    if not person_has_any_reseller_role(db, person_id=person.id):
        raise HTTPException(status_code=403, detail="Reseller role required")

    return org


def get_reseller_context(db: Session, *, actor_person_id: uuid.UUID) -> tuple[Organization, set[uuid.UUID]]:
    reseller_org = ensure_reseller_portal_access(db, person_id=actor_person_id)
    allowed_org_ids = get_allowed_org_ids(db, reseller_org_id=reseller_org.id)
    return reseller_org, allowed_org_ids


def get_allowed_org_ids(db: Session, *, reseller_org_id: uuid.UUID) -> set[uuid.UUID]:
    """Return reseller org + all descendants (any depth)."""
    # SQLite fallback for tests (no recursion / limited recursive support).
    if db.bind and db.bind.dialect.name == "sqlite":
        allowed: set[uuid.UUID] = set()
        frontier = [reseller_org_id]
        while frontier:
            current = frontier.pop()
            if current in allowed:
                continue
            allowed.add(current)
            child_ids = [row[0] for row in db.query(Organization.id).filter(Organization.parent_id == current).all()]
            frontier.extend(child_ids)
        return allowed

    # Postgres: recursive CTE.
    # Start set includes root.
    orgs = (
        select(Organization.id, Organization.parent_id)
        .where(Organization.id == reseller_org_id)
        .cte(name="org_tree", recursive=True)
    )
    orgs = orgs.union_all(select(Organization.id, Organization.parent_id).where(Organization.parent_id == orgs.c.id))
    ids = [row[0] for row in db.execute(select(orgs.c.id)).all()]
    return {uuid.UUID(str(v)) if not isinstance(v, uuid.UUID) else v for v in ids if v}


def list_child_organizations(db: Session, *, actor_person_id: uuid.UUID) -> list[Organization]:
    reseller_org, _allowed = get_reseller_context(db, actor_person_id=actor_person_id)
    return (
        db.query(Organization)
        .filter(Organization.parent_id == reseller_org.id)
        .filter(Organization.is_active.is_(True))
        .order_by(Organization.name.asc())
        .all()
    )


def list_scope_organizations(db: Session, *, actor_person_id: uuid.UUID) -> list[Organization]:
    """List reseller org + all descendant orgs the actor is allowed to operate on."""
    _reseller_org, allowed = get_reseller_context(db, actor_person_id=actor_person_id)
    if not allowed:
        return []
    return (
        db.query(Organization)
        .filter(Organization.id.in_(list(allowed)))
        .filter(Organization.is_active.is_(True))
        .order_by(Organization.name.asc())
        .all()
    )


def create_child_organization(
    db: Session,
    *,
    actor_person_id: uuid.UUID,
    name: str,
    domain: str | None = None,
) -> Organization:
    """Create a direct child org under the reseller org. Requires reseller_admin."""
    if not person_has_reseller_admin_role(db, person_id=actor_person_id):
        raise HTTPException(status_code=403, detail="Reseller admin role required")

    parent_org, _allowed = get_reseller_context(db, actor_person_id=actor_person_id)

    org_name = (name or "").strip()
    if not org_name:
        raise HTTPException(status_code=400, detail="Organization name required")

    child = Organization(
        name=org_name[:160],
        domain=(domain or "").strip() or None,
        parent_id=parent_org.id,
        account_type=AccountType.customer,
        is_active=True,
        metadata_={"created_by": "reseller_portal"},
    )
    db.add(child)
    db.flush()

    # Ensure actor can manage the new org.
    membership = (
        db.query(OrganizationMembership)
        .filter(OrganizationMembership.organization_id == child.id)
        .filter(OrganizationMembership.person_id == actor_person_id)
        .first()
    )
    if not membership:
        db.add(
            OrganizationMembership(
                organization_id=child.id,
                person_id=actor_person_id,
                role=OrganizationMembershipRole.owner,
                is_active=True,
            )
        )

    db.commit()
    db.refresh(child)
    return child


def ensure_org_in_reseller_scope(
    db: Session,
    *,
    actor_person_id: uuid.UUID,
    target_org_id: uuid.UUID,
) -> None:
    _reseller_org, allowed = get_reseller_context(db, actor_person_id=actor_person_id)
    if target_org_id not in allowed:
        raise HTTPException(status_code=403, detail="Organization out of scope")


def create_contact(
    db: Session,
    *,
    actor_person_id: uuid.UUID,
    organization_id: uuid.UUID | str,
    first_name: str,
    last_name: str,
    email: str,
    phone: str | None = None,
) -> Person:
    """Create (or link) a Person contact within reseller scope.

    Person.email is globally unique; if the email already exists we reuse the Person record
    and add an OrganizationMembership to link them to the target org.
    """
    org_id = coerce_uuid(organization_id)
    ensure_org_in_reseller_scope(db, actor_person_id=actor_person_id, target_org_id=org_id)

    email_norm = (email or "").strip().lower()
    if not email_norm or "@" not in email_norm:
        raise HTTPException(status_code=400, detail="Invalid email")

    existing = db.query(Person).filter(Person.email == email_norm).first()
    if existing:
        link = (
            db.query(OrganizationMembership)
            .filter(OrganizationMembership.organization_id == org_id)
            .filter(OrganizationMembership.person_id == existing.id)
            .first()
        )
        if not link:
            db.add(
                OrganizationMembership(
                    organization_id=org_id,
                    person_id=existing.id,
                    role=OrganizationMembershipRole.member,
                    is_active=True,
                )
            )
            db.commit()
        return existing

    person = Person(
        first_name=(first_name or "").strip() or "Contact",
        last_name=(last_name or "").strip() or "User",
        email=email_norm,
        phone=(phone or "").strip() or None,
        organization_id=org_id,
        party_status=PartyStatus.contact,
        metadata_={"created_by": "reseller_portal", "created_by_person_id": str(actor_person_id)},
    )
    db.add(person)
    db.commit()
    db.refresh(person)
    return person


def list_contacts_for_actor(db: Session, *, actor_person_id: uuid.UUID) -> list[Person]:
    _reseller_org, allowed = get_reseller_context(db, actor_person_id=actor_person_id)
    if not allowed:
        return []
    membership_person_ids = (
        db.query(OrganizationMembership.person_id)
        .filter(OrganizationMembership.organization_id.in_(list(allowed)))
        .filter(OrganizationMembership.is_active.is_(True))
        .subquery()
    )
    return (
        db.query(Person)
        .options(joinedload(Person.organization))
        .filter(Person.is_active.is_(True))
        .filter(
            (Person.organization_id.in_(list(allowed))) | (Person.id.in_(select(membership_person_ids.c.person_id)))
        )
        .order_by(Person.created_at.desc())
        .all()
    )


def create_subscriber(
    db: Session,
    *,
    actor_person_id: uuid.UUID,
    organization_id: uuid.UUID | str,
    subscriber_number: str,
    status: str = "active",
    service_name: str | None = None,
) -> Subscriber:
    org_id = coerce_uuid(organization_id)
    ensure_org_in_reseller_scope(db, actor_person_id=actor_person_id, target_org_id=org_id)

    sub_num = (subscriber_number or "").strip()
    if not sub_num:
        raise HTTPException(status_code=400, detail="subscriber_number required")

    try:
        status_value = SubscriberStatus(status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid subscriber status") from exc

    subscriber = Subscriber(
        subscriber_number=sub_num,
        status=status_value,
        service_name=(service_name or "").strip() or None,
        organization_id=org_id,
        is_active=True,
        metadata_={"created_by": "reseller_portal", "created_by_person_id": str(actor_person_id)},
    )
    db.add(subscriber)
    db.commit()
    db.refresh(subscriber)
    return subscriber


def list_subscribers_for_actor(db: Session, *, actor_person_id: uuid.UUID) -> list[Subscriber]:
    _reseller_org, allowed = get_reseller_context(db, actor_person_id=actor_person_id)
    if not allowed:
        return []
    return (
        db.query(Subscriber)
        .options(joinedload(Subscriber.person), joinedload(Subscriber.organization))
        .filter(Subscriber.is_active.is_(True))
        .filter(Subscriber.organization_id.in_(list(allowed)))
        .order_by(Subscriber.created_at.desc())
        .all()
    )
