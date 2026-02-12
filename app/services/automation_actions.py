"""Action executor for automation rules.

Executes configured actions against entities resolved from event context.
Each action is wrapped in try/except for partial failure semantics.
"""

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.crm.conversation import Conversation, ConversationAssignment, ConversationTag
from app.models.crm.enums import AgentPresenceStatus, ConversationStatus
from app.models.crm.presence import AgentPresence
from app.models.crm.team import CrmAgent, CrmAgentTeam
from app.models.notification import Notification, NotificationChannel, NotificationStatus
from app.models.projects import Project
from app.models.tickets import Ticket
from app.models.workforce import WorkOrder
from app.services.common import coerce_uuid
from app.services.events.types import Event

logger = logging.getLogger(__name__)


class CreationRejectedError(Exception):
    """Raised by reject_creation action to signal that entity creation should be blocked."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


_MAX_AUTOMATION_DEPTH = 3

# Whitelisted fields per entity type to prevent arbitrary attribute mutation
_ALLOWED_FIELDS: dict[str, set[str]] = {
    "ticket": {"status", "priority", "assigned_to_person_id", "ticket_type"},
    "project": {"status", "priority", "region"},
    "work_order": {"status", "priority", "assigned_technician_id"},
    "conversation": {"status"},
}

_ENTITY_RESOLVERS: dict[str, Any] = {
    "ticket": lambda db, e: db.get(Ticket, e.ticket_id) if e.ticket_id else None,
    "project": lambda db, e: db.get(Project, e.project_id) if e.project_id else None,
    "work_order": lambda db, e: db.get(WorkOrder, e.work_order_id) if e.work_order_id else None,
    "conversation": lambda db, e: (
        db.get(Conversation, coerce_uuid(e.payload.get("conversation_id")))
        if e.payload.get("conversation_id")
        else None
    ),
}


def execute_actions(
    db: Session,
    actions: list[dict],
    event: Event,
    *,
    triggered_by_automation: bool = False,
) -> list[dict]:
    """Execute a list of actions and return per-action results.

    Returns list of {action_type, success, error} dicts.
    """
    results: list[dict] = []

    for action in actions:
        action_type = action.get("action_type", "")
        params = action.get("params", {})

        try:
            _dispatch_action(db, action_type, params, event, triggered_by_automation)
            results.append({"action_type": action_type, "success": True, "error": None})
        except Exception as exc:
            logger.exception("Automation action %s failed: %s", action_type, exc)
            results.append({"action_type": action_type, "success": False, "error": str(exc)})

    return results


def _dispatch_action(
    db: Session,
    action_type: str,
    params: dict,
    event: Event,
    triggered_by_automation: bool,
) -> None:
    """Route to the appropriate action handler."""
    if action_type == "assign_conversation":
        _execute_assign_conversation(db, params, event)
    elif action_type == "assign_conversation_auto":
        _execute_assign_conversation_auto(db, params, event)
    elif action_type == "set_field":
        _execute_set_field(db, params, event)
    elif action_type == "add_tag":
        _execute_add_tag(db, params, event)
    elif action_type == "send_notification":
        _execute_send_notification(db, params, event)
    elif action_type == "create_work_order":
        _execute_create_work_order(db, params, event)
    elif action_type == "emit_event":
        _execute_emit_event(db, params, event, triggered_by_automation)
    elif action_type == "reject_creation":
        _execute_reject_creation(params, event)
    else:
        raise ValueError(f"Unknown action type: {action_type}")


def _resolve_entity(db: Session, entity_type: str, event: Event) -> Any:
    """Resolve an entity from event context."""
    resolver = _ENTITY_RESOLVERS.get(entity_type)
    if not resolver:
        return None
    return resolver(db, event)


def _execute_assign_conversation(db: Session, params: dict, event: Event) -> None:
    """Assign a conversation to an agent."""
    conversation = _resolve_entity(db, "conversation", event)
    if not conversation:
        raise ValueError("Cannot resolve conversation from event context")

    agent_id = params.get("agent_id")
    if not agent_id:
        raise ValueError("agent_id is required for assign_conversation")
    try:
        agent_id = coerce_uuid(str(agent_id))
    except Exception as exc:
        raise ValueError("agent_id must be a valid UUID") from exc
    team_id = params.get("team_id")
    if team_id:
        try:
            team_id = coerce_uuid(str(team_id))
        except Exception as exc:
            raise ValueError("team_id must be a valid UUID") from exc
    assigned_by_id = params.get("assigned_by_id")
    if assigned_by_id:
        try:
            assigned_by_id = coerce_uuid(str(assigned_by_id))
        except Exception as exc:
            raise ValueError("assigned_by_id must be a valid UUID") from exc
    from app.services.crm.conversations.service import assign_conversation

    assign_conversation(
        db,
        conversation_id=str(conversation.id),
        agent_id=str(agent_id),
        team_id=str(team_id) if team_id else None,
        assigned_by_id=str(assigned_by_id) if assigned_by_id else None,
    )


def _execute_assign_conversation_auto(db: Session, params: dict, event: Event) -> None:
    """Assign a conversation to the best available online agent."""
    conversation = _resolve_entity(db, "conversation", event)
    if not conversation:
        raise ValueError("Cannot resolve conversation from event context")

    existing_assignment = (
        db.query(ConversationAssignment)
        .filter(ConversationAssignment.conversation_id == conversation.id)
        .filter(ConversationAssignment.is_active.is_(True))
        .first()
    )
    if existing_assignment:
        return

    online_window_minutes_raw = params.get("online_window_minutes", 60)
    if online_window_minutes_raw is None:
        raise ValueError("online_window_minutes must be an integer")
    try:
        online_window_minutes = int(online_window_minutes_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("online_window_minutes must be an integer") from exc
    if online_window_minutes <= 0:
        raise ValueError("online_window_minutes must be greater than 0")

    max_assigned_raw = params.get("max_assigned")
    if max_assigned_raw not in (None, "", "null"):
        try:
            max_assigned = int(str(max_assigned_raw))
        except (TypeError, ValueError) as exc:
            raise ValueError("max_assigned must be an integer") from exc
        if max_assigned < 0:
            raise ValueError("max_assigned must be 0 or greater")
    else:
        max_assigned = None

    team_id = params.get("team_id")
    if team_id:
        try:
            team_id = coerce_uuid(str(team_id))
        except Exception as exc:
            raise ValueError("team_id must be a valid UUID") from exc

    assigned_by_id = params.get("assigned_by_id")
    if assigned_by_id:
        try:
            assigned_by_id = coerce_uuid(str(assigned_by_id))
        except Exception as exc:
            raise ValueError("assigned_by_id must be a valid UUID") from exc

    def _parse_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return []
            if raw.startswith("[") and raw.endswith("]"):
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, list):
                        return [str(item).strip() for item in parsed if str(item).strip()]
                except Exception:
                    logger.debug("Failed to parse JSON list for automation action.", exc_info=True)
            return [item.strip() for item in raw.split(",") if item.strip()]
        return [str(value).strip()]

    eligible_statuses_raw = _parse_list(params.get("eligible_statuses"))
    if not eligible_statuses_raw:
        eligible_statuses = [AgentPresenceStatus.online, AgentPresenceStatus.away]
    else:
        eligible_statuses = []
        for status in eligible_statuses_raw:
            if status not in {s.value for s in AgentPresenceStatus}:
                raise ValueError(
                    f"eligible_statuses contains invalid value '{status}'. "
                    f"Allowed: {[s.value for s in AgentPresenceStatus]}"
                )
            eligible_statuses.append(AgentPresenceStatus(status))

    load_statuses_raw = _parse_list(params.get("load_statuses"))
    if not load_statuses_raw:
        load_statuses = [
            ConversationStatus.open,
            ConversationStatus.pending,
            ConversationStatus.snoozed,
        ]
    else:
        load_statuses = []
        for status in load_statuses_raw:
            if status not in {s.value for s in ConversationStatus}:
                raise ValueError(
                    f"load_statuses contains invalid value '{status}'. Allowed: {[s.value for s in ConversationStatus]}"
                )
            load_statuses.append(ConversationStatus(status))

    cutoff = datetime.now(UTC) - timedelta(minutes=online_window_minutes)

    candidate_query = (
        db.query(CrmAgent.id, CrmAgent.created_at)
        .join(AgentPresence, AgentPresence.agent_id == CrmAgent.id)
        .filter(CrmAgent.is_active.is_(True))
        .filter(AgentPresence.status.in_(eligible_statuses))
        .filter(AgentPresence.last_seen_at.isnot(None))
        .filter(AgentPresence.last_seen_at >= cutoff)
    )
    if team_id:
        candidate_query = candidate_query.join(CrmAgentTeam, CrmAgentTeam.agent_id == CrmAgent.id).filter(
            CrmAgentTeam.team_id == team_id,
            CrmAgentTeam.is_active.is_(True),
        )

    candidates = candidate_query.all()
    if not candidates:
        return

    candidate_ids = [row.id for row in candidates]
    count_rows = (
        db.query(
            ConversationAssignment.agent_id,
            func.count(ConversationAssignment.id),
        )
        .join(Conversation, ConversationAssignment.conversation_id == Conversation.id)
        .filter(ConversationAssignment.is_active.is_(True))
        .filter(ConversationAssignment.agent_id.in_(candidate_ids))
        .filter(Conversation.is_active.is_(True))
        .filter(Conversation.status.in_(load_statuses))
        .group_by(ConversationAssignment.agent_id)
        .all()
    )
    counts: dict[UUID, int] = {row[0]: int(row[1]) for row in count_rows if row[0] is not None}

    if max_assigned is not None:
        candidates = [row for row in candidates if counts.get(row.id, 0) <= max_assigned]
        if not candidates:
            return

    selected = min(
        candidates,
        key=lambda row: (
            counts.get(row.id, 0),
            row.created_at,
            str(row.id),
        ),
    )

    from app.services.crm.conversations.service import assign_conversation

    assign_conversation(
        db,
        conversation_id=str(conversation.id),
        agent_id=str(selected.id),
        team_id=str(team_id) if team_id else None,
        assigned_by_id=str(assigned_by_id) if assigned_by_id else None,
    )


def _execute_set_field(db: Session, params: dict, event: Event) -> None:
    """Set a whitelisted field on an entity."""
    entity_type = params.get("entity", "")
    field_name = params.get("field", "")
    value = params.get("value")

    if not entity_type:
        raise ValueError("entity is required for set_field")
    if not field_name:
        raise ValueError("field is required for set_field")
    allowed = _ALLOWED_FIELDS.get(entity_type, set())
    if field_name not in allowed:
        raise ValueError(f"Field '{field_name}' is not allowed on entity '{entity_type}'. Allowed: {sorted(allowed)}")

    entity = _resolve_entity(db, entity_type, event)
    if not entity:
        raise ValueError(f"Cannot resolve {entity_type} from event context")

    setattr(entity, field_name, value)
    db.flush()


def _execute_add_tag(db: Session, params: dict, event: Event) -> None:
    """Append a tag to an entity's JSON tags array."""
    entity_type = params.get("entity", "")
    tag = params.get("tag", "")

    if not tag:
        raise ValueError("tag is required for add_tag")

    entity = _resolve_entity(db, entity_type, event)
    if not entity:
        raise ValueError(f"Cannot resolve {entity_type} from event context")

    if entity_type == "conversation":
        existing = (
            db.query(ConversationTag)
            .filter(ConversationTag.conversation_id == entity.id)
            .filter(ConversationTag.tag == tag)
            .first()
        )
        if not existing:
            db.add(ConversationTag(conversation_id=entity.id, tag=tag))
            try:
                db.flush()
            except IntegrityError:
                db.rollback()
                return
        return

    if not hasattr(entity, "tags"):
        raise ValueError(f"Entity {entity_type} does not have a tags field")

    current_tags = entity.tags or []
    if tag not in current_tags:
        entity.tags = [*current_tags, tag]
        db.flush()


