"""Tests for CRM team service."""

import uuid

import pytest
from fastapi import HTTPException

from app.models.crm.enums import ChannelType
from app.schemas.crm.team import (
    AgentCreate,
    AgentTeamCreate,
    AgentUpdate,
    RoutingRuleCreate,
    RoutingRuleUpdate,
    TeamChannelCreate,
    TeamCreate,
    TeamUpdate,
)
from app.services.crm import team as team_service

# =============================================================================
# Teams CRUD Tests
# =============================================================================


def test_create_team(db_session):
    """Test creating a CRM team."""
    team = team_service.Teams.create(
        db_session,
        TeamCreate(name="Sales Team", notes="Handle sales inquiries"),
    )
    assert team.name == "Sales Team"
    assert team.notes == "Handle sales inquiries"
    assert team.is_active is True


def test_get_team(db_session, crm_team):
    """Test getting a team by ID."""
    fetched = team_service.Teams.get(db_session, str(crm_team.id))
    assert fetched.id == crm_team.id
    assert fetched.name == crm_team.name


def test_get_team_not_found(db_session):
    """Test getting non-existent team raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        team_service.Teams.get(db_session, str(uuid.uuid4()))
    assert exc_info.value.status_code == 404
    assert "Team not found" in exc_info.value.detail


def test_list_teams(db_session):
    """Test listing teams."""
    team_service.Teams.create(db_session, TeamCreate(name="List Test Team 1"))
    team_service.Teams.create(db_session, TeamCreate(name="List Test Team 2"))

    teams = team_service.Teams.list(
        db_session,
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert len(teams) >= 2


def test_list_teams_filter_inactive(db_session):
    """Test listing only inactive teams."""
    team = team_service.Teams.create(
        db_session, TeamCreate(name="Inactive Team", is_active=False)
    )

    teams = team_service.Teams.list(
        db_session,
        is_active=False,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert any(t.id == team.id for t in teams)


def test_list_teams_order_by_name(db_session):
    """Test listing teams ordered by name."""
    teams = team_service.Teams.list(
        db_session,
        is_active=None,
        order_by="name",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    # Just verify it doesn't raise an error
    assert isinstance(teams, list)


def test_list_teams_invalid_order_by(db_session):
    """Test listing teams with invalid order_by raises 400."""
    with pytest.raises(HTTPException) as exc_info:
        team_service.Teams.list(
            db_session,
            is_active=None,
            order_by="invalid_column",
            order_dir="asc",
            limit=10,
            offset=0,
        )
    assert exc_info.value.status_code == 400


def test_update_team(db_session, crm_team):
    """Test updating a team."""
    updated = team_service.Teams.update(
        db_session,
        str(crm_team.id),
        TeamUpdate(name="Updated Team Name"),
    )
    assert updated.name == "Updated Team Name"


def test_update_team_not_found(db_session):
    """Test updating non-existent team raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        team_service.Teams.update(
            db_session, str(uuid.uuid4()), TeamUpdate(name="New Name")
        )
    assert exc_info.value.status_code == 404


def test_delete_team(db_session):
    """Test deleting (soft delete) a team."""
    team = team_service.Teams.create(db_session, TeamCreate(name="To Delete"))
    team_service.Teams.delete(db_session, str(team.id))
    db_session.refresh(team)
    assert team.is_active is False


