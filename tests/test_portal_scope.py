"""Actor-aware portal scoping: subscriber sees self; reseller sees its subtree."""

from __future__ import annotations

import uuid

from app.models.person import Person
from app.models.subscriber import AccountType, Organization, Subscriber
from app.services.crm import portal_scope
from app.services.portal_auth import PortalPrincipal


def _org(db, name, account_type, parent_id=None):
    org = Organization(name=name, account_type=account_type, parent_id=parent_id)
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def _person(db, org_id=None):
    p = Person(first_name="C", last_name="R", email=f"p-{uuid.uuid4().hex[:8]}@example.com", organization_id=org_id)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _subscriber(db, org_id, person_id):
    s = Subscriber(
        organization_id=org_id, person_id=person_id, external_system="selfcare", external_id=uuid.uuid4().hex
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _principal(actor, subject_id):
    return PortalPrincipal(subject_id=str(subject_id), actor=actor, scopes=["quotes:read"])


def test_subscriber_scope_is_self_only(db_session):
    org = _org(db_session, "Direct", AccountType.customer)
    person = _person(db_session, org.id)
    sub = _subscriber(db_session, org.id, person.id)

    ids = portal_scope.resolve_subscriber_ids(db_session, _principal("subscriber", sub.id))
    assert ids == [str(sub.id)]


def test_reseller_scope_spans_org_subtree(db_session):
    reseller = _org(db_session, "Reseller X", AccountType.reseller)
    child = _org(db_session, "Customer Y", AccountType.customer, parent_id=reseller.id)
    # An unrelated org's subscriber must NOT leak into the reseller's scope.
    other = _org(db_session, "Other Z", AccountType.customer)

    p1 = _person(db_session, reseller.id)
    p2 = _person(db_session, child.id)
    p3 = _person(db_session, other.id)
    s1 = _subscriber(db_session, reseller.id, p1.id)
    s2 = _subscriber(db_session, child.id, p2.id)
    _subscriber(db_session, other.id, p3.id)

    sub_ids = set(portal_scope.resolve_subscriber_ids(db_session, _principal("reseller", reseller.id)))
    assert sub_ids == {str(s1.id), str(s2.id)}

    # Person ids resolve through the same subtree (quotes key on person_id).
    person_ids = set(portal_scope.resolve_person_ids(db_session, _principal("reseller", reseller.id)))
    assert person_ids == {str(p1.id), str(p2.id)}


def test_reseller_target_resolution_enforces_scope(db_session):
    import pytest
    from fastapi import HTTPException

    reseller = _org(db_session, "Reseller X", AccountType.reseller)
    child = _org(db_session, "Customer Y", AccountType.customer, parent_id=reseller.id)
    outside = _org(db_session, "Outside", AccountType.customer)
    p_in = _person(db_session, child.id)
    p_out = _person(db_session, outside.id)
    s_in = _subscriber(db_session, child.id, p_in.id)
    s_out = _subscriber(db_session, outside.id, p_out.id)

    principal = _principal("reseller", reseller.id)
    # In-subtree target resolves.
    assert portal_scope.resolve_target_subscriber(db_session, principal, str(s_in.id)).id == s_in.id
    # Out-of-subtree target is rejected.
    with pytest.raises(HTTPException) as exc:
        portal_scope.resolve_target_subscriber(db_session, principal, str(s_out.id))
    assert exc.value.status_code == 403
    # Reseller without a target is rejected (must name the customer).
    with pytest.raises(HTTPException) as exc2:
        portal_scope.resolve_target_subscriber(db_session, principal, None)
    assert exc2.value.status_code == 422
