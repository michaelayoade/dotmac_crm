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


def _pick_least_loaded_agent(db: Session, team_id: str) -> str | None:
    agents = _list_active_agents(db, team_id)
    if not agents:
        return None
    agent_ids = [agent.id for agent in agents]
    if not agent_ids:
        return None
    load_rows = (
        db.query(ConversationAssignment.agent_id, func.count(ConversationAssignment.id))
        .join(Conversation, Conversation.id == ConversationAssignment.conversation_id)
        .filter(ConversationAssignment.agent_id.in_(agent_ids))
        .filter(ConversationAssignment.is_active.is_(True))
        .filter(Conversation.is_active.is_(True))
        .filter(Conversation.status != ConversationStatus.resolved)
        .group_by(ConversationAssignment.agent_id)
        .all()
    )
    load_map = {row[0]: int(row[1]) for row in load_rows}
    agent_ids_sorted = sorted(agent_ids, key=lambda agent_id: load_map.get(agent_id, 0))
    return str(agent_ids_sorted[0]) if agent_ids_sorted else None


def _pick_round_robin_agent(db: Session, team: CrmTeam, rule_id: str) -> str | None:
    agents = _list_active_agents(db, str(team.id))
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


def _resolve_agent_for_rule(db: Session, team: CrmTeam, rule: CrmRoutingRule) -> str | None:
    config = rule.rule_config if isinstance(rule.rule_config, dict) else {}
    strategy = str(config.get("strategy") or "round_robin").lower()
    if strategy == "least_loaded":
        return _pick_least_loaded_agent(db, str(team.id))
    return _pick_round_robin_agent(db, team, str(rule.id))


def apply_routing_rules(
    db: Session,
    *,
    conversation: Conversation,
    message: Message,
) -> RoutingDecision | None:
    # Skip if already assigned to an agent
    existing = (
        db.query(ConversationAssignment)
        .filter(ConversationAssignment.conversation_id == conversation.id)
        .filter(ConversationAssignment.is_active.is_(True))
        .filter(ConversationAssignment.agent_id.isnot(None))
        .first()
    )
    if existing:
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
        assignment = conversation_service.assign_conversation(
            db,
            conversation_id=str(conversation.id),
            agent_id=agent_id,
            team_id=str(team.id),
            assigned_by_id=None,
            update_lead_owner=False,
        )
        return RoutingDecision(
            rule_id=str(rule.id),
            team_id=str(team.id),
            agent_id=str(assignment.agent_id) if assignment and assignment.agent_id else None,
        )
    return None
