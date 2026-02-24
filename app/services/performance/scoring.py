from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models.crm.team import CrmAgent
from app.models.domain_settings import SettingDomain
from app.models.performance import AgentPerformanceScore, AgentPerformanceSnapshot, PerformanceDomain
from app.models.person import Person
from app.models.projects import ProjectTask, TaskStatus
from app.models.service_team import ServiceTeam, ServiceTeamMember, ServiceTeamType
from app.models.subscriber import Organization
from app.models.tickets import Ticket, TicketComment, TicketSlaEvent
from app.models.workforce import WorkOrder, WorkOrderNote, WorkOrderStatus
from app.services.common import coerce_uuid
from app.services.crm import reports as crm_reports
from app.services.settings_spec import resolve_value


@dataclass(frozen=True)
class ScoreWindow:
    start_at: datetime
    end_at: datetime


@dataclass
class SupportInputs:
    sla_rate: float
    avg_resolution_minutes: float | None
    escalation_rate: float
    csat_score: float | None


@dataclass
class CommunicationInputs:
    frt_minutes: float | None
    resolution_minutes: float | None
    volume: float
    channel_coverage: float


@dataclass
class SalesInputs:
    win_rate: float
    won_value: float
    quote_acceptance_rate: float
    activity_count: float


@dataclass
class OperationsInputs:
    completion_rate: float
    on_time_rate: float
    effort_accuracy: float
    blocked_rate: float


@dataclass
class FieldInputs:
    completion_rate: float
    avg_delay_minutes: float
    duration_accuracy: float
    documentation_rate: float


@dataclass
class DataQualityInputs:
    contact_completeness: float
    organization_completeness: float
    tagging_discipline: float
    note_thoroughness: float


def _safe_div(num: float, den: float) -> float:
    if den <= 0:
        return 0.0
    return num / den


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        if isinstance(value, Decimal):
            return float(value)
        return float(value)
    except (TypeError, ValueError):
        return default


def _default_window() -> ScoreWindow:
    end_at = datetime.now(UTC)
    start_at = end_at - timedelta(days=7)
    return ScoreWindow(start_at=start_at, end_at=end_at)


def _resolve_weights(db: Session, team_type: str | None, sales_ratio: float) -> dict[PerformanceDomain, float]:
    default_weights = {
        PerformanceDomain.support: 20.0,
        PerformanceDomain.operations: 15.0,
        PerformanceDomain.field_service: 15.0,
        PerformanceDomain.communication: 20.0,
        PerformanceDomain.sales: 20.0,
        PerformanceDomain.data_quality: 10.0,
    }
    base = resolve_value(db, SettingDomain.performance, "domain_weights")
    if isinstance(base, dict):
        default_weights.update(
            {PerformanceDomain(k): _to_float(v) for k, v in base.items() if k in PerformanceDomain._value2member_map_}
        )

    key_map = {
        "operations": "domain_weights_operations",
        "support": "domain_weights_support",
        "field_service": "domain_weights_field_service",
    }
    if team_type in key_map:
        override = resolve_value(db, SettingDomain.performance, key_map[team_type])
        if isinstance(override, dict):
            default_weights.update(
                {
                    PerformanceDomain(k): _to_float(v)
                    for k, v in override.items()
                    if k in PerformanceDomain._value2member_map_
                }
            )

    sales_profile_min_ratio = _to_float(resolve_value(db, SettingDomain.performance, "sales_profile_min_ratio"), 0.5)
    if sales_ratio >= sales_profile_min_ratio:
        sales_override = resolve_value(db, SettingDomain.performance, "domain_weights_sales_profile")
        if isinstance(sales_override, dict):
            default_weights.update(
                {
                    PerformanceDomain(k): _to_float(v)
                    for k, v in sales_override.items()
                    if k in PerformanceDomain._value2member_map_
                }
            )

    return default_weights


def _person_team_lookup(db: Session) -> dict[str, dict[str, Any]]:
    rows = (
        db.query(ServiceTeamMember, ServiceTeam)
        .join(ServiceTeam, ServiceTeam.id == ServiceTeamMember.team_id)
        .filter(ServiceTeamMember.is_active.is_(True), ServiceTeam.is_active.is_(True))
        .all()
    )
    out: dict[str, dict[str, Any]] = {}
    for member, team in rows:
        key = str(member.person_id)
        if key in out:
            continue
        out[key] = {
            "team_id": str(team.id),
            "team_type": team.team_type.value if isinstance(team.team_type, ServiceTeamType) else str(team.team_type),
        }
    return out


