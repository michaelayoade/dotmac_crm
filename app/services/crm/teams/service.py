from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.crm.enums import ChannelType
from app.models.crm.team import (
    CrmAgent,
    CrmAgentTeam,
    CrmRoutingRule,
    CrmTeam,
    CrmTeamChannel,
)
from app.services.common import apply_ordering, apply_pagination, coerce_uuid, validate_enum
from app.services.response import ListResponseMixin


def _validate_channel_type(value: str) -> ChannelType:
    return validate_enum(value, ChannelType, "channel_type")


class Teams(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload):
        team = CrmTeam(**payload.model_dump())
        db.add(team)
        db.commit()
        db.refresh(team)
        return team

    @staticmethod
    def get(db: Session, team_id: str):
        team = db.get(CrmTeam, coerce_uuid(team_id))
        if not team:
            raise HTTPException(status_code=404, detail="Team not found")
        return team

    @staticmethod
    def list(
        db: Session,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(CrmTeam)
        if is_active is None:
            query = query.filter(CrmTeam.is_active.is_(True))
        else:
            query = query.filter(CrmTeam.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": CrmTeam.created_at, "name": CrmTeam.name},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, team_id: str, payload):
        team = db.get(CrmTeam, coerce_uuid(team_id))
        if not team:
            raise HTTPException(status_code=404, detail="Team not found")
        data = payload.model_dump(exclude_unset=True)
        for key, value in data.items():
            setattr(team, key, value)
        db.commit()
        db.refresh(team)
        return team

    @staticmethod
    def delete(db: Session, team_id: str):
        team = db.get(CrmTeam, coerce_uuid(team_id))
        if not team:
            raise HTTPException(status_code=404, detail="Team not found")
        team.is_active = False
        db.commit()


class Agents(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload):
        agent = CrmAgent(**payload.model_dump())
        db.add(agent)
        db.commit()
        db.refresh(agent)
        return agent

    @staticmethod
    def list(
        db: Session,
        person_id: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(CrmAgent)
        if person_id:
            query = query.filter(CrmAgent.person_id == coerce_uuid(person_id))
        if is_active is None:
            query = query.filter(CrmAgent.is_active.is_(True))
        else:
            query = query.filter(CrmAgent.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": CrmAgent.created_at},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, agent_id: str, payload):
        agent = db.get(CrmAgent, coerce_uuid(agent_id))
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        data = payload.model_dump(exclude_unset=True)
        for key, value in data.items():
            setattr(agent, key, value)
        db.commit()
        db.refresh(agent)
        return agent


class AgentTeams(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload):
        team = db.get(CrmTeam, payload.team_id)
        if not team:
            raise HTTPException(status_code=404, detail="Team not found")
        agent = db.get(CrmAgent, payload.agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        link = CrmAgentTeam(**payload.model_dump())
        db.add(link)
        db.commit()
        db.refresh(link)
        return link

    @staticmethod
    def list(
        db: Session,
        agent_id: str | None,
        team_id: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(CrmAgentTeam)
        if agent_id:
            query = query.filter(CrmAgentTeam.agent_id == coerce_uuid(agent_id))
        if team_id:
            query = query.filter(CrmAgentTeam.team_id == coerce_uuid(team_id))
        if is_active is None:
            query = query.filter(CrmAgentTeam.is_active.is_(True))
        else:
            query = query.filter(CrmAgentTeam.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": CrmAgentTeam.created_at},
        )
        return apply_pagination(query, limit, offset).all()


class TeamChannels(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload):
        team = db.get(CrmTeam, payload.team_id)
        if not team:
            raise HTTPException(status_code=404, detail="Team not found")
        if payload.channel_target_id is None:
            existing = (
                db.query(CrmTeamChannel)
                .filter(CrmTeamChannel.team_id == payload.team_id)
                .filter(CrmTeamChannel.channel_type == payload.channel_type)
                .filter(CrmTeamChannel.channel_target_id.is_(None))
                .first()
            )
            if existing:
                raise HTTPException(
                    status_code=400,
                    detail="Default channel target already exists for team/channel",
                )
        channel = CrmTeamChannel(**payload.model_dump())
        db.add(channel)
        db.commit()
        db.refresh(channel)
        return channel

    @staticmethod
    def get(db: Session, channel_id: str):
        channel = db.get(CrmTeamChannel, coerce_uuid(channel_id))
        if not channel:
            raise HTTPException(status_code=404, detail="Channel not found")
        return channel

    @staticmethod
    def list(
        db: Session,
        team_id: str | None,
        channel_type: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(CrmTeamChannel)
        if team_id:
            query = query.filter(CrmTeamChannel.team_id == coerce_uuid(team_id))
        if channel_type:
            channel_value = _validate_channel_type(channel_type)
            query = query.filter(CrmTeamChannel.channel_type == channel_value)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": CrmTeamChannel.created_at},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, channel_id: str, payload):
        channel = db.get(CrmTeamChannel, coerce_uuid(channel_id))
        if not channel:
            raise HTTPException(status_code=404, detail="Channel not found")
        data = payload.model_dump(exclude_unset=True)
        for key, value in data.items():
            setattr(channel, key, value)
        db.commit()
        db.refresh(channel)
        return channel

    @staticmethod
    def delete(db: Session, channel_id: str):
        channel = db.get(CrmTeamChannel, coerce_uuid(channel_id))
        if not channel:
            raise HTTPException(status_code=404, detail="Channel not found")
        db.delete(channel)
        db.commit()


class RoutingRules(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload):
        team = db.get(CrmTeam, payload.team_id)
        if not team:
            raise HTTPException(status_code=404, detail="Team not found")
        rule = CrmRoutingRule(**payload.model_dump())
        db.add(rule)
        db.commit()
        db.refresh(rule)
        return rule

    @staticmethod
    def list(
        db: Session,
        team_id: str | None,
        channel_type: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(CrmRoutingRule)
        if team_id:
            query = query.filter(CrmRoutingRule.team_id == coerce_uuid(team_id))
        if channel_type:
            channel_value = _validate_channel_type(channel_type)
            query = query.filter(CrmRoutingRule.channel_type == channel_value)
        if is_active is None:
            query = query.filter(CrmRoutingRule.is_active.is_(True))
        else:
            query = query.filter(CrmRoutingRule.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": CrmRoutingRule.created_at},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, rule_id: str, payload):
        rule = db.get(CrmRoutingRule, coerce_uuid(rule_id))
        if not rule:
            raise HTTPException(status_code=404, detail="Routing rule not found")
        data = payload.model_dump(exclude_unset=True)
        for key, value in data.items():
            setattr(rule, key, value)
        db.commit()
        db.refresh(rule)
        return rule

    @staticmethod
    def delete(db: Session, rule_id: str) -> None:
        rule = db.get(CrmRoutingRule, coerce_uuid(rule_id))
        if not rule:
            raise HTTPException(status_code=404, detail="Routing rule not found")
        db.delete(rule)
        db.commit()


def get_agent_labels(db: Session, agents: list) -> dict[str, str]:
    """Get display labels for a list of agents efficiently.

    Uses bulk Person fetch to avoid N+1 queries.
    Returns: {agent_id: display_label}
    """
    from app.models.person import Person

    if not agents:
        return {}

    # Bulk fetch all persons for the agents
    person_ids = [agent.person_id for agent in agents if agent.person_id]
    if not person_ids:
        return {}

    persons = db.query(Person).filter(Person.id.in_(person_ids)).all()
    person_map = {person.id: person for person in persons}

    labels = {}
    for agent in agents:
        if agent.person_id and agent.person_id in person_map:
            person = person_map[agent.person_id]
            label = (
                person.display_name or " ".join(part for part in [person.first_name, person.last_name] if part).strip()
            )
            labels[str(agent.id)] = label or "Agent"
        else:
            labels[str(agent.id)] = "Agent"

    return labels


def get_agent_team_options(db: Session) -> dict:
    """Get agents and teams for assignment dropdowns.

    Returns: {agents, teams, agent_labels}
    """
    teams = db.query(CrmTeam).filter(CrmTeam.is_active.is_(True)).order_by(CrmTeam.name.asc()).limit(200).all()
    agents = (
        db.query(CrmAgent).filter(CrmAgent.is_active.is_(True)).order_by(CrmAgent.created_at.desc()).limit(200).all()
    )
    agent_labels = get_agent_labels(db, agents)

    return {"agents": agents, "teams": teams, "agent_labels": agent_labels}


# Singleton instances
teams = Teams()
agents = Agents()
agent_teams = AgentTeams()
team_channels = TeamChannels()
routing_rules = RoutingRules()
