"""Detect CRM <-> DotMac ERP identity mirror drift.

Read-only: this module never mutates CRM or ERP business rows. It only emits
fingerprinted infrastructure-health results for the existing alert lifecycle.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.models.infrastructure import InfrastructureAlertCategory, InfrastructureAlertSeverity
from app.models.person import Person
from app.models.subscriber import Organization
from app.services import settings_spec
from app.services.dotmac_erp.client import DotMacERPClient
from app.services.infrastructure_health import HealthCheckResult
from app.services.secrets import resolve_secret

CHECK_PREFIX = "dotmac_erp_identity"
COMPONENT = "CRM ERP identity mirror"
TARGET_URL = "/admin/system/health/alerts?category=external_integrations"
PAGE_LIMIT = 500
SAMPLE_LIMIT = 20


@dataclass(frozen=True)
class IdentityDriftRun:
    """Summary returned by the scheduled task."""

    checked_at: datetime
    results: list[HealthCheckResult]
    duration_seconds: float

    @property
    def unhealthy(self) -> int:
        return sum(1 for result in self.results if result.creates_alert)

    @property
    def counts_by_check(self) -> dict[str, int]:
        return {result.check_key: int((result.metadata or {}).get("count") or 0) for result in self.results}


def run_identity_drift_detection(db: Session) -> IdentityDriftRun:
    """Collect ERP identity mirror drift as infrastructure-health results."""

    started = datetime.now(UTC)
    client = _get_client(db)
    try:
        if client is None:
            return IdentityDriftRun(
                checked_at=started,
                duration_seconds=(datetime.now(UTC) - started).total_seconds(),
                results=[
                    _result(
                        "config_missing",
                        status="degraded",
                        severity=InfrastructureAlertSeverity.warning,
                        count=1,
                        samples=[],
                        summary="DotMac ERP identity drift check is enabled but ERP contact sync is not configured.",
                        details="Set DotMac ERP base URL/token or disable the identity drift task.",
                    )
                ],
            )

        companies = _fetch_all(client.get_companies)
        contacts = _fetch_all(client.get_contacts)
        results = [
            _result(
                "config_missing",
                status="healthy",
                severity=InfrastructureAlertSeverity.info,
                count=0,
                samples=[],
                summary="DotMac ERP identity drift check is configured.",
                details=None,
            ),
            *collect_identity_drift_results(db, companies=companies, contacts=contacts),
        ]
        return IdentityDriftRun(
            checked_at=started,
            duration_seconds=(datetime.now(UTC) - started).total_seconds(),
            results=results,
        )
    finally:
        if client is not None:
            client.close()


def _get_client(db: Session) -> DotMacERPClient | None:
    base_url_value = settings_spec.resolve_value(db, SettingDomain.integration, "dotmac_erp_base_url")
    token_value = resolve_secret(settings_spec.resolve_value(db, SettingDomain.integration, "dotmac_erp_token"))
    base_url = str(base_url_value) if base_url_value else None
    token = str(token_value) if token_value else None
    if not base_url or not token:
        return None
    timeout_value = settings_spec.resolve_value(db, SettingDomain.integration, "dotmac_erp_timeout_seconds")
    timeout = int(timeout_value) if isinstance(timeout_value, int | str) else 30
    return DotMacERPClient(base_url=base_url, token=token, timeout=timeout)


def _fetch_all(fetch_page) -> list[dict]:
    offset = 0
    rows: list[dict] = []
    while True:
        page = fetch_page(limit=PAGE_LIMIT, offset=offset, include_inactive=True)
        page = page if isinstance(page, list) else []
        rows.extend(item for item in page if isinstance(item, dict))
        if len(page) < PAGE_LIMIT:
            return rows
        offset += PAGE_LIMIT


def collect_identity_drift_results(
    db: Session, *, companies: list[dict], contacts: list[dict]
) -> list[HealthCheckResult]:
    company_by_id, duplicate_company_ids = _index_by_id(companies, "customer_id")
    contact_by_id, duplicate_contact_ids = _index_by_id(contacts, "contact_id")
    active_company_ids = _active_external_ids(company_by_id)
    active_contact_ids = _active_external_ids(contact_by_id)

    org_ids = _local_org_ids(db)
    person_erp_person_ids = _local_person_ids(db, "erp_person_id")
    person_erp_customer_ids = _local_person_ids(db, "erp_customer_id")
    local_contact_ids = set(person_erp_person_ids) | set(person_erp_customer_ids)

    contact_org_mismatches = _contact_org_mismatches(
        db,
        contacts=contacts,
        person_erp_person_ids=person_erp_person_ids,
        person_erp_customer_ids=person_erp_customer_ids,
    )

    checks = [
        _drift_check(
            "erp_duplicate_company_id",
            len(duplicate_company_ids),
            duplicate_company_ids,
            InfrastructureAlertSeverity.critical,
            "ERP returned duplicate customer IDs to CRM.",
            "Fix the duplicate customer IDs in ERP before CRM can have a deterministic mirror.",
        ),
        _drift_check(
            "erp_duplicate_contact_id",
            len(duplicate_contact_ids),
            duplicate_contact_ids,
            InfrastructureAlertSeverity.critical,
            "ERP returned duplicate contact IDs to CRM.",
            "Fix the duplicate contact IDs in ERP before CRM can have a deterministic mirror.",
        ),
        _drift_check(
            "crm_duplicate_org_erp_id",
            *_duplicates(org_ids),
            InfrastructureAlertSeverity.critical,
            "Multiple CRM organizations point at the same ERP customer.",
            "Merge or relink duplicate CRM organizations; Organization.erp_id must identify one row.",
        ),
        _drift_check(
            "crm_duplicate_person_erp_person_id",
            *_duplicates(person_erp_person_ids),
            InfrastructureAlertSeverity.critical,
            "Multiple CRM people point at the same ERP contact.",
            "Merge or relink duplicate CRM people; Person.erp_person_id must identify one row.",
        ),
        _drift_check(
            "crm_duplicate_person_erp_customer_id",
            *_duplicates(person_erp_customer_ids),
            InfrastructureAlertSeverity.critical,
            "Multiple CRM people point at the same ERP customer/contact ID.",
            "Merge or relink duplicate CRM people; Person.erp_customer_id must identify one row.",
        ),
        _drift_check(
            "erp_company_missing_crm_org",
            *(_missing(active_company_ids, org_ids.keys())),
            InfrastructureAlertSeverity.warning,
            "ERP customers are missing from the CRM organization mirror.",
            "Run/retry DotMac ERP contact sync, then investigate records that remain missing.",
        ),
        _drift_check(
            "crm_org_missing_erp_company",
            *(_missing(org_ids.keys(), company_by_id.keys())),
            InfrastructureAlertSeverity.warning,
            "CRM organizations point at ERP customers that ERP no longer returns.",
            "Confirm whether the ERP customer was archived/deleted and tombstone or relink the CRM organization.",
        ),
        _drift_check(
            "erp_contact_missing_crm_person",
            *(_missing(active_contact_ids, local_contact_ids)),
            InfrastructureAlertSeverity.warning,
            "ERP contacts are missing from the CRM people mirror.",
            "Run/retry DotMac ERP contact sync, then investigate records that remain missing.",
        ),
        _drift_check(
            "crm_person_missing_erp_contact",
            *(_missing(local_contact_ids, contact_by_id.keys())),
            InfrastructureAlertSeverity.warning,
            "CRM people point at ERP contacts that ERP no longer returns.",
            "Confirm whether the ERP contact was archived/deleted and tombstone or relink the CRM person.",
        ),
        _drift_check(
            "crm_person_erp_company_mismatch",
            len(contact_org_mismatches),
            contact_org_mismatches,
            InfrastructureAlertSeverity.critical,
            "CRM people are linked to a different organization than their ERP contact company.",
            "Relink the CRM person to the CRM organization whose erp_id matches the ERP contact company_id.",
        ),
    ]
    return checks


def _active_external_ids(rows_by_id: dict[str, dict]) -> set[str]:
    return {external_id for external_id, row in rows_by_id.items() if bool(row.get("is_active", True))}


def _index_by_id(rows: list[dict], field: str) -> tuple[dict[str, dict], list[dict[str, Any]]]:
    by_id: dict[str, dict] = {}
    seen: defaultdict[str, int] = defaultdict(int)
    for row in rows:
        external_id = _clean(row.get(field))
        if not external_id:
            continue
        seen[external_id] += 1
        by_id.setdefault(external_id, row)
    duplicates = [
        {"external_id": external_id, "count": count} for external_id, count in sorted(seen.items()) if count > 1
    ]
    return by_id, duplicates


def _local_org_ids(db: Session) -> dict[str, list[str]]:
    rows = db.query(Organization.id, Organization.erp_id).filter(Organization.erp_id.isnot(None)).all()
    return _group_local_ids(rows)


def _local_person_ids(db: Session, field_name: str) -> dict[str, list[str]]:
    field = getattr(Person, field_name)
    rows = db.query(Person.id, field).filter(field.isnot(None)).all()
    return _group_local_ids(rows)


def _group_local_ids(rows) -> dict[str, list[str]]:
    grouped: defaultdict[str, list[str]] = defaultdict(list)
    for local_id, external_id in rows:
        cleaned = _clean(external_id)
        if cleaned:
            grouped[cleaned].append(str(local_id))
    return dict(grouped)


def _contact_org_mismatches(
    db: Session,
    *,
    contacts: list[dict],
    person_erp_person_ids: dict[str, list[str]],
    person_erp_customer_ids: dict[str, list[str]],
) -> list[dict[str, Any]]:
    org_by_erp_id = {
        _clean(org.erp_id): org
        for org in db.query(Organization).filter(Organization.erp_id.isnot(None)).all()
        if _clean(org.erp_id)
    }
    people_by_id = {
        str(person.id): person
        for person in db.query(Person)
        .filter((Person.erp_person_id.isnot(None)) | (Person.erp_customer_id.isnot(None)))
        .all()
    }
    mismatches: list[dict[str, Any]] = []
    for contact in contacts:
        contact_id = _clean(contact.get("contact_id"))
        company_id = _clean(contact.get("company_id"))
        if not contact_id or not company_id:
            continue
        expected_org = org_by_erp_id.get(company_id)
        if expected_org is None:
            continue
        person_ids = set(person_erp_person_ids.get(contact_id, [])) | set(person_erp_customer_ids.get(contact_id, []))
        for person_id in sorted(person_ids):
            person = people_by_id.get(person_id)
            if person is None:
                continue
            if person.organization_id != expected_org.id:
                mismatches.append(
                    {
                        "contact_id": contact_id,
                        "company_id": company_id,
                        "person_id": person_id,
                        "current_organization_id": str(person.organization_id) if person.organization_id else None,
                        "expected_organization_id": str(expected_org.id),
                    }
                )
    return mismatches


def _duplicates(grouped: dict[str, list[str]]) -> tuple[int, list[dict[str, Any]]]:
    samples = [
        {"external_id": external_id, "crm_ids": crm_ids, "count": len(crm_ids)}
        for external_id, crm_ids in sorted(grouped.items())
        if len(crm_ids) > 1
    ]
    return len(samples), samples


def _missing(left: Any, right: Any) -> tuple[int, list[dict[str, Any]]]:
    missing = sorted(set(left) - set(right))
    return len(missing), [{"external_id": external_id} for external_id in missing]


def _drift_check(
    mismatch_type: str,
    count: int,
    samples: list[dict[str, Any]],
    severity: InfrastructureAlertSeverity,
    summary: str,
    action: str,
) -> HealthCheckResult:
    if count <= 0:
        return _result(
            mismatch_type,
            status="healthy",
            severity=InfrastructureAlertSeverity.info,
            count=0,
            samples=[],
            summary=f"No {mismatch_type.replace('_', ' ')} drift detected.",
            details=None,
        )
    return _result(
        mismatch_type,
        status="unhealthy" if severity == InfrastructureAlertSeverity.critical else "degraded",
        severity=severity,
        count=count,
        samples=samples,
        summary=f"{summary} Count: {count}.",
        details=action,
    )


def _result(
    mismatch_type: str,
    *,
    status: str,
    severity: InfrastructureAlertSeverity,
    count: int,
    samples: list[dict[str, Any]],
    summary: str,
    details: str | None,
) -> HealthCheckResult:
    return HealthCheckResult(
        category=InfrastructureAlertCategory.external_integrations,
        component=COMPONENT,
        check_key=f"{CHECK_PREFIX}_{mismatch_type}",
        status=status,
        severity=severity,
        summary=summary,
        details=details,
        source="dotmac_erp",
        target_url=TARGET_URL,
        metadata={
            "count": count,
            "samples": samples[:SAMPLE_LIMIT],
            "sample_limit": SAMPLE_LIMIT,
            "suggested_owner": "crm_integrations",
            "suggested_action": details,
        },
    )


def _clean(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