def _crm_agent_mappings(db: Session) -> tuple[dict[str, str], dict[str, str]]:
    from app.models.crm.team import CrmAgent

    rows = db.query(CrmAgent.id, CrmAgent.person_id).filter(CrmAgent.is_active.is_(True)).all()
    agent_to_person = {str(agent_id): str(person_id) for agent_id, person_id in rows if person_id}
    person_to_agent = {person_id: agent_id for agent_id, person_id in agent_to_person.items()}
    return agent_to_person, person_to_agent


def _build_support_inputs(db: Session, person_id: str, window: ScoreWindow) -> SupportInputs:
    pid = coerce_uuid(person_id)
    tickets = (
        db.query(Ticket)
        .filter(
            Ticket.assigned_to_person_id == pid,
            Ticket.created_at >= window.start_at,
            Ticket.created_at <= window.end_at,
        )
        .all()
    )
    resolution_minutes: list[float] = []
    for ticket in tickets:
        if ticket.resolved_at:
            resolution_minutes.append((ticket.resolved_at - ticket.created_at).total_seconds() / 60)

    sla_events = (
        db.query(TicketSlaEvent)
        .join(Ticket, Ticket.id == TicketSlaEvent.ticket_id)
        .filter(
            Ticket.assigned_to_person_id == pid,
            TicketSlaEvent.created_at >= window.start_at,
            TicketSlaEvent.created_at <= window.end_at,
            TicketSlaEvent.expected_at.isnot(None),
            TicketSlaEvent.actual_at.isnot(None),
        )
        .all()
    )
    sla_total = len(sla_events)
    sla_met = sum(
        1 for event in sla_events if event.actual_at and event.expected_at and event.actual_at <= event.expected_at
    )
    sla_rate = _safe_div(float(sla_met), float(sla_total))

    escalation_statuses = {"pending", "waiting_on_customer", "on_hold", "lastmile_rerun", "site_under_construction"}
    escalation_count = 0
    for ticket in tickets:
        status_value = ticket.status.value if ticket.status is not None else None
        if status_value in escalation_statuses:
            escalation_count += 1
    escalation_rate = _safe_div(float(escalation_count), float(len(tickets)))

    # CSAT is not currently attributable to assignees in a reliable way; keep neutral midpoint.
    return SupportInputs(
        sla_rate=sla_rate,
        avg_resolution_minutes=(sum(resolution_minutes) / len(resolution_minutes)) if resolution_minutes else None,
        escalation_rate=escalation_rate,
        csat_score=None,
    )


def _build_communication_inputs(agent_row: dict[str, Any]) -> CommunicationInputs:
    total = _to_float(agent_row.get("total_conversations"))
    return CommunicationInputs(
        frt_minutes=_to_float(agent_row.get("avg_first_response_minutes"), -1) or None,
        resolution_minutes=_to_float(agent_row.get("avg_resolution_minutes"), -1) or None,
        volume=total,
        channel_coverage=1.0 if total > 0 else 0.0,
    )


def _build_sales_inputs(sales_row: dict[str, Any]) -> SalesInputs:
    won = _to_float(sales_row.get("deals_won"))
    lost = _to_float(sales_row.get("deals_lost"))
    total = max(won + lost, 0.0)
    win_rate = _safe_div(won, total)
    return SalesInputs(
        win_rate=win_rate,
        won_value=_to_float(sales_row.get("won_value")),
        quote_acceptance_rate=win_rate,
        activity_count=_to_float(sales_row.get("activity_count")),
    )