def _execute_send_notification(db: Session, params: dict, event: Event) -> None:
    """Create a queued Notification record."""
    recipient = params.get("recipient", "")
    subject = params.get("subject", "Automation notification")
    body = params.get("body", "")
    channel_str = params.get("channel", "email")

    if not recipient:
        raise ValueError("recipient is required for send_notification")

    try:
        channel = NotificationChannel(channel_str)
    except ValueError:
        channel = NotificationChannel.email

    notification = Notification(
        channel=channel,
        recipient=recipient,
        subject=subject,
        body=body,
        status=NotificationStatus.queued,
    )
    db.add(notification)
    db.flush()


def _execute_create_work_order(db: Session, params: dict, event: Event) -> None:
    """Create a WorkOrder linked to a ticket."""
    title = params.get("title", "Auto-generated work order")

    work_order = WorkOrder(title=title)

    if event.ticket_id:
        work_order.ticket_id = event.ticket_id
    if event.project_id:
        work_order.project_id = event.project_id
    if params.get("assigned_technician_id"):
        work_order.assigned_to_person_id = coerce_uuid(params["assigned_technician_id"])

    db.add(work_order)
    db.flush()


def _execute_emit_event(db: Session, params: dict, event: Event, triggered_by_automation: bool) -> None:
    """Emit a chained event with depth tracking."""
    from app.services.events.dispatcher import emit_event
    from app.services.events.types import EventType

    event_type_str = params.get("event_type", "")
    if not event_type_str:
        raise ValueError("event_type is required for emit_event")

    current_depth = event.payload.get("_automation_depth", 0)
    if current_depth >= _MAX_AUTOMATION_DEPTH:
        raise ValueError(f"Automation depth limit ({_MAX_AUTOMATION_DEPTH}) reached")

    try:
        event_type = EventType(event_type_str)
    except ValueError as exc:
        raise ValueError(f"Invalid event type: {event_type_str}") from exc

    payload = dict(params.get("payload", {}))
    payload["_automation_depth"] = current_depth + 1

    emit_event(
        db,
        event_type,
        payload,
        actor="automation",
        subscriber_id=event.subscriber_id,
        account_id=event.account_id,
        ticket_id=event.ticket_id,
        project_id=event.project_id,
        work_order_id=event.work_order_id,
    )


def _execute_reject_creation(params: dict, event: Event) -> None:
    """Reject entity creation by raising CreationRejectedError."""
    message = params.get("message", "Creation blocked by automation rule")
    raise CreationRejectedError(message)
