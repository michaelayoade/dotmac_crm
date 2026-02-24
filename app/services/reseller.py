from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.auth import AuthProvider, UserCredential
from app.models.organization_membership import OrganizationMembership, OrganizationMembershipRole
from app.models.person import Person
from app.models.rbac import PersonRole, Role
from app.models.subscriber import AccountType, Organization
from app.services.auth_flow import hash_password
from app.services.reseller_portal import RESELLER_ROLE_ADMIN


def _now() -> datetime:
    return datetime.now(UTC)


def _normalize_email(value: str) -> str:
    return (value or "").strip().lower()


def ensure_reseller_context(db: Session, person: Person) -> Organization:
    if not person.organization_id:
        raise HTTPException(status_code=403, detail="Reseller organization required")
    org = db.get(Organization, person.organization_id)
    if not org or not org.is_active:
        raise HTTPException(status_code=403, detail="Reseller organization required")
    if org.account_type != AccountType.reseller:
        raise HTTPException(status_code=403, detail="Reseller organization required")
    return org


def register_reseller(*_args, **_kwargs) -> Person:  # pragma: no cover
    """Legacy stub.

    Reseller creation is admin-controlled. Public reseller signup is intentionally disabled.
    """
    raise HTTPException(status_code=403, detail="Reseller signup is disabled")


def list_child_organizations(db: Session, *, reseller_org_id: uuid.UUID) -> list[Organization]:
    return (
        db.query(Organization)
        .filter(Organization.parent_id == reseller_org_id)
        .filter(Organization.is_active.is_(True))
        .order_by(Organization.name.asc())
        .all()
    )


def create_child_organization(
    db: Session,
    *,
    reseller_org_id: uuid.UUID,
    created_by_person_id: uuid.UUID,
    name: str,
    domain: str | None = None,
) -> Organization:
    parent_org = db.get(Organization, reseller_org_id)
    if not parent_org or not parent_org.is_active or parent_org.account_type != AccountType.reseller:
        raise HTTPException(status_code=404, detail="Reseller organization not found")

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

    membership = OrganizationMembership(
        organization_id=child.id,
        person_id=created_by_person_id,
        role=OrganizationMembershipRole.owner,
        is_active=True,
    )
    db.add(membership)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Organization creation conflict") from exc

    db.refresh(child)
    return child


def person_can_manage_org(db: Session, *, person_id: uuid.UUID, organization_id: uuid.UUID) -> bool:
    membership = (
        db.query(OrganizationMembership)
        .filter(OrganizationMembership.person_id == person_id)
        .filter(OrganizationMembership.organization_id == organization_id)
        .filter(OrganizationMembership.is_active.is_(True))
        .first()
    )
    if membership:
        return True
    # Allow managing their primary org without an explicit membership row.
    person = db.get(Person, person_id)
    return bool(person and person.organization_id and person.organization_id == organization_id)


def admin_create_reseller(
    db: Session,
    *,
    organization_name: str,
    organization_domain: str | None,
    user_first_name: str,
    user_last_name: str,
    user_email: str,
    user_phone: str | None,
    password: str | None,
    reset_password_if_exists: bool = False,
) -> tuple[Organization, Person]:
    """
    Admin tool: create (or reuse) a reseller Organization and login Person.

    - Creates Organization(account_type=reseller)
    - Reuses existing Person by primary email if present (does not duplicate)
    - Sets reseller_override/is_reseller markers so inference isn't required
    - Creates a local credential if needed; optionally resets if requested
    """
    email_norm = _normalize_email(user_email)
    if not email_norm or "@" not in email_norm:
        raise HTTPException(status_code=400, detail="Invalid email")

    org_name = (organization_name or "").strip()
    if not org_name:
        raise HTTPException(status_code=400, detail="Organization name required")

    org_domain = (organization_domain or "").strip() or None

    org = Organization(
        name=org_name[:160],
        domain=org_domain,
        account_type=AccountType.reseller,
        is_active=True,
        metadata_={"explicit_admin_create": True},
    )
    db.add(org)
    db.flush()

    person = db.query(Person).filter(Person.email == email_norm).first()
    if person:
        # Reuse existing person; attach them to reseller org (primary org) and mark override.
        person.organization_id = org.id
        person.first_name = (user_first_name or person.first_name).strip() or person.first_name
        person.last_name = (user_last_name or person.last_name).strip() or person.last_name
        if user_phone:
            person.phone = user_phone.strip() or person.phone
        meta = dict(person.metadata_ or {})
        meta["reseller_override"] = True
        meta["is_reseller"] = True
        meta.setdefault("explicit_reseller_signup_at", _now().isoformat())
        person.metadata_ = meta
    else:
        person = Person(
            first_name=(user_first_name or "").strip() or "Reseller",
            last_name=(user_last_name or "").strip() or "User",
            email=email_norm,
            phone=(user_phone or "").strip() or None,
            organization_id=org.id,
            metadata_={"reseller_override": True, "is_reseller": True, "explicit_admin_create_at": _now().isoformat()},
        )
        db.add(person)
        db.flush()

    # Ensure credential exists (and optionally reset).
    credential = (
        db.query(UserCredential)
        .filter(UserCredential.person_id == person.id)
        .filter(UserCredential.provider == AuthProvider.local)
        .filter(UserCredential.is_active.is_(True))
        .first()
    )
    if not credential:
        if not password:
            raise HTTPException(status_code=400, detail="Password required for new user")
        db.add(
            UserCredential(
                person_id=person.id,
                provider=AuthProvider.local,
                username=email_norm,
                password_hash=hash_password(password),
                password_updated_at=_now(),
                is_active=True,
            )
        )
    elif reset_password_if_exists and password:
        credential.password_hash = hash_password(password)
        credential.password_updated_at = _now()

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Reseller creation conflict") from exc

    db.refresh(org)
    db.refresh(person)

    # Ensure reseller RBAC role exists and is granted to the login person.
    role = db.query(Role).filter(Role.name == RESELLER_ROLE_ADMIN).first()
    if not role:
        role = Role(name=RESELLER_ROLE_ADMIN, description="Reseller portal administrator", is_active=True)
        db.add(role)
        db.flush()
    existing_link = (
        db.query(PersonRole).filter(PersonRole.person_id == person.id).filter(PersonRole.role_id == role.id).first()
    )
    if not existing_link:
        db.add(PersonRole(person_id=person.id, role_id=role.id))
        db.commit()
        db.refresh(person)

    return org, person