def _build_operations_inputs(db: Session, person_id: str, window: ScoreWindow) -> OperationsInputs:
    pid = coerce_uuid(person_id)
    tasks = (
        db.query(ProjectTask)
        .filter(
            ProjectTask.assigned_to_person_id == pid,
            ProjectTask.created_at >= window.start_at,
            ProjectTask.created_at <= window.end_at,
        )
        .all()
    )
    assigned = float(len(tasks))
    done_tasks = [task for task in tasks if task.status == TaskStatus.done]
    blocked_tasks = [task for task in tasks if task.status == TaskStatus.blocked]
    on_time_tasks = [
        task for task in done_tasks if task.due_at and task.completed_at and task.completed_at <= task.due_at
    ]
    effort_accuracy_values: list[float] = []
    for task in done_tasks:
        if task.effort_hours and task.effort_hours > 0 and task.completed_at and task.created_at:
            actual_hours = max((task.completed_at - task.created_at).total_seconds() / 3600, 0.0)
            estimated_hours = max(float(task.effort_hours), 0.1)
            effort_accuracy_values.append(max(0.0, 1 - abs(actual_hours - estimated_hours) / estimated_hours))

    return OperationsInputs(
        completion_rate=_safe_div(float(len(done_tasks)), assigned),
        on_time_rate=_safe_div(float(len(on_time_tasks)), max(float(len(done_tasks)), 1.0)),
        effort_accuracy=(sum(effort_accuracy_values) / len(effort_accuracy_values)) if effort_accuracy_values else 0.5,
        blocked_rate=_safe_div(float(len(blocked_tasks)), assigned),
    )


def _build_field_inputs(db: Session, person_id: str, window: ScoreWindow) -> FieldInputs:
    pid = coerce_uuid(person_id)
    orders = (
        db.query(WorkOrder)
        .filter(
            WorkOrder.assigned_to_person_id == pid,
            WorkOrder.created_at >= window.start_at,
            WorkOrder.created_at <= window.end_at,
        )
        .all()
    )
    assigned = float(len(orders))
    completed_orders = [order for order in orders if order.status == WorkOrderStatus.completed]

    delay_values: list[float] = []
    duration_accuracy_values: list[float] = []
    for order in completed_orders:
        if order.scheduled_end and order.completed_at:
            delay_values.append(max((order.completed_at - order.scheduled_end).total_seconds() / 60, 0.0))
        if order.estimated_duration_minutes and order.started_at and order.completed_at:
            actual = max((order.completed_at - order.started_at).total_seconds() / 60, 1.0)
            estimated = max(float(order.estimated_duration_minutes), 1.0)
            duration_accuracy_values.append(max(0.0, 1 - abs(actual - estimated) / estimated))

    completed_ids = [order.id for order in completed_orders]
    noted_ids: set[Any] = set()
    if completed_ids:
        note_rows = (
            db.query(WorkOrderNote.work_order_id)
            .filter(WorkOrderNote.work_order_id.in_(completed_ids))
            .distinct()
            .all()
        )
        noted_ids = {row[0] for row in note_rows}

    return FieldInputs(
        completion_rate=_safe_div(float(len(completed_orders)), assigned),
        avg_delay_minutes=(sum(delay_values) / len(delay_values)) if delay_values else 60.0,
        duration_accuracy=(sum(duration_accuracy_values) / len(duration_accuracy_values))
        if duration_accuracy_values
        else 0.5,
        documentation_rate=_safe_div(float(len(noted_ids)), max(float(len(completed_orders)), 1.0)),
    )


def _build_data_quality_inputs(db: Session, person_id: str, window: ScoreWindow) -> DataQualityInputs:
    person = db.get(Person, coerce_uuid(person_id))
    if not person:
        return DataQualityInputs(0.0, 0.5, 0.0, 0.0)

    fields = [person.email, person.phone, person.first_name, person.last_name, person.city, person.country_code]
    contact_completeness = _safe_div(float(sum(1 for field in fields if field)), float(len(fields)))

    assigned_tickets = (
        db.query(Ticket)
        .filter(
            Ticket.assigned_to_person_id == coerce_uuid(person_id),
            Ticket.created_at >= window.start_at,
            Ticket.created_at <= window.end_at,
        )
        .all()
    )
    tagged = sum(
        1 for ticket in assigned_tickets if ticket.tags and isinstance(ticket.tags, list) and len(ticket.tags) > 0
    )
    tagging_discipline = _safe_div(float(tagged), float(len(assigned_tickets)))

    authored_comments = (
        db.query(TicketComment)
        .filter(
            TicketComment.author_person_id == coerce_uuid(person_id),
            TicketComment.created_at >= window.start_at,
            TicketComment.created_at <= window.end_at,
        )
        .all()
    )
    long_notes = sum(1 for comment in authored_comments if len((comment.body or "").strip()) >= 100)
    note_thoroughness = _safe_div(float(long_notes), float(len(authored_comments)))
    organization_completeness = 0.0
    if person.organization_id:
        organization = db.get(Organization, person.organization_id)
        if organization:
            org_fields = [
                organization.name,
                organization.phone,
                organization.email,
                organization.domain,
                organization.industry,
                organization.city,
                organization.country_code,
            ]
            organization_completeness = _safe_div(
                float(sum(1 for field in org_fields if field)), float(len(org_fields))
            )
    else:
        organization_completeness = 0.5

    return DataQualityInputs(
        contact_completeness=contact_completeness,
        organization_completeness=organization_completeness,
        tagging_discipline=tagging_discipline,
        note_thoroughness=note_thoroughness,
    )


