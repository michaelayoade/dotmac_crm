"""Conversation provider for the Workqueue.

The CRM `Conversation` model has no `sla_due_at`, `last_inbound_at`, or
`is_assigned_unread` columns and `ConversationAssignment.agent_id` references
`crm_agents`, not a person directly.  Per the implementation plan we derive
what we need without altering the schema:

* SLA due time and the latest inbound message timestamp are read from
  `Conversation.metadata_` (JSON).  Producers (webhooks, services, fixtures)
  populate these alongside other conversation metadata.
* Assignment to the current user is resolved via a join through
  `ConversationAssignment` and `CrmAgent` on ``person_id``.
* The "assigned unread" classification path is intentionally omitted here
  until we have a clean way to compute it without N+1 queries; the plan
  explicitly allows skipping it in this slice.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.crm.conversation import Conversation, ConversationAssignment
from app.models.crm.enums import ConversationStatus
from app.services.workqueue.providers import register
from app.services.workqueue.scope import WorkqueueScope, apply_conversation_scope
from app.services.workqueue.scoring_config import (
    CONV_SLA_IMMINENT_SEC,
    CONV_SLA_SOON_SEC,
    CONVERSATION_SCORES,
    PROVIDER_LIMIT,
)
from app.services.workqueue.types import (
    ActionKind,
    ItemKind,
    WorkqueueAudience,
    WorkqueueItem,
    urgency_for_score,
)

_OPEN_STATUSES = (ConversationStatus.open, ConversationStatus.pending)
logger = logging.getLogger(__name__)


def _parse_dt(value) -> datetime | None:
    """Parse a datetime stored in JSON metadata.

    Accepts ``datetime`` instances or ISO-8601 strings.  Returns timezone-aware
    UTC datetimes; naive values are assumed to be UTC.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _meta(conv: Conversation) -> dict:
    return conv.metadata_ or {}


def _sla_due_at(conv: Conversation) -> datetime | None:
    return _parse_dt(_meta(conv).get("sla_due_at"))


def _last_inbound_at(conv: Conversation) -> datetime | None:
    return _parse_dt(_meta(conv).get("last_inbound_at"))


def _active_assignee_person_id(conv: Conversation) -> UUID | None:
    """Return the person id of the active assignee, if any."""
    for assignment in conv.assignments or ():
        if not assignment.is_active:
            continue
        agent = assignment.agent
        if agent is not None:
            return agent.person_id
    return None


def _visibility_source(conv: Conversation, scope: WorkqueueScope) -> str:
    assignee = _active_assignee_person_id(conv)
    team_ids = {
        assignment.team_id for assignment in (conv.assignments or ()) if assignment.is_active and assignment.team_id
    }
    if assignee == scope.person_id:
        return "direct_assignment"
    if assignee is not None and assignee in scope.accessible_person_ids:
        return "profile_related_assignment"
    if scope.accessible_crm_team_ids and team_ids.intersection(scope.accessible_crm_team_ids):
        return "department_team_assignment"
    return "unknown"


def _classify(conv: Conversation, now: datetime) -> tuple[str, int] | None:
    sla_due = _sla_due_at(conv)
    if sla_due is not None:
        delta = (sla_due - now).total_seconds()
        if delta <= 0:
            return "sla_breach", CONVERSATION_SCORES["sla_breach"]
        if delta <= CONV_SLA_IMMINENT_SEC:
            return "sla_imminent", CONVERSATION_SCORES["sla_imminent"]
        if delta <= CONV_SLA_SOON_SEC:
            return "sla_soon", CONVERSATION_SCORES["sla_soon"]

    last_in = _last_inbound_at(conv)
    if last_in is not None and (now - last_in).total_seconds() > 4 * 3600:
        return "awaiting_reply_long", CONVERSATION_SCORES["awaiting_reply_long"]

    return None


def _deep_link(conv: Conversation) -> str:
    return f"/admin/inbox/conversations/{conv.id}"


def _title(conv: Conversation) -> str:
    return conv.subject or f"Conversation {conv.id}"


def _subtitle(reason: str, conv: Conversation, now: datetime) -> str:
    sla_due = _sla_due_at(conv)
    if reason == "sla_breach" and sla_due:
        secs = int((now - sla_due).total_seconds())
        return f"SLA breached {secs // 60}m ago"
    if reason in ("sla_imminent", "sla_soon") and sla_due:
        secs = int((sla_due - now).total_seconds())
        return f"SLA in {secs // 60}m"
    if reason == "awaiting_reply_long":
        return "Awaiting reply > 4h"
    return reason.replace("_", " ").title()


class ConversationsProvider:
    """Workqueue provider that surfaces actionable CRM conversations."""

    kind = ItemKind.conversation

    def fetch(
        self,
        db: Session,
        *,
        user,
        audience: WorkqueueAudience,
        scope: WorkqueueScope,
        snoozed_ids: set[UUID],
        limit: int = PROVIDER_LIMIT,
    ) -> list[WorkqueueItem]:
        now = datetime.now(UTC)

        stmt = (
            select(Conversation)
            .options(selectinload(Conversation.assignments).selectinload(ConversationAssignment.agent))
            .where(Conversation.status.in_(_OPEN_STATUSES))
            .where(Conversation.is_active.is_(True))
        )
        stmt = apply_conversation_scope(stmt, scope)

        if snoozed_ids:
            stmt = stmt.where(~Conversation.id.in_(snoozed_ids))

        stmt = stmt.limit(limit * 2)
        rows = db.execute(stmt).scalars().unique().all()

        items: list[WorkqueueItem] = []
        for conv in rows:
            verdict = _classify(conv, now)
            if verdict is None:
                continue
            reason, score = verdict
            assignee = _active_assignee_person_id(conv)
            actions = {ActionKind.open, ActionKind.snooze, ActionKind.complete}
            if assignee is None:
                actions.add(ActionKind.claim)
            visibility_source = _visibility_source(conv, scope)
            logger.info(
                "workqueue_item_included kind=conversation user_id=%s item_id=%s visibility_source=%s assignee_source=%s team_source=%s",
                scope.person_id,
                conv.id,
                visibility_source,
                assignee,
                [
                    str(assignment.team_id)
                    for assignment in (conv.assignments or ())
                    if assignment.is_active and assignment.team_id
                ],
            )
            items.append(
                WorkqueueItem(
                    kind=ItemKind.conversation,
                    item_id=conv.id,
                    title=_title(conv),
                    subtitle=_subtitle(reason, conv, now),
                    score=score,
                    reason=reason,
                    urgency=urgency_for_score(score),
                    deep_link=_deep_link(conv),
                    assignee_id=assignee,
                    is_unassigned=assignee is None,
                    happened_at=(_last_inbound_at(conv) or conv.last_message_at or conv.updated_at or now),
                    actions=frozenset(actions),
                    metadata={
                        "priority": getattr(conv.priority, "value", None) if conv.priority is not None else None,
                        "visibility_source": visibility_source,
                    },
                )
            )

        items.sort(key=lambda i: -i.score)
        return items[:limit]


conversations_provider = register(ConversationsProvider())