def test_delete_team_not_found(db_session):
    """Test deleting non-existent team raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        team_service.Teams.delete(db_session, str(uuid.uuid4()))
    assert exc_info.value.status_code == 404


# =============================================================================
# Agents CRUD Tests
# =============================================================================


def test_create_agent(db_session, person):
    """Test creating a CRM agent."""
    agent = team_service.Agents.create(
        db_session,
        AgentCreate(person_id=person.id, title="Senior Agent"),
    )
    assert agent.person_id == person.id
    assert agent.title == "Senior Agent"
    assert agent.is_active is True


def test_list_agents(db_session, crm_agent):
    """Test listing agents."""
    agents = team_service.Agents.list(
        db_session,
        person_id=None,
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert len(agents) >= 1


def test_list_agents_filter_by_person(db_session, person, crm_agent):
    """Test listing agents filtered by person_id."""
    agents = team_service.Agents.list(
        db_session,
        person_id=str(person.id),
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert len(agents) >= 1
    assert all(a.person_id == person.id for a in agents)


def test_list_agents_filter_inactive(db_session, person):
    """Test listing only inactive agents."""
    agent = team_service.Agents.create(
        db_session,
        AgentCreate(person_id=person.id, is_active=False),
    )

    agents = team_service.Agents.list(
        db_session,
        person_id=None,
        is_active=False,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert any(a.id == agent.id for a in agents)


def test_update_agent(db_session, crm_agent):
    """Test updating an agent."""
    updated = team_service.Agents.update(
        db_session,
        str(crm_agent.id),
        AgentUpdate(title="Lead Agent"),
    )
    assert updated.title == "Lead Agent"


def test_update_agent_not_found(db_session):
    """Test updating non-existent agent raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        team_service.Agents.update(
            db_session, str(uuid.uuid4()), AgentUpdate(title="New")
        )
    assert exc_info.value.status_code == 404


# =============================================================================
# Agent Teams Tests
# =============================================================================


def test_create_agent_team(db_session, crm_agent, crm_team):
    """Test creating an agent-team link."""
    link = team_service.AgentTeams.create(
        db_session,
        AgentTeamCreate(agent_id=crm_agent.id, team_id=crm_team.id),
    )
    assert link.agent_id == crm_agent.id
    assert link.team_id == crm_team.id


def test_create_agent_team_team_not_found(db_session, crm_agent):
    """Test creating agent-team with non-existent team raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        team_service.AgentTeams.create(
            db_session,
            AgentTeamCreate(agent_id=crm_agent.id, team_id=uuid.uuid4()),
        )
    assert exc_info.value.status_code == 404
    assert "Team not found" in exc_info.value.detail


def test_create_agent_team_agent_not_found(db_session, crm_team):
    """Test creating agent-team with non-existent agent raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        team_service.AgentTeams.create(
            db_session,
            AgentTeamCreate(agent_id=uuid.uuid4(), team_id=crm_team.id),
        )
    assert exc_info.value.status_code == 404
    assert "Agent not found" in exc_info.value.detail


# =============================================================================
# Team Channels Tests
# =============================================================================


def test_create_team_channel(db_session, crm_team):
    """Test creating a team channel with no target (default handler)."""
    channel = team_service.TeamChannels.create(
        db_session,
        TeamChannelCreate(
            team_id=crm_team.id,
            channel_type=ChannelType.email,
            channel_target_id=None,
        ),
    )
    assert channel.team_id == crm_team.id
    assert channel.channel_type == ChannelType.email


