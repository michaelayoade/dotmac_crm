"""Person.merge must reassign customer-subject FKs (reseller commissions,
referrals, ...) — not just channels/leads/quotes/conversations/subscribers.

Actor/audit/auth references are intentionally NOT moved (covered by the
service comment); this locks in the customer-subject reassignments.
"""

import uuid
from datetime import date
from decimal import Decimal

from app.models.comms import Survey, SurveyInvitation, SurveyResponse
from app.models.crm.referral import Referral
from app.models.organization_membership import OrganizationMembership
from app.models.person import Person
from app.models.reseller_commission import ResellerCommission
from app.models.sales_order import SalesOrder
from app.models.subscriber import Organization
from app.models.subscriber_outreach import SubscriberOfflineOutreachLog
from app.models.tickets import Ticket
from app.services.person import people


def _person(db) -> Person:
    p = Person(first_name="X", last_name="Y", email=f"p-{uuid.uuid4().hex[:10]}@example.com")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def test_merge_reassigns_reseller_commissions_and_referrals(db_session):
    source = _person(db_session)
    target = _person(db_session)
    other = _person(db_session)
    org = Organization(name="Reseller Org")
    db_session.add(org)
    db_session.commit()

    ref_referrer = Referral(referrer_person_id=source.id)
    ref_referred = Referral(referrer_person_id=other.id, referred_person_id=source.id)
    commission = ResellerCommission(reseller_org_id=org.id, person_id=source.id, amount=Decimal("10.00"))
    db_session.add_all([ref_referrer, ref_referred, commission])
    db_session.commit()

    people.merge(db_session, source.id, target.id)

    db_session.refresh(ref_referrer)
    db_session.refresh(ref_referred)
    db_session.refresh(commission)
    assert ref_referrer.referrer_person_id == target.id  # referrer moved
    assert ref_referred.referred_person_id == target.id  # referred moved
    assert commission.person_id == target.id  # reseller commission moved


def test_merge_reassigns_ticket_and_sales_order_subjects(db_session):
    source = _person(db_session)
    target = _person(db_session)

    ticket = Ticket(title="Line down", customer_person_id=source.id)
    order = SalesOrder(person_id=source.id)
    db_session.add_all([ticket, order])
    db_session.commit()

    people.merge(db_session, source.id, target.id)

    db_session.refresh(ticket)
    db_session.refresh(order)
    assert ticket.customer_person_id == target.id
    assert order.person_id == target.id


def test_merge_reassigns_survey_and_outreach_subjects(db_session):
    source = _person(db_session)
    target = _person(db_session)

    survey = Survey(name="CSAT")
    db_session.add(survey)
    db_session.commit()
    response = SurveyResponse(survey_id=survey.id, person_id=source.id)
    invitation = SurveyInvitation(
        survey_id=survey.id, person_id=source.id, token=uuid.uuid4().hex, email="c@example.com"
    )
    outreach = SubscriberOfflineOutreachLog(
        person_id=source.id,
        run_local_date=date(2026, 7, 1),
        external_customer_id="EXT-1",
        decision_status="sent",
    )
    db_session.add_all([response, invitation, outreach])
    db_session.commit()

    people.merge(db_session, source.id, target.id)

    db_session.refresh(response)
    db_session.refresh(invitation)
    db_session.refresh(outreach)
    assert response.person_id == target.id
    assert invitation.person_id == target.id
    assert outreach.person_id == target.id


def test_merge_dedupes_organization_memberships(db_session):
    source = _person(db_session)
    target = _person(db_session)
    org_shared = Organization(name="Shared Org")
    org_only_source = Organization(name="Source-only Org")
    db_session.add_all([org_shared, org_only_source])
    db_session.commit()

    # Both source and target already belong to org_shared -> unique constraint
    # would collide, so the source's duplicate membership must be dropped.
    db_session.add_all(
        [
            OrganizationMembership(organization_id=org_shared.id, person_id=source.id),
            OrganizationMembership(organization_id=org_shared.id, person_id=target.id),
            OrganizationMembership(organization_id=org_only_source.id, person_id=source.id),
        ]
    )
    db_session.commit()

    people.merge(db_session, source.id, target.id)

    target_orgs = {
        m.organization_id
        for m in db_session.query(OrganizationMembership).filter(OrganizationMembership.person_id == target.id)
    }
    # target keeps its shared membership + inherits the source-only one, no dupes.
    assert target_orgs == {org_shared.id, org_only_source.id}
    assert db_session.query(OrganizationMembership).filter(OrganizationMembership.person_id == source.id).count() == 0
