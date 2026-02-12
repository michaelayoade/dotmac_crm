"""Resolve gate logic for CRM inbox.

When an agent resolves a conversation, this module checks whether the
conversation's person already has an active Lead.  If not, an interstitial
("gate") is shown so the agent can deliberately create a lead, link to an
existing contact, or skip lead creation entirely.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from app.models.crm.conversation import Conversation
from app.models.crm.sales import Lead
from app.services.common import coerce_uuid
from app.services.crm.inbox.conversation_status import update_conversation_status


@dataclass(frozen=True)
class GateCheckResult:
    kind: Literal["needs_gate", "no_gate", "not_found"]


def check_resolve_gate(
    db: Session,
    conversation_id: str,
) -> GateCheckResult:
    """Check whether the resolve gate should be shown for a conversation.

    Returns ``needs_gate`` when the conversation's person has **no** active
    Lead record (``is_active=True``).  Returns ``no_gate`` when at least one
    active lead exists (including lost leads, since the agent already
    processed them).
    """
    try:
        conv_uuid = coerce_uuid(conversation_id)
    except Exception:
        return GateCheckResult(kind="not_found")

    conversation = db.get(Conversation, conv_uuid)
    if not conversation or not conversation.person_id:
        return GateCheckResult(kind="not_found")

    has_active_lead = (
        db.query(Lead.id).filter(Lead.person_id == conversation.person_id).filter(Lead.is_active.is_(True)).first()
    ) is not None

    if has_active_lead:
        return GateCheckResult(kind="no_gate")
    return GateCheckResult(kind="needs_gate")


def resolve_with_lead(
    db: Session,
    *,
    conversation_id: str,
    actor_id: str | None = None,
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
) -> Literal["updated", "forbidden", "not_found", "error"]:
    """Create a Lead for the conversation's person, then resolve."""
    from app.schemas.crm.sales import LeadCreate
    from app.services.crm.sales.service import leads

    try:
        conv_uuid = coerce_uuid(conversation_id)
    except Exception:
        return "not_found"

    conversation = db.get(Conversation, conv_uuid)
    if not conversation or not conversation.person_id:
        return "not_found"

    # Create the lead (Leads.create handles title, owner, currency defaults)
    leads.create(db, LeadCreate(person_id=conversation.person_id))

    result = update_conversation_status(
        db,
        conversation_id=conversation_id,
        new_status="resolved",
        actor_id=actor_id,
        roles=roles,
        scopes=scopes,
    )
    if result.kind == "forbidden":
        return "forbidden"
    if result.kind in ("not_found", "invalid_status", "invalid_transition"):
        return "error"
    return "updated"


def resolve_without_lead(
    db: Session,
    *,
    conversation_id: str,
    actor_id: str | None = None,
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
) -> Literal["updated", "forbidden", "not_found", "error"]:
    """Resolve the conversation without creating a lead."""
    result = update_conversation_status(
        db,
        conversation_id=conversation_id,
        new_status="resolved",
        actor_id=actor_id,
        roles=roles,
        scopes=scopes,
    )
    if result.kind == "forbidden":
        return "forbidden"
    if result.kind in ("not_found", "invalid_status", "invalid_transition"):
        return "error"
    return "updated"
