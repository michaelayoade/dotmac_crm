"""Person.merge must reassign customer-subject FKs (reseller commissions,
referrals, ...) — not just channels/leads/quotes/conversations/subscribers.

Actor/audit/auth references are intentionally NOT moved (covered by the
service comment); this locks in the customer-subject reassignments.
"""

import uuid
from decimal import Decimal

from app.models.crm.referral import Referral
from app.models.person import Person
from app.models.reseller_commission import ResellerCommission
from app.models.subscriber import Organization
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
