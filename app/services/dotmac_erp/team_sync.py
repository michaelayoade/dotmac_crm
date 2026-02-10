"""Pull departments from DotMac ERP into ServiceTeam model."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.models.person import Person
from app.models.service_team import ServiceTeam, ServiceTeamMember, ServiceTeamMemberRole, ServiceTeamType
from app.services import settings_spec
from app.services.dotmac_erp.client import DotMacERPClient, DotMacERPError

logger = logging.getLogger(__name__)

# Map ERP department_type to local ServiceTeamType
_TEAM_TYPE_MAP: dict[str, ServiceTeamType] = {
    "operations": ServiceTeamType.operations,
    "support": ServiceTeamType.support,
    "field_service": ServiceTeamType.field_service,
}

# Map ERP member role to local ServiceTeamMemberRole
_ROLE_MAP: dict[str, ServiceTeamMemberRole] = {
    "member": ServiceTeamMemberRole.member,
    "lead": ServiceTeamMemberRole.lead,
    "manager": ServiceTeamMemberRole.manager,
}


@dataclass
class TeamSyncResult:
    """Result of a team/department sync operation."""

    teams_created: int = 0
    teams_updated: int = 0
    teams_deactivated: int = 0
    members_added: int = 0
    members_updated: int = 0
    members_deactivated: int = 0
    persons_matched: int = 0
    persons_skipped: int = 0
    crm_agents_synced: int = 0
    errors: list[dict] = field(default_factory=list)
    duration_seconds: float = 0.0

    @property
    def total_synced(self) -> int:
        return self.teams_created + self.teams_updated + self.members_added + self.members_updated

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0


class DotMacERPTeamSync:
    """Pull departments from DotMac ERP and sync to ServiceTeam model."""

    def __init__(self, db: Session):
        self.db = db
        self._client: DotMacERPClient | None = None
        self._person_cache: dict[str, Person | None] = {}

    def _get_client(self) -> DotMacERPClient | None:
        """Get configured ERP client, or None if not configured."""
        if self._client is not None:
            return self._client

        enabled = settings_spec.resolve_value(self.db, SettingDomain.integration, "dotmac_erp_team_sync_enabled")
        if not enabled:
            return None

        base_url_value = settings_spec.resolve_value(self.db, SettingDomain.integration, "dotmac_erp_base_url")
        token_value = settings_spec.resolve_value(self.db, SettingDomain.integration, "dotmac_erp_token")

        base_url = str(base_url_value) if base_url_value else None
        token = str(token_value) if token_value else None

        if not base_url or not token:
            logger.warning("DotMac ERP team sync enabled but not configured (missing URL or token)")
            return None

        timeout_value = settings_spec.resolve_value(self.db, SettingDomain.integration, "dotmac_erp_timeout_seconds")
        timeout = int(timeout_value) if isinstance(timeout_value, int | str) else 30

        self._client = DotMacERPClient(base_url=base_url, token=token, timeout=timeout)
        return self._client

    def close(self):
        if self._client:
            self._client.close()
            self._client = None

    def _resolve_person_by_employee(self, erp_data: dict) -> Person | None:
        """Resolve ERP employee to local Person. Tries erp_employee_id via TechnicianProfile, then email."""
        employee_id = erp_data.get("employee_id", "")
        email = (erp_data.get("email") or "").lower()

        cache_key = employee_id or email
        if cache_key in self._person_cache:
            return self._person_cache[cache_key]

        person = None

        # Try by TechnicianProfile.erp_employee_id → person
        if employee_id:
            from app.models.dispatch import TechnicianProfile

            tech = (
                self.db.query(TechnicianProfile)
                .filter(TechnicianProfile.erp_employee_id == employee_id)
                .filter(TechnicianProfile.is_active.is_(True))
                .first()
            )
            if tech:
                person = self.db.get(Person, tech.person_id)

        # Fallback to email
        if not person and email:
            person = self.db.query(Person).filter(Person.email == email).first()

        self._person_cache[cache_key] = person
        return person

    def _upsert_team(self, dept: dict, result: TeamSyncResult) -> ServiceTeam | None:
        """Upsert a ServiceTeam from ERP department data."""
        erp_department = dept.get("department_id")
        if not erp_department:
            return None

        team_type = _TEAM_TYPE_MAP.get(dept.get("department_type", ""), ServiceTeamType.operations)

        # Find existing team by erp_department
        team = self.db.query(ServiceTeam).filter(ServiceTeam.erp_department == erp_department).first()

        # Resolve manager
        manager_person = None
        manager_data = dept.get("manager")
        if manager_data:
            manager_person = self._resolve_person_by_employee(manager_data)

        if team:
            team.name = dept.get("department_name") or team.name
            team.team_type = team_type
            team.region = dept.get("region") or team.region
            team.is_active = dept.get("is_active", True)
            if manager_person:
                team.manager_person_id = manager_person.id
            result.teams_updated += 1
        else:
            team = ServiceTeam(
                name=dept.get("department_name", "Unknown"),
                team_type=team_type,
                region=dept.get("region"),
                erp_department=erp_department,
                is_active=dept.get("is_active", True),
                manager_person_id=manager_person.id if manager_person else None,
            )
            self.db.add(team)
            self.db.flush()
            result.teams_created += 1

        return team

    def _sync_team_members(
        self, team: ServiceTeam, members_data: list[dict], result: TeamSyncResult
    ) -> None:
        """Sync members for a single team."""
        seen_person_ids: set = set()

        for member_data in members_data:
            if not member_data.get("is_active", True):
                continue

            person = self._resolve_person_by_employee(member_data)
            if not person:
                result.persons_skipped += 1
                continue

            result.persons_matched += 1
            seen_person_ids.add(person.id)
            role = _ROLE_MAP.get(member_data.get("role", ""), ServiceTeamMemberRole.member)

            # Find existing membership
            existing = (
                self.db.query(ServiceTeamMember)
                .filter(ServiceTeamMember.team_id == team.id, ServiceTeamMember.person_id == person.id)
                .first()
            )

            if existing:
                existing.role = role
                existing.is_active = True
                result.members_updated += 1
            else:
                self.db.add(ServiceTeamMember(
                    team_id=team.id,
                    person_id=person.id,
                    role=role,
                    is_active=True,
                ))
                result.members_added += 1

        # Deactivate members no longer in the ERP department
        if seen_person_ids:
            stale = (
                self.db.query(ServiceTeamMember)
                .filter(
                    ServiceTeamMember.team_id == team.id,
                    ServiceTeamMember.is_active.is_(True),
                    ~ServiceTeamMember.person_id.in_(seen_person_ids),
                )
                .all()
            )
        else:
            stale = (
                self.db.query(ServiceTeamMember)
                .filter(ServiceTeamMember.team_id == team.id, ServiceTeamMember.is_active.is_(True))
                .all()
            )

        for member in stale:
            member.is_active = False
            result.members_deactivated += 1

    def sync_departments(self) -> TeamSyncResult:
        """Pull departments from ERP and sync to ServiceTeam + ServiceTeamMember."""
        start_time = datetime.now(UTC)
        result = TeamSyncResult()

        client = self._get_client()
        if not client:
            result.errors.append({"type": "config", "error": "ERP team sync not configured or disabled"})
            return result

        try:
            seen_erp_departments: set[str] = set()
            offset = 0
            limit = 500

            while True:
                departments = client.get_departments(limit=limit, offset=offset)
                if not departments:
                    break

                logger.info("Fetched %d departments from ERP (offset=%d)", len(departments), offset)

                for dept in departments:
                    erp_id = dept.get("department_id")
                    if not erp_id:
                        continue
                    seen_erp_departments.add(erp_id)

                    try:
                        team = self._upsert_team(dept, result)
                        if not team:
                            continue

                        members_data = dept.get("members") or []
                        self._sync_team_members(team, members_data, result)

                    except Exception as e:
                        logger.error("Failed to sync department %s: %s", erp_id, e)
                        result.errors.append({"type": "department", "erp_id": erp_id, "error": str(e)})

                if len(departments) < limit:
                    break
                offset += limit

            # Deactivate teams no longer in ERP
            if seen_erp_departments:
                stale_teams = (
                    self.db.query(ServiceTeam)
                    .filter(
                        ServiceTeam.erp_department.isnot(None),
                        ServiceTeam.is_active.is_(True),
                        ~ServiceTeam.erp_department.in_(seen_erp_departments),
                    )
                    .all()
                )
                for team in stale_teams:
                    team.is_active = False
                    result.teams_deactivated += 1

            self.db.commit()

            # Auto-sync CRM agents for affected teams
            synced_teams = (
                self.db.query(ServiceTeam)
                .filter(ServiceTeam.erp_department.in_(seen_erp_departments))
                .all()
            )
            for team in synced_teams:
                try:
                    from app.services.service_teams import sync_crm_agents

                    sync_crm_agents(self.db, str(team.id))
                    result.crm_agents_synced += 1
                except Exception as e:
                    logger.warning("Failed to sync CRM agents for team %s: %s", team.id, e)

        except DotMacERPError as e:
            logger.error("Department sync failed: %s", e)
            result.errors.append({"type": "api", "error": str(e)})
            self.db.rollback()

        result.duration_seconds = (datetime.now(UTC) - start_time).total_seconds()

        logger.info(
            "Team sync complete: %d created, %d updated, %d deactivated, "
            "%d members added, %d updated, %d deactivated",
            result.teams_created,
            result.teams_updated,
            result.teams_deactivated,
            result.members_added,
            result.members_updated,
            result.members_deactivated,
        )

        return result


def dotmac_erp_team_sync(db: Session) -> DotMacERPTeamSync:
    """Create a DotMac ERP team sync service instance."""
    return DotMacERPTeamSync(db)
