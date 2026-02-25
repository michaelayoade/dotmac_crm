from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from app.models.ai_insight import AIInsight, AIInsightStatus, InsightDomain
from app.models.audit import AuditActorType, AuditEvent
from app.models.domain_settings import SettingDomain, SettingValueType
from app.models.integration import IntegrationRun, IntegrationRunStatus
from app.schemas.settings import DomainSettingUpdate
from app.services.ai.context_builders.batch_scanners import batch_scanners
from app.services.ai.gateway import ai_gateway
from app.services.ai.insights import ai_insights
from app.services.ai.personas import persona_registry
from app.services.domain_settings import integration_settings
from app.services.settings_spec import resolve_value

ALERT_SNOOZE_HOURS_ALLOWED = {8, 24, 72}


def _bool_value(value: object | None, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _coerce_int(value: object | None, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int | float):
        return int(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            return default
    return default


def _normalize_alert_snooze_hours(hours: int) -> int:
    normalized = int(hours)
    if normalized not in ALERT_SNOOZE_HOURS_ALLOWED:
        raise ValueError("hours must be one of: 8, 24, 72")
    return normalized


def _persona_enabled(db: Session, setting_key: str | None) -> bool:
    if not setting_key:
        return True
    return _bool_value(resolve_value(db, SettingDomain.integration, setting_key), True)


def _scan_persona_candidates(
    db: Session,
    *,
    persona_key: str,
    domain: str,
    sample_limit: int,
) -> list[tuple[str, str, dict[str, Any]]]:
    scanner = batch_scanners.get(domain)
    if scanner:
        return scanner(db, persona_key, limit=sample_limit)
    return []


def get_data_health_report(db: Session, *, sample_limit: int = 20) -> dict[str, Any]:
    limit = max(1, min(int(sample_limit), 100))

    budget = _coerce_int(resolve_value(db, SettingDomain.integration, "intelligence_daily_token_budget"), 0)
    used_today = ai_insights.tokens_used_today(db)
    budget_remaining = max(budget - used_today, 0) if budget > 0 else None

    personas: list[dict[str, Any]] = []
    domain_missing_counts: dict[str, Counter[str]] = {}
    for spec in persona_registry.list_all():
        enabled = _persona_enabled(db, spec.setting_key)
        candidates = _scan_persona_candidates(
            db,
            persona_key=spec.key,
            domain=spec.domain.value,
            sample_limit=limit,
        )

        scores: list[float] = []
        missing_counts: Counter[str] = Counter()
        errors = 0

        for _, _, params in candidates:
            try:
                quality = spec.context_quality_scorer(db, params or {})
            except Exception:
                errors += 1
                continue
            score = round(max(0.0, min(float(quality.score), 1.0)), 3)
            scores.append(score)
            missing_counts.update(quality.missing_fields)

        sample_size = len(scores)
        avg_quality = round(sum(scores) / sample_size, 3) if sample_size else None
        sufficient = sum(1 for score in scores if score >= spec.min_context_quality)
        pct_sufficient = round((sufficient / sample_size) * 100, 1) if sample_size else None

        if not enabled:
            readiness = "disabled"
        elif sample_size == 0:
            readiness = "no_candidates"
        elif avg_quality is not None and avg_quality >= spec.min_context_quality:
            readiness = "ready"
        else:
            readiness = "degraded"

        personas.append(
            {
                "persona_key": spec.key,
                "name": spec.name,
                "domain": spec.domain.value,
                "enabled": enabled,
                "supports_scheduled": bool(spec.supports_scheduled),
                "min_context_quality": round(spec.min_context_quality, 3),
                "sample_size": sample_size,
                "avg_quality": avg_quality,
                "pct_sufficient": pct_sufficient,
                "readiness": readiness,
                "scoring_errors": errors,
                "top_missing_fields": [
                    {"field": field_name, "count": count}
                    for field_name, count in missing_counts.most_common(5)
                ],
            }
        )
        domain_counter = domain_missing_counts.setdefault(spec.domain.value, Counter())
        domain_counter.update(missing_counts)

    return {
        "gateway_enabled": ai_gateway.enabled(db),
        "engine_scheduled_enabled": _bool_value(
            resolve_value(db, SettingDomain.integration, "intelligence_enabled"),
            False,
        ),
        "daily_token_budget": budget,
        "daily_tokens_used": used_today,
        "daily_tokens_remaining": budget_remaining,
        "sample_limit": limit,
        "personas": personas,
        "domain_missing_fields": {
            domain: [{"field": field_name, "count": count} for field_name, count in counter.most_common(5)]
            for domain, counter in domain_missing_counts.items()
        },
    }


def get_data_health_trend(
    db: Session,
    *,
    days: int = 14,
    persona_key: str | None = None,
    domain: str | None = None,
) -> dict[str, Any]:
    window_days = max(1, min(int(days), 90))
    start_at = datetime.now(UTC) - timedelta(days=window_days - 1)

    query = (
        db.query(
            func.date(AIInsight.created_at).label("date"),
            func.avg(func.coalesce(AIInsight.context_quality_score, 0.0)).label("avg_quality"),
            func.count(AIInsight.id).label("insight_count"),
            func.sum(case((AIInsight.status == AIInsightStatus.completed, 1), else_=0)).label("completed_count"),
            func.sum(case((AIInsight.status == AIInsightStatus.skipped, 1), else_=0)).label("skipped_count"),
            func.sum(case((AIInsight.status == AIInsightStatus.failed, 1), else_=0)).label("failed_count"),
        )
        .filter(AIInsight.created_at >= start_at)
    )
    if persona_key:
        query = query.filter(AIInsight.persona_key == persona_key)
    if domain:
        query = query.filter(AIInsight.domain == InsightDomain(domain))

    rows = query.group_by(func.date(AIInsight.created_at)).order_by(func.date(AIInsight.created_at).asc()).all()
    row_map: dict[str, Any] = {str(row.date): row for row in rows}

    points: list[dict[str, Any]] = []
    for day_offset in range(window_days):
        day = (start_at + timedelta(days=day_offset)).date().isoformat()
        row = row_map.get(day)
        if row is None:
            points.append(
                {
                    "date": day,
                    "avg_quality": None,
                    "insight_count": 0,
                    "completed_count": 0,
                    "skipped_count": 0,
                    "failed_count": 0,
                }
            )
            continue
        avg_quality = float(row.avg_quality) if row.avg_quality is not None else None
        points.append(
            {
                "date": day,
                "avg_quality": round(avg_quality, 3) if avg_quality is not None else None,
                "insight_count": int(row.insight_count or 0),
                "completed_count": int(row.completed_count or 0),
                "skipped_count": int(row.skipped_count or 0),
                "failed_count": int(row.failed_count or 0),
            }
        )

    return {
        "days": window_days,
        "persona_key": persona_key,
        "domain": domain,
        "points": points,
    }


def build_data_health_baseline_snapshot(
    db: Session,
    *,
    sample_limit: int = 20,
    trend_days: int = 14,
) -> dict[str, Any]:
    report = get_data_health_report(db, sample_limit=sample_limit)
    trend = get_data_health_trend(db, days=trend_days)

    readiness_counts: Counter[str] = Counter()
    missing_counts: Counter[str] = Counter()
    domain_missing_counts: dict[str, Counter[str]] = {}
    for persona in report.get("personas", []):
        readiness_counts.update([str(persona.get("readiness") or "unknown")])
        domain = str(persona.get("domain") or "unknown")
        domain_counter = domain_missing_counts.setdefault(domain, Counter())
        for item in persona.get("top_missing_fields", []):
            field_name = str(item.get("field") or "").strip()
            count = int(item.get("count") or 0)
            if not field_name or count <= 0:
                continue
            missing_counts[field_name] += count
            domain_counter[field_name] += count

    latest_point = trend["points"][-1] if trend.get("points") else None
    risk_inventory = _build_data_quality_risk_inventory(db)
    return {
        "captured_at": datetime.now(UTC).isoformat(),
        "sample_limit": int(report.get("sample_limit") or sample_limit),
        "trend_days": int(trend.get("days") or trend_days),
        "gateway_enabled": bool(report.get("gateway_enabled")),
        "engine_scheduled_enabled": bool(report.get("engine_scheduled_enabled")),
        "daily_tokens_used": int(report.get("daily_tokens_used") or 0),
        "daily_tokens_remaining": report.get("daily_tokens_remaining"),
        "persona_count": len(report.get("personas") or []),
        "readiness_counts": {
            "ready": readiness_counts.get("ready", 0),
            "degraded": readiness_counts.get("degraded", 0),
            "disabled": readiness_counts.get("disabled", 0),
            "no_candidates": readiness_counts.get("no_candidates", 0),
        },
        "top_missing_fields": [
            {"field": field_name, "count": count}
            for field_name, count in missing_counts.most_common(10)
        ],
        "domain_missing_fields": {
            domain: [{"field": field_name, "count": count} for field_name, count in counter.most_common(5)]
            for domain, counter in domain_missing_counts.items()
        },
        "latest_trend_point": latest_point,
        "risk_inventory": risk_inventory,
    }


def persist_data_health_baseline_snapshot(db: Session, snapshot: dict[str, Any]) -> dict[str, Any]:
    existing_last = get_latest_data_health_baseline_snapshot(db)
    if isinstance(existing_last, dict):
        integration_settings.upsert_by_key(
            db,
            "intelligence_data_health_baseline_previous",
            DomainSettingUpdate(
                value_type=SettingValueType.json,
                value_text=None,
                value_json=existing_last,
                is_secret=False,
                is_active=True,
            ),
        )
    integration_settings.upsert_by_key(
        db,
        "intelligence_data_health_baseline_last",
        DomainSettingUpdate(
            value_type=SettingValueType.json,
            value_text=None,
            value_json=snapshot,
            is_secret=False,
            is_active=True,
        ),
    )
    return snapshot


def get_latest_data_health_baseline_snapshot(db: Session) -> dict[str, Any] | None:
    value = resolve_value(db, SettingDomain.integration, "intelligence_data_health_baseline_last")
    return value if isinstance(value, dict) else None


def get_previous_data_health_baseline_snapshot(db: Session) -> dict[str, Any] | None:
    value = resolve_value(db, SettingDomain.integration, "intelligence_data_health_baseline_previous")
    return value if isinstance(value, dict) else None


def compute_risk_inventory_deltas(
    latest_snapshot: dict[str, Any] | None,
    previous_snapshot: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    latest_items = (latest_snapshot or {}).get("risk_inventory") or []
    prev_items = (previous_snapshot or {}).get("risk_inventory") or []

    prev_map: dict[str, dict[str, Any]] = {}
    for item in prev_items:
        key = str(item.get("source_key") or "").strip()
        if key:
            prev_map[key] = item

    deltas: dict[str, dict[str, Any]] = {}
    for item in latest_items:
        key = str(item.get("source_key") or "").strip()
        if not key:
            continue
        latest_failures = int(item.get("failure_count") or 0)
        prev_failures = int((prev_map.get(key) or {}).get("failure_count") or 0)
        delta = latest_failures - prev_failures
        if delta > 0:
            trend = "up"
        elif delta < 0:
            trend = "down"
        else:
            trend = "flat"
        deltas[key] = {
            "delta_failures": delta,
            "trend": trend,
            "previous_failures": prev_failures,
            "latest_failures": latest_failures,
        }
    return deltas


def compute_risk_alerts(
    latest_snapshot: dict[str, Any] | None,
    previous_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    latest_items = (latest_snapshot or {}).get("risk_inventory") or []
    deltas = compute_risk_inventory_deltas(latest_snapshot, previous_snapshot)

    triggered: list[dict[str, Any]] = []
    for item in latest_items:
        source_key = str(item.get("source_key") or "").strip()
        severity = str(item.get("severity") or "").strip().lower()
        if not source_key or severity != "high":
            continue
        delta = deltas.get(source_key) or {}
        if str(delta.get("trend") or "") != "up":
            continue
        triggered.append(
            {
                "source_key": source_key,
                "label": item.get("label"),
                "severity": severity,
                "failure_count": int(item.get("failure_count") or 0),
                "delta_failures": int(delta.get("delta_failures") or 0),
                "owner_role": item.get("owner_role"),
            }
        )

    return {
        "has_alerts": len(triggered) > 0,
        "count": len(triggered),
        "items": triggered,
    }


def get_risk_alert_state(db: Session) -> dict[str, Any]:
    ack_raw = resolve_value(db, SettingDomain.integration, "intelligence_data_health_risk_alert_ack")
    snooze_raw = resolve_value(db, SettingDomain.integration, "intelligence_data_health_risk_alert_snooze_until")
    ack = ack_raw if isinstance(ack_raw, dict) else {}
    snooze = snooze_raw if isinstance(snooze_raw, dict) else {}
    return {
        "acknowledged_at": _parse_iso_datetime(ack.get("acknowledged_at")),
        "acknowledged_by": str(ack.get("acknowledged_by") or "").strip() or None,
        "snooze_until": _parse_iso_datetime(snooze.get("snooze_until")),
        "snoozed_by": str(snooze.get("snoozed_by") or "").strip() or None,
    }


def acknowledge_risk_alerts(db: Session, *, actor_person_id: str | None = None) -> dict[str, Any]:
    payload = {
        "acknowledged_at": datetime.now(UTC).isoformat(),
        "acknowledged_by": actor_person_id,
    }
    integration_settings.upsert_by_key(
        db,
        "intelligence_data_health_risk_alert_ack",
        DomainSettingUpdate(
            value_type=SettingValueType.json,
            value_text=None,
            value_json=payload,
            is_secret=False,
            is_active=True,
        ),
    )
    return payload


def snooze_risk_alerts(
    db: Session,
    *,
    hours: int = 24,
    actor_person_id: str | None = None,
) -> dict[str, Any]:
    clamped_hours = _normalize_alert_snooze_hours(hours)
    until = datetime.now(UTC) + timedelta(hours=clamped_hours)
    payload = {
        "snooze_until": until.isoformat(),
        "snoozed_by": actor_person_id,
        "hours": clamped_hours,
    }
    integration_settings.upsert_by_key(
        db,
        "intelligence_data_health_risk_alert_snooze_until",
        DomainSettingUpdate(
            value_type=SettingValueType.json,
            value_text=None,
            value_json=payload,
            is_secret=False,
            is_active=True,
        ),
    )
    return payload


def compute_effective_risk_alerts(
    db: Session,
    *,
    latest_snapshot: dict[str, Any] | None,
    previous_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    raw = compute_risk_alerts(latest_snapshot, previous_snapshot)
    state = get_risk_alert_state(db)
    now = datetime.now(UTC)
    acknowledged_by = state.get("acknowledged_by")
    snoozed_by = state.get("snoozed_by")

    snooze_until = state.get("snooze_until")
    if isinstance(snooze_until, datetime) and snooze_until > now:
        return {
            **raw,
            "has_alerts": False,
            "suppressed": True,
            "suppression_reason": "snoozed",
            "snooze_until": snooze_until.isoformat(),
            "snoozed_by": snoozed_by,
            "raw_count": raw.get("count", 0),
        }

    acknowledged_at = state.get("acknowledged_at")
    latest_captured_at = _parse_iso_datetime((latest_snapshot or {}).get("captured_at"))
    if (
        isinstance(acknowledged_at, datetime)
        and isinstance(latest_captured_at, datetime)
        and latest_captured_at <= acknowledged_at
    ):
        return {
            **raw,
            "has_alerts": False,
            "suppressed": True,
            "suppression_reason": "acknowledged",
            "acknowledged_at": acknowledged_at.isoformat(),
            "acknowledged_by": acknowledged_by,
            "raw_count": raw.get("count", 0),
        }

    return {
        **raw,
        "suppressed": False,
        "suppression_reason": None,
        "acknowledged_at": acknowledged_at.isoformat() if isinstance(acknowledged_at, datetime) else None,
        "acknowledged_by": acknowledged_by,
        "snooze_until": snooze_until.isoformat() if isinstance(snooze_until, datetime) else None,
        "snoozed_by": snoozed_by,
        "raw_count": raw.get("count", 0),
    }


def _parse_iso_datetime(value: object | None) -> datetime | None:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _build_data_quality_risk_inventory(db: Session, *, days: int = 7) -> list[dict[str, Any]]:
    since = datetime.now(UTC) - timedelta(days=max(1, min(int(days), 30)))

    inbox_failures = (
        db.query(func.count(AuditEvent.id))
        .filter(AuditEvent.occurred_at >= since)
        .filter(AuditEvent.is_success.is_(False))
        .filter(
            or_(
                AuditEvent.action.ilike("%inbox%"),
                AuditEvent.action.ilike("%conversation%"),
                AuditEvent.action.ilike("%message%"),
                AuditEvent.action.ilike("%webhook%"),
                AuditEvent.entity_type.ilike("%conversation%"),
                AuditEvent.entity_type.ilike("%message%"),
            )
        )
        .scalar()
        or 0
    )

    ticket_assignment_failures = (
        db.query(func.count(AuditEvent.id))
        .filter(AuditEvent.occurred_at >= since)
        .filter(AuditEvent.is_success.is_(False))
        .filter(
            or_(
                AuditEvent.action.ilike("ticket_auto_assign%"),
                AuditEvent.action.ilike("%assignment%"),
                AuditEvent.entity_type.ilike("%ticket%"),
            )
        )
        .scalar()
        or 0
    )

    admin_manual_edit_failures = (
        db.query(func.count(AuditEvent.id))
        .filter(AuditEvent.occurred_at >= since)
        .filter(AuditEvent.is_success.is_(False))
        .filter(AuditEvent.actor_type == AuditActorType.user)
        .filter(
            or_(
                AuditEvent.action.in_(["create", "update", "delete", "status_change", "priority_change"]),
                AuditEvent.action.ilike("%manual%"),
            )
        )
        .scalar()
        or 0
    )

    import_sync_failures = (
        db.query(func.count(IntegrationRun.id))
        .filter(IntegrationRun.created_at >= since)
        .filter(IntegrationRun.status == IntegrationRunStatus.failed)
        .scalar()
        or 0
    )

    risks = [
        _risk_item(
            source_key="inbox_ingest",
            label="Inbox Ingest",
            failure_count=int(inbox_failures),
            rationale="Webhook/inbox message handling and conversation updates.",
        ),
        _risk_item(
            source_key="ticket_assignment",
            label="Ticket Assignment",
            failure_count=int(ticket_assignment_failures),
            rationale="Rule evaluation, candidate filtering, and assignment writes.",
        ),
        _risk_item(
            source_key="admin_manual_edits",
            label="Admin Manual Edits",
            failure_count=int(admin_manual_edit_failures),
            rationale="Direct human edits in admin forms and actions.",
        ),
        _risk_item(
            source_key="imports_sync",
            label="Imports and Sync Pipelines",
            failure_count=int(import_sync_failures),
            rationale="Scheduled/triggered integration runs and import pipelines.",
        ),
    ]
    return sorted(risks, key=lambda item: (item["severity_rank"], item["failure_count"]), reverse=True)


def _risk_item(*, source_key: str, label: str, failure_count: int, rationale: str) -> dict[str, Any]:
    owner_role, immediate_actions = _risk_response_playbook(source_key)
    if failure_count >= 20:
        severity = "high"
        severity_rank = 3
    elif failure_count >= 5:
        severity = "medium"
        severity_rank = 2
    else:
        severity = "low"
        severity_rank = 1
    return {
        "source_key": source_key,
        "label": label,
        "failure_count": int(max(failure_count, 0)),
        "severity": severity,
        "severity_rank": severity_rank,
        "rationale": rationale,
        "owner_role": owner_role,
        "immediate_actions": immediate_actions,
    }


def _risk_response_playbook(source_key: str) -> tuple[str, list[str]]:
    playbooks: dict[str, tuple[str, list[str]]] = {
        "inbox_ingest": (
            "Support Operations Lead",
            [
                "Inspect failed webhook and inbox audit events from the last 24h.",
                "Validate dedupe keys and channel identity normalization.",
                "Replay one failed payload in staging before production retry.",
            ],
        ),
        "ticket_assignment": (
            "Support Engineering Lead",
            [
                "Review rule match and candidate guard outputs for failed assignments.",
                "Verify queue fallback behavior for unmatched or zero-candidate paths.",
                "Patch rule ordering/conditions and backfill missed assignments.",
            ],
        ),
        "admin_manual_edits": (
            "System Admin",
            [
                "Review failed admin mutations and identify missing field constraints.",
                "Add form-level validation hints for high-error fields.",
                "Restrict risky bulk actions to scoped permissions where needed.",
            ],
        ),
        "imports_sync": (
            "Integration Engineer",
            [
                "Review failed integration runs and top connector error codes.",
                "Retry failed runs after credential/rate-limit correction.",
                "Add idempotency regression tests for affected sync path.",
            ],
        ),
    }
    return playbooks.get(
        source_key,
        (
            "Operations",
            [
                "Review recent failures and identify recurring validation errors.",
                "Define owner-specific remediation and retry runbook.",
            ],
        ),
    )