def test_create_team_channel_team_not_found(db_session):
    """Test creating team channel with non-existent team raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        team_service.TeamChannels.create(
            db_session,
            TeamChannelCreate(
                team_id=uuid.uuid4(),
                channel_type=ChannelType.email,
            ),
        )
    assert exc_info.value.status_code == 404
    assert "Team not found" in exc_info.value.detail


def test_create_team_channel_duplicate_default(db_session, crm_team):
    """Test creating duplicate default channel for same type raises 400."""
    # Create first default channel (no target_id)
    team_service.TeamChannels.create(
        db_session,
        TeamChannelCreate(
            team_id=crm_team.id,
            channel_type=ChannelType.email,
            channel_target_id=None,
        ),
    )

    # Try to create another default channel for same type
    with pytest.raises(HTTPException) as exc_info:
        team_service.TeamChannels.create(
            db_session,
            TeamChannelCreate(
                team_id=crm_team.id,
                channel_type=ChannelType.email,
                channel_target_id=None,
            ),
        )
    assert exc_info.value.status_code == 400
    assert "Default channel target already exists" in exc_info.value.detail


def test_list_team_channels(db_session, crm_team):
    """Test listing team channels."""
    team_service.TeamChannels.create(
        db_session,
        TeamChannelCreate(
            team_id=crm_team.id,
            channel_type=ChannelType.whatsapp,
            channel_target_id=None,
        ),
    )

    channels = team_service.TeamChannels.list(
        db_session,
        team_id=str(crm_team.id),
        channel_type=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert len(channels) >= 1


def test_list_team_channels_filter_by_type(db_session, crm_team):
    """Test listing team channels filtered by channel type."""
    team_service.TeamChannels.create(
        db_session,
        TeamChannelCreate(
            team_id=crm_team.id,
            channel_type=ChannelType.whatsapp,
            channel_target_id=None,
        ),
    )

    channels = team_service.TeamChannels.list(
        db_session,
        team_id=str(crm_team.id),
        channel_type="whatsapp",
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert all(c.channel_type == ChannelType.whatsapp for c in channels)


def test_list_team_channels_invalid_type(db_session):
    """Test listing team channels with invalid type raises 400."""
    with pytest.raises(HTTPException) as exc_info:
        team_service.TeamChannels.list(
            db_session,
            team_id=None,
            channel_type="invalid_type",
            order_by="created_at",
            order_dir="asc",
            limit=10,
            offset=0,
        )
    assert exc_info.value.status_code == 400


# =============================================================================
# Routing Rules Tests
# =============================================================================


def test_create_routing_rule(db_session, crm_team):
    """Test creating a routing rule."""
    rule = team_service.RoutingRules.create(
        db_session,
        RoutingRuleCreate(
            team_id=crm_team.id,
            channel_type=ChannelType.email,
            rule_config={"priority": "high"},
        ),
    )
    assert rule.team_id == crm_team.id
    assert rule.channel_type == ChannelType.email
    assert rule.rule_config == {"priority": "high"}
    assert rule.is_active is True


def test_create_routing_rule_team_not_found(db_session):
    """Test creating routing rule with non-existent team raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        team_service.RoutingRules.create(
            db_session,
            RoutingRuleCreate(
                team_id=uuid.uuid4(),
                channel_type=ChannelType.email,
            ),
        )
    assert exc_info.value.status_code == 404
    assert "Team not found" in exc_info.value.detail


def test_list_routing_rules(db_session, crm_team):
    """Test listing routing rules."""
    team_service.RoutingRules.create(
        db_session,
        RoutingRuleCreate(team_id=crm_team.id, channel_type=ChannelType.email),
    )

    rules = team_service.RoutingRules.list(
        db_session,
        team_id=str(crm_team.id),
        channel_type=None,
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert len(rules) >= 1


def test_list_routing_rules_filter_by_type(db_session, crm_team):
    """Test listing routing rules filtered by channel type."""
    team_service.RoutingRules.create(
        db_session,
        RoutingRuleCreate(team_id=crm_team.id, channel_type=ChannelType.whatsapp),
    )

    rules = team_service.RoutingRules.list(
        db_session,
        team_id=str(crm_team.id),
        channel_type="whatsapp",
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert all(r.channel_type == ChannelType.whatsapp for r in rules)


def test_list_routing_rules_filter_inactive(db_session, crm_team):
    """Test listing only inactive routing rules."""
    rule = team_service.RoutingRules.create(
        db_session,
        RoutingRuleCreate(
            team_id=crm_team.id, channel_type=ChannelType.email, is_active=False
        ),
    )

    rules = team_service.RoutingRules.list(
        db_session,
        team_id=None,
        channel_type=None,
        is_active=False,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert any(r.id == rule.id for r in rules)


def test_update_routing_rule(db_session, crm_team):
    """Test updating a routing rule."""
    rule = team_service.RoutingRules.create(
        db_session,
        RoutingRuleCreate(team_id=crm_team.id, channel_type=ChannelType.email),
    )

    updated = team_service.RoutingRules.update(
        db_session,
        str(rule.id),
        RoutingRuleUpdate(rule_config={"updated": True}, is_active=False),
    )
    assert updated.rule_config == {"updated": True}
    assert updated.is_active is False


def test_update_routing_rule_not_found(db_session):
    """Test updating non-existent routing rule raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        team_service.RoutingRules.update(
            db_session,
            str(uuid.uuid4()),
            RoutingRuleUpdate(is_active=False),
        )
    assert exc_info.value.status_code == 404
    assert "Routing rule not found" in exc_info.value.detail
