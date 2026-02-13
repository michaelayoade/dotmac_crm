"""Sync CRM inbox agents from DotMac ERP employees.

Rule: ERP employees in a configured department are treated as CRM agents.
Behavior: Upsert CRM agents for eligible employees; deactivate ERP-linked
agents that are no longer eligible.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.crm.team import CrmAgent
from app.models.domain_settings import SettingDomain
from app.models.person import Person
from app.services import settings_spec
from app.services.dotmac_erp.client import DotMacERPClient, DotMacERPNotFoundError

logger = logging.getLogger(__name__)


@dataclass
class AgentSyncResult:
    """Result of a CRM agent sync operation."""

    persons_created: int = 0
    persons_updated: int = 0
    agents_created: int = 0
    agents_updated: int = 0
    agents_reactivated: int = 0
    agents_deactivated: int = 0
    employees_seen: int = 0
    employees_eligible: int = 0
    errors: list[dict] = field(default_factory=list)
    duration_seconds: float = 0.0

    @property
    def total_synced(self) -> int:
        return self.persons_created + self.persons_updated + self.agents_created + self.agents_updated

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0


class DotMacERPAgentSync:
    """Pull ERP employees and sync eligible CRM agents into CrmAgent."""

    def __init__(self, db: Session):
        self.db = db
        self._client: DotMacERPClient | None = None
        self._person_cache_by_email: dict[str, Person | None] = {}

    def _get_client(self) -> DotMacERPClient | None:
        """Get configured ERP client, or None if not configured/enabled."""
        if self._client is not None:
            return self._client

        enabled = settings_spec.resolve_value(self.db, SettingDomain.integration, "dotmac_erp_agent_sync_enabled")
        if not enabled:
            return None

        base_url_value = settings_spec.resolve_value(self.db, SettingDomain.integration, "dotmac_erp_base_url")
        token_value = settings_spec.resolve_value(self.db, SettingDomain.integration, "dotmac_erp_token")

        base_url = str(base_url_value) if base_url_value else None
        token = str(token_value) if token_value else None
        if not base_url or not token:
            logger.warning("DotMac ERP agent sync enabled but not configured (missing URL or token)")
            return None

        timeout_value = settings_spec.resolve_value(self.db, SettingDomain.integration, "dotmac_erp_timeout_seconds")
        timeout = int(timeout_value) if isinstance(timeout_value, int | str) else 30

        self._client = DotMacERPClient(base_url=base_url, token=token, timeout=timeout)
        return self._client

    def close(self):
        if self._client:
            self._client.close()
            self._client = None

    @staticmethod
    def _normalize(value: str | None) -> str:
        return (value or "").strip().casefold()

    def _agent_department_name(self) -> str:
        value = settings_spec.resolve_value(self.db, SettingDomain.integration, "dotmac_erp_agent_sync_department")
        return (str(value).strip() if value else "Customer Experience") or "Customer Experience"

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
    def _extract_department_name(employee: dict) -> str | None:
        raw = employee.get("department") or employee.get("department_name")
        if isinstance(raw, str):
            return raw.strip()
        if isinstance(raw, dict):
            name = raw.get("department_name") or raw.get("name")
            if isinstance(name, str):
                return name.strip()
        return None

    @staticmethod
    def _extract_designation(employee: dict) -> str | None:
        for key in ("designation", "designation_name", "job_title", "title", "position"):
            val = employee.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        return None

    @staticmethod
    def _is_active_employee(employee: dict) -> bool:
        value = employee.get("is_active", True)
        if isinstance(value, str):
            return value.strip().lower() not in ("false", "0", "no")
        return bool(value)

    def _resolve_department_members(self, departments: list[dict]) -> tuple[dict | None, list[dict]]:
        target = self._normalize(self._agent_department_name())
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

    def _get_or_create_person(self, emp: dict, result: AgentSyncResult) -> Person | None:
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

    def _upsert_agent(self, emp: dict, person: Person, result: AgentSyncResult) -> None:
        employee_id = (emp.get("employee_id") or "").strip()
        if not employee_id:
            result.errors.append({"type": "data", "error": "employee_missing_id", "email": person.email})
            return

        agent = self.db.query(CrmAgent).filter(CrmAgent.person_id == person.id).first()
        designation = emp.get("designation")
        department = emp.get("department")
        full_name = emp.get("full_name")

        if not agent:
            agent = CrmAgent(
                person_id=person.id,
                title=(str(designation).strip()[:120] if designation else None),
                is_active=True,
                metadata_={
                    "dotmac_erp": {
                        "source": "crm_agent_sync",
                        "employee_id": employee_id,
                        "department": department,
                        "designation": designation,
                        "full_name": full_name,
                        "email": person.email,
                        "last_synced_at": datetime.now(UTC).isoformat(),
                    }
                },
            )
            self.db.add(agent)
            result.agents_created += 1
            return

        was_inactive = not bool(agent.is_active)
        agent.person_id = person.id
        agent.is_active = True

        if designation and not agent.title:
            agent.title = str(designation).strip()[:120]

        meta = agent.metadata_ if isinstance(agent.metadata_, dict) else {}
        meta_dotmac = meta.get("dotmac_erp") if isinstance(meta.get("dotmac_erp"), dict) else {}
        meta_dotmac.update(
            {
                "source": "crm_agent_sync",
                "employee_id": employee_id,
                "department": department,
                "designation": designation,
                "full_name": full_name,
                "email": person.email,
                "last_synced_at": datetime.now(UTC).isoformat(),
            }
        )
        meta["dotmac_erp"] = meta_dotmac
        agent.metadata_ = meta

        if was_inactive:
            result.agents_reactivated += 1
        else:
            result.agents_updated += 1

    def sync_all(self, limit: int = 500) -> AgentSyncResult:
        """Sync CRM agents from ERP employees in the configured department."""
        start = datetime.now(UTC)
        result = AgentSyncResult()

        client = self._get_client()
        if not client:
            result.errors.append({"type": "config", "error": "ERP agent sync not configured or disabled"})
            return result

        employees: list[dict] = []
        offset = 0
        max_pages = 50
        pages = 0

        try:
            while pages < max_pages:
                batch = client.get_employees(include_inactive=True, limit=limit, offset=offset)
                if not batch:
                    break
                employees.extend(batch)
                if len(batch) < limit:
                    break
                offset += limit
                pages += 1
        except DotMacERPNotFoundError:
            # Some ERP deployments do not expose the employees endpoint.
            employees = []
        except Exception as exc:
            result.errors.append({"type": "api", "error": str(exc)})
            result.duration_seconds = (datetime.now(UTC) - start).total_seconds()
            return result

        target_department = self._normalize(self._agent_department_name())
        department_in_payload = any(self._extract_department_name(emp) for emp in employees)
        eligible: list[dict] = []
        can_deactivate = False

        if employees and department_in_payload:
            can_deactivate = True
            result.employees_seen = len(employees)
            for emp in employees:
                dept_name = self._extract_department_name(emp)
                if not dept_name:
                    continue
                if self._normalize(dept_name) != target_department:
                    continue
                if not self._is_active_employee(emp):
                    continue
                emp = {
                    **emp,
                    "department": dept_name,
                    "designation": self._extract_designation(emp) or emp.get("designation"),
                }
                eligible.append(emp)
        else:
            departments: list[dict] = []
            offset = 0
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
            except Exception as exc:
                result.errors.append({"type": "api", "error": str(exc)})
                result.duration_seconds = (datetime.now(UTC) - start).total_seconds()
                return result

            dept, members = self._resolve_department_members(departments)
            if not dept:
                result.errors.append(
                    {
                        "type": "data",
                        "error": "agent_department_not_found",
                        "department_name": self._agent_department_name(),
                    }
                )
                result.duration_seconds = (datetime.now(UTC) - start).total_seconds()
                return result

            can_deactivate = True
            result.employees_seen = len(members)
            for emp in members:
                if not self._is_active_employee(emp):
                    continue
                emp = {
                    **emp,
                    "department": dept.get("department_name") or dept.get("name"),
                    "department_id": dept.get("department_id"),
                    "designation": self._extract_designation(emp) or emp.get("designation"),
                }
                eligible.append(emp)

        result.employees_eligible = len(eligible)
        eligible_employee_ids: set[str] = set()

        for emp in eligible:
            employee_id = (emp.get("employee_id") or "").strip()
            if employee_id:
                eligible_employee_ids.add(employee_id)

        for emp in eligible:
            person = self._get_or_create_person(emp, result)
            if not person:
                continue
            self._upsert_agent(emp, person, result)

        # Deactivate ERP-linked agents that are no longer eligible.
        # Only deactivate agents created by this sync (metadata dotmac_erp.source == crm_agent_sync).
        if can_deactivate:
            active_agents = (
                self.db.query(CrmAgent)
                .filter(CrmAgent.is_active.is_(True))
                .filter(CrmAgent.metadata_.isnot(None))
                .all()
            )
            for agent in active_agents:
                meta = agent.metadata_ if isinstance(agent.metadata_, dict) else {}
                meta_dotmac = meta.get("dotmac_erp") if isinstance(meta.get("dotmac_erp"), dict) else {}
                if meta_dotmac.get("source") != "crm_agent_sync":
                    continue
                employee_id = str(meta_dotmac.get("employee_id") or "").strip()
                if not employee_id or employee_id not in eligible_employee_ids:
                    agent.is_active = False
                    result.agents_deactivated += 1

        self.db.commit()
        result.duration_seconds = (datetime.now(UTC) - start).total_seconds()
        return result


def dotmac_erp_agent_sync(db: Session) -> DotMacERPAgentSync:
    return DotMacERPAgentSync(db)
