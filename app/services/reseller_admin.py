from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.organization_membership import OrganizationMembership, OrganizationMembershipRole
from app.models.person import Person
from app.models.rbac import PersonRole, Role
from app.models.subscriber import AccountType, Organization
from app.services.reseller_portal import RESELLER_ROLE_ADMIN, RESELLER_ROLE_MEMBER


def _ensure_platform_admin(db: Session, *, actor_person_id: uuid.UUID | None) -> None:
    if not actor_person_id:
        raise HTTPException(status_code=403, detail="Admin required")
    admin_role = db.query(Role).filter(Role.name == "admin").filter(Role.is_active.is_(True)).first()
    if not admin_role:
        raise HTTPException(status_code=403, detail="Admin role missing")
    link = (
        db.query(PersonRole)
        .filter(PersonRole.person_id == actor_person_id)
        .filter(PersonRole.role_id == admin_role.id)
        .first()
    )
    if not link:
        raise HTTPException(status_code=403, detail="Admin required")


def _get_or_create_role(db: Session, *, name: str, description: str) -> Role:
    role = db.query(Role).filter(Role.name == name).first()
    if role:
        if not role.is_active:
            role.is_active = True
        return role
    role = Role(name=name, description=description, is_active=True)
    db.add(role)
    db.flush()
    return role


def _ensure_reseller_roles_exist(db: Session) -> tuple[Role, Role]:
    admin = _get_or_create_role(db, name=RESELLER_ROLE_ADMIN, description="Reseller portal administrator")
    member = _get_or_create_role(db, name=RESELLER_ROLE_MEMBER, description="Reseller portal member")
    return admin, member


def _candidate_org_admin_person_ids(db: Session, *, organization_id: uuid.UUID) -> set[uuid.UUID]:
    """Best-effort "org admins" for role grants.

    We currently don't have a canonical org-admin concept, so:
    - include anyone whose primary org is this org
    - include any explicit membership links with role owner/admin
    """
    person_ids = {pid for (pid,) in db.query(Person.id).filter(Person.organization_id == organization_id).all() if pid}
    membership_ids = {
        pid
        for (pid,) in (
            db.query(OrganizationMembership.person_id)
            .filter(OrganizationMembership.organization_id == organization_id)
            .filter(OrganizationMembership.is_active.is_(True))
            .filter(
                OrganizationMembership.role.in_([OrganizationMembershipRole.owner, OrganizationMembershipRole.admin])
            )
            .all()
        )
        if pid
    }
    return person_ids | membership_ids


def _grant_role(db: Session, *, person_id: uuid.UUID, role_id: uuid.UUID) -> None:
    exists = (
        db.query(PersonRole).filter(PersonRole.person_id == person_id).filter(PersonRole.role_id == role_id).first()
    )
    if exists:
        return
    db.add(PersonRole(person_id=person_id, role_id=role_id))


def _person_has_other_reseller_org(db: Session, *, person: Person, excluding_org_id: uuid.UUID) -> bool:
    if person.organization_id and person.organization_id != excluding_org_id:
        org = db.get(Organization, person.organization_id)
        if org and org.is_active and org.account_type == AccountType.reseller:
            return True
    other_membership_org_ids = [
        oid
        for (oid,) in (
            db.query(OrganizationMembership.organization_id)
            .filter(OrganizationMembership.person_id == person.id)
            .filter(OrganizationMembership.is_active.is_(True))
            .filter(OrganizationMembership.organization_id != excluding_org_id)
            .all()
        )
        if oid
    ]
    if not other_membership_org_ids:
        return False
    other_reseller = (
        db.query(Organization.id)
        .filter(Organization.id.in_(other_membership_org_ids))
        .filter(Organization.is_active.is_(True))
        .filter(Organization.account_type == AccountType.reseller)
        .first()
    )
    return other_reseller is not None


def promote_organization_to_reseller(
    db: Session,
    *,
    organization_id: uuid.UUID,
    actor_person_id: uuid.UUID | None,
) -> tuple[Organization, list[uuid.UUID]]:
    """Admin-controlled promotion: sets account_type=reseller and grants reseller roles."""
    _ensure_platform_admin(db, actor_person_id=actor_person_id)
    org = db.get(Organization, organization_id)
    if not org or not org.is_active:
        raise HTTPException(status_code=404, detail="Organization not found")

    reseller_admin_role, _reseller_member_role = _ensure_reseller_roles_exist(db)

    org.account_type = AccountType.reseller

    grantee_ids = sorted(list(_candidate_org_admin_person_ids(db, organization_id=org.id)))
    for person_id in grantee_ids:
        _grant_role(db, person_id=person_id, role_id=reseller_admin_role.id)
        # member role is optional; keep model simple by granting only admin for now.
        # _grant_role(db, person_id=person_id, role_id=reseller_member_role.id)

    db.commit()
    db.refresh(org)
    return org, grantee_ids


def demote_organization_from_reseller(
    db: Session,
    *,
    organization_id: uuid.UUID,
    actor_person_id: uuid.UUID | None,
) -> tuple[Organization, list[uuid.UUID]]:
    """Admin-controlled demotion: removes reseller roles and blocks reseller portal access."""
    _ensure_platform_admin(db, actor_person_id=actor_person_id)
    org = db.get(Organization, organization_id)
    if not org or not org.is_active:
        raise HTTPException(status_code=404, detail="Organization not found")

    org.account_type = AccountType.customer

    reseller_admin_role, reseller_member_role = _ensure_reseller_roles_exist(db)

    # Candidates: anyone primarily in this org or explicitly a member of it.
    candidate_ids = _candidate_org_admin_person_ids(db, organization_id=org.id)
    candidate_ids |= {
        pid
        for (pid,) in (
            db.query(OrganizationMembership.person_id)
            .filter(OrganizationMembership.organization_id == org.id)
            .filter(OrganizationMembership.is_active.is_(True))
            .all()
        )
        if pid
    }

    removed_from: list[uuid.UUID] = []
    for person_id in sorted(list(candidate_ids)):
        person = db.get(Person, person_id)
        if not person:
            continue
        if _person_has_other_reseller_org(db, person=person, excluding_org_id=org.id):
            continue
        # Remove reseller roles (global roles, so be careful).
        db.query(PersonRole).filter(PersonRole.person_id == person.id).filter(
            PersonRole.role_id.in_([reseller_admin_role.id, reseller_member_role.id])
        ).delete(synchronize_session=False)
        removed_from.append(person.id)

    db.commit()
    db.refresh(org)
    return org, removed_from