def _support_score(inputs: SupportInputs, team_avg_resolution: float | None) -> tuple[float, dict[str, float]]:
    sla_component = _clamp(inputs.sla_rate * 40, 0, 40)
    if inputs.avg_resolution_minutes is None or team_avg_resolution is None or team_avg_resolution <= 0:
        resolution_component = 15.0
    else:
        resolution_component = _clamp(30 * max(0.0, 1 - (inputs.avg_resolution_minutes / team_avg_resolution)), 0, 30)
    escalation_component = _clamp(20 * (1 - inputs.escalation_rate), 0, 20)
    csat_component = _clamp((inputs.csat_score or 0.5) * 10, 0, 10)
    raw = sla_component + resolution_component + escalation_component + csat_component
    return round(_clamp(raw), 2), {
        "sla_component": round(sla_component, 2),
        "resolution_component": round(resolution_component, 2),
        "escalation_component": round(escalation_component, 2),
        "csat_component": round(csat_component, 2),
    }


def _communication_score(
    inputs: CommunicationInputs,
    team_avg_frt: float | None,
    team_avg_resolution: float | None,
    team_avg_volume: float | None,
) -> tuple[float, dict[str, float]]:
    if inputs.frt_minutes is None or team_avg_frt is None or team_avg_frt <= 0:
        frt_component = 15.0
    else:
        frt_component = _clamp(30 * max(0.0, 1 - (inputs.frt_minutes / team_avg_frt)), 0, 30)

    if inputs.resolution_minutes is None or team_avg_resolution is None or team_avg_resolution <= 0:
        resolution_component = 12.5
    else:
        resolution_component = _clamp(25 * max(0.0, 1 - (inputs.resolution_minutes / team_avg_resolution)), 0, 25)

    if team_avg_volume is None or team_avg_volume <= 0:
        volume_component = 12.5
    else:
        volume_component = _clamp(min(inputs.volume / team_avg_volume, 1.0) * 25, 0, 25)

    channel_component = _clamp(inputs.channel_coverage * 20, 0, 20)
    raw = frt_component + resolution_component + volume_component + channel_component
    return round(_clamp(raw), 2), {
        "frt_component": round(frt_component, 2),
        "resolution_component": round(resolution_component, 2),
        "volume_component": round(volume_component, 2),
        "channel_component": round(channel_component, 2),
    }


def _sales_score(
    inputs: SalesInputs, team_avg_value: float | None, team_avg_activity: float | None
) -> tuple[float, dict[str, float]]:
    win_rate_component = _clamp(inputs.win_rate * 35, 0, 35)

    if team_avg_value is None or team_avg_value <= 0:
        value_component = 12.5
    else:
        value_component = _clamp(min(inputs.won_value / team_avg_value, 1.0) * 25, 0, 25)

    acceptance_component = _clamp(inputs.quote_acceptance_rate * 25, 0, 25)

    if team_avg_activity is None or team_avg_activity <= 0:
        activity_component = 7.5
    else:
        activity_component = _clamp(min(inputs.activity_count / team_avg_activity, 1.0) * 15, 0, 15)

    raw = win_rate_component + value_component + acceptance_component + activity_component
    return round(_clamp(raw), 2), {
        "win_rate_component": round(win_rate_component, 2),
        "value_component": round(value_component, 2),
        "acceptance_component": round(acceptance_component, 2),
        "activity_component": round(activity_component, 2),
    }


def _operations_score(inputs: OperationsInputs) -> tuple[float, dict[str, float]]:
    completion_component = _clamp(inputs.completion_rate * 35, 0, 35)
    on_time_component = _clamp(inputs.on_time_rate * 30, 0, 30)
    effort_component = _clamp(inputs.effort_accuracy * 20, 0, 20)
    blocked_component = _clamp((1 - inputs.blocked_rate) * 15, 0, 15)
    raw = completion_component + on_time_component + effort_component + blocked_component
    return round(_clamp(raw), 2), {
        "completion_component": round(completion_component, 2),
        "on_time_component": round(on_time_component, 2),
        "effort_component": round(effort_component, 2),
        "blocked_component": round(blocked_component, 2),
    }


