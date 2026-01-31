"""CRM Teams submodule.

Handles teams, agents, routing rules, and team channels.
"""

from app.services.crm.teams.service import (
    Teams,
    Agents,
    AgentTeams,
    TeamChannels,
    RoutingRules,
    teams,
    agents,
    agent_teams,
    team_channels,
    routing_rules,
)

__all__ = [
    "Teams",
    "Agents",
    "AgentTeams",
    "TeamChannels",
    "RoutingRules",
    "teams",
    "agents",
    "agent_teams",
    "team_channels",
    "routing_rules",
]
