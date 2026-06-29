"""Routing rules for inbound CRM inbox messages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.crm.conversation import Conversation, ConversationAssignment, Message
from app.models.crm.enums import AgentPresenceStatus, ConversationStatus
from app.models.crm.presence import AgentPresence
from app.models.crm.team import CrmAgent, CrmAgentTeam, CrmRoutingRule, CrmTeam
from app.services.crm import conversation as conversation_service
from app.services.crm.presence import DEFAULT_STALE_MINUTES
from app.services.crm.presence import agent_presence as presence_service

DEFAULT_MAX_CONCURRENT_CHATS = 3


@dataclass(frozen=True)
class RoutingDecision:
    rule_id: str
    team_id: str
    agent_id: str | None


def _normalize_keywords(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip().lower() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip().lower()]
    return []


def _message_matches_keywords(message: Message, rule_config: dict) -> bool:
    keywords = _normalize_keywords(rule_config.get("keywords"))
    if not keywords:
        return True
    body = (message.body or "").lower()
    if not body:
        return False
    match_mode = str(rule_config.get("match") or "any").lower()
    if match_mode == "all":
        return all(keyword in body for keyword in keywords)
    return any(keyword in body for keyword in keywords)


def _message_matches_target(message: Message, rule_config: dict) -> bool:
    target_id = rule_config.get("target_id")
    if not target_id:
        return True
    return str(message.channel_target_id) == str(target_id)


def _list_active_agents(db: Session, team_id: str) -> list[CrmAgent]:
    cutoff = datetime.now(UTC) - timedelta(minutes=DEFAULT_STALE_MINUTES)

    return (
        db.query(CrmAgent)
        .join(CrmAgentTeam, CrmAgentTeam.agent_id == CrmAgent.id)
        .join(AgentPresence, AgentPresence.agent_id == CrmAgent.id)
        .filter(CrmAgentTeam.team_id == team_id)
        .filter(CrmAgentTeam.is_active.is_(True))
        .filter(CrmAgent.is_active.is_(True))
        # Only agents with current heartbeat and available status can be auto-routed.
        .filter(AgentPresence.manual_override_status.is_(None))
        .filter(AgentPresence.status.in_([AgentPresenceStatus.online, AgentPresenceStatus.away]))
        .filter(AgentPresence.last_seen_at.isnot(None))
        .filter(AgentPresence.last_seen_at >= cutoff)
        .order_by(CrmAgent.created_at.asc())
        .all()
    )


def _global_max_concurrent(db: Session) -> int:
    """Default per-agent concurrent-chat cap (used when the agent has no override)."""
    from app.services.settings_spec import SettingDomain, resolve_value

    value = resolve_value(db, SettingDomain.notification, "crm_chat_max_concurrent_per_agent")
    if isinstance(value, int):
        return value if value > 0 else DEFAULT_MAX_CONCURRENT_CHATS
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return DEFAULT_MAX_CONCURRENT_CHATS


def _agent_active_chat_counts(db: Session, agent_ids: list) -> dict:
    """Active-chat count per agent. The canonical definition of an agent's load:
    an active assignment to an active conversation whose status is open or pending
    (excludes snoozed/resolved/closed)."""
    if not agent_ids:
        return {}
    rows = (
        db.query(ConversationAssignment.agent_id, func.count(ConversationAssignment.id))
        .join(Conversation, Conversation.id == ConversationAssignment.conversation_id)
        .filter(ConversationAssignment.agent_id.in_(agent_ids))
        .filter(ConversationAssignment.is_active.is_(True))
        .filter(Conversation.is_active.is_(True))
        .filter(Conversation.status.in_([ConversationStatus.open, ConversationStatus.pending]))
        .group_by(ConversationAssignment.agent_id)
        .all()
    )
    return {row[0]: int(row[1]) for row in rows}


def _agent_cap(agent: CrmAgent, default_cap: int) -> int:
    return agent.max_concurrent_chats if agent.max_concurrent_chats is not None else default_cap


def _list_available_agents(db: Session, team_id: str) -> list[CrmAgent]:
    """Online/away agents in the team that are still under their concurrency cap."""
    agents = _list_active_agents(db, team_id)
    if not agents:
        return []
    default_cap = _global_max_concurrent(db)
    counts = _agent_active_chat_counts(db, [agent.id for agent in agents])
    return [agent for agent in agents if counts.get(agent.id, 0) < _agent_cap(agent, default_cap)]


def _pick_least_loaded_agent(db: Session, team_id: str) -> str | None:
    agents = _list_active_agents(db, team_id)
    if not agents:
        return None
    # One load query drives both the capacity filter and the least-loaded sort.
    load_map = _agent_active_chat_counts(db, [agent.id for agent in agents])
    default_cap = _global_max_concurrent(db)
    available = [agent for agent in agents if load_map.get(agent.id, 0) < _agent_cap(agent, default_cap)]
    if not available:
        return None
    available.sort(key=lambda agent: load_map.get(agent.id, 0))
    return str(available[0].id)


def _pick_round_robin_agent(db: Session, team: CrmTeam, rule_id: str) -> str | None:
    agents = _list_available_agents(db, str(team.id))
    if not agents:
        return None
    agent_ids = [str(agent.id) for agent in agents]
    metadata = team.metadata_ if isinstance(team.metadata_, dict) else {}
    rr_state = metadata.get("routing_rr")
    if not isinstance(rr_state, dict):
        rr_state = {}
    last_agent_id = rr_state.get(rule_id)
    next_agent_id = agent_ids[0]
    if last_agent_id and last_agent_id in agent_ids:
        idx = agent_ids.index(last_agent_id)
        next_agent_id = agent_ids[(idx + 1) % len(agent_ids)]
    rr_state[rule_id] = next_agent_id
    metadata["routing_rr"] = rr_state
    team.metadata_ = metadata
    db.flush()
    return next_agent_id


def _agent_is_assignable(db: Session, agent_id: str) -> bool:
    agent = db.get(CrmAgent, agent_id)
    if not agent or not agent.is_active:
        return False
    presence = db.query(AgentPresence).filter(AgentPresence.agent_id == agent.id).first()
    effective_status = presence_service.effective_status(presence) if presence else AgentPresenceStatus.offline
    return effective_status in {AgentPresenceStatus.online, AgentPresenceStatus.away}


def _invalidate_unavailable_existing_assignment(
    db: Session,
    *,
    conversation: Conversation,
) -> bool:
    existing = (
        db.query(ConversationAssignment)
        .filter(ConversationAssignment.conversation_id == conversation.id)
        .filter(ConversationAssignment.is_active.is_(True))
        .filter(ConversationAssignment.agent_id.isnot(None))
        .first()
    )
    if not existing:
        return False
    if _agent_is_assignable(db, str(existing.agent_id)):
        return True

    existing.is_active = False
    existing.updated_at = datetime.now(UTC)
    from app.services.crm.inbox.summaries import recompute_conversation_summary_and_invalidate_cache

    recompute_conversation_summary_and_invalidate_cache(db, str(conversation.id))
    db.commit()
    return False


def _resolve_agent_for_rule(db: Session, team: CrmTeam, rule: CrmRoutingRule) -> str | None:
    config = rule.rule_config if isinstance(rule.rule_config, dict) else {}
    strategy = str(config.get("strategy") or "round_robin").lower()
    if strategy == "least_loaded":
        return _pick_least_loaded_agent(db, str(team.id))
    return _pick_round_robin_agent(db, team, str(rule.id))


def mark_conversation_queued(db: Session, conversation: Conversation) -> None:
    """Stamp ``queued_at`` the first time a conversation waits for an available
    agent. Idempotent: only sets it when currently unset and there is no active
    agent assignment, so a chat that bounces team->agent->team keeps its original
    queue-entry time (the correct denominator for queue-wait metrics)."""
    if conversation.queued_at is not None:
        return
    has_agent = (
        db.query(ConversationAssignment.id)
        .filter(ConversationAssignment.conversation_id == conversation.id)
        .filter(ConversationAssignment.is_active.is_(True))
        .filter(ConversationAssignment.agent_id.isnot(None))
        .first()
    )
    if has_agent is not None:
        return
    queued_at = datetime.now(UTC)
    conversation.queued_at = queued_at
    conversation.last_queued_at = queued_at
    db.add(conversation)
    db.commit()


def assign_or_enqueue(
    db: Session,
    *,
    conversation: Conversation,
    team_id: str,
    agent_id: str | None = None,
    assigned_by_id: str | None = None,
) -> ConversationAssignment | None:
    """Assign to an available agent, otherwise hold the conversation in the team
    queue. When ``agent_id`` is not supplied, the least-loaded available agent is
    chosen (capacity-aware); when nobody is available the chat is enqueued."""
    if agent_id is None:
        agent_id = _pick_least_loaded_agent(db, team_id)
    assignment = conversation_service.assign_conversation(
        db,
        conversation_id=str(conversation.id),
        agent_id=agent_id,
        team_id=team_id,
        assigned_by_id=assigned_by_id,
        update_lead_owner=False,
    )
    if assignment is None or assignment.agent_id is None:
        mark_conversation_queued(db, conversation)
    return assignment


def apply_routing_rules(
    db: Session,
    *,
    conversation: Conversation,
    message: Message,
) -> RoutingDecision | None:
    # Skip if already assigned to an agent
    if _invalidate_unavailable_existing_assignment(db, conversation=conversation):
        return None

    rules = (
        db.query(CrmRoutingRule)
        .filter(CrmRoutingRule.channel_type == message.channel_type)
        .filter(CrmRoutingRule.is_active.is_(True))
        .order_by(CrmRoutingRule.created_at.asc())
        .all()
    )
    for rule in rules:
        config = rule.rule_config if isinstance(rule.rule_config, dict) else {}
        if not _message_matches_keywords(message, config):
            continue
        if not _message_matches_target(message, config):
            continue
        team = db.get(CrmTeam, rule.team_id)
        if not team or not team.is_active:
            continue
        agent_id = _resolve_agent_for_rule(db, team, rule)
        assignment = assign_or_enqueue(
            db,
            conversation=conversation,
            team_id=str(team.id),
            agent_id=agent_id,
            assigned_by_id=None,
        )
        return RoutingDecision(
            rule_id=str(rule.id),
            team_id=str(team.id),
            agent_id=str(assignment.agent_id) if assignment and assignment.agent_id else None,
        )
    return None
