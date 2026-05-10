"""Lead + Quote provider for the Workqueue.

The CRM `Lead` model has no ``next_action_at`` or ``last_activity_at``
columns; per the implementation plan we derive them from
``Lead.metadata_`` (JSON) — the same pattern used by the conversations
and tickets providers.  Lead ownership uses the real ``Lead.owner_agent_id``
column joined through ``CrmAgent.person_id``.

The CRM `Quote` model has no owner column at all and no ``sent_at``
column, so we stash ``owner_person_id`` and ``sent_at`` inside
``Quote.metadata_`` (JSON).

This single provider returns items of two kinds (``ItemKind.lead`` and
``ItemKind.quote``); the aggregator partitions by ``item.kind`` so each
ends up in its own UI section.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.crm.enums import LeadStatus, QuoteStatus
from app.models.crm.sales import Lead, Quote
from app.models.crm.team import CrmAgent
from app.services.workqueue.providers import register
from app.services.workqueue.scoring_config import LEAD_QUOTE_SCORES, PROVIDER_LIMIT
from app.services.workqueue.types import (
    ActionKind,
    ItemKind,
    WorkqueueAudience,
    WorkqueueItem,
    urgency_for_score,
)

_HIGH_VALUE_THRESHOLD = 5000.0


def _parse_dt(value) -> datetime | None:
    """Parse a datetime stored in JSON metadata."""
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


def _parse_uuid(value) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError:
            return None
    return None


def _meta(obj) -> dict:
    return getattr(obj, "metadata_", None) or {}


def _lead_next_action_at(lead: Lead) -> datetime | None:
    return _parse_dt(_meta(lead).get("next_action_at"))


def _lead_last_activity_at(lead: Lead) -> datetime | None:
    return _parse_dt(_meta(lead).get("last_activity_at"))


def _quote_sent_at(q: Quote) -> datetime | None:
    return _parse_dt(_meta(q).get("sent_at"))


def _quote_owner_person_id(q: Quote) -> UUID | None:
    return _parse_uuid(_meta(q).get("owner_person_id"))


def _classify_quote(q: Quote, now: datetime) -> tuple[str, int] | None:
    if q.status != QuoteStatus.sent:
        return None
    if q.expires_at is not None:
        expires_at = q.expires_at if q.expires_at.tzinfo else q.expires_at.replace(tzinfo=UTC)
        delta = (expires_at - now).total_seconds()
        if 0 < delta <= 24 * 3600:
            return "quote_expires_today", LEAD_QUOTE_SCORES["quote_expires_today"]
        if 24 * 3600 < delta <= 3 * 24 * 3600:
            return "quote_expires_3d", LEAD_QUOTE_SCORES["quote_expires_3d"]
    sent_at = _quote_sent_at(q)
    if sent_at is not None and (now - sent_at).total_seconds() > 7 * 24 * 3600:
        return "quote_sent_no_response_7d", LEAD_QUOTE_SCORES["quote_sent_no_response_7d"]
    return None


def _classify_lead(lead: Lead, now: datetime) -> tuple[str, int] | None:
    if lead.status in (LeadStatus.won, LeadStatus.lost):
        return None
    next_action = _lead_next_action_at(lead)
    if next_action is not None and next_action < now:
        return "lead_overdue_followup", LEAD_QUOTE_SCORES["lead_overdue_followup"]
    weighted = float(lead.estimated_value or 0) * float(lead.probability or 0)
    last_touch = _lead_last_activity_at(lead) or lead.updated_at
    if last_touch is not None and last_touch.tzinfo is None:
        last_touch = last_touch.replace(tzinfo=UTC)
    if (
        weighted >= _HIGH_VALUE_THRESHOLD
        and last_touch is not None
        and (now - last_touch).total_seconds() > 3 * 24 * 3600
    ):
        return "lead_high_value_idle_3d", LEAD_QUOTE_SCORES["lead_high_value_idle_3d"]
    return None


class LeadsQuotesProvider:
    """Workqueue provider that surfaces actionable leads and quotes."""

    # Primary registration kind; quote items emitted by the same provider are
    # routed to their own section by the aggregator (which partitions by
    # ``item.kind``, not ``provider.kind``).
    kind = ItemKind.lead

    def fetch(
        self,
        db: Session,
        *,
        user,
        audience: WorkqueueAudience,
        snoozed_ids: set[UUID],
        limit: int = PROVIDER_LIMIT,
    ) -> list[WorkqueueItem]:
        now = datetime.now(UTC)
        items: list[WorkqueueItem] = []

        # ---- Leads ------------------------------------------------------
        lead_stmt = (
            select(Lead).where(Lead.is_active.is_(True)).where(Lead.status.notin_((LeadStatus.won, LeadStatus.lost)))
        )
        if audience is WorkqueueAudience.self_:
            lead_stmt = lead_stmt.join(CrmAgent, CrmAgent.id == Lead.owner_agent_id).where(
                CrmAgent.person_id == user.person_id
            )
        elif audience is WorkqueueAudience.team:
            lead_stmt = lead_stmt.outerjoin(CrmAgent, CrmAgent.id == Lead.owner_agent_id).where(
                or_(CrmAgent.person_id == user.person_id, Lead.owner_agent_id.is_(None))
            )
        # WorkqueueAudience.org: surface every actionable lead.

        if snoozed_ids:
            lead_stmt = lead_stmt.where(~Lead.id.in_(snoozed_ids))

        lead_rows = db.execute(lead_stmt.limit(limit * 2)).scalars().unique().all()
        # Pre-load owner agents to resolve assignee_id without N+1.
        lead_owner_ids = {row.owner_agent_id for row in lead_rows if row.owner_agent_id}
        agents_by_id: dict[UUID, CrmAgent] = {}
        if lead_owner_ids:
            for agent in db.execute(select(CrmAgent).where(CrmAgent.id.in_(lead_owner_ids))).scalars().all():
                agents_by_id[agent.id] = agent

        for lead in lead_rows:
            verdict = _classify_lead(lead, now)
            if verdict is None:
                continue
            reason, score = verdict
            assignee_person_id: UUID | None = None
            if lead.owner_agent_id is not None:
                owner_agent = agents_by_id.get(lead.owner_agent_id)
                if owner_agent is not None:
                    assignee_person_id = owner_agent.person_id
            actions = {ActionKind.open, ActionKind.snooze}
            if assignee_person_id is None:
                actions.add(ActionKind.claim)
            items.append(
                WorkqueueItem(
                    kind=ItemKind.lead,
                    item_id=lead.id,
                    title=lead.title or f"Lead {lead.id}",
                    subtitle=reason.replace("_", " ").title(),
                    score=score,
                    reason=reason,
                    urgency=urgency_for_score(score),
                    deep_link=f"/admin/leads/{lead.id}",
                    assignee_id=assignee_person_id,
                    is_unassigned=assignee_person_id is None,
                    happened_at=lead.updated_at or now,
                    actions=frozenset(actions),
                    metadata={"value": float(lead.estimated_value or 0)},
                )
            )

        # ---- Quotes -----------------------------------------------------
        # Quote has no owner column; ownership is stored in metadata_.  We
        # fetch all sent quotes and filter by the metadata-derived owner in
        # Python, mirroring the conversations/tickets pattern for fields not
        # present on the model.
        quote_stmt = select(Quote).where(Quote.is_active.is_(True)).where(Quote.status == QuoteStatus.sent)
        if snoozed_ids:
            quote_stmt = quote_stmt.where(~Quote.id.in_(snoozed_ids))

        quote_rows = db.execute(quote_stmt.limit(limit * 4)).scalars().unique().all()

        for q in quote_rows:
            owner_person_id = _quote_owner_person_id(q)
            if audience is WorkqueueAudience.self_ and owner_person_id != user.person_id:
                continue
            if audience is WorkqueueAudience.team and owner_person_id not in (
                user.person_id,
                None,
            ):
                continue

            verdict = _classify_quote(q, now)
            if verdict is None:
                continue
            reason, score = verdict
            actions = {ActionKind.open, ActionKind.snooze}
            if owner_person_id is None:
                actions.add(ActionKind.claim)
            items.append(
                WorkqueueItem(
                    kind=ItemKind.quote,
                    item_id=q.id,
                    title=f"Q-{q.id}",
                    subtitle=reason.replace("_", " ").title(),
                    score=score,
                    reason=reason,
                    urgency=urgency_for_score(score),
                    deep_link=f"/admin/quotes/{q.id}",
                    assignee_id=owner_person_id,
                    is_unassigned=owner_person_id is None,
                    happened_at=q.updated_at or now,
                    actions=frozenset(actions),
                    metadata={"total": float(q.total or 0)},
                )
            )

        items.sort(key=lambda i: -i.score)
        return items[:limit]


leads_quotes_provider = register(LeadsQuotesProvider())
