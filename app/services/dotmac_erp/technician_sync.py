"""Sync service for pulling technicians from DotMac ERP (departments roster).

Rule: ERP employees in department "Projects" are treated as technicians.
Behavior: Upsert technician profiles for eligible employees; deactivate ERP-linked
technicians that are no longer eligible.

Optional: If an HR API key is configured, enrich technician title from the ERP HR
employee detail endpoint (designation/title).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.dispatch import TechnicianProfile
from app.models.domain_settings import SettingDomain
from app.models.person import Person
from app.services import settings_spec
from app.services.dotmac_erp.client import DotMacERPClient

logger = logging.getLogger(__name__)


@dataclass
class TechnicianSyncResult:
    """Result of a technician sync operation."""

    persons_created: int = 0
    persons_updated: int = 0
    technicians_created: int = 0
    technicians_updated: int = 0
    technicians_reactivated: int = 0
    technicians_deactivated: int = 0
    employees_seen: int = 0
    employees_eligible: int = 0
    errors: list[dict] = field(default_factory=list)
    duration_seconds: float = 0.0

    @property
    def total_synced(self) -> int:
        return self.persons_created + self.persons_updated + self.technicians_created + self.technicians_updated

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0


class DotMacERPTechnicianSync:
    """Pull ERP employees and sync eligible technicians into TechnicianProfile."""

    def __init__(self, db: Session):
        self.db = db
        self._client: DotMacERPClient | None = None
        self._person_cache_by_email: dict[str, Person | None] = {}

    def _get_client(self) -> DotMacERPClient | None:
        """Get configured ERP client, or None if not configured/enabled."""
        if self._client is not None:
            return self._client

        enabled = settings_spec.resolve_value(self.db, SettingDomain.integration, "dotmac_erp_technician_sync_enabled")
        if not enabled:
            return None

        base_url_value = settings_spec.resolve_value(self.db, SettingDomain.integration, "dotmac_erp_base_url")
        token_value = settings_spec.resolve_value(self.db, SettingDomain.integration, "dotmac_erp_token")

        base_url = str(base_url_value) if base_url_value else None
        token = str(token_value) if token_value else None
        if not base_url or not token:
            logger.warning("DotMac ERP technician sync enabled but not configured (missing URL or token)")
            return None

        timeout_value = settings_spec.resolve_value(self.db, SettingDomain.integration, "dotmac_erp_timeout_seconds")
        timeout = int(timeout_value) if isinstance(timeout_value, int | str) else 30

        self._client = DotMacERPClient(base_url=base_url, token=token, timeout=timeout)
        return self._client

    def _get_hr_api_key(self) -> str | None:
        value = settings_spec.resolve_value(self.db, SettingDomain.integration, "dotmac_erp_hr_api_key")
        key = str(value).strip() if value else ""
        return key or None

    def close(self):
        if self._client:
            self._client.close()
            self._client = None

    @staticmethod
    def _normalize(value: str | None) -> str:
        return (value or "").strip().casefold()

    def _technician_department_name(self) -> str:
        value = settings_spec.resolve_value(self.db, SettingDomain.integration, "dotmac_erp_technician_sync_department")
        return (str(value).strip() if value else "Projects") or "Projects"

    def _resolve_department_members(self, departments: list[dict]) -> tuple[dict | None, list[dict]]:
        """Return (department, active_members) for the configured technician department name."""
        target = self._normalize(self._technician_department_name())
        for dept in departments:
            name = self._normalize(dept.get("department_name") or dept.get("name"))
            if not name:
                continue
            if name == target:
                members = dept.get("members") or []
                if not isinstance(members, list):
                    members = []
                active_members = [m for m in members if isinstance(m, dict) and m.get("is_active", True)]
                return dept, active_members
        return None, []

    @staticmethod
    def _split_name(full_name: str | None) -> tuple[str, str, str | None]:
        name = (full_name or "").strip()
        if not name:
            return ("Unknown", "Unknown", None)
        parts = [p for p in name.split(" ") if p]
        if len(parts) == 1:
            return (parts[0][:80], "Unknown", name[:120])
        first = parts[0][:80]
        last = " ".join(parts[1:])[:80] or "Unknown"
        return (first, last, name[:120])

    @staticmethod
    def _extract_designation(hr_employee: dict) -> str | None:
        # Be defensive about ERP payload shape.
        for key in ("designation", "designation_name", "job_title", "title", "position"):
            val = hr_employee.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()

        val = hr_employee.get("designation")
        if isinstance(val, dict):
            for key in ("designation_name", "name", "title"):
                inner = val.get(key)
                if isinstance(inner, str) and inner.strip():
                    return inner.strip()

        return None

    def _fetch_hr_employee_designations(
        self,
        employee_ids: set[str],
        result: TechnicianSyncResult,
    ) -> dict[str, str]:
        """Optional: fetch employee designation/title from ERP HR endpoints.

        The standard CRM sync API key typically cannot access HR endpoints (401/403).
        If `dotmac_erp_hr_api_key` is set and authorized, we will map the HR designation
        into TechnicianProfile.title.
        """
        hr_key = self._get_hr_api_key()
        if not hr_key:
            return {}

        base_url_value = settings_spec.resolve_value(self.db, SettingDomain.integration, "dotmac_erp_base_url")
        base_url = str(base_url_value).strip() if base_url_value else ""
        if not base_url:
            return {}

        timeout_value = settings_spec.resolve_value(self.db, SettingDomain.integration, "dotmac_erp_timeout_seconds")
        timeout = int(timeout_value) if isinstance(timeout_value, int | str) else 30

        designations: dict[str, str] = {}
        unauthorized = False

        with httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers={"X-API-Key": hr_key, "Accept": "application/json"},
        ) as client:
            for employee_id in sorted(employee_ids):
                try:
                    r = client.get(f"/api/v1/people/hr/employees/{employee_id}")
                    if r.status_code in (401, 403):
                        unauthorized = True
                        break
                    if r.status_code != 200:
                        continue
                    data = r.json()
                    if not isinstance(data, dict):
                        continue
                    designation = self._extract_designation(data)
                    if designation:
                        designations[employee_id] = designation
                except Exception:
                    continue

        if unauthorized:
            result.errors.append({"type": "auth", "error": "hr_employee_detail_unauthorized"})
            return {}

        return designations

    def _get_or_create_person(self, emp: dict, result: TechnicianSyncResult) -> Person | None:
        email_raw = (emp.get("email") or "").strip().lower()
        if not email_raw:
            result.errors.append(
                {"type": "data", "error": "employee_missing_email", "employee_id": emp.get("employee_id")}
            )
            return None

        if email_raw in self._person_cache_by_email:
            return self._person_cache_by_email[email_raw]

        person = self.db.query(Person).filter(func.lower(Person.email) == email_raw).first()
        full_name = emp.get("full_name")
        designation = emp.get("designation")
        department = emp.get("department")

        if not person:
            first_name, last_name, display_name = self._split_name(full_name)
            person = Person(
                first_name=first_name,
                last_name=last_name,
                display_name=display_name,
                email=email_raw,
                job_title=(str(designation).strip()[:120] if designation else None),
                is_active=True,
            )
            # Store ERP hints for traceability without creating new columns.
            person.metadata_ = {
                "dotmac_erp": {
                    "employee_id": emp.get("employee_id"),
                    "department": department,
                    "designation": designation,
                    "full_name": full_name,
                }
            }
            self.db.add(person)
            self.db.flush()
            result.persons_created += 1
        else:
            # Keep updates light; do not overwrite user-managed fields aggressively.
            updated = False
            if full_name and not person.display_name:
                person.display_name = str(full_name)[:120]
                updated = True
            if designation and not person.job_title:
                person.job_title = str(designation).strip()[:120]
                updated = True
            if updated:
                result.persons_updated += 1

        self._person_cache_by_email[email_raw] = person
        return person

    def _upsert_technician(self, emp: dict, person: Person, result: TechnicianSyncResult) -> None:
        employee_id = (emp.get("employee_id") or "").strip()
        if not employee_id:
            result.errors.append({"type": "data", "error": "employee_missing_id", "email": person.email})
            return

        # Prefer matching by ERP employee ID if present; otherwise link by person.
        tech = self.db.query(TechnicianProfile).filter(TechnicianProfile.erp_employee_id == employee_id).first()
        if not tech:
            tech = self.db.query(TechnicianProfile).filter(TechnicianProfile.person_id == person.id).first()

        designation = emp.get("designation")
        designation_id = emp.get("designation_id")
        department = emp.get("department")
        full_name = emp.get("full_name")

        if not tech:
            tech = TechnicianProfile(
                person_id=person.id,
                title=(str(designation).strip()[:120] if designation else None),
                region=None,
                erp_employee_id=employee_id,
                metadata_={
                    "dotmac_erp": {
                        "department": department,
                        "designation": designation,
                        "designation_id": designation_id,
                        "full_name": full_name,
                        "email": person.email,
                        "last_synced_at": datetime.now(UTC).isoformat(),
                    }
                },
                is_active=True,
            )
            self.db.add(tech)
            result.technicians_created += 1
            return

        # Update existing
        was_inactive = not bool(tech.is_active)
        tech.person_id = person.id
        tech.erp_employee_id = employee_id
        tech.title = str(designation).strip()[:120] if designation else tech.title
        tech.is_active = True

        meta: dict[str, object] = tech.metadata_ if isinstance(tech.metadata_, dict) else {}
        raw_dotmac = meta.get("dotmac_erp")
        meta_dotmac: dict[str, object] = raw_dotmac if isinstance(raw_dotmac, dict) else {}
        meta_dotmac.update(
            {
                "department": department,
                "designation": designation,
                "designation_id": designation_id,
                "full_name": full_name,
                "email": person.email,
                "last_synced_at": datetime.now(UTC).isoformat(),
            }
        )
        meta["dotmac_erp"] = meta_dotmac
        tech.metadata_ = meta

        if was_inactive:
            result.technicians_reactivated += 1
        else:
            result.technicians_updated += 1

    def sync_all(self, limit: int = 500) -> TechnicianSyncResult:
        """Sync technicians from ERP workforce departments feed."""
        start = datetime.now(UTC)
        result = TechnicianSyncResult()

        client = self._get_client()
        if not client:
            result.errors.append({"type": "config", "error": "ERP technician sync not configured or disabled"})
            return result

        # Fetch departments (the only workforce endpoint confirmed available in this ERP deployment).
        departments: list[dict] = []
        offset = 0
        max_pages = 50  # safety guard
        pages = 0
        try:
            while pages < max_pages:
                batch = client.get_departments(include_inactive=True, limit=limit, offset=offset)
                if not batch:
                    break
                departments.extend(batch)
                if len(batch) < limit:
                    break
                offset += limit
                pages += 1
        except Exception as e:
            result.errors.append({"type": "api", "error": str(e)})
            result.duration_seconds = (datetime.now(UTC) - start).total_seconds()
            return result

        dept, members = self._resolve_department_members(departments)
        if not dept:
            # Department missing means no employees are currently eligible; continue so
            # previously linked technicians can be deactivated.
            result.errors.append(
                {
                    "type": "data",
                    "error": "technician_department_not_found",
                    "department_name": self._technician_department_name(),
                }
            )
            members = []

        # Treat members of the department as eligible employees.
        result.employees_seen = len(members)
        result.employees_eligible = len(members)
        eligible_employee_ids: set[str] = set()

        for emp in members:
            employee_id = (emp.get("employee_id") or "").strip()
            if employee_id:
                eligible_employee_ids.add(employee_id)

        # Optional enrichment: designation/title via HR endpoint. If present, it overrides any
        # designation fields coming from the roster payload.
        designation_by_employee_id = self._fetch_hr_employee_designations(eligible_employee_ids, result)

        for emp in members:
            employee_id = (emp.get("employee_id") or "").strip()
            designation = designation_by_employee_id.get(employee_id)
            if not designation:
                # Future-proof: if the roster payload is extended in ERP to include designation,
                # we will pick it up automatically.
                designation = (
                    emp.get("designation") or emp.get("designation_name") or emp.get("job_title") or emp.get("title")
                )
            # Enrich member with department context for metadata/debugging.
            assert dept is not None  # narrowed by if-not-dept guard above
            emp = {
                **emp,
                "department": dept.get("department_name") or dept.get("name"),
                "department_id": dept.get("department_id"),
                "department_type": dept.get("department_type"),
                "designation": designation,
            }
            person = self._get_or_create_person(emp, result)
            if not person:
                continue
            self._upsert_technician(emp, person, result)

        # Deactivate ERP-linked technicians that are no longer eligible.
        # We only act on technicians that have erp_employee_id set, leaving manual/unlinked technicians alone.
        active_linked = (
            self.db.query(TechnicianProfile)
            .filter(TechnicianProfile.is_active.is_(True))
            .filter(TechnicianProfile.erp_employee_id.isnot(None))
            .all()
        )
        for tech in active_linked:
            if not tech.erp_employee_id:
                continue
            if tech.erp_employee_id not in eligible_employee_ids:
                tech.is_active = False
                result.technicians_deactivated += 1

        self.db.commit()
        result.duration_seconds = (datetime.now(UTC) - start).total_seconds()
        return result


def dotmac_erp_technician_sync(db: Session) -> DotMacERPTechnicianSync:
    return DotMacERPTechnicianSync(db)
