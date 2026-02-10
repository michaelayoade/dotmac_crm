"""CRM Teams submodule.

Handles teams, agents, routing rules, and team channels.
"""

from app.services.crm.teams.service import (
    Agents,
    AgentTeams,
    RoutingRules,
    TeamChannels,
    Teams,
    agent_teams,
    agents,
    routing_rules,
    team_channels,
    teams,
)

__all__ = [
    "AgentTeams",
    "Agents",
    "RoutingRules",
    "TeamChannels",
    "Teams",
    "agent_teams",
    "agents",
    "routing_rules",
    "team_channels",
    "teams",
]
