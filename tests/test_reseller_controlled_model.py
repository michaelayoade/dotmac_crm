import uuid

import pytest
from fastapi import HTTPException

from app.models.organization_membership import OrganizationMembership
from app.models.person import Person
from app.models.rbac import PersonRole, Role
from app.models.subscriber import AccountType, Organization
from app.services import reseller_admin as reseller_admin_service
from app.services import reseller_portal as reseller_portal_service
from app.services.reseller_portal import RESELLER_ROLE_ADMIN


def _make_org(db_session, *, name: str, account_type: AccountType) -> Organization:
    org = Organization(name=name, account_type=account_type, is_active=True)
    db_session.add(org)
    db_session.commit()
    db_session.refresh(org)
    return org


def _make_person(db_session, *, email: str, org_id: uuid.UUID) -> Person:
    person = Person(
        first_name="Test",
        last_name="User",
        email=email.lower(),
        organization_id=org_id,
        is_active=True,
    )
    db_session.add(person)
    db_session.commit()
    db_session.refresh(person)
    return person


def _make_platform_admin(db_session) -> Person:
    # Create a global platform admin user (not tied to a specific org for this test).
    org = Organization(name="Platform", account_type=AccountType.other, is_active=True)
    db_session.add(org)
    db_session.commit()
    db_session.refresh(org)

    admin = _make_person(db_session, email=f"admin-{uuid.uuid4().hex}@example.com", org_id=org.id)
    role = Role(name="admin", description="Platform admin", is_active=True)
    db_session.add(role)
    db_session.commit()
    db_session.refresh(role)
    db_session.add(PersonRole(person_id=admin.id, role_id=role.id))
    db_session.commit()
    return admin


def test_admin_promotion_sets_account_type_and_grants_reseller_role(db_session):
    actor = _make_platform_admin(db_session)
    org = _make_org(db_session, name="Acme", account_type=AccountType.customer)
    person = _make_person(db_session, email=f"user-{uuid.uuid4().hex}@example.com", org_id=org.id)

    updated_org, grantee_ids = reseller_admin_service.promote_organization_to_reseller(
        db_session,
        organization_id=org.id,
        actor_person_id=actor.id,
    )

    assert updated_org.account_type == AccountType.reseller
    assert person.id in grantee_ids

    role = db_session.query(Role).filter(Role.name == RESELLER_ROLE_ADMIN).first()
    assert role is not None
    link = (
        db_session.query(PersonRole)
        .filter(PersonRole.person_id == person.id)
        .filter(PersonRole.role_id == role.id)
        .first()
    )
    assert link is not None


def test_reseller_portal_access_requires_role_and_reseller_org(db_session):
    org = _make_org(db_session, name="Reseller Org", account_type=AccountType.reseller)
    person = _make_person(db_session, email=f"reseller-{uuid.uuid4().hex}@example.com", org_id=org.id)

    with pytest.raises(HTTPException) as exc:
        reseller_portal_service.ensure_reseller_portal_access(db_session, person_id=person.id)
    assert exc.value.status_code == 403

    # Grant reseller role and try again.
    role = db_session.query(Role).filter(Role.name == RESELLER_ROLE_ADMIN).first()
    if not role:
        role = Role(name=RESELLER_ROLE_ADMIN, description="Reseller portal administrator", is_active=True)
        db_session.add(role)
        db_session.commit()
        db_session.refresh(role)
    db_session.add(PersonRole(person_id=person.id, role_id=role.id))
    db_session.commit()

    reseller_org = reseller_portal_service.ensure_reseller_portal_access(db_session, person_id=person.id)
    assert reseller_org.id == org.id


def test_reseller_admin_can_create_child_org_and_membership_is_created(db_session):
    reseller_org = _make_org(db_session, name="Reseller Org", account_type=AccountType.reseller)
    person = _make_person(db_session, email=f"reseller-{uuid.uuid4().hex}@example.com", org_id=reseller_org.id)

    role = Role(name=RESELLER_ROLE_ADMIN, description="Reseller portal administrator", is_active=True)
    db_session.add(role)
    db_session.commit()
    db_session.refresh(role)
    db_session.add(PersonRole(person_id=person.id, role_id=role.id))
    db_session.commit()

    child = reseller_portal_service.create_child_organization(
        db_session,
        actor_person_id=person.id,
        name="Customer One",
        domain="customer-one.test",
    )

    assert child.parent_id == reseller_org.id
    assert child.account_type == AccountType.customer

    membership = (
        db_session.query(OrganizationMembership)
        .filter(OrganizationMembership.person_id == person.id)
        .filter(OrganizationMembership.organization_id == child.id)
        .first()
    )
    assert membership is not None
    assert membership.is_active is True


def test_demotion_blocks_portal_and_removes_reseller_role(db_session):
    actor = _make_platform_admin(db_session)
    org = _make_org(db_session, name="Reseller Org", account_type=AccountType.reseller)
    person = _make_person(db_session, email=f"reseller-{uuid.uuid4().hex}@example.com", org_id=org.id)

    role = Role(name=RESELLER_ROLE_ADMIN, description="Reseller portal administrator", is_active=True)
    db_session.add(role)
    db_session.commit()
    db_session.refresh(role)
    db_session.add(PersonRole(person_id=person.id, role_id=role.id))
    db_session.commit()

    reseller_portal_service.ensure_reseller_portal_access(db_session, person_id=person.id)

    updated_org, removed = reseller_admin_service.demote_organization_from_reseller(
        db_session,
        organization_id=org.id,
        actor_person_id=actor.id,
    )
    assert updated_org.account_type == AccountType.customer
    assert person.id in removed

    with pytest.raises(HTTPException) as exc:
        reseller_portal_service.ensure_reseller_portal_access(db_session, person_id=person.id)
    assert exc.value.status_code == 403
