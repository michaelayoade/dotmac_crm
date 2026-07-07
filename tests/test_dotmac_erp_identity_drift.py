"""Tests for CRM <-> DotMac ERP identity drift detection."""

import uuid

from app.models.infrastructure import (
    InfrastructureAlert,
    InfrastructureAlertCategory,
    InfrastructureAlertSeverity,
    InfrastructureAlertStatus,
)
from app.models.person import Person
from app.models.subscriber import Organization
from app.services.dotmac_erp.identity_drift import collect_identity_drift_results
from app.services.infrastructure_health import HealthCheckResult, upsert_alerts_from_results


def _result_by_key(results):
    return {result.check_key: result for result in results}


def _count(result) -> int:
    return int((result.metadata or {}).get("count") or 0)


def _sample_external_ids(result) -> set[str]:
    return {str(sample.get("external_id")) for sample in (result.metadata or {}).get("samples") or []}


def _company(customer_id: str, **overrides):
    return {
        "customer_id": customer_id,
        "customer_name": f"Company {customer_id}",
        "is_active": True,
        **overrides,
    }


def _contact(contact_id: str, *, email: str | None = None, company_id: str | None = None, **overrides):
    return {
        "contact_id": contact_id,
        "email": email or f"{contact_id.lower()}@example.test",
        "company_id": company_id,
        "is_active": True,
        **overrides,
    }


def _person(email: str, *, erp_person_id: str | None = None, erp_customer_id: str | None = None, org=None):
    return Person(
        first_name="Test",
        last_name="Person",
        email=email,
        erp_person_id=erp_person_id,
        erp_customer_id=erp_customer_id,
        organization_id=getattr(org, "id", None),
    )


def test_flags_duplicate_crm_people_for_one_erp_contact(db_session):
    contact_id = f"000-CON-DUP-{uuid.uuid4().hex}"
    db_session.add_all(
        [
            _person(f"{contact_id}-one@example.test", erp_person_id=contact_id),
            _person(f"{contact_id}-two@example.test", erp_person_id=contact_id),
        ]
    )
    db_session.flush()

    results = _result_by_key(
        collect_identity_drift_results(
            db_session,
            companies=[],
            contacts=[_contact(contact_id)],
        )
    )

    duplicate = results["dotmac_erp_identity_crm_duplicate_person_erp_person_id"]
    assert duplicate.creates_alert is True
    assert _count(duplicate) >= 1
    assert contact_id in _sample_external_ids(duplicate)


def test_flags_missing_mirrors_and_wrong_company_link(db_session):
    suffix = uuid.uuid4().hex
    expected_org_id = f"000-ORG-EXPECTED-{suffix}"
    wrong_org_id = f"000-ORG-WRONG-{suffix}"
    stale_org_id = f"000-ORG-STALE-{suffix}"
    erp_only_org_id = f"000-ORG-ERP-ONLY-{suffix}"
    linked_contact_id = f"000-CON-LINKED-{suffix}"
    stale_contact_id = f"000-CON-STALE-{suffix}"
    erp_only_contact_id = f"000-CON-ERP-ONLY-{suffix}"
    expected_org = Organization(name="Expected Org", erp_id=expected_org_id)
    wrong_org = Organization(name="Wrong Org", erp_id=wrong_org_id)
    stale_org = Organization(name="Stale Org", erp_id=stale_org_id)
    db_session.add_all([expected_org, wrong_org, stale_org])
    db_session.flush()
    db_session.add_all(
        [
            _person(f"linked-{suffix}@example.test", erp_person_id=linked_contact_id, org=wrong_org),
            _person(f"stale-{suffix}@example.test", erp_person_id=stale_contact_id),
        ]
    )
    db_session.flush()

    results = _result_by_key(
        collect_identity_drift_results(
            db_session,
            companies=[_company(expected_org_id), _company(erp_only_org_id)],
            contacts=[_contact(linked_contact_id, company_id=expected_org_id), _contact(erp_only_contact_id)],
        )
    )

    assert erp_only_org_id in _sample_external_ids(results["dotmac_erp_identity_erp_company_missing_crm_org"])
    assert stale_org_id in _sample_external_ids(results["dotmac_erp_identity_crm_org_missing_erp_company"])
    assert erp_only_contact_id in _sample_external_ids(results["dotmac_erp_identity_erp_contact_missing_crm_person"])
    assert stale_contact_id in _sample_external_ids(results["dotmac_erp_identity_crm_person_missing_erp_contact"])
    mismatch = results["dotmac_erp_identity_crm_person_erp_company_mismatch"]
    assert mismatch.creates_alert is True
    assert _count(mismatch) >= 1
    assert any(
        sample.get("contact_id") == linked_contact_id and sample.get("company_id") == expected_org_id
        for sample in mismatch.metadata["samples"]
    )


def test_archived_erp_rows_do_not_raise_missing_crm_mirror_noise(db_session):
    suffix = uuid.uuid4().hex
    active_org_id = f"000-ORG-ACTIVE-{suffix}"
    archived_org_id = f"000-ORG-ARCHIVED-{suffix}"
    active_contact_id = f"000-CON-ACTIVE-{suffix}"
    archived_contact_id = f"000-CON-ARCHIVED-{suffix}"
    results = _result_by_key(
        collect_identity_drift_results(
            db_session,
            companies=[
                _company(active_org_id),
                _company(archived_org_id, is_active=False),
            ],
            contacts=[
                _contact(active_contact_id),
                _contact(archived_contact_id, is_active=False),
            ],
        )
    )

    company_missing = results["dotmac_erp_identity_erp_company_missing_crm_org"]
    contact_missing = results["dotmac_erp_identity_erp_contact_missing_crm_person"]
    assert active_org_id in _sample_external_ids(company_missing)
    assert archived_org_id not in _sample_external_ids(company_missing)
    assert active_contact_id in _sample_external_ids(contact_missing)
    assert archived_contact_id not in _sample_external_ids(contact_missing)


def test_alert_lifecycle_resolves_when_drift_disappears(db_session):
    check_key = f"dotmac_erp_identity_test_lifecycle_{uuid.uuid4().hex}"
    active_result = HealthCheckResult(
        category=InfrastructureAlertCategory.external_integrations,
        component="CRM ERP identity mirror",
        check_key=check_key,
        status="unhealthy",
        severity=InfrastructureAlertSeverity.critical,
        summary="Synthetic drift for lifecycle test.",
        metadata={"count": 1, "samples": [{"external_id": "synthetic"}]},
    )
    clean_result = HealthCheckResult(
        category=InfrastructureAlertCategory.external_integrations,
        component="CRM ERP identity mirror",
        check_key=check_key,
        status="healthy",
        severity=InfrastructureAlertSeverity.info,
        summary="Synthetic drift resolved.",
        metadata={"count": 0, "samples": []},
    )

    created = upsert_alerts_from_results(db_session, [active_result])
    assert created["created"] == 1
    alert = db_session.query(InfrastructureAlert).filter(InfrastructureAlert.check_key == check_key).one()
    assert alert.status == InfrastructureAlertStatus.open

    resolved = upsert_alerts_from_results(db_session, [clean_result])
    db_session.refresh(alert)

    assert resolved["resolved"] == 1
    assert alert.status == InfrastructureAlertStatus.resolved
