from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.models.performance import AgentPerformanceReview, AgentPerformanceSnapshot
from app.models.person import Person
from app.models.service_team import ServiceTeam, ServiceTeamMember, ServiceTeamMemberRole
from app.services.common import coerce_uuid
from app.services.settings_spec import resolve_value


@dataclass(frozen=True)
class AccessScope:
    person_id: str
    managed_person_ids: set[str]


class PerformanceReportsService:
    def build_access_scope(self, db: Session, person_id: str, roles: list[str], permissions: list[str]) -> AccessScope:
        role_set = {str(role).lower() for role in roles}
        perm_set = {str(permission) for permission in permissions}
        if "admin" in role_set or "reports" in perm_set or "reports:operations" in perm_set:
            rows = db.query(Person.id).filter(Person.is_active.is_(True)).all()
            return AccessScope(person_id=person_id, managed_person_ids={str(row[0]) for row in rows})

        managed_team_ids: set[str] = set()
        managed_team_ids.update(
            str(row[0])
            for row in db.query(ServiceTeam.id)
            .filter(ServiceTeam.manager_person_id == coerce_uuid(person_id), ServiceTeam.is_active.is_(True))
            .all()
        )
        managed_team_ids.update(
            str(row[0])
            for row in db.query(ServiceTeamMember.team_id)
            .filter(
                ServiceTeamMember.person_id == coerce_uuid(person_id),
                ServiceTeamMember.role.in_([ServiceTeamMemberRole.manager, ServiceTeamMemberRole.lead]),
                ServiceTeamMember.is_active.is_(True),
            )
            .all()
        )

        if not managed_team_ids:
            return AccessScope(person_id=person_id, managed_person_ids={person_id})

        rows = (
            db.query(ServiceTeamMember.person_id)
            .filter(ServiceTeamMember.team_id.in_([coerce_uuid(tid) for tid in managed_team_ids]))
            .filter(ServiceTeamMember.is_active.is_(True))
            .all()
        )
        managed_ids = {str(row[0]) for row in rows}
        managed_ids.add(person_id)
        return AccessScope(person_id=person_id, managed_person_ids=managed_ids)

    def resolve_effective_person_id(self, scope: AccessScope, requested_person_id: str | None) -> str:
        if not requested_person_id:
            return scope.person_id
        if requested_person_id not in scope.managed_person_ids:
            raise ValueError("Forbidden")
        return requested_person_id

    def assert_can_access_person(self, scope: AccessScope, person_id: str) -> None:
        if person_id not in scope.managed_person_ids:
            raise ValueError("Forbidden")

    def score_history(self, db: Session, person_id: str, weeks: int = 12) -> list[AgentPerformanceSnapshot]:
        cutoff = datetime.now(UTC) - timedelta(days=max(1, weeks) * 7)
        return (
            db.query(AgentPerformanceSnapshot)
            .filter(AgentPerformanceSnapshot.person_id == coerce_uuid(person_id))
            .filter(AgentPerformanceSnapshot.score_period_start >= cutoff)
            .order_by(AgentPerformanceSnapshot.score_period_start.asc())
            .all()
        )

    def score_history_for_scope(
        self,
        db: Session,
        scope: AccessScope,
        requested_person_id: str | None,
        weeks: int = 12,
    ) -> list[AgentPerformanceSnapshot]:
        person_id = self.resolve_effective_person_id(scope, requested_person_id)
        return self.score_history(db, person_id, weeks=weeks)

    def scores_for_scope(
        self,
        db: Session,
        scope: AccessScope,
        requested_person_id: str | None,
        *,
        start_at: datetime | None = None,
        limit: int = 52,
    ) -> list[AgentPerformanceSnapshot]:
        person_id = self.resolve_effective_person_id(scope, requested_person_id)
        query = db.query(AgentPerformanceSnapshot).filter(AgentPerformanceSnapshot.person_id == coerce_uuid(person_id))
        if start_at:
            query = query.filter(AgentPerformanceSnapshot.score_period_start >= start_at)
        return query.order_by(AgentPerformanceSnapshot.score_period_start.desc()).limit(max(1, min(limit, 104))).all()

    def leaderboard(self, db: Session, person_ids: set[str], period_start: datetime | None = None) -> list[dict[str, Any]]:
        if not person_ids:
            return []
        query = db.query(AgentPerformanceSnapshot).filter(
            AgentPerformanceSnapshot.person_id.in_([coerce_uuid(pid) for pid in person_ids])
        )
        if period_start:
            query = query.filter(AgentPerformanceSnapshot.score_period_start == period_start)
        else:
            latest = query.order_by(AgentPerformanceSnapshot.score_period_start.desc()).first()
            if not latest:
                return []
            query = query.filter(AgentPerformanceSnapshot.score_period_start == latest.score_period_start)

        snapshots = query.all()
        people = db.query(Person).filter(Person.id.in_([s.person_id for s in snapshots])).all()
        person_map = {str(person.id): person for person in people}

        rows: list[dict[str, Any]] = []
        for snapshot in snapshots:
            person = person_map.get(str(snapshot.person_id))
            name = (
                person.display_name
                if person and person.display_name
                else f"{person.first_name or ''} {person.last_name or ''}".strip()
                if person
                else "Unknown"
            )
            rows.append(
                {
                    "person_id": str(snapshot.person_id),
                    "name": name or "Unknown",
                    "team_id": str(snapshot.team_id) if snapshot.team_id else None,
                    "team_type": snapshot.team_type,
                    "composite_score": float(snapshot.composite_score),
                    "domain_scores": snapshot.domain_scores_json,
                    "score_period_start": snapshot.score_period_start,
                    "score_period_end": snapshot.score_period_end,
                }
            )

        rows.sort(key=lambda row: float(row.get("composite_score") or 0.0), reverse=True)
        return rows

    def leaderboard_for_scope(
        self,
        db: Session,
        scope: AccessScope,
        *,
        period_start: datetime | None = None,
        team_id: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = self.leaderboard(db, scope.managed_person_ids, period_start=period_start)
        if team_id:
            rows = [row for row in rows if row.get("team_id") == team_id]
        return rows

    def reviews(self, db: Session, person_id: str, limit: int = 20) -> list[AgentPerformanceReview]:
        return (
            db.query(AgentPerformanceReview)
            .filter(AgentPerformanceReview.person_id == coerce_uuid(person_id))
            .order_by(AgentPerformanceReview.created_at.desc())
            .limit(max(1, min(limit, 100)))
            .all()
        )

    def reviews_for_scope(
        self,
        db: Session,
        scope: AccessScope,
        requested_person_id: str | None,
        *,
        limit: int = 20,
    ) -> list[AgentPerformanceReview]:
        person_id = self.resolve_effective_person_id(scope, requested_person_id)
        return self.reviews(db, person_id, limit=limit)

    def review_detail_for_scope(self, db: Session, scope: AccessScope, review_id: str) -> AgentPerformanceReview:
        review = db.get(AgentPerformanceReview, coerce_uuid(review_id))
        if not review:
            raise LookupError("Review not found")
        if str(review.person_id) not in scope.managed_person_ids:
            raise ValueError("Forbidden")
        return review

    def peer_comparison(self, db: Session, scope: AccessScope) -> dict:
        leaderboard = self.leaderboard_for_scope(db, scope)
        mine = next((row for row in leaderboard if row.get("person_id") == scope.person_id), None)
        min_size_raw = resolve_value(db, SettingDomain.performance, "peer_comparison_min_team_size")
        try:
            if min_size_raw is None:
                min_size = 3
            elif isinstance(min_size_raw, bool):
                min_size = int(min_size_raw)
            elif isinstance(min_size_raw, int | float):
                min_size = int(min_size_raw)
            elif isinstance(min_size_raw, str):
                min_size = int(min_size_raw.strip())
            else:
                min_size = 3
        except (TypeError, ValueError):
            min_size = 3
        if len(leaderboard) < max(min_size, 1):
            return {
                "mine": mine,
                "team_average": None,
                "count": len(leaderboard),
                "comparison_available": False,
                "min_team_size": max(min_size, 1),
            }
        return {
            "mine": mine,
            "team_average": round(sum(row["composite_score"] for row in leaderboard) / len(leaderboard), 2)
            if leaderboard
            else 0,
            "count": len(leaderboard),
            "comparison_available": True,
            "min_team_size": max(min_size, 1),
        }

    def team_summary(
        self, db: Session, scope: AccessScope, team_id: str | None = None, period: str | None = None
    ) -> dict:
        rows = self.leaderboard_for_scope(db, scope, team_id=team_id)
        return {
            "team_id": team_id,
            "period": period,
            "count": len(rows),
            "average_score": round(sum(row["composite_score"] for row in rows) / len(rows), 2) if rows else 0,
            "rows": rows,
        }


performance_reports = PerformanceReportsService()


# Backwards compatibility for existing imports.
def get_managed_person_ids(db: Session, person_id: str, roles: list[str], permissions: list[str]) -> set[str]:
    return performance_reports.build_access_scope(db, person_id, roles, permissions).managed_person_ids
