"""Compatibility wrapper for team services."""

from app.services.crm.teams.service import (
    Agents,
    AgentTeams,
    RoutingRules,
    TeamChannels,
    Teams,
    agent_teams,
    agents,
    get_agent_labels,
    get_agent_team_options,
    routing_rules,
    team_channels,
    teams,
)

__all__ = [
    "Agents",
    "AgentTeams",
    "RoutingRules",
    "TeamChannels",
    "Teams",
    "agent_teams",
    "agents",
    "get_agent_labels",
    "get_agent_team_options",
    "routing_rules",
    "team_channels",
    "teams",
]
