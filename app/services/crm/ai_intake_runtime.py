from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from celery.app.control import Inspect
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.celery_app import celery_app
from app.metrics import (
    set_ai_intake_last_success_age,
    set_ai_provider_circuit_open_duration,
    set_ai_queue_depth,
    set_ai_queue_oldest_task_age,
    set_ai_worker_health,
)
from app.models.crm.conversation import Conversation, Message
from app.models.crm.enums import ChannelType, MessageDirection
from app.services.ai.gateway import ai_gateway

logger = logging.getLogger(__name__)

_AI_RUNTIME_AUDIT_WINDOW = timedelta(hours=24)
_AI_ELIGIBLE_CHANNELS = (
    ChannelType.whatsapp,
    ChannelType.facebook_messenger,
    ChannelType.instagram_dm,
    ChannelType.chat_widget,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _safe_seconds_since(value: datetime | None) -> float | None:
    if value is None:
        return None
    return max((_now() - value).total_seconds(), 0.0)


def _redis_client():
    try:
        import redis
    except ImportError:
        return None

    broker_url = str(celery_app.conf.broker_url or "").strip()
    if not broker_url.startswith("redis"):
        return None
    return redis.Redis.from_url(broker_url)


def _inspect(timeout: float = 1.5) -> Inspect:
    return celery_app.control.inspect(timeout=timeout)


def _normalize_ping_response(raw: Any) -> dict[str, bool]:
    if isinstance(raw, dict):
        return {str(worker): True for worker in raw}
    if isinstance(raw, list):
        normalized: dict[str, bool] = {}
        for item in raw:
            if isinstance(item, dict):
                normalized.update({str(worker): True for worker in item})
        return normalized
    return {}


def _queue_names_from_active_queues(raw: dict[str, Any] | None) -> list[str]:
    names: list[str] = []
    for queues in (raw or {}).values():
        if not isinstance(queues, list):
            continue
        for queue in queues:
            if not isinstance(queue, dict):
                continue
            name = str(queue.get("name") or "").strip()
            if name:
                names.append(name)
    return sorted(set(names)) or ["celery"]


def _decode_queue_message_timestamp(raw: bytes | str | None) -> datetime | None:
    if raw is None:
        return None
    payload_text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    headers = payload.get("headers")
    if not isinstance(headers, dict):
        return None

    for key in ("sent_at", "enqueued_at", "timestamp", "eta"):
        parsed = _parse_timestamp(headers.get(key))
        if parsed is not None:
            return parsed
    return None


def ai_provider_connection_pool_state(db: Session) -> dict[str, Any]:
    primary = ai_gateway.get_endpoint_config(db, "primary")
    secondary = ai_gateway.get_endpoint_config(db, "secondary")
    return {
        "client_mode": "per_request_httpx_client",
        "pool_reuse_enabled": False,
        "stale_keepalive_risk": "low",
        "note": "AI requests instantiate a fresh synchronous httpx.Client for each request; there is no long-lived shared HTTP pool to recycle.",
        "endpoints": [
            {
                "endpoint_name": "primary",
                "provider": primary.label,
                "base_url": primary.base_url,
                "host": urlparse(primary.base_url).hostname if primary.base_url else None,
                "model": primary.model,
                "configured": bool(primary.base_url and primary.model),
                "timeout_seconds": primary.timeout_seconds,
                "max_retries": primary.max_retries,
            },
            {
                "endpoint_name": "secondary",
                "provider": secondary.label,
                "base_url": secondary.base_url,
                "host": urlparse(secondary.base_url).hostname if secondary.base_url else None,
                "model": secondary.model,
                "configured": bool(secondary.base_url and secondary.model),
                "timeout_seconds": secondary.timeout_seconds,
                "max_retries": secondary.max_retries,
            },
        ],
    }


def ai_circuit_state_snapshot(db: Session) -> dict[str, Any]:
    endpoints: list[dict[str, Any]] = []
    for endpoint_name in ("primary", "secondary"):
        state = ai_gateway.circuit_state(db, endpoint_name)
        set_ai_provider_circuit_open_duration(
            provider=str(state.get("provider") or endpoint_name),
            model=str(state.get("model") or ""),
            endpoint=endpoint_name,
            duration_seconds=float(state.get("open_duration_seconds") or 0.0),
        )
        endpoints.append(state)
    return {
        "captured_at": _now().isoformat(),
        "endpoints": endpoints,
        "any_open": any(bool(item.get("is_open")) for item in endpoints),
    }


def ai_worker_health_snapshot(timeout: float = 1.5) -> dict[str, Any]:
    inspector = _inspect(timeout=timeout)
    ping_raw = inspector.ping() or {}
    ping = _normalize_ping_response(ping_raw)
    stats = inspector.stats() or {}
    active = inspector.active() or {}
    reserved = inspector.reserved() or {}
    scheduled = inspector.scheduled() or {}
    active_queues = inspector.active_queues() or {}

    worker_names = sorted(set(stats) | set(active) | set(reserved) | set(scheduled) | set(ping))
    workers: list[dict[str, Any]] = []
    for worker_name in worker_names:
        worker_stats = stats.get(worker_name) if isinstance(stats, dict) else None
        worker_stats_dict: dict[str, Any] = worker_stats if isinstance(worker_stats, dict) else {}
        pool_info_raw = worker_stats_dict.get("pool")
        pool_info: dict[str, Any] = pool_info_raw if isinstance(pool_info_raw, dict) else {}
        rusage_info_raw = worker_stats_dict.get("rusage")
        rusage_info: dict[str, Any] = rusage_info_raw if isinstance(rusage_info_raw, dict) else {}
        active_tasks = len(active.get(worker_name) or [])
        reserved_tasks = len(reserved.get(worker_name) or [])
        scheduled_tasks = len(scheduled.get(worker_name) or [])
        queues = active_queues.get(worker_name) or []
        is_up = bool(ping.get(worker_name))
        worker_snapshot = {
            "worker_name": worker_name,
            "is_up": is_up,
            "active_tasks": active_tasks,
            "reserved_tasks": reserved_tasks,
            "scheduled_tasks": scheduled_tasks,
            "prefetch_count": pool_info.get("prefetch_count"),
            "pool": {
                "implementation": pool_info.get("implementation"),
                "max_concurrency": pool_info.get("max-concurrency"),
                "max_tasks_per_child": pool_info.get("max-tasks-per-child"),
                "processes": pool_info.get("processes"),
                "put_guarded_by_semaphore": pool_info.get("put-guarded-by-semaphore"),
                "timeouts": pool_info.get("timeouts"),
                "writes": pool_info.get("writes"),
            },
            "broker": worker_stats_dict.get("broker"),
            "uptime_seconds": worker_stats_dict.get("uptime"),
            "pid": worker_stats_dict.get("pid"),
            "rusage": {
                "maxrss": rusage_info.get("maxrss"),
                "utime": rusage_info.get("utime"),
                "stime": rusage_info.get("stime"),
                "nvcsw": rusage_info.get("nvcsw"),
                "nivcsw": rusage_info.get("nivcsw"),
            },
            "queues": [
                {
                    "name": queue.get("name"),
                    "routing_key": queue.get("routing_key"),
                    "exchange": (queue.get("exchange") or {}).get("name") if isinstance(queue, dict) else None,
                }
                for queue in queues
                if isinstance(queue, dict)
            ],
        }
        set_ai_worker_health(
            worker_name=worker_name,
            is_up=is_up,
            active_tasks=active_tasks,
            reserved_tasks=reserved_tasks,
            scheduled_tasks=scheduled_tasks,
        )
        workers.append(worker_snapshot)

    return {
        "captured_at": _now().isoformat(),
        "worker_count": len(workers),
        "workers": workers,
        "queue_names": _queue_names_from_active_queues(active_queues if isinstance(active_queues, dict) else {}),
    }


def ai_queue_depth_snapshot(queue_names: Iterable[str] | None = None) -> dict[str, Any]:
    client = _redis_client()
    if client is None:
        return {
            "captured_at": _now().isoformat(),
            "available": False,
            "reason": "redis_broker_unavailable",
            "queues": [],
            "total_depth": 0,
        }

    if queue_names is None:
        worker_snapshot = ai_worker_health_snapshot()
        queue_names = worker_snapshot.get("queue_names") or ["celery"]

    queues: list[dict[str, Any]] = []
    total_depth = 0
    for queue_name in sorted(set(str(name) for name in queue_names if str(name).strip())):
        try:
            key_type = client.type(queue_name)
            normalized_type = key_type.decode() if isinstance(key_type, bytes) else str(key_type)
            depth = int(client.llen(queue_name) if normalized_type == "list" else 0)
            oldest_raw = client.lindex(queue_name, -1) if depth > 0 and normalized_type == "list" else None
            oldest_task_at = _decode_queue_message_timestamp(oldest_raw)
            oldest_age_seconds = _safe_seconds_since(oldest_task_at)
        except Exception as exc:
            logger.warning("ai_queue_depth_snapshot_failed queue=%s error=%s", queue_name, exc)
            normalized_type = "error"
            depth = 0
            oldest_task_at = None
            oldest_age_seconds = None
        total_depth += depth
        set_ai_queue_depth(queue_name=queue_name, depth=depth)
        set_ai_queue_oldest_task_age(queue_name=queue_name, age_seconds=oldest_age_seconds)
        queues.append(
            {
                "queue_name": queue_name,
                "redis_type": normalized_type,
                "depth": depth,
                "oldest_task_at": oldest_task_at.isoformat() if oldest_task_at else None,
                "oldest_task_age_seconds": oldest_age_seconds,
                "oldest_task_age_available": oldest_task_at is not None,
            }
        )

    return {
        "captured_at": _now().isoformat(),
        "available": True,
        "queues": queues,
        "total_depth": total_depth,
    }


def _recent_hourly_status_counts(db: Session, window_start: datetime) -> list[dict[str, Any]]:
    rows = (
        db.query(
            func.coalesce(Message.received_at, Message.created_at).label("message_at"),
            Conversation.metadata_["ai_intake"]["status"].as_string().label("status"),
        )
        .join(Conversation, Conversation.id == Message.conversation_id)
        .filter(Message.direction == MessageDirection.inbound)
        .filter(Message.channel_type.in_(_AI_ELIGIBLE_CHANNELS))
        .filter(func.coalesce(Message.received_at, Message.created_at) >= window_start)
        .filter(Conversation.metadata_.isnot(None))
        .all()
    )
    counts: dict[tuple[str, str], int] = {}
    for row in rows:
        message_at = row.message_at
        if not isinstance(message_at, datetime):
            continue
        normalized = message_at.astimezone(UTC) if message_at.tzinfo else message_at.replace(tzinfo=UTC)
        hour = normalized.replace(minute=0, second=0, microsecond=0).isoformat()
        status = str(row.status or "unknown")
        counts[(hour, status)] = counts.get((hour, status), 0) + 1

    items = [{"hour_utc": hour, "status": status, "count": count} for (hour, status), count in counts.items()]
    items.sort(key=lambda item: (item["hour_utc"], item["status"]), reverse=True)
    return items


def _recent_ai_error_conversations(db: Session, limit: int = 20) -> list[dict[str, Any]]:
    rows = (
        db.query(
            Conversation.id,
            Conversation.updated_at,
            Conversation.metadata_["ai_intake"]["escalated_reason"].as_string().label("escalated_reason"),
            Conversation.metadata_["ai_intake"]["failure_type"].as_string().label("failure_type"),
            Conversation.metadata_["ai_intake"]["timeout_type"].as_string().label("timeout_type"),
            Conversation.metadata_["ai_intake"]["endpoint"].as_string().label("endpoint"),
            Conversation.metadata_["ai_intake"]["provider"].as_string().label("provider"),
            Conversation.metadata_["ai_intake"]["request_id"].as_string().label("request_id"),
        )
        .filter(Conversation.metadata_.isnot(None))
        .filter(Conversation.metadata_["ai_intake"]["status"].as_string() == "escalated")
        .filter(Conversation.metadata_["ai_intake"]["escalated_reason"].as_string() == "ai_error")
        .order_by(Conversation.updated_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "conversation_id": str(row.id),
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "escalated_reason": row.escalated_reason,
            "failure_type": row.failure_type,
            "timeout_type": row.timeout_type,
            "endpoint": row.endpoint,
            "provider": row.provider,
            "request_id": row.request_id,
        }
        for row in rows
    ]


def _last_resolved_ai_intake_at(db: Session) -> datetime | None:
    conversation = (
        db.query(Conversation)
        .filter(Conversation.metadata_.isnot(None))
        .filter(Conversation.metadata_["ai_intake"]["status"].as_string() == "resolved")
        .order_by(Conversation.updated_at.desc())
        .first()
    )
    if conversation is None:
        return None
    metadata = conversation.metadata_ if isinstance(conversation.metadata_, dict) else {}
    ai_intake_state = metadata.get("ai_intake")
    state: dict[str, Any] = ai_intake_state if isinstance(ai_intake_state, dict) else {}
    return _parse_timestamp(state.get("resolved_at")) or (
        conversation.updated_at.astimezone(UTC)
        if conversation.updated_at and conversation.updated_at.tzinfo
        else conversation.updated_at
    )


def ai_intake_runtime_audit(db: Session) -> dict[str, Any]:
    captured_at = _now()
    window_start = captured_at - _AI_RUNTIME_AUDIT_WINDOW
    circuit = ai_circuit_state_snapshot(db)
    worker = ai_worker_health_snapshot()
    queue = ai_queue_depth_snapshot(worker.get("queue_names"))
    recent_ai_errors = _recent_ai_error_conversations(db)
    last_resolved_at = _last_resolved_ai_intake_at(db)
    last_resolved_age_seconds = _safe_seconds_since(last_resolved_at)
    set_ai_intake_last_success_age(age_seconds=last_resolved_age_seconds)

    pending_status_rows = (
        db.query(Conversation.metadata_["ai_intake"]["status"].as_string().label("status"))
        .filter(Conversation.metadata_.isnot(None))
        .filter(
            Conversation.metadata_["ai_intake"]["status"]
            .as_string()
            .in_(("pending", "awaiting_customer", "awaiting_timeout"))
        )
        .all()
    )
    pending_state_counts: dict[str, int] = {}
    for row in pending_status_rows:
        status = str(row.status or "unknown")
        pending_state_counts[status] = pending_state_counts.get(status, 0) + 1

    intake = {
        "captured_at": captured_at.isoformat(),
        "last_resolved_at": last_resolved_at.isoformat() if last_resolved_at else None,
        "last_resolved_age_seconds": last_resolved_age_seconds,
        "recent_ai_error_count": len(recent_ai_errors),
        "recent_ai_errors": recent_ai_errors,
        "pending_state_counts": pending_state_counts,
        "hourly_status_counts": _recent_hourly_status_counts(db, window_start),
    }

    return {
        "captured_at": captured_at.isoformat(),
        "classification_candidates": {
            "provider_timeout": any(
                item.get("failure_type") in {None, "timeout"} or item.get("timeout_type") is not None
                for item in recent_ai_errors
            ),
            "worker_stall": False,
            "celery_queue_blocked": bool(queue.get("total_depth", 0) > 100),
            "redis_connectivity": not bool(queue.get("available")),
            "stale_http_connections": False,
            "db_pool_exhaustion": False,
            "circuit_breaker_never_recovering": bool(
                any(
                    bool(endpoint.get("is_open")) and float(endpoint.get("open_duration_seconds") or 0.0) > 300.0
                    for endpoint in circuit.get("endpoints", [])
                )
            ),
        },
        "provider_connection_pool": ai_provider_connection_pool_state(db),
        "circuit": circuit,
        "worker": worker,
        "queue": queue,
        "intake": intake,
    }