def _field_service_score(inputs: FieldInputs) -> tuple[float, dict[str, float]]:
    completion_component = _clamp(inputs.completion_rate * 30, 0, 30)
    schedule_component = _clamp(25 * max(0.0, 1 - (inputs.avg_delay_minutes / 60.0)), 0, 25)
    duration_component = _clamp(inputs.duration_accuracy * 25, 0, 25)
    documentation_component = _clamp(inputs.documentation_rate * 20, 0, 20)
    raw = completion_component + schedule_component + duration_component + documentation_component
    return round(_clamp(raw), 2), {
        "completion_component": round(completion_component, 2),
        "schedule_component": round(schedule_component, 2),
        "duration_component": round(duration_component, 2),
        "documentation_component": round(documentation_component, 2),
    }


def _data_quality_score(inputs: DataQualityInputs) -> tuple[float, dict[str, float]]:
    contact_component = _clamp(inputs.contact_completeness * 40, 0, 40)
    org_component = _clamp(inputs.organization_completeness * 25, 0, 25)
    tagging_component = _clamp(inputs.tagging_discipline * 20, 0, 20)
    notes_component = _clamp(inputs.note_thoroughness * 15, 0, 15)
    raw = contact_component + org_component + tagging_component + notes_component
    return round(_clamp(raw), 2), {
        "contact_component": round(contact_component, 2),
        "organization_component": round(org_component, 2),
        "tagging_component": round(tagging_component, 2),
        "notes_component": round(notes_component, 2),
    }


def _sales_ratio(total_sales_events: float, total_events: float) -> float:
    return _safe_div(total_sales_events, total_events)


def _upsert_score_row(
    db: Session,
    *,
    person_id: str,
    window: ScoreWindow,
    domain: PerformanceDomain,
    raw_score: float,
    weighted_score: float,
    metrics_json: dict[str, Any],
) -> None:
    existing = (
        db.query(AgentPerformanceScore)
        .filter(
            AgentPerformanceScore.person_id == coerce_uuid(person_id),
            AgentPerformanceScore.score_period_start == window.start_at,
            AgentPerformanceScore.domain == domain,
        )
        .first()
    )
    if existing:
        existing.score_period_end = window.end_at
        existing.raw_score = raw_score
        existing.weighted_score = weighted_score
        existing.metrics_json = metrics_json
        return

    db.add(
        AgentPerformanceScore(
            person_id=coerce_uuid(person_id),
            score_period_start=window.start_at,
            score_period_end=window.end_at,
            domain=domain,
            raw_score=raw_score,
            weighted_score=weighted_score,
            metrics_json=metrics_json,
        )
    )


def _upsert_snapshot(
    db: Session,
    *,
    person_id: str,
    window: ScoreWindow,
    team_id: str | None,
    team_type: str | None,
    domain_scores: dict[str, float],
    weights: dict[str, float],
    sales_ratio: float,
    composite_score: float,
) -> None:
    existing = (
        db.query(AgentPerformanceSnapshot)
        .filter(
            AgentPerformanceSnapshot.person_id == coerce_uuid(person_id),
            AgentPerformanceSnapshot.score_period_start == window.start_at,
            AgentPerformanceSnapshot.score_period_end == window.end_at,
        )
        .first()
    )
    if existing:
        existing.team_id = coerce_uuid(team_id) if team_id else None
        existing.team_type = team_type
        existing.composite_score = composite_score
        existing.domain_scores_json = domain_scores
        existing.weights_json = weights
        existing.sales_activity_ratio = sales_ratio
        return

    db.add(
        AgentPerformanceSnapshot(
            person_id=coerce_uuid(person_id),
            team_id=coerce_uuid(team_id) if team_id else None,
            score_period_start=window.start_at,
            score_period_end=window.end_at,
            composite_score=composite_score,
            domain_scores_json=domain_scores,
            weights_json=weights,
            team_type=team_type,
            sales_activity_ratio=sales_ratio,
        )
    )


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _candidate_person_ids(db: Session, window: ScoreWindow) -> set[str]:
    # Keep scoring bounded: only people with activity / assignment signals.
    ids: set[str] = set()

    ids.update(
        str(pid)
        for (pid,) in db.query(ServiceTeamMember.person_id)
        .filter(ServiceTeamMember.is_active.is_(True))
        .filter(ServiceTeamMember.person_id.isnot(None))
        .distinct()
        .all()
    )
    ids.update(
        str(pid)
        for (pid,) in db.query(CrmAgent.person_id)
        .filter(CrmAgent.is_active.is_(True))
        .filter(CrmAgent.person_id.isnot(None))
        .distinct()
        .all()
    )
    ids.update(
        str(pid)
        for (pid,) in db.query(Ticket.assigned_to_person_id)
        .filter(Ticket.assigned_to_person_id.isnot(None))
        .filter(Ticket.created_at >= window.start_at, Ticket.created_at <= window.end_at)
        .distinct()
        .all()
    )
    ids.update(
        str(pid)
        for (pid,) in db.query(ProjectTask.assigned_to_person_id)
        .filter(ProjectTask.assigned_to_person_id.isnot(None))
        .filter(ProjectTask.created_at >= window.start_at, ProjectTask.created_at <= window.end_at)
        .distinct()
        .all()
    )
    ids.update(
        str(pid)
        for (pid,) in db.query(WorkOrder.assigned_to_person_id)
        .filter(WorkOrder.assigned_to_person_id.isnot(None))
        .filter(WorkOrder.created_at >= window.start_at, WorkOrder.created_at <= window.end_at)
        .distinct()
        .all()
    )

    return ids


