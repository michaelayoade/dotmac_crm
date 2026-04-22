from __future__ import annotations

import pytest

from app.models.person import ChannelType, Person
from app.models.rbac import PersonRole, Role
from app.models.subscriber import AccountType, Organization
from app.schemas.crm.contact import ContactCreate
from app.services.crm.contacts.service import Contacts
from app.services.crm.web_contacts import (
    ContactUpsertInput,
)
from app.services.crm.web_contacts import (
    create_contact as create_web_contact,
)
from app.services.crm.web_contacts import (
    update_contact as update_web_contact,
)
from app.services.reseller_contact_policy import (
    COMM_OWNER_ORG_ID_METADATA_KEY,
    CONTACT_IDENTITY_MODE_METADATA_KEY,
    CONTACT_IDENTITY_MODE_RESELLER_PROXY,
    RESELLER_CONTACT_METADATA_KEY,
)
from app.services.reseller_portal import RESELLER_ROLE_ADMIN
from app.services.reseller_portal import create_contact as create_reseller_contact


def _make_org(db_session, *, name: str, account_type: AccountType, parent_id=None) -> Organization:
    org = Organization(name=name, account_type=account_type, parent_id=parent_id, is_active=True)
    db_session.add(org)
    db_session.commit()
    db_session.refresh(org)
    return org


def _make_person(db_session, *, email: str, org_id, phone: str | None = None) -> Person:
    person = Person(
        first_name="Test",
        last_name="User",
        email=email,
        phone=phone,
        organization_id=org_id,
        is_active=True,
    )
    db_session.add(person)
    db_session.commit()
    db_session.refresh(person)
    return person


def test_web_create_reseller_linked_contact_assigns_placeholder_and_null_phone(db_session):
    reseller_org = _make_org(db_session, name="Reseller", account_type=AccountType.reseller)
    child_org = _make_org(db_session, name="Child", account_type=AccountType.customer, parent_id=reseller_org.id)

    create_web_contact(
        db_session,
        ContactUpsertInput(
            display_name="Site Contact",
            emails=["real.person@example.com"],
            phones=["+15550001111"],
            address_line1="12 Site Street",
            city="Berlin",
            organization_id=str(child_org.id),
            is_active="true",
        ),
    )

    person = (
        db_session.query(Person)
        .filter(Person.organization_id == child_org.id)
        .order_by(Person.created_at.desc())
        .first()
    )
    assert person is not None
    assert person.email.endswith("@reseller.dotmac.ng")
    assert person.phone is None
    assert person.address_line1 == "12 Site Street"
    assert person.city == "Berlin"
    assert isinstance(person.metadata_, dict)
    assert person.metadata_[RESELLER_CONTACT_METADATA_KEY] is True
    assert person.metadata_[COMM_OWNER_ORG_ID_METADATA_KEY] == str(reseller_org.id)
    assert person.metadata_[CONTACT_IDENTITY_MODE_METADATA_KEY] == CONTACT_IDENTITY_MODE_RESELLER_PROXY


def test_web_update_reseller_linked_contact_enforces_placeholder_and_null_phone(db_session):
    reseller_org = _make_org(db_session, name="Reseller", account_type=AccountType.reseller)
    child_org = _make_org(db_session, name="Child", account_type=AccountType.customer, parent_id=reseller_org.id)
    person = _make_person(
        db_session,
        email="before@example.com",
        phone="+15550002222",
        org_id=child_org.id,
    )

    update_web_contact(
        db_session,
        str(person.id),
        ContactUpsertInput(
            display_name="Updated Contact",
            emails=["should.not.persist@example.com"],
            phones=["+15559990000"],
            address_line1="22 Updated Ave",
            organization_id=str(child_org.id),
            is_active="true",
        ),
    )

    db_session.refresh(person)
    assert person.email.endswith("@reseller.dotmac.ng")
    assert person.phone is None
    assert person.address_line1 == "22 Updated Ave"
    assert isinstance(person.metadata_, dict)
    assert person.metadata_[RESELLER_CONTACT_METADATA_KEY] is True
    assert person.metadata_[COMM_OWNER_ORG_ID_METADATA_KEY] == str(reseller_org.id)


def test_contacts_api_create_reseller_linked_overrides_email_and_phone(db_session):
    reseller_org = _make_org(db_session, name="Reseller", account_type=AccountType.reseller)
    child_org = _make_org(db_session, name="Child", account_type=AccountType.customer, parent_id=reseller_org.id)

    person = Contacts.create(
        db_session,
        ContactCreate(
            first_name="API",
            last_name="Contact",
            email="api.real@example.com",
            phone="+15550123456",
            organization_id=child_org.id,
            address_line1="10 Api Road",
        ),
    )

    assert person.email.endswith("@reseller.dotmac.ng")
    assert person.phone is None
    assert isinstance(person.metadata_, dict)
    assert person.metadata_[RESELLER_CONTACT_METADATA_KEY] is True
    assert person.metadata_[COMM_OWNER_ORG_ID_METADATA_KEY] == str(reseller_org.id)


def test_non_reseller_create_keeps_duplicate_email_validation(db_session):
    customer_org = _make_org(db_session, name="Customer", account_type=AccountType.customer)
    _make_person(db_session, email="dup@example.com", org_id=customer_org.id)

    with pytest.raises(ValueError, match="Email already belongs to another contact"):
        create_web_contact(
            db_session,
            ContactUpsertInput(
                display_name="Duplicate Email",
                emails=["dup@example.com"],
                organization_id=str(customer_org.id),
                is_active="true",
            ),
        )


def test_reseller_portal_create_contact_uses_placeholder_and_metadata(db_session):
    reseller_org = _make_org(db_session, name="Reseller", account_type=AccountType.reseller)
    child_org = _make_org(db_session, name="Child", account_type=AccountType.customer, parent_id=reseller_org.id)
    actor = _make_person(db_session, email="actor@example.com", org_id=reseller_org.id)

    role = db_session.query(Role).filter(Role.name == RESELLER_ROLE_ADMIN).first()
    if role is None:
        role = Role(name=RESELLER_ROLE_ADMIN, description="Reseller admin", is_active=True)
        db_session.add(role)
        db_session.commit()
        db_session.refresh(role)
    db_session.add(PersonRole(person_id=actor.id, role_id=role.id))
    db_session.commit()

    person = create_reseller_contact(
        db_session,
        actor_person_id=actor.id,
        organization_id=child_org.id,
        first_name="Portal",
        last_name="Contact",
        email=None,
        phone="+15554443333",
    )

    assert person.email.endswith("@reseller.dotmac.ng")
    assert person.phone is None
    assert isinstance(person.metadata_, dict)
    assert person.metadata_["created_by"] == "reseller_portal"
    assert person.metadata_[RESELLER_CONTACT_METADATA_KEY] is True
    assert person.metadata_[COMM_OWNER_ORG_ID_METADATA_KEY] == str(reseller_org.id)
    assert person.metadata_[CONTACT_IDENTITY_MODE_METADATA_KEY] == CONTACT_IDENTITY_MODE_RESELLER_PROXY
    assert not any(channel.channel_type == ChannelType.phone for channel in (person.channels or []))
