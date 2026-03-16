from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.projects import Project, ProjectTask
from app.models.tickets import Ticket
from app.models.workflow import SlaBreach, SlaBreachStatus, SlaClock, WorkflowEntityType
from app.web.admin.projects import REGION_OPTIONS

SlaReportType = Literal["ticket", "project", "project_task"]


def _base_query(db: Session, entity_type: SlaReportType):
    query = db.query(SlaBreach, SlaClock).join(SlaClock, SlaClock.id == SlaBreach.clock_id)
    return query.filter(SlaClock.entity_type == WorkflowEntityType(entity_type))


def _duration_label(started_at: datetime | None, ended_at: datetime | None) -> str:
    if not started_at:
        return "-"
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    end_value = ended_at or datetime.now(UTC)
    if end_value.tzinfo is None:
        end_value = end_value.replace(tzinfo=UTC)
    delta = end_value - started_at
    total_seconds = max(int(delta.total_seconds()), 0)
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


class OperationsSlaViolationsReport:
    def region_options(self, db: Session, entity_type: SlaReportType) -> list[str]:
        configured_regions = {region.strip() for region in REGION_OPTIONS if region and region.strip()}
        query = _base_query(db, entity_type)
        if entity_type == "ticket":
            rows = query.join(Ticket, Ticket.id == SlaClock.entity_id).with_entities(Ticket.region).distinct().all()
        elif entity_type == "project":
            rows = query.join(Project, Project.id == SlaClock.entity_id).with_entities(Project.region).distinct().all()
        else:
            rows = (
                query.join(ProjectTask, ProjectTask.id == SlaClock.entity_id)
                .join(Project, Project.id == ProjectTask.project_id)
                .with_entities(Project.region)
                .distinct()
                .all()
            )
        observed_regions = {str(row[0]).strip() for row in rows if row[0] and str(row[0]).strip()}
        return sorted(configured_regions | observed_regions)

    def list_records(
        self,
        db: Session,
        *,
        entity_type: SlaReportType,
        region: str | None,
        start_at: datetime | None,
        end_at: datetime | None,
        limit: int = 200,
    ) -> list[dict]:
        query = _base_query(db, entity_type)
        if start_at:
            query = query.filter(SlaBreach.breached_at >= start_at)
        if end_at:
            query = query.filter(SlaBreach.breached_at <= end_at)

        records: list[dict] = []
        if entity_type == "ticket":
            rows = (
                query.join(Ticket, Ticket.id == SlaClock.entity_id)
                .filter(Ticket.region == region if region else True)
                .with_entities(SlaBreach, SlaClock, Ticket)
                .order_by(SlaBreach.breached_at.desc())
                .limit(limit)
                .all()
            )
            for breach, clock, ticket in rows:
                ended_at = clock.completed_at if breach.status == SlaBreachStatus.resolved else None
                ref = ticket.number or str(ticket.id)
                records.append(
                    {
                        "id": ref,
                        "title": ticket.title,
                        "project": "",
                        "region": ticket.region or "Unassigned",
                        "sla_type": "Ticket",
                        "status": breach.status.value,
                        "breach_duration": _duration_label(breach.breached_at, ended_at),
                        "detail_url": f"/admin/support/tickets/{ref}",
                    }
                )
            return records

        if entity_type == "project":
            rows = (
                query.join(Project, Project.id == SlaClock.entity_id)
                .filter(Project.region == region if region else True)
                .with_entities(SlaBreach, SlaClock, Project)
                .order_by(SlaBreach.breached_at.desc())
                .limit(limit)
                .all()
            )
            for breach, clock, project in rows:
                ended_at = clock.completed_at if breach.status == SlaBreachStatus.resolved else None
                ref = project.number or str(project.id)
                records.append(
                    {
                        "id": ref,
                        "title": project.name,
                        "project": "",
                        "region": project.region or "Unassigned",
                        "sla_type": "Project",
                        "status": breach.status.value,
                        "breach_duration": _duration_label(breach.breached_at, ended_at),
                        "detail_url": f"/admin/projects/{ref}",
                    }
                )
            return records

        rows = (
            query.join(ProjectTask, ProjectTask.id == SlaClock.entity_id)
            .join(Project, Project.id == ProjectTask.project_id)
            .filter(Project.region == region if region else True)
            .with_entities(SlaBreach, SlaClock, ProjectTask, Project)
            .order_by(SlaBreach.breached_at.desc())
            .limit(limit)
            .all()
        )
        for breach, clock, task, project in rows:
            ended_at = clock.completed_at if breach.status == SlaBreachStatus.resolved else None
            ref = task.number or str(task.id)
            records.append(
                {
                    "id": ref,
                    "title": task.title,
                    "project": project.name,
                    "region": project.region or "Unassigned",
                    "sla_type": "Project Task",
                    "status": breach.status.value,
                    "breach_duration": _duration_label(breach.breached_at, ended_at),
                    "detail_url": f"/admin/projects/tasks/{ref}",
                }
            )
        return records

    def summary(
        self,
        db: Session,
        *,
        entity_type: SlaReportType,
        region: str | None,
        start_at: datetime | None,
        end_at: datetime | None,
    ) -> dict:
        records = self.list_records(
            db,
            entity_type=entity_type,
            region=region,
            start_at=start_at,
            end_at=end_at,
            limit=1000,
        )
        open_count = sum(1 for record in records if record["status"] != SlaBreachStatus.resolved.value)
        longest = "-"
        if records:

            def _duration_minutes(value: str) -> int:
                total = 0
                for part in value.split():
                    if part.endswith("d"):
                        total += int(part[:-1]) * 1440
                    elif part.endswith("h"):
                        total += int(part[:-1]) * 60
                    elif part.endswith("m"):
                        total += int(part[:-1])
                return total

            longest = max((record["breach_duration"] for record in records), key=_duration_minutes, default="-")
        return {
            "total_violations": len(records),
            "open_violations": open_count,
            "regions_affected": len(
                {record["region"] for record in records if record["region"] and record["region"] != "Unassigned"}
            ),
            "longest_active_breach": longest,
        }

    def by_region(
        self,
        db: Session,
        *,
        entity_type: SlaReportType,
        region: str | None,
        start_at: datetime | None,
        end_at: datetime | None,
    ) -> list[dict]:
        query = _base_query(db, entity_type)
        if start_at:
            query = query.filter(SlaBreach.breached_at >= start_at)
        if end_at:
            query = query.filter(SlaBreach.breached_at <= end_at)

        if entity_type == "ticket":
            rows = (
                query.join(Ticket, Ticket.id == SlaClock.entity_id)
                .filter(Ticket.region == region if region else True)
                .with_entities(Ticket.region, func.count(SlaBreach.id))
                .group_by(Ticket.region)
                .order_by(func.count(SlaBreach.id).desc(), Ticket.region.asc())
                .all()
            )
        elif entity_type == "project":
            rows = (
                query.join(Project, Project.id == SlaClock.entity_id)
                .filter(Project.region == region if region else True)
                .with_entities(Project.region, func.count(SlaBreach.id))
                .group_by(Project.region)
                .order_by(func.count(SlaBreach.id).desc(), Project.region.asc())
                .all()
            )
        else:
            rows = (
                query.join(ProjectTask, ProjectTask.id == SlaClock.entity_id)
                .join(Project, Project.id == ProjectTask.project_id)
                .filter(Project.region == region if region else True)
                .with_entities(Project.region, func.count(SlaBreach.id))
                .group_by(Project.region)
                .order_by(func.count(SlaBreach.id).desc(), Project.region.asc())
                .all()
            )
        return [{"label": value or "Unassigned", "count": int(count or 0)} for value, count in rows]

    def trend_daily(
        self,
        db: Session,
        *,
        entity_type: SlaReportType,
        region: str | None,
        start_at: datetime | None,
        end_at: datetime | None,
    ) -> list[dict]:
        query = _base_query(db, entity_type)
        if start_at:
            query = query.filter(SlaBreach.breached_at >= start_at)
        if end_at:
            query = query.filter(SlaBreach.breached_at <= end_at)

        if entity_type == "ticket":
            query = query.join(Ticket, Ticket.id == SlaClock.entity_id)
            if region:
                query = query.filter(Ticket.region == region)
        elif entity_type == "project":
            query = query.join(Project, Project.id == SlaClock.entity_id)
            if region:
                query = query.filter(Project.region == region)
        else:
            query = query.join(ProjectTask, ProjectTask.id == SlaClock.entity_id).join(
                Project, Project.id == ProjectTask.project_id
            )
            if region:
                query = query.filter(Project.region == region)

        rows = (
            query.with_entities(func.date(SlaBreach.breached_at), func.count(SlaBreach.id))
            .group_by(func.date(SlaBreach.breached_at))
            .order_by(func.date(SlaBreach.breached_at).asc())
            .all()
        )
        return [{"date": str(day), "count": int(count or 0)} for day, count in rows]


operations_sla_violations_report = OperationsSlaViolationsReport()