class PerformanceScoringService:
    def compute_period(self, db: Session, window: ScoreWindow | None = None) -> dict[str, Any]:
        window = window or _default_window()
        if not bool(resolve_value(db, SettingDomain.performance, "scoring_enabled")):
            return {"processed": 0, "window": {"start_at": window.start_at, "end_at": window.end_at}, "skipped": True}

        candidate_ids = _candidate_person_ids(db, window)
        if not candidate_ids:
            return {"processed": 0, "window": {"start_at": window.start_at, "end_at": window.end_at}}

        active_people = (
            db.query(Person)
            .filter(Person.is_active.is_(True))
            .filter(Person.id.in_([coerce_uuid(pid) for pid in candidate_ids]))
            .all()
        )
        if not active_people:
            return {"processed": 0, "window": {"start_at": window.start_at, "end_at": window.end_at}}

        agent_stats = crm_reports.agent_performance_metrics(
            db=db,
            start_at=window.start_at,
            end_at=window.end_at,
            agent_id=None,
            team_id=None,
            channel_type=None,
        )
        sales_stats = crm_reports.agent_sales_performance(
            db=db,
            start_at=window.start_at,
            end_at=window.end_at,
            pipeline_id=None,
        )

        crm_agent_to_person, _person_to_crm_agent = _crm_agent_mappings(db)
        agent_map = {
            crm_agent_to_person.get(str(row.get("agent_id"))): row
            for row in agent_stats
            if row.get("agent_id") and crm_agent_to_person.get(str(row.get("agent_id")))
        }
        sales_map = {
            crm_agent_to_person.get(str(row.get("agent_id"))): row
            for row in sales_stats
            if row.get("agent_id") and crm_agent_to_person.get(str(row.get("agent_id")))
        }
        team_map = _person_team_lookup(db)

        support_inputs_map: dict[str, SupportInputs] = {}
        communication_inputs_map: dict[str, CommunicationInputs] = {}
        sales_inputs_map: dict[str, SalesInputs] = {}
        operations_inputs_map: dict[str, OperationsInputs] = {}
        field_inputs_map: dict[str, FieldInputs] = {}
        quality_inputs_map: dict[str, DataQualityInputs] = {}

        for person in active_people:
            person_id = str(person.id)
            support_inputs_map[person_id] = _build_support_inputs(db, person_id, window)
            communication_inputs_map[person_id] = _build_communication_inputs(agent_map.get(person_id, {}))
            sales_inputs_map[person_id] = _build_sales_inputs(sales_map.get(person_id, {}))
            operations_inputs_map[person_id] = _build_operations_inputs(db, person_id, window)
            field_inputs_map[person_id] = _build_field_inputs(db, person_id, window)
            quality_inputs_map[person_id] = _build_data_quality_inputs(db, person_id, window)

        support_resolution_by_team: dict[str, list[float]] = {}
        comm_frt_by_team: dict[str, list[float]] = {}
        comm_resolution_by_team: dict[str, list[float]] = {}
        comm_volume_by_team: dict[str, list[float]] = {}
        sales_value_by_team: dict[str, list[float]] = {}
        sales_activity_by_team: dict[str, list[float]] = {}

        for person in active_people:
            person_id = str(person.id)
            team_id = team_map.get(person_id, {}).get("team_id") or "__default__"

            support_avg = support_inputs_map[person_id].avg_resolution_minutes
            if support_avg is not None:
                support_resolution_by_team.setdefault(team_id, []).append(support_avg)

            comm = communication_inputs_map[person_id]
            if comm.frt_minutes is not None:
                comm_frt_by_team.setdefault(team_id, []).append(comm.frt_minutes)
            if comm.resolution_minutes is not None:
                comm_resolution_by_team.setdefault(team_id, []).append(comm.resolution_minutes)
            comm_volume_by_team.setdefault(team_id, []).append(comm.volume)

            sales = sales_inputs_map[person_id]
            sales_value_by_team.setdefault(team_id, []).append(sales.won_value)
            sales_activity_by_team.setdefault(team_id, []).append(sales.activity_count)

        processed = 0
        for person in active_people:
            person_id = str(person.id)
            team_info = team_map.get(person_id, {})
            team_id = team_info.get("team_id") or "__default__"

            support_raw, support_metrics = _support_score(
                support_inputs_map[person_id],
                _avg(support_resolution_by_team.get(team_id, [])),
            )
            communication_raw, communication_metrics = _communication_score(
                communication_inputs_map[person_id],
                _avg(comm_frt_by_team.get(team_id, [])),
                _avg(comm_resolution_by_team.get(team_id, [])),
                _avg(comm_volume_by_team.get(team_id, [])),
            )
            sales_raw, sales_metrics = _sales_score(
                sales_inputs_map[person_id],
                _avg(sales_value_by_team.get(team_id, [])),
                _avg(sales_activity_by_team.get(team_id, [])),
            )
            operations_raw, operations_metrics = _operations_score(operations_inputs_map[person_id])
            field_raw, field_metrics = _field_service_score(field_inputs_map[person_id])
            quality_raw, quality_metrics = _data_quality_score(quality_inputs_map[person_id])

            total_events = communication_inputs_map[person_id].volume + sales_inputs_map[person_id].activity_count
            sales_ratio = _sales_ratio(sales_inputs_map[person_id].activity_count, max(total_events, 1.0))
            weights_map = _resolve_weights(db, team_info.get("team_type"), sales_ratio)

            domain_raw = {
                PerformanceDomain.support: support_raw,
                PerformanceDomain.operations: operations_raw,
                PerformanceDomain.field_service: field_raw,
                PerformanceDomain.communication: communication_raw,
                PerformanceDomain.sales: sales_raw,
                PerformanceDomain.data_quality: quality_raw,
            }
            domain_metrics = {
                PerformanceDomain.support: support_metrics,
                PerformanceDomain.operations: operations_metrics,
                PerformanceDomain.field_service: field_metrics,
                PerformanceDomain.communication: communication_metrics,
                PerformanceDomain.sales: sales_metrics,
                PerformanceDomain.data_quality: quality_metrics,
            }

            weighted_total = 0.0
            weight_sum = 0.0
            for domain, raw in domain_raw.items():
                weight = _to_float(weights_map.get(domain, 0.0))
                weighted = _safe_div(raw * weight, 100.0)
                _upsert_score_row(
                    db,
                    person_id=person_id,
                    window=window,
                    domain=domain,
                    raw_score=round(raw, 2),
                    weighted_score=round(weighted, 2),
                    metrics_json=domain_metrics[domain],
                )
                weighted_total += raw * weight
                weight_sum += weight

            composite = round(_safe_div(weighted_total, weight_sum), 2) if weight_sum > 0 else 0.0
            _upsert_snapshot(
                db,
                person_id=person_id,
                window=window,
                team_id=team_info.get("team_id"),
                team_type=team_info.get("team_type"),
                domain_scores={k.value: v for k, v in domain_raw.items()},
                weights={k.value: _to_float(v) for k, v in weights_map.items()},
                sales_ratio=sales_ratio,
                composite_score=composite,
            )
            processed += 1

        db.commit()
        return {
            "processed": processed,
            "window": {"start_at": window.start_at, "end_at": window.end_at},
        }


performance_scoring = PerformanceScoringService()
