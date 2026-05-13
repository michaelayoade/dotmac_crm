import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.metrics import AI_INTAKE_ESCALATIONS
from app.models.crm.ai_intake import AiIntakeConfig
from app.models.crm.conversation import Conversation, ConversationAssignment, Message
from app.models.crm.enums import AgentPresenceStatus, ChannelType, ConversationStatus, MessageDirection, MessageStatus
from app.models.crm.presence import AgentPresence
from app.models.crm.team import CrmAgent, CrmAgentTeam, CrmTeam
from app.models.person import Person
from app.schemas.crm.conversation import MessageCreate
from app.services.crm import conversation as conversation_service
from app.services.crm.ai_intake import (
    AI_INTAKE_HANDOFF_STATE_AWAITING_AGENT,
    AI_INTAKE_HANDOFF_STATE_IN_PROGRESS,
    AI_INTAKE_METADATA_KEY,
    _build_prompt,
    _coerce_ai_bool,
    _handoff_message_for_department,
    _handoff_reassurance_message_for_department,
    _history,
    _normalize_department_key,
    _send_handoff_message,
    backfill_missing_handoff_states,
    escalate_expired_pending_intakes,
    make_scope_key,
    process_pending_intake,
    recover_ai_error_escalations,
    save_ai_intake_config,
    send_due_handoff_reassurance_followups,
)


def _make_person(db_session):
    person = Person(email=f"ai-intake-{uuid.uuid4().hex[:8]}@example.com", first_name="AI", last_name="Test")
    db_session.add(person)
    db_session.flush()
    return person


def _make_conversation(db_session, person):
    conversation = Conversation(person_id=person.id, status=ConversationStatus.open, is_active=True)
    db_session.add(conversation)
    db_session.flush()
    return conversation


def _make_message(db_session, conversation, *, body="Need help", metadata=None):
    message = Message(
        conversation_id=conversation.id,
        channel_type=ChannelType.chat_widget,
        direction=MessageDirection.inbound,
        status=MessageStatus.received,
        body=body,
        metadata_=metadata or {"widget_config_id": str(uuid.uuid4())},
    )
    db_session.add(message)
    db_session.commit()
    db_session.refresh(message)
    db_session.refresh(conversation)
    return message


def _make_agent(db_session, team, *, label, status):
    person = Person(email=f"ai-agent-{label}-{uuid.uuid4().hex[:8]}@example.com", first_name=label, last_name="Agent")
    db_session.add(person)
    db_session.flush()

    agent = CrmAgent(person_id=person.id, is_active=True, title="Support Agent")
    db_session.add(agent)
    db_session.flush()

    db_session.add(CrmAgentTeam(agent_id=agent.id, team_id=team.id, is_active=True))
    db_session.add(
        AgentPresence(
            agent_id=agent.id,
            status=status,
            manual_override_status=None,
            last_seen_at=datetime.now(UTC),
        )
    )
    db_session.commit()
    return agent


def _make_agent_reply(db_session, conversation, agent, *, body="On it", sent_at=None):
    message = Message(
        conversation_id=conversation.id,
        channel_type=ChannelType.chat_widget,
        direction=MessageDirection.outbound,
        status=MessageStatus.sent,
        body=body,
        author_id=agent.person_id,
        sent_at=sent_at or datetime.now(UTC),
    )
    db_session.add(message)
    db_session.commit()
    db_session.refresh(message)
    return message


def _make_config(db_session, *, scope_key, team_id=None, exclude_campaign_attribution=True):
    config = AiIntakeConfig(
        scope_key=scope_key,
        channel_type=ChannelType.chat_widget,
        is_enabled=True,
        confidence_threshold=0.75,
        allow_followup_questions=True,
        max_clarification_turns=1,
        escalate_after_minutes=5,
        exclude_campaign_attribution=exclude_campaign_attribution,
        fallback_team_id=team_id,
        department_mappings=[
            {
                "key": "support",
                "label": "Support",
                "team_id": str(team_id) if team_id else str(uuid.uuid4()),
                "tags": ["support"],
                "priority": "high",
                "notify_email": "",
            }
        ],
    )
    db_session.add(config)
    db_session.commit()
    return config


def _make_split_billing_config(
    db_session,
    *,
    scope_key,
    sales_team_id,
    fallback_team_id,
):
    config = AiIntakeConfig(
        scope_key=scope_key,
        channel_type=ChannelType.chat_widget,
        is_enabled=True,
        confidence_threshold=0.75,
        allow_followup_questions=True,
        max_clarification_turns=1,
        escalate_after_minutes=5,
        exclude_campaign_attribution=True,
        fallback_team_id=fallback_team_id,
        department_mappings=[
            {
                "key": "billing_payment",
                "label": "Billing Payment",
                "team_id": str(sales_team_id),
                "tags": ["billing-payment"],
                "priority": "medium",
                "notify_email": "",
            },
            {
                "key": "billing_renewal",
                "label": "Billing Renewal",
                "team_id": str(sales_team_id),
                "tags": ["billing-renewal"],
                "priority": "medium",
                "notify_email": "",
            },
            {
                "key": "billing_reactivation",
                "label": "Billing Reactivation",
                "team_id": str(sales_team_id),
                "tags": ["billing-reactivation"],
                "priority": "medium",
                "notify_email": "",
            },
            {
                "key": "billing_adjustment",
                "label": "Billing Adjustment",
                "team_id": str(sales_team_id),
                "tags": ["billing-adjustment"],
                "priority": "medium",
                "notify_email": "",
            },
            {
                "key": "billing_general",
                "label": "Billing General",
                "team_id": str(sales_team_id),
                "tags": ["billing-general"],
                "priority": "medium",
                "notify_email": "",
            },
        ],
    )
    db_session.add(config)
    db_session.commit()
    return config


def _seed_resolved_handoff_state(
    db_session,
    *,
    conversation,
    department="support",
    handoff_sent_at=None,
    handoff_state=None,
):
    handoff_at = handoff_sent_at or (datetime.now(UTC) - timedelta(minutes=16))
    conversation.status = ConversationStatus.open
    state = {
        "status": "resolved",
        "scope_key": "widget:test",
        "started_at": (handoff_at - timedelta(minutes=1)).isoformat(),
        "resolved_at": handoff_at.isoformat(),
        "department": department,
        "handoff_department": department,
        "handoff_sent": True,
        "handoff_sent_at": handoff_at.isoformat(),
        "handoff_followup_due_at": (handoff_at + timedelta(minutes=15)).isoformat(),
        "handoff_followup_sent_at": None,
        "first_human_reply_at": None,
    }
    if handoff_state is not None:
        state["handoff_state"] = handoff_state
    conversation.metadata_ = {AI_INTAKE_METADATA_KEY: state}
    db_session.commit()
    return handoff_at


def test_make_scope_key_for_widget():
    widget_id = str(uuid.uuid4())
    assert make_scope_key(channel_type=ChannelType.chat_widget, widget_config_id=widget_id) == f"widget:{widget_id}"


def test_process_pending_intake_excludes_campaign_attribution(db_session, monkeypatch):
    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    widget_id = str(uuid.uuid4())
    message = _make_message(
        db_session,
        conversation,
        metadata={"widget_config_id": widget_id, "attribution": {"utm_source": "meta", "campaign_id": "c1"}},
    )
    _make_config(db_session, scope_key=f"widget:{widget_id}")

    monkeypatch.setenv("CRM_AI_PENDING_INTAKE_ENABLED", "1")
    monkeypatch.setattr("app.services.crm.ai_intake.ai_gateway.enabled", lambda db: True)

    result = process_pending_intake(
        db_session,
        conversation=conversation,
        message=message,
        scope_key=f"widget:{widget_id}",
        is_new_conversation=True,
    )

    db_session.refresh(conversation)
    assert result.handled is False
    assert conversation.metadata_[AI_INTAKE_METADATA_KEY]["status"] == "excluded"


def test_process_pending_intake_resolves_and_assigns_team(db_session, monkeypatch):
    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    widget_id = str(uuid.uuid4())
    message = _make_message(db_session, conversation, metadata={"widget_config_id": widget_id})
    team = CrmTeam(name="Support", is_active=True)
    db_session.add(team)
    db_session.commit()
    agent = _make_agent(db_session, team, label="Assigned", status=AgentPresenceStatus.online)
    _make_config(db_session, scope_key=f"widget:{widget_id}", team_id=team.id)

    monkeypatch.setenv("CRM_AI_PENDING_INTAKE_ENABLED", "1")
    monkeypatch.setattr("app.services.crm.ai_intake.ai_gateway.enabled", lambda db: True)
    monkeypatch.setattr(
        "app.services.crm.ai_intake.ai_gateway.generate_with_fallback",
        lambda db, **kwargs: (
            SimpleNamespace(
                content='{"department":"support","confidence":0.93,"reason":"service issue","needs_followup":false,"followup_question":""}'
            ),
            {"endpoint": "primary", "fallback_used": False},
        ),
    )
    sent = {}

    def _fake_send_message(db, payload, author_id=None, trace_id=None):
        sent["body"] = payload.body
        outbound = Message(
            conversation_id=payload.conversation_id,
            channel_type=payload.channel_type,
            direction=MessageDirection.outbound,
            status=MessageStatus.sent,
            body=payload.body,
        )
        db.add(outbound)
        db.commit()
        db.refresh(outbound)
        return outbound

    monkeypatch.setattr("app.services.crm.ai_intake.send_message", _fake_send_message)

    result = process_pending_intake(
        db_session,
        conversation=conversation,
        message=message,
        scope_key=f"widget:{widget_id}",
        is_new_conversation=True,
    )

    db_session.refresh(conversation)
    assignment = (
        db_session.query(ConversationAssignment)
        .filter(ConversationAssignment.conversation_id == conversation.id)
        .filter(ConversationAssignment.is_active.is_(True))
        .first()
    )
    assert result.handled is True
    assert result.resolved is True
    assert conversation.status == ConversationStatus.open
    assert assignment is not None
    assert assignment.team_id == team.id
    assert assignment.agent_id == agent.id
    assert conversation.priority is not None
    assert conversation.priority.value == "high"
    assert conversation.metadata_[AI_INTAKE_METADATA_KEY]["status"] == "resolved"
    assert conversation.metadata_[AI_INTAKE_METADATA_KEY]["handoff_state"] == AI_INTAKE_HANDOFF_STATE_AWAITING_AGENT
    assert conversation.metadata_[AI_INTAKE_METADATA_KEY]["department"] == "support"
    assert {tag.tag for tag in conversation.tags} == {"support"}
    assert sent["body"] == "A member of our support team will respond within 15-30 minutes."
    assert conversation.metadata_[AI_INTAKE_METADATA_KEY]["handoff_sent"] is True
    assert conversation.metadata_[AI_INTAKE_METADATA_KEY]["handoff_sent_at"] is not None
    assert conversation.metadata_[AI_INTAKE_METADATA_KEY]["handoff_followup_due_at"] is not None


def test_process_pending_intake_assigns_agents_round_robin(db_session, monkeypatch):
    team = CrmTeam(name="Support", is_active=True)
    db_session.add(team)
    db_session.commit()
    first_agent = _make_agent(db_session, team, label="First", status=AgentPresenceStatus.online)
    second_agent = _make_agent(db_session, team, label="Second", status=AgentPresenceStatus.away)

    widget_id = str(uuid.uuid4())
    _make_config(db_session, scope_key=f"widget:{widget_id}", team_id=team.id)

    monkeypatch.setenv("CRM_AI_PENDING_INTAKE_ENABLED", "1")
    monkeypatch.setattr("app.services.crm.ai_intake.ai_gateway.enabled", lambda db: True)
    monkeypatch.setattr(
        "app.services.crm.ai_intake.ai_gateway.generate_with_fallback",
        lambda db, **kwargs: (
            SimpleNamespace(
                content='{"department":"support","confidence":0.93,"reason":"service issue","needs_followup":false,"followup_question":""}'
            ),
            {"endpoint": "primary", "fallback_used": False},
        ),
    )
    monkeypatch.setattr(
        "app.services.crm.ai_intake.send_message",
        lambda db, payload, author_id=None, trace_id=None: Message(
            conversation_id=payload.conversation_id,
            channel_type=payload.channel_type,
            direction=MessageDirection.outbound,
            status=MessageStatus.sent,
            body=payload.body,
        ),
    )

    conversation_one = _make_conversation(db_session, _make_person(db_session))
    message_one = _make_message(db_session, conversation_one, metadata={"widget_config_id": widget_id})
    process_pending_intake(
        db_session,
        conversation=conversation_one,
        message=message_one,
        scope_key=f"widget:{widget_id}",
        is_new_conversation=True,
    )

    conversation_two = _make_conversation(db_session, _make_person(db_session))
    message_two = _make_message(db_session, conversation_two, metadata={"widget_config_id": widget_id})
    process_pending_intake(
        db_session,
        conversation=conversation_two,
        message=message_two,
        scope_key=f"widget:{widget_id}",
        is_new_conversation=True,
    )

    assignments = (
        db_session.query(ConversationAssignment)
        .filter(ConversationAssignment.is_active.is_(True))
        .filter(ConversationAssignment.conversation_id.in_([conversation_one.id, conversation_two.id]))
        .order_by(ConversationAssignment.created_at.asc())
        .all()
    )

    assert len(assignments) == 2
    assert assignments[0].team_id == team.id
    assert assignments[1].team_id == team.id
    assert assignments[0].agent_id == first_agent.id
    assert assignments[1].agent_id == second_agent.id


def test_process_pending_intake_routes_billing_payment_to_sales_call_center(db_session, monkeypatch):
    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    widget_id = str(uuid.uuid4())
    message = _make_message(db_session, conversation, metadata={"widget_config_id": widget_id})
    sales_team = CrmTeam(name="Sales", is_active=True)
    db_session.add(sales_team)
    db_session.commit()
    _make_agent(db_session, sales_team, label="SalesBilling", status=AgentPresenceStatus.online)
    _make_split_billing_config(
        db_session,
        scope_key=f"widget:{widget_id}",
        sales_team_id=sales_team.id,
        fallback_team_id=sales_team.id,
    )

    monkeypatch.setenv("CRM_AI_PENDING_INTAKE_ENABLED", "1")
    monkeypatch.setattr("app.services.crm.ai_intake.ai_gateway.enabled", lambda db: True)
    monkeypatch.setattr(
        "app.services.crm.ai_intake.ai_gateway.generate_with_fallback",
        lambda db, **kwargs: (
            SimpleNamespace(
                content='{"department":"billing_payment","confidence":0.96,"reason":"payment confirmation","needs_followup":false,"followup_question":""}'
            ),
            {"endpoint": "primary", "fallback_used": False},
        ),
    )
    sent = {}

    def _fake_send_message(db, payload, author_id=None, trace_id=None):
        sent["body"] = payload.body
        outbound = Message(
            conversation_id=payload.conversation_id,
            channel_type=payload.channel_type,
            direction=MessageDirection.outbound,
            status=MessageStatus.sent,
            body=payload.body,
        )
        db.add(outbound)
        db.commit()
        db.refresh(outbound)
        return outbound

    monkeypatch.setattr("app.services.crm.ai_intake.send_message", _fake_send_message)

    result = process_pending_intake(
        db_session,
        conversation=conversation,
        message=message,
        scope_key=f"widget:{widget_id}",
        is_new_conversation=True,
    )

    assignment = (
        db_session.query(ConversationAssignment)
        .filter(ConversationAssignment.conversation_id == conversation.id)
        .filter(ConversationAssignment.is_active.is_(True))
        .first()
    )
    assert result.resolved is True
    assert assignment is not None
    assert assignment.team_id == sales_team.id
    assert assignment.agent_id is not None
    assert sent["body"] == "A member of our billing team will respond within 15-30 minutes."


def test_process_pending_intake_routes_billing_renewal_to_sales(db_session, monkeypatch):
    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    widget_id = str(uuid.uuid4())
    message = _make_message(db_session, conversation, metadata={"widget_config_id": widget_id})
    sales_team = CrmTeam(name="Sales", is_active=True)
    db_session.add(sales_team)
    db_session.commit()
    _make_agent(db_session, sales_team, label="SalesRenewal", status=AgentPresenceStatus.online)
    _make_split_billing_config(
        db_session,
        scope_key=f"widget:{widget_id}",
        sales_team_id=sales_team.id,
        fallback_team_id=sales_team.id,
    )

    monkeypatch.setenv("CRM_AI_PENDING_INTAKE_ENABLED", "1")
    monkeypatch.setattr("app.services.crm.ai_intake.ai_gateway.enabled", lambda db: True)
    monkeypatch.setattr(
        "app.services.crm.ai_intake.ai_gateway.generate_with_fallback",
        lambda db, **kwargs: (
            SimpleNamespace(
                content='{"department":"billing_renewal","confidence":0.96,"reason":"subscription renewal","needs_followup":false,"followup_question":""}'
            ),
            {"endpoint": "primary", "fallback_used": False},
        ),
    )
    sent = {}

    def _fake_send_message(db, payload, author_id=None, trace_id=None):
        sent["body"] = payload.body
        outbound = Message(
            conversation_id=payload.conversation_id,
            channel_type=payload.channel_type,
            direction=MessageDirection.outbound,
            status=MessageStatus.sent,
            body=payload.body,
        )
        db.add(outbound)
        db.commit()
        db.refresh(outbound)
        return outbound

    monkeypatch.setattr("app.services.crm.ai_intake.send_message", _fake_send_message)

    result = process_pending_intake(
        db_session,
        conversation=conversation,
        message=message,
        scope_key=f"widget:{widget_id}",
        is_new_conversation=True,
    )

    assignment = (
        db_session.query(ConversationAssignment)
        .filter(ConversationAssignment.conversation_id == conversation.id)
        .filter(ConversationAssignment.is_active.is_(True))
        .first()
    )
    assert result.resolved is True
    assert assignment is not None
    assert assignment.team_id == sales_team.id
    assert assignment.agent_id is not None
    assert sent["body"] == "A member of our billing team will respond within 15-30 minutes."


def test_process_pending_intake_assigns_specific_sales_agent_immediately(db_session, monkeypatch):
    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    widget_id = str(uuid.uuid4())
    message = _make_message(db_session, conversation, metadata={"widget_config_id": widget_id})
    sales_team = CrmTeam(name="Sales", is_active=True)
    db_session.add(sales_team)
    db_session.commit()
    first_agent = _make_agent(db_session, sales_team, label="OnlineOne", status=AgentPresenceStatus.online)
    second_agent = _make_agent(db_session, sales_team, label="AwayTwo", status=AgentPresenceStatus.away)
    _make_config(db_session, scope_key=f"widget:{widget_id}", team_id=sales_team.id)

    monkeypatch.setenv("CRM_AI_PENDING_INTAKE_ENABLED", "1")
    monkeypatch.setattr("app.services.crm.ai_intake.ai_gateway.enabled", lambda db: True)
    monkeypatch.setattr(
        "app.services.crm.ai_intake.ai_gateway.generate_with_fallback",
        lambda db, **kwargs: (
            SimpleNamespace(
                content='{"department":"support","confidence":0.95,"reason":"direct assign","needs_followup":false,"followup_question":""}'
            ),
            {"endpoint": "primary", "fallback_used": False},
        ),
    )
    monkeypatch.setattr(
        "app.services.crm.ai_intake.send_message",
        lambda db, payload, author_id=None, trace_id=None: Message(
            conversation_id=payload.conversation_id,
            channel_type=payload.channel_type,
            direction=MessageDirection.outbound,
            status=MessageStatus.sent,
            body=payload.body,
        ),
    )

    config = db_session.query(AiIntakeConfig).filter(AiIntakeConfig.scope_key == f"widget:{widget_id}").first()
    config.department_mappings = [
        {
            "key": "support",
            "label": "Support",
            "team_id": str(sales_team.id),
            "tags": ["support"],
            "priority": "high",
            "notify_email": "",
        }
    ]
    db_session.commit()

    result = process_pending_intake(
        db_session,
        conversation=conversation,
        message=message,
        scope_key=f"widget:{widget_id}",
        is_new_conversation=True,
    )

    assignment = (
        db_session.query(ConversationAssignment)
        .filter(ConversationAssignment.conversation_id == conversation.id)
        .filter(ConversationAssignment.is_active.is_(True))
        .first()
    )
    assert result.resolved is True
    assert assignment is not None
    assert assignment.team_id == sales_team.id
    assert assignment.agent_id in {first_agent.id, second_agent.id}


def test_process_pending_intake_normalizes_department_and_boolean_output(db_session, monkeypatch):
    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    widget_id = str(uuid.uuid4())
    message = _make_message(db_session, conversation, metadata={"widget_config_id": widget_id})

    sales_team = CrmTeam(name="Sales", is_active=True)
    fallback_team = CrmTeam(name="Fallback", is_active=True)
    db_session.add_all([sales_team, fallback_team])
    db_session.commit()
    _make_agent(db_session, sales_team, label="SalesPayment", status=AgentPresenceStatus.online)
    _make_split_billing_config(
        db_session,
        scope_key=f"widget:{widget_id}",
        sales_team_id=sales_team.id,
        fallback_team_id=fallback_team.id,
    )

    monkeypatch.setenv("CRM_AI_PENDING_INTAKE_ENABLED", "1")
    monkeypatch.setattr("app.services.crm.ai_intake.ai_gateway.enabled", lambda db: True)
    monkeypatch.setattr(
        "app.services.crm.ai_intake.ai_gateway.generate_with_fallback",
        lambda db, **kwargs: (
            SimpleNamespace(
                content='{"department":"billing payment","confidence":0.91,"reason":"payment issue","needs_followup":"false","followup_question":""}'
            ),
            {"endpoint": "primary", "fallback_used": False},
        ),
    )
    monkeypatch.setattr(
        "app.services.crm.ai_intake.send_message",
        lambda db, payload, author_id=None, trace_id=None: Message(
            conversation_id=payload.conversation_id,
            channel_type=payload.channel_type,
            direction=MessageDirection.outbound,
            status=MessageStatus.sent,
            body=payload.body,
        ),
    )

    result = process_pending_intake(
        db_session,
        conversation=conversation,
        message=message,
        scope_key=f"widget:{widget_id}",
        is_new_conversation=True,
    )

    db_session.refresh(conversation)
    assignment = (
        db_session.query(ConversationAssignment)
        .filter(ConversationAssignment.conversation_id == conversation.id)
        .filter(ConversationAssignment.is_active.is_(True))
        .first()
    )
    assert result.handled is True
    assert result.resolved is True
    assert assignment is not None
    assert assignment.team_id == sales_team.id
    assert assignment.agent_id is not None
    assert conversation.metadata_[AI_INTAKE_METADATA_KEY]["department"] == "billing_payment"


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("Billing", "billing"),
        ("billing", "billing"),
        ("billing-payment", "billing_payment"),
        ("billing payment", "billing_payment"),
        ("Support", "support"),
        ("SALES", "sales"),
        (" technical support ", "support"),
    ],
)
def test_normalize_department_key_variants(raw_value, expected):
    assert _normalize_department_key(raw_value) == expected


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        (True, True),
        (False, False),
        ("true", True),
        ("false", False),
        (None, False),
        ("", False),
        ("yes", True),
    ],
)
def test_coerce_ai_bool_variants(raw_value, expected):
    assert _coerce_ai_bool(raw_value) is expected


@pytest.mark.parametrize(
    ("department", "handoff_message", "reassurance_message"),
    [
        (
            "support",
            "A member of our support team will respond within 15-30 minutes.",
            "Thanks for your patience - our support team is still reviewing your request and will respond as soon as possible.",
        ),
        (
            "billing_payment",
            "A member of our billing team will respond within 15-30 minutes.",
            "Thanks for your patience - our billing team is still reviewing your request and will respond as soon as possible.",
        ),
        (
            "sales",
            "A member of our sales team will respond within 15-30 minutes.",
            "Thanks for your patience - our sales team is still reviewing your request and will respond as soon as possible.",
        ),
    ],
)
def test_handoff_copy_is_department_specific(department, handoff_message, reassurance_message):
    assert _handoff_message_for_department(department) == handoff_message
    assert _handoff_reassurance_message_for_department(department) == reassurance_message


def test_process_pending_intake_ignores_offline_agents_for_round_robin(db_session, monkeypatch):
    team = CrmTeam(name="Support", is_active=True)
    db_session.add(team)
    db_session.commit()
    online_agent = _make_agent(db_session, team, label="Online", status=AgentPresenceStatus.online)
    _make_agent(db_session, team, label="Offline", status=AgentPresenceStatus.offline)

    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    widget_id = str(uuid.uuid4())
    message = _make_message(db_session, conversation, metadata={"widget_config_id": widget_id})
    _make_config(db_session, scope_key=f"widget:{widget_id}", team_id=team.id)

    monkeypatch.setenv("CRM_AI_PENDING_INTAKE_ENABLED", "1")
    monkeypatch.setattr("app.services.crm.ai_intake.ai_gateway.enabled", lambda db: True)
    monkeypatch.setattr(
        "app.services.crm.ai_intake.ai_gateway.generate_with_fallback",
        lambda db, **kwargs: (
            SimpleNamespace(
                content='{"department":"support","confidence":0.93,"reason":"service issue","needs_followup":false,"followup_question":""}'
            ),
            {"endpoint": "primary", "fallback_used": False},
        ),
    )
    monkeypatch.setattr(
        "app.services.crm.ai_intake.send_message",
        lambda db, payload, author_id=None, trace_id=None: Message(
            conversation_id=payload.conversation_id,
            channel_type=payload.channel_type,
            direction=MessageDirection.outbound,
            status=MessageStatus.sent,
            body=payload.body,
        ),
    )

    process_pending_intake(
        db_session,
        conversation=conversation,
        message=message,
        scope_key=f"widget:{widget_id}",
        is_new_conversation=True,
    )

    assignment = (
        db_session.query(ConversationAssignment)
        .filter(ConversationAssignment.conversation_id == conversation.id)
        .filter(ConversationAssignment.is_active.is_(True))
        .first()
    )
    assert assignment is not None
    assert assignment.team_id == team.id
    assert assignment.agent_id == online_agent.id


def test_process_pending_intake_preserves_selected_department_when_team_has_no_members(
    db_session,
    monkeypatch,
    caplog,
):
    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    widget_id = str(uuid.uuid4())
    message = _make_message(db_session, conversation, metadata={"widget_config_id": widget_id})

    sales_team = CrmTeam(name="Sales Queue", is_active=True)
    support_team = CrmTeam(name="Customer Support", is_active=True)
    db_session.add_all([sales_team, support_team])
    db_session.commit()
    _make_agent(db_session, support_team, label="SupportOnline", status=AgentPresenceStatus.online)
    _make_split_billing_config(
        db_session,
        scope_key=f"widget:{widget_id}",
        sales_team_id=sales_team.id,
        fallback_team_id=support_team.id,
    )

    monkeypatch.setenv("CRM_AI_PENDING_INTAKE_ENABLED", "1")
    monkeypatch.setattr("app.services.crm.ai_intake.ai_gateway.enabled", lambda db: True)
    monkeypatch.setattr(
        "app.services.crm.ai_intake.ai_gateway.generate_with_fallback",
        lambda db, **kwargs: (
            SimpleNamespace(
                content='{"department":"billing_payment","confidence":0.98,"reason":"payment request","needs_followup":false,"followup_question":""}'
            ),
            {"endpoint": "primary", "fallback_used": False},
        ),
    )

    def _fake_send_message(db, payload, author_id=None, trace_id=None):
        outbound = Message(
            conversation_id=payload.conversation_id,
            channel_type=payload.channel_type,
            direction=MessageDirection.outbound,
            status=MessageStatus.sent,
            body=payload.body,
        )
        db.add(outbound)
        db.commit()
        db.refresh(outbound)
        return outbound

    monkeypatch.setattr("app.services.crm.ai_intake.send_message", _fake_send_message)
    caplog.set_level("INFO")

    result = process_pending_intake(
        db_session,
        conversation=conversation,
        message=message,
        scope_key=f"widget:{widget_id}",
        is_new_conversation=True,
    )

    db_session.refresh(conversation)
    assignment = (
        db_session.query(ConversationAssignment)
        .filter(ConversationAssignment.conversation_id == conversation.id)
        .filter(ConversationAssignment.is_active.is_(True))
        .first()
    )
    state = conversation.metadata_[AI_INTAKE_METADATA_KEY]

    assert result.handled is True
    assert result.resolved is True
    assert assignment is not None
    assert assignment.team_id == sales_team.id
    assert assignment.agent_id is None
    assert state["department"] == "billing_payment"
    assert state["routing_state"] == "waiting_for_agent"
    assert state["routing_assigned_team_id"] == str(sales_team.id)
    assert state["routing_assigned_agent_id"] is None
    assert state["routing_assignment_skipped_reason"] == "no_team_members"
    assert state["routing_department_preserved"] is True
    assert state["routing_fallback_blocked"] is True
    assert "routing_no_eligible_agents" in caplog.text
    assert "routing_department_preserved" in caplog.text
    assert "routing_fallback_blocked" in caplog.text


def test_process_pending_intake_preserves_selected_department_when_team_agents_are_offline(
    db_session,
    monkeypatch,
):
    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    widget_id = str(uuid.uuid4())
    message = _make_message(db_session, conversation, metadata={"widget_config_id": widget_id})

    sales_team = CrmTeam(name="Sales Queue", is_active=True)
    support_team = CrmTeam(name="Customer Support", is_active=True)
    db_session.add_all([sales_team, support_team])
    db_session.commit()
    _make_agent(db_session, sales_team, label="SalesOffline", status=AgentPresenceStatus.offline)
    support_agent = _make_agent(db_session, support_team, label="SupportOnline", status=AgentPresenceStatus.online)
    _make_split_billing_config(
        db_session,
        scope_key=f"widget:{widget_id}",
        sales_team_id=sales_team.id,
        fallback_team_id=support_team.id,
    )

    monkeypatch.setenv("CRM_AI_PENDING_INTAKE_ENABLED", "1")
    monkeypatch.setattr("app.services.crm.ai_intake.ai_gateway.enabled", lambda db: True)
    monkeypatch.setattr(
        "app.services.crm.ai_intake.ai_gateway.generate_with_fallback",
        lambda db, **kwargs: (
            SimpleNamespace(
                content='{"department":"billing_payment","confidence":0.99,"reason":"payment follow-up","needs_followup":false,"followup_question":""}'
            ),
            {"endpoint": "primary", "fallback_used": False},
        ),
    )
    monkeypatch.setattr(
        "app.services.crm.ai_intake.send_message",
        lambda db, payload, author_id=None, trace_id=None: Message(
            conversation_id=payload.conversation_id,
            channel_type=payload.channel_type,
            direction=MessageDirection.outbound,
            status=MessageStatus.sent,
            body=payload.body,
        ),
    )

    result = process_pending_intake(
        db_session,
        conversation=conversation,
        message=message,
        scope_key=f"widget:{widget_id}",
        is_new_conversation=True,
    )

    db_session.refresh(conversation)
    assignment = (
        db_session.query(ConversationAssignment)
        .filter(ConversationAssignment.conversation_id == conversation.id)
        .filter(ConversationAssignment.is_active.is_(True))
        .first()
    )
    state = conversation.metadata_[AI_INTAKE_METADATA_KEY]

    assert result.handled is True
    assert result.resolved is True
    assert assignment is not None
    assert assignment.team_id == sales_team.id
    assert assignment.agent_id is None
    assert state["routing_state"] == "waiting_for_agent"
    assert state["routing_assignment_skipped_reason"] == "no_eligible_agents"
    assert state["routing_assigned_team_id"] == str(sales_team.id)
    assert state["routing_fallback_blocked"] is True
    assert state["routing_assigned_agent_id"] != str(support_agent.id)


def test_process_pending_intake_timeout_escalation_preserves_selected_department_queue(
    db_session,
    monkeypatch,
):
    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    widget_id = str(uuid.uuid4())
    support_team = CrmTeam(name="Customer Support", is_active=True)
    sales_team = CrmTeam(name="Sales Queue", is_active=True)
    db_session.add_all([support_team, sales_team])
    db_session.commit()
    _make_agent(db_session, support_team, label="FallbackOnline", status=AgentPresenceStatus.online)
    _make_split_billing_config(
        db_session,
        scope_key=f"widget:{widget_id}",
        sales_team_id=sales_team.id,
        fallback_team_id=support_team.id,
    )

    conversation.status = ConversationStatus.pending
    conversation.metadata_ = {
        AI_INTAKE_METADATA_KEY: {
            "status": "awaiting_timeout",
            "scope_key": f"widget:{widget_id}",
            "started_at": (datetime.now(UTC) - timedelta(minutes=10)).isoformat(),
            "escalate_at": (datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
            "department": "billing_payment",
            "channel": ChannelType.chat_widget.value,
        }
    }
    db_session.commit()
    message = _make_message(db_session, conversation, metadata={"widget_config_id": widget_id})

    monkeypatch.setenv("CRM_AI_PENDING_INTAKE_ENABLED", "1")
    monkeypatch.setattr("app.services.crm.ai_intake.ai_gateway.enabled", lambda db: True)

    result = process_pending_intake(
        db_session,
        conversation=conversation,
        message=message,
        scope_key=f"widget:{widget_id}",
        is_new_conversation=False,
    )

    db_session.refresh(conversation)
    assignment = (
        db_session.query(ConversationAssignment)
        .filter(ConversationAssignment.conversation_id == conversation.id)
        .filter(ConversationAssignment.is_active.is_(True))
        .first()
    )
    state = conversation.metadata_[AI_INTAKE_METADATA_KEY]

    assert result.handled is True
    assert result.escalated is True
    assert result.fallback_used is False
    assert assignment is not None
    assert assignment.team_id == sales_team.id
    assert assignment.agent_id is None
    assert state["status"] == "escalated"
    assert state["department"] == "billing_payment"
    assert state["routing_state"] == "waiting_for_agent"
    assert state["routing_fallback_blocked"] is True
    assert state["routing_assignment_skipped_reason"] == "no_team_members"


def test_manual_assignment_still_works_after_ai_leaves_team_only_queue(db_session, monkeypatch):
    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    widget_id = str(uuid.uuid4())
    message = _make_message(db_session, conversation, metadata={"widget_config_id": widget_id})

    sales_team = CrmTeam(name="Sales Queue", is_active=True)
    support_team = CrmTeam(name="Customer Support", is_active=True)
    db_session.add_all([sales_team, support_team])
    db_session.commit()
    support_agent = _make_agent(db_session, support_team, label="ManualSupport", status=AgentPresenceStatus.online)
    actor = _make_person(db_session)
    _make_split_billing_config(
        db_session,
        scope_key=f"widget:{widget_id}",
        sales_team_id=sales_team.id,
        fallback_team_id=support_team.id,
    )

    monkeypatch.setenv("CRM_AI_PENDING_INTAKE_ENABLED", "1")
    monkeypatch.setattr("app.services.crm.ai_intake.ai_gateway.enabled", lambda db: True)
    monkeypatch.setattr(
        "app.services.crm.ai_intake.ai_gateway.generate_with_fallback",
        lambda db, **kwargs: (
            SimpleNamespace(
                content='{"department":"billing_payment","confidence":0.95,"reason":"payment request","needs_followup":false,"followup_question":""}'
            ),
            {"endpoint": "primary", "fallback_used": False},
        ),
    )
    monkeypatch.setattr(
        "app.services.crm.ai_intake.send_message",
        lambda db, payload, author_id=None, trace_id=None: Message(
            conversation_id=payload.conversation_id,
            channel_type=payload.channel_type,
            direction=MessageDirection.outbound,
            status=MessageStatus.sent,
            body=payload.body,
        ),
    )

    process_pending_intake(
        db_session,
        conversation=conversation,
        message=message,
        scope_key=f"widget:{widget_id}",
        is_new_conversation=True,
    )
    manual_assignment = conversation_service.assign_conversation(
        db_session,
        conversation_id=str(conversation.id),
        agent_id=str(support_agent.id),
        team_id=str(support_team.id),
        assigned_by_id=str(actor.id),
        update_lead_owner=False,
    )

    active_assignment = (
        db_session.query(ConversationAssignment)
        .filter(ConversationAssignment.conversation_id == conversation.id)
        .filter(ConversationAssignment.is_active.is_(True))
        .first()
    )
    assert manual_assignment is not None
    assert active_assignment is not None
    assert active_assignment.team_id == support_team.id
    assert active_assignment.agent_id == support_agent.id


def test_process_pending_intake_sends_followup_when_uncertain(db_session, monkeypatch):
    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    widget_id = str(uuid.uuid4())
    message = _make_message(db_session, conversation, metadata={"widget_config_id": widget_id})
    _make_config(db_session, scope_key=f"widget:{widget_id}")

    monkeypatch.setenv("CRM_AI_PENDING_INTAKE_ENABLED", "1")
    monkeypatch.setattr("app.services.crm.ai_intake.ai_gateway.enabled", lambda db: True)
    monkeypatch.setattr(
        "app.services.crm.ai_intake.ai_gateway.generate_with_fallback",
        lambda db, **kwargs: (
            SimpleNamespace(
                content='{"department":null,"confidence":0.41,"reason":"needs more context","needs_followup":true,"followup_question":"Is this about an existing service issue or a new order?"}'
            ),
            {"endpoint": "primary", "fallback_used": False},
        ),
    )
    sent = {}

    def _fake_send_message(db, payload, author_id=None, trace_id=None):
        sent["body"] = payload.body
        outbound = Message(
            conversation_id=payload.conversation_id,
            channel_type=payload.channel_type,
            direction=MessageDirection.outbound,
            status=MessageStatus.sent,
            body=payload.body,
        )
        db.add(outbound)
        db.commit()
        db.refresh(outbound)
        return outbound

    monkeypatch.setattr("app.services.crm.ai_intake.send_message", _fake_send_message)

    result = process_pending_intake(
        db_session,
        conversation=conversation,
        message=message,
        scope_key=f"widget:{widget_id}",
        is_new_conversation=True,
    )

    db_session.refresh(conversation)
    assert result.handled is True
    assert result.followup_sent is True
    assert conversation.status == ConversationStatus.pending
    assert sent["body"] == "Is this about an existing service issue or a new order?"
    assert conversation.metadata_[AI_INTAKE_METADATA_KEY]["turn_count"] == 1


def test_process_pending_intake_escalates_after_single_followup_retry_limit(db_session, monkeypatch):
    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    widget_id = str(uuid.uuid4())
    team = CrmTeam(name="Fallback", is_active=True)
    db_session.add(team)
    db_session.commit()
    config = _make_config(db_session, scope_key=f"widget:{widget_id}", team_id=team.id)
    config.max_clarification_turns = 1
    config.escalate_after_minutes = 0
    db_session.commit()

    conversation.status = ConversationStatus.pending
    conversation.metadata_ = {
        AI_INTAKE_METADATA_KEY: {
            "status": "awaiting_customer",
            "scope_key": f"widget:{widget_id}",
            "started_at": datetime.now(UTC).isoformat(),
            "escalate_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
            "turn_count": 1,
            "followup_question": "What do you need help with?",
        }
    }
    db_session.commit()

    message = Message(
        conversation_id=conversation.id,
        channel_type=ChannelType.chat_widget,
        direction=MessageDirection.inbound,
        status=MessageStatus.received,
        body="Not sure",
        metadata_={"widget_config_id": widget_id},
    )
    db_session.add(message)
    db_session.commit()
    db_session.refresh(message)

    monkeypatch.setenv("CRM_AI_PENDING_INTAKE_ENABLED", "1")
    monkeypatch.setattr("app.services.crm.ai_intake.ai_gateway.enabled", lambda db: True)
    monkeypatch.setattr(
        "app.services.crm.ai_intake.ai_gateway.generate_with_fallback",
        lambda db, **kwargs: (
            SimpleNamespace(
                content='{"department":null,"confidence":0.30,"reason":"still unclear","needs_followup":true,"followup_question":"Can you clarify?"}'
            ),
            {"endpoint": "primary", "fallback_used": False},
        ),
    )

    result = process_pending_intake(
        db_session,
        conversation=conversation,
        message=message,
        scope_key=f"widget:{widget_id}",
        is_new_conversation=False,
    )

    db_session.refresh(conversation)
    assignment = (
        db_session.query(ConversationAssignment)
        .filter(ConversationAssignment.conversation_id == conversation.id)
        .filter(ConversationAssignment.is_active.is_(True))
        .first()
    )
    assert result.handled is True
    assert result.escalated is True
    assert result.followup_sent is False
    assert conversation.metadata_[AI_INTAKE_METADATA_KEY]["status"] == "escalated"
    assert conversation.metadata_[AI_INTAKE_METADATA_KEY]["escalated_reason"] == "timeout"
    assert assignment is not None
    assert assignment.team_id == team.id


def test_process_pending_intake_existing_without_state_skips(db_session, monkeypatch):
    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    widget_id = str(uuid.uuid4())
    message = _make_message(db_session, conversation, metadata={"widget_config_id": widget_id})
    _make_config(db_session, scope_key=f"widget:{widget_id}")

    monkeypatch.setenv("CRM_AI_PENDING_INTAKE_ENABLED", "1")
    monkeypatch.setattr("app.services.crm.ai_intake.ai_gateway.enabled", lambda db: True)

    result = process_pending_intake(
        db_session,
        conversation=conversation,
        message=message,
        scope_key=f"widget:{widget_id}",
        is_new_conversation=False,
    )

    db_session.refresh(conversation)
    assert result.handled is False
    assert conversation.metadata_ in (None, {})


def test_send_due_handoff_reassurance_followups_sends_after_15_minutes(db_session, monkeypatch):
    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    _make_message(db_session, conversation, body="Need help")
    handoff_at = _seed_resolved_handoff_state(
        db_session,
        conversation=conversation,
        department="support",
        handoff_sent_at=datetime.now(UTC) - timedelta(minutes=16),
    )

    sent = []

    def _fake_send_message(db, payload, author_id=None, trace_id=None):
        outbound = Message(
            conversation_id=payload.conversation_id,
            channel_type=payload.channel_type,
            direction=MessageDirection.outbound,
            status=MessageStatus.sent,
            body=payload.body,
        )
        db.add(outbound)
        db.commit()
        db.refresh(outbound)
        sent.append(payload.body)
        return outbound

    monkeypatch.setenv("CRM_AI_PENDING_INTAKE_ENABLED", "1")
    monkeypatch.setattr("app.services.crm.ai_intake.send_message", _fake_send_message)

    result = send_due_handoff_reassurance_followups(db_session, limit=20)

    db_session.refresh(conversation)
    state = conversation.metadata_[AI_INTAKE_METADATA_KEY]
    assert result["sent"] == 1
    assert result["suppressed"] == 0
    assert sent == [
        "Thanks for your patience - our support team is still reviewing your request and will respond as soon as possible."
    ]
    assert state["handoff_followup_sent_at"] is not None
    assert state["handoff_followup_message"] == sent[0]
    assert state["first_human_reply_at"] is None
    assert state["handoff_state"] == AI_INTAKE_HANDOFF_STATE_AWAITING_AGENT
    assert handoff_at.isoformat() == state["handoff_sent_at"]


def test_send_due_handoff_reassurance_followups_waits_until_due(db_session, monkeypatch):
    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    _make_message(db_session, conversation, body="Need help")
    _seed_resolved_handoff_state(
        db_session,
        conversation=conversation,
        department="support",
        handoff_sent_at=datetime.now(UTC) - timedelta(minutes=14, seconds=30),
    )

    monkeypatch.setenv("CRM_AI_PENDING_INTAKE_ENABLED", "1")
    monkeypatch.setattr(
        "app.services.crm.ai_intake.send_message",
        lambda db, payload, author_id=None, trace_id=None: pytest.fail("follow-up should not send before due"),
    )

    result = send_due_handoff_reassurance_followups(db_session, limit=20)

    db_session.refresh(conversation)
    state = conversation.metadata_[AI_INTAKE_METADATA_KEY]
    assert result["sent"] == 0
    assert result["suppressed"] == 1
    assert state["handoff_followup_sent_at"] is None


def test_send_due_handoff_reassurance_followups_requires_awaiting_agent_handoff_state(db_session, monkeypatch):
    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    _make_message(db_session, conversation, body="Need help")
    _seed_resolved_handoff_state(
        db_session,
        conversation=conversation,
        department="support",
        handoff_sent_at=datetime.now(UTC) - timedelta(minutes=16),
        handoff_state="assigned",
    )

    monkeypatch.setenv("CRM_AI_PENDING_INTAKE_ENABLED", "1")
    monkeypatch.setattr(
        "app.services.crm.ai_intake.send_message",
        lambda db, payload, author_id=None, trace_id=None: pytest.fail(
            "follow-up should only send while awaiting_agent"
        ),
    )

    result = send_due_handoff_reassurance_followups(db_session, limit=20)

    db_session.refresh(conversation)
    state = conversation.metadata_[AI_INTAKE_METADATA_KEY]
    assert result["sent"] == 0
    assert result["suppressed"] == 1
    assert state["handoff_state"] == "assigned"
    assert state["handoff_followup_sent_at"] is None


def test_send_due_handoff_reassurance_followups_skips_after_agent_reply(db_session, monkeypatch):
    team = CrmTeam(name="Support", is_active=True)
    db_session.add(team)
    db_session.commit()
    agent = _make_agent(db_session, team, label="Reply", status=AgentPresenceStatus.online)
    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    _make_message(db_session, conversation, body="Need help")
    handoff_at = _seed_resolved_handoff_state(
        db_session,
        conversation=conversation,
        department="support",
        handoff_sent_at=datetime.now(UTC) - timedelta(minutes=16),
    )
    _make_agent_reply(
        db_session,
        conversation,
        agent,
        sent_at=handoff_at + timedelta(minutes=5),
    )

    monkeypatch.setenv("CRM_AI_PENDING_INTAKE_ENABLED", "1")
    monkeypatch.setattr(
        "app.services.crm.ai_intake.send_message",
        lambda db, payload, author_id=None, trace_id=None: pytest.fail("follow-up should not send after agent reply"),
    )

    result = send_due_handoff_reassurance_followups(db_session, limit=20)

    db_session.refresh(conversation)
    state = conversation.metadata_[AI_INTAKE_METADATA_KEY]
    assert result["sent"] == 0
    assert result["suppressed"] == 1
    assert state["first_human_reply_at"] is not None
    assert state["handoff_state"] == AI_INTAKE_HANDOFF_STATE_IN_PROGRESS
    assert state["handoff_followup_sent_at"] is None


def test_agent_reply_immediately_transitions_handoff_state_without_waiting_for_scheduler(db_session):
    team = CrmTeam(name="Support", is_active=True)
    db_session.add(team)
    db_session.commit()
    agent = _make_agent(db_session, team, label="Immediate", status=AgentPresenceStatus.online)
    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    _seed_resolved_handoff_state(
        db_session,
        conversation=conversation,
        department="support",
        handoff_sent_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    outbound_sent_at = (conversation.created_at or datetime.now(UTC).replace(tzinfo=None)) + timedelta(minutes=1)

    outbound = conversation_service.Messages.create(
        db_session,
        MessageCreate(
            conversation_id=conversation.id,
            channel_type=ChannelType.chat_widget,
            direction=MessageDirection.outbound,
            status=MessageStatus.sent,
            body="We are reviewing this now.",
            author_id=agent.person_id,
            sent_at=outbound_sent_at,
        ),
    )

    db_session.refresh(conversation)
    state = conversation.metadata_[AI_INTAKE_METADATA_KEY]
    assert outbound.author_id == agent.person_id
    assert state["handoff_state"] == AI_INTAKE_HANDOFF_STATE_IN_PROGRESS
    assert state["first_human_reply_at"] == outbound_sent_at.replace(tzinfo=UTC).isoformat()
    assert state["handoff_followup_sent_at"] is None


def test_send_due_handoff_reassurance_followups_does_not_send_twice(db_session, monkeypatch):
    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    _make_message(db_session, conversation, body="Need help")
    _seed_resolved_handoff_state(
        db_session,
        conversation=conversation,
        department="support",
        handoff_sent_at=datetime.now(UTC) - timedelta(minutes=16),
    )

    sent = []

    def _fake_send_message(db, payload, author_id=None, trace_id=None):
        outbound = Message(
            conversation_id=payload.conversation_id,
            channel_type=payload.channel_type,
            direction=MessageDirection.outbound,
            status=MessageStatus.sent,
            body=payload.body,
        )
        db.add(outbound)
        db.commit()
        db.refresh(outbound)
        sent.append(payload.body)
        return outbound

    monkeypatch.setenv("CRM_AI_PENDING_INTAKE_ENABLED", "1")
    monkeypatch.setattr("app.services.crm.ai_intake.send_message", _fake_send_message)

    first = send_due_handoff_reassurance_followups(db_session, limit=20)
    second = send_due_handoff_reassurance_followups(db_session, limit=20)

    assert first["sent"] == 1
    assert second["sent"] == 0
    assert second["suppressed"] == 1
    assert len(sent) == 1


def test_send_due_handoff_reassurance_followups_does_not_mark_sent_when_send_fails(db_session, monkeypatch):
    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    conversation_id = conversation.id
    _make_message(db_session, conversation, body="Need help")
    _seed_resolved_handoff_state(
        db_session,
        conversation=conversation,
        department="support",
        handoff_sent_at=datetime.now(UTC) - timedelta(minutes=16),
    )

    monkeypatch.setenv("CRM_AI_PENDING_INTAKE_ENABLED", "1")
    monkeypatch.setattr(
        "app.services.crm.ai_intake.send_message",
        lambda db, payload, author_id=None, trace_id=None: (_ for _ in ()).throw(RuntimeError("send failed")),
    )

    result = send_due_handoff_reassurance_followups(db_session, limit=20)

    conversation = db_session.get(Conversation, conversation_id)
    state = conversation.metadata_[AI_INTAKE_METADATA_KEY]
    assert result["sent"] == 0
    assert len(result["errors"]) == 1
    assert state["handoff_followup_sent_at"] is None
    assert state.get("handoff_followup_claimed_at") is None


def test_send_due_handoff_reassurance_followups_reconciles_existing_message_without_resend(db_session, monkeypatch):
    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    inbound = _make_message(db_session, conversation, body="Need help")
    handoff_at = _seed_resolved_handoff_state(
        db_session,
        conversation=conversation,
        department="support",
        handoff_sent_at=datetime.now(UTC) - timedelta(minutes=16),
    )
    outbound = Message(
        conversation_id=conversation.id,
        channel_type=inbound.channel_type,
        direction=MessageDirection.outbound,
        status=MessageStatus.sent,
        body="Thanks for your patience - our support team is still reviewing your request and will respond as soon as possible.",
        sent_at=handoff_at + timedelta(minutes=15),
        metadata_={
            "ai_intake_generated": True,
            "ai_intake_message_kind": "handoff_reassurance",
        },
    )
    db_session.add(outbound)
    db_session.commit()

    monkeypatch.setenv("CRM_AI_PENDING_INTAKE_ENABLED", "1")
    monkeypatch.setattr(
        "app.services.crm.ai_intake.send_message",
        lambda db, payload, author_id=None, trace_id=None: pytest.fail("existing follow-up should be reconciled"),
    )

    result = send_due_handoff_reassurance_followups(db_session, limit=20)

    db_session.refresh(conversation)
    state = conversation.metadata_[AI_INTAKE_METADATA_KEY]
    assert result["sent"] == 0
    assert result["suppressed"] == 1
    assert state["handoff_followup_sent_at"] == outbound.sent_at.replace(tzinfo=UTC).isoformat()
    assert state["handoff_followup_message"] == outbound.body


def test_send_due_handoff_reassurance_followups_suppresses_resolved_conversation_status(db_session, monkeypatch):
    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    _make_message(db_session, conversation, body="Need help")
    _seed_resolved_handoff_state(
        db_session,
        conversation=conversation,
        department="support",
        handoff_sent_at=datetime.now(UTC) - timedelta(minutes=16),
    )
    conversation.status = ConversationStatus.resolved
    db_session.commit()

    monkeypatch.setenv("CRM_AI_PENDING_INTAKE_ENABLED", "1")
    monkeypatch.setattr(
        "app.services.crm.ai_intake.send_message",
        lambda db, payload, author_id=None, trace_id=None: pytest.fail("follow-up should be suppressed"),
    )

    result = send_due_handoff_reassurance_followups(db_session, limit=20)

    assert result["sent"] == 0
    assert result["suppressed"] == 0


def test_send_due_handoff_reassurance_followups_is_idempotent_across_repeated_runs(db_session, monkeypatch):
    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    _make_message(db_session, conversation, body="Need help")
    _seed_resolved_handoff_state(
        db_session,
        conversation=conversation,
        department="sales",
        handoff_sent_at=datetime.now(UTC) - timedelta(minutes=16),
    )

    sent = []

    def _fake_send_message(db, payload, author_id=None, trace_id=None):
        outbound = Message(
            conversation_id=payload.conversation_id,
            channel_type=payload.channel_type,
            direction=MessageDirection.outbound,
            status=MessageStatus.sent,
            body=payload.body,
        )
        db.add(outbound)
        db.commit()
        db.refresh(outbound)
        sent.append(payload.body)
        return outbound

    monkeypatch.setenv("CRM_AI_PENDING_INTAKE_ENABLED", "1")
    monkeypatch.setattr("app.services.crm.ai_intake.send_message", _fake_send_message)

    send_due_handoff_reassurance_followups(db_session, limit=20)
    send_due_handoff_reassurance_followups(db_session, limit=20)

    db_session.refresh(conversation)
    state = conversation.metadata_[AI_INTAKE_METADATA_KEY]
    assert len(sent) == 1
    assert state["handoff_followup_sent_at"] is not None


def test_backfill_missing_handoff_states_sets_awaiting_agent_for_legacy_resolved_state(db_session):
    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    handoff_at = datetime.now(UTC) - timedelta(minutes=16)
    conversation.metadata_ = {
        AI_INTAKE_METADATA_KEY: {
            "status": "resolved",
            "scope_key": "widget:test",
            "started_at": (handoff_at - timedelta(minutes=1)).isoformat(),
            "resolved_at": handoff_at.isoformat(),
            "department": "support",
            "handoff_department": "support",
            "handoff_sent": True,
            "handoff_sent_at": handoff_at.isoformat(),
            "handoff_followup_sent_at": None,
            "first_human_reply_at": None,
        }
    }
    db_session.commit()

    result = backfill_missing_handoff_states(db_session, limit=50)

    db_session.refresh(conversation)
    state = conversation.metadata_[AI_INTAKE_METADATA_KEY]
    assert result["updated"] == 1
    assert state["status"] == "resolved"
    assert state["handoff_state"] == AI_INTAKE_HANDOFF_STATE_AWAITING_AGENT
    assert state["handoff_sent_at"] == handoff_at.isoformat()
    assert state["handoff_followup_due_at"] == (handoff_at + timedelta(minutes=15)).isoformat()


def test_backfill_missing_handoff_states_leaves_non_resolved_state_as_none(db_session):
    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    conversation.metadata_ = {
        AI_INTAKE_METADATA_KEY: {
            "status": "pending",
            "scope_key": "widget:test",
            "started_at": datetime.now(UTC).isoformat(),
        }
    }
    db_session.commit()

    result = backfill_missing_handoff_states(db_session, limit=50)

    db_session.refresh(conversation)
    state = conversation.metadata_[AI_INTAKE_METADATA_KEY]
    assert result["updated"] == 0
    assert state["status"] == "pending"
    assert "handoff_state" not in state


def test_send_handoff_message_does_not_mark_sent_when_send_fails(db_session, monkeypatch):
    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    conversation_id = conversation.id
    message = _make_message(db_session, conversation, body="Need help")
    conversation.metadata_ = {
        AI_INTAKE_METADATA_KEY: {
            "status": "resolved",
            "handoff_state": AI_INTAKE_HANDOFF_STATE_AWAITING_AGENT,
            "department": "support",
        }
    }
    db_session.commit()

    monkeypatch.setattr(
        "app.services.crm.ai_intake.send_message",
        lambda db, payload, author_id=None, trace_id=None: (_ for _ in ()).throw(RuntimeError("handoff failed")),
    )

    with pytest.raises(RuntimeError):
        _send_handoff_message(
            db_session,
            conversation=conversation,
            message=message,
            department="support",
        )

    conversation = db_session.get(Conversation, conversation_id)
    state = conversation.metadata_[AI_INTAKE_METADATA_KEY]
    assert state.get("handoff_sent") is not True
    assert state.get("handoff_sent_at") is None
    assert state.get("handoff_send_claimed_at") is None


def test_send_handoff_message_reconciles_existing_message_without_resend(db_session, monkeypatch):
    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    inbound = _make_message(db_session, conversation, body="Need help")
    outbound = Message(
        conversation_id=conversation.id,
        channel_type=inbound.channel_type,
        direction=MessageDirection.outbound,
        status=MessageStatus.sent,
        body="A member of our support team will respond within 15-30 minutes.",
        sent_at=datetime.now(UTC),
        metadata_={
            "ai_intake_generated": True,
            "ai_intake_message_kind": "handoff",
        },
    )
    db_session.add(outbound)
    conversation.metadata_ = {
        AI_INTAKE_METADATA_KEY: {
            "status": "resolved",
            "handoff_state": AI_INTAKE_HANDOFF_STATE_AWAITING_AGENT,
            "department": "support",
        }
    }
    db_session.commit()

    monkeypatch.setattr(
        "app.services.crm.ai_intake.send_message",
        lambda db, payload, author_id=None, trace_id=None: pytest.fail("existing handoff should be reconciled"),
    )

    result = _send_handoff_message(
        db_session,
        conversation=conversation,
        message=inbound,
        department="support",
    )

    db_session.refresh(conversation)
    state = conversation.metadata_[AI_INTAKE_METADATA_KEY]
    assert result is False
    assert state["handoff_sent"] is True
    assert state["handoff_sent_at"] == outbound.sent_at.replace(tzinfo=UTC).isoformat()
    assert state["handoff_message"] == outbound.body


def test_process_pending_intake_existing_pending_state_continues(db_session, monkeypatch):
    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    widget_id = str(uuid.uuid4())
    _make_message(db_session, conversation, metadata={"widget_config_id": widget_id}, body="Hi")
    team = CrmTeam(name="Support", is_active=True)
    db_session.add(team)
    db_session.commit()
    _make_config(db_session, scope_key=f"widget:{widget_id}", team_id=team.id)
    conversation.status = ConversationStatus.pending
    conversation.metadata_ = {
        AI_INTAKE_METADATA_KEY: {
            "status": "awaiting_customer",
            "scope_key": f"widget:{widget_id}",
            "started_at": datetime.now(UTC).isoformat(),
            "escalate_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
            "turn_count": 1,
        }
    }
    db_session.commit()

    second_message = Message(
        conversation_id=conversation.id,
        channel_type=ChannelType.chat_widget,
        direction=MessageDirection.inbound,
        status=MessageStatus.received,
        body="Support",
        metadata_={"widget_config_id": widget_id},
    )
    db_session.add(second_message)
    db_session.commit()
    db_session.refresh(second_message)

    monkeypatch.setenv("CRM_AI_PENDING_INTAKE_ENABLED", "1")
    monkeypatch.setattr("app.services.crm.ai_intake.ai_gateway.enabled", lambda db: True)
    monkeypatch.setattr(
        "app.services.crm.ai_intake.ai_gateway.generate_with_fallback",
        lambda db, **kwargs: (
            SimpleNamespace(
                content='{"department":"support","confidence":0.96,"reason":"customer answered support","needs_followup":false,"followup_question":""}'
            ),
            {"endpoint": "primary", "fallback_used": False},
        ),
    )
    monkeypatch.setattr(
        "app.services.crm.ai_intake.send_message",
        lambda db, payload, author_id=None, trace_id=None: Message(
            conversation_id=payload.conversation_id,
            channel_type=payload.channel_type,
            direction=MessageDirection.outbound,
            status=MessageStatus.sent,
            body=payload.body,
        ),
    )

    result = process_pending_intake(
        db_session,
        conversation=conversation,
        message=second_message,
        scope_key=f"widget:{widget_id}",
        is_new_conversation=False,
    )

    db_session.refresh(conversation)
    assert result.handled is True
    assert result.resolved is True
    assert conversation.metadata_[AI_INTAKE_METADATA_KEY]["status"] == "resolved"


def test_process_pending_intake_waits_for_timeout_when_followup_disabled(db_session, monkeypatch):
    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    widget_id = str(uuid.uuid4())
    message = _make_message(db_session, conversation, metadata={"widget_config_id": widget_id})
    team = CrmTeam(name="Live", is_active=True)
    db_session.add(team)
    db_session.commit()
    config = _make_config(db_session, scope_key=f"widget:{widget_id}", team_id=team.id)
    config.allow_followup_questions = False
    config.max_clarification_turns = 0
    config.escalate_after_minutes = 15
    db_session.commit()

    monkeypatch.setenv("CRM_AI_PENDING_INTAKE_ENABLED", "1")
    monkeypatch.setattr("app.services.crm.ai_intake.ai_gateway.enabled", lambda db: True)
    monkeypatch.setattr(
        "app.services.crm.ai_intake.ai_gateway.generate_with_fallback",
        lambda db, **kwargs: (
            SimpleNamespace(
                content='{"department":null,"confidence":0.22,"reason":"unclear","needs_followup":true,"followup_question":"What do you need help with?"}'
            ),
            {"endpoint": "primary", "fallback_used": False},
        ),
    )

    result = process_pending_intake(
        db_session,
        conversation=conversation,
        message=message,
        scope_key=f"widget:{widget_id}",
        is_new_conversation=True,
    )

    db_session.refresh(conversation)
    state = conversation.metadata_[AI_INTAKE_METADATA_KEY]
    assert result.handled is True
    assert result.followup_sent is False
    assert result.escalated is False
    assert conversation.status == ConversationStatus.pending
    assert state["status"] == "awaiting_timeout"


@pytest.mark.parametrize(
    "content",
    [
        "",
        "not-json",
        '{"department": ',
    ],
)
def test_process_pending_intake_ai_failure_escalates_safely(db_session, monkeypatch, content):
    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    widget_id = str(uuid.uuid4())
    message = _make_message(db_session, conversation, metadata={"widget_config_id": widget_id})
    team = CrmTeam(name="Fallback", is_active=True)
    db_session.add(team)
    db_session.commit()
    _make_config(db_session, scope_key=f"widget:{widget_id}", team_id=team.id)

    monkeypatch.setenv("CRM_AI_PENDING_INTAKE_ENABLED", "1")
    monkeypatch.setattr("app.services.crm.ai_intake.ai_gateway.enabled", lambda db: True)
    monkeypatch.setattr(
        "app.services.crm.ai_intake.ai_gateway.generate_with_fallback",
        lambda db, **kwargs: (SimpleNamespace(content=content), {"endpoint": "primary", "fallback_used": False}),
    )

    result = process_pending_intake(
        db_session,
        conversation=conversation,
        message=message,
        scope_key=f"widget:{widget_id}",
        is_new_conversation=True,
    )

    db_session.refresh(conversation)
    assignment = (
        db_session.query(ConversationAssignment)
        .filter(ConversationAssignment.conversation_id == conversation.id)
        .filter(ConversationAssignment.is_active.is_(True))
        .first()
    )
    assert result.handled is True
    assert result.escalated is True
    assert conversation.metadata_[AI_INTAKE_METADATA_KEY]["status"] == "escalated"
    assert conversation.metadata_[AI_INTAKE_METADATA_KEY]["escalated_reason"] == "ai_error"
    assert assignment is not None
    assert assignment.team_id == team.id


def test_process_pending_intake_timeout_error_escalates_safely(db_session, monkeypatch):
    from app.services.ai.client import AIClientError

    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    widget_id = str(uuid.uuid4())
    message = _make_message(db_session, conversation, metadata={"widget_config_id": widget_id})
    team = CrmTeam(name="Fallback", is_active=True)
    db_session.add(team)
    db_session.commit()
    _make_config(db_session, scope_key=f"widget:{widget_id}", team_id=team.id)

    monkeypatch.setenv("CRM_AI_PENDING_INTAKE_ENABLED", "1")
    monkeypatch.setattr("app.services.crm.ai_intake.ai_gateway.enabled", lambda db: True)

    def _raise_timeout(db, **kwargs):
        raise AIClientError("timeout", failure_type="timeout", timeout_type="read", transient=True)

    monkeypatch.setattr("app.services.crm.ai_intake.ai_gateway.generate_with_fallback", _raise_timeout)
    before = AI_INTAKE_ESCALATIONS.labels(reason="ai_error")._value.get()

    result = process_pending_intake(
        db_session,
        conversation=conversation,
        message=message,
        scope_key=f"widget:{widget_id}",
        is_new_conversation=True,
    )

    db_session.refresh(conversation)
    assert result.handled is True
    assert result.escalated is True
    assert conversation.metadata_[AI_INTAKE_METADATA_KEY]["status"] == "escalated"
    assert conversation.metadata_[AI_INTAKE_METADATA_KEY]["failure_type"] == "timeout"
    assert conversation.metadata_[AI_INTAKE_METADATA_KEY]["error_class"] == "AIClientError"
    assert AI_INTAKE_ESCALATIONS.labels(reason="ai_error")._value.get() == before + 1


def test_recover_ai_error_escalations_reprocesses_eligible_conversation(db_session, monkeypatch):
    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    widget_id = str(uuid.uuid4())
    _make_message(db_session, conversation, metadata={"widget_config_id": widget_id})
    team = CrmTeam(name="Recovery Support", is_active=True)
    db_session.add(team)
    db_session.commit()
    _make_agent(db_session, team, label="Recovery", status=AgentPresenceStatus.online)
    _make_config(db_session, scope_key=f"widget:{widget_id}", team_id=team.id)

    now = datetime.now(UTC)
    conversation.metadata_ = {
        AI_INTAKE_METADATA_KEY: {
            "status": "escalated",
            "scope_key": f"widget:{widget_id}",
            "started_at": (now - timedelta(minutes=2)).isoformat(),
            "escalated_at": now.isoformat(),
            "escalated_reason": "ai_error",
            "failure_type": "provider_billing",
            "response_preview": '{"error":{"message":"Insufficient Balance"}}',
            "handoff_state": "none",
            "handoff_sent": False,
        }
    }
    db_session.commit()

    monkeypatch.setenv("CRM_AI_PENDING_INTAKE_ENABLED", "1")
    monkeypatch.setattr("app.services.crm.ai_intake.ai_gateway.enabled", lambda db: True)
    monkeypatch.setattr(
        "app.services.crm.ai_intake.ai_gateway.generate_with_fallback",
        lambda db, **kwargs: (
            SimpleNamespace(
                content='{"department":"support","confidence":0.92,"reason":"service issue","needs_followup":false,"followup_question":""}'
            ),
            {"endpoint": "primary", "fallback_used": False},
        ),
    )
    monkeypatch.setattr(
        "app.services.crm.ai_intake.send_message",
        lambda db, payload, author_id=None, trace_id=None: Message(
            conversation_id=payload.conversation_id,
            channel_type=payload.channel_type,
            direction=MessageDirection.outbound,
            status=MessageStatus.sent,
            body=payload.body,
        ),
    )

    result = recover_ai_error_escalations(db_session, limit=10)

    conversation = db_session.get(Conversation, conversation.id)
    state = conversation.metadata_[AI_INTAKE_METADATA_KEY]
    assert result["retried"] == 1
    assert result["recovered"] == 1
    assert state["status"] == "resolved"
    assert state["recovery_attempt_count"] == 1


def test_recover_ai_error_escalations_skips_active_assignments(db_session, monkeypatch):
    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    widget_id = str(uuid.uuid4())
    _make_message(db_session, conversation, metadata={"widget_config_id": widget_id})
    team = CrmTeam(name="Assigned Recovery", is_active=True)
    db_session.add(team)
    db_session.commit()
    agent = _make_agent(db_session, team, label="AssignedRecovery", status=AgentPresenceStatus.online)
    _make_config(db_session, scope_key=f"widget:{widget_id}", team_id=team.id)
    db_session.add(
        ConversationAssignment(
            conversation_id=conversation.id,
            agent_id=agent.id,
            team_id=team.id,
            is_active=True,
        )
    )
    now = datetime.now(UTC)
    conversation.metadata_ = {
        AI_INTAKE_METADATA_KEY: {
            "status": "escalated",
            "scope_key": f"widget:{widget_id}",
            "started_at": (now - timedelta(minutes=2)).isoformat(),
            "escalated_at": now.isoformat(),
            "escalated_reason": "ai_error",
            "failure_type": "provider_billing",
            "response_preview": '{"error":{"message":"Insufficient Balance"}}',
            "handoff_state": "none",
            "handoff_sent": False,
        }
    }
    db_session.commit()

    monkeypatch.setenv("CRM_AI_PENDING_INTAKE_ENABLED", "1")
    monkeypatch.setattr("app.services.crm.ai_intake.ai_gateway.enabled", lambda db: True)

    result = recover_ai_error_escalations(db_session, limit=10)

    db_session.refresh(conversation)
    state = conversation.metadata_[AI_INTAKE_METADATA_KEY]
    assert result["retried"] == 0
    assert result["skipped"] >= 1
    assert state["status"] == "escalated"


def test_history_and_prompt_include_chronological_transcript(db_session):
    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    team = CrmTeam(name="Support", is_active=True)
    db_session.add(team)
    db_session.commit()
    widget_id = str(uuid.uuid4())
    config = _make_config(db_session, scope_key=f"widget:{widget_id}", team_id=team.id)
    _make_message(db_session, conversation, metadata={"widget_config_id": widget_id}, body="Hello")
    outbound = Message(
        conversation_id=conversation.id,
        channel_type=ChannelType.chat_widget,
        direction=MessageDirection.outbound,
        status=MessageStatus.sent,
        body="How can I help?",
    )
    db_session.add(outbound)
    db_session.commit()
    latest = Message(
        conversation_id=conversation.id,
        channel_type=ChannelType.chat_widget,
        direction=MessageDirection.inbound,
        status=MessageStatus.received,
        body="I need support",
        metadata_={"widget_config_id": widget_id},
    )
    db_session.add(latest)
    db_session.commit()
    history = _history(db_session, conversation)
    mappings = [SimpleNamespace(**item) for item in config.department_mappings]
    system, prompt = _build_prompt(
        conversation=conversation,
        history=history,
        config=config,
        mappings=mappings,
        state={},
    )

    assert [item.body for item in history][-3:] == ["Hello", "How can I help?", "I need support"]
    assert "customer: Hello" in prompt
    assert "assistant: How can I help?" in prompt
    assert (
        prompt.index("customer: Hello")
        < prompt.index("assistant: How can I help?")
        < prompt.index("customer: I need support")
    )
    assert "Return strict JSON only" in system


def test_process_pending_intake_does_not_duplicate_handoff_or_assignment(db_session, monkeypatch):
    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    widget_id = str(uuid.uuid4())
    message = _make_message(db_session, conversation, metadata={"widget_config_id": widget_id})
    team = CrmTeam(name="Support", is_active=True)
    db_session.add(team)
    db_session.commit()
    _make_config(db_session, scope_key=f"widget:{widget_id}", team_id=team.id)

    monkeypatch.setenv("CRM_AI_PENDING_INTAKE_ENABLED", "1")
    monkeypatch.setattr("app.services.crm.ai_intake.ai_gateway.enabled", lambda db: True)
    monkeypatch.setattr(
        "app.services.crm.ai_intake.ai_gateway.generate_with_fallback",
        lambda db, **kwargs: (
            SimpleNamespace(
                content='{"department":"support","confidence":0.96,"reason":"service issue","needs_followup":false,"followup_question":""}'
            ),
            {"endpoint": "primary", "fallback_used": False},
        ),
    )
    sent_messages = []

    def _fake_send_message(db, payload, author_id=None, trace_id=None):
        outbound = Message(
            conversation_id=payload.conversation_id,
            channel_type=payload.channel_type,
            direction=MessageDirection.outbound,
            status=MessageStatus.sent,
            body=payload.body,
        )
        sent_messages.append(payload.body)
        db.add(outbound)
        db.commit()
        db.refresh(outbound)
        return outbound

    monkeypatch.setattr("app.services.crm.ai_intake.send_message", _fake_send_message)

    first = process_pending_intake(
        db_session,
        conversation=conversation,
        message=message,
        scope_key=f"widget:{widget_id}",
        is_new_conversation=True,
    )
    second = process_pending_intake(
        db_session,
        conversation=conversation,
        message=message,
        scope_key=f"widget:{widget_id}",
        is_new_conversation=False,
    )

    active_assignments = (
        db_session.query(ConversationAssignment)
        .filter(ConversationAssignment.conversation_id == conversation.id)
        .filter(ConversationAssignment.is_active.is_(True))
        .all()
    )
    assert first.resolved is True
    assert second.handled is False
    assert len(active_assignments) == 1
    assert sent_messages.count("A member of our support team will respond within 15-30 minutes.") == 1


def test_escalate_expired_pending_intakes_opens_and_assigns_fallback_team(db_session, monkeypatch):
    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    team = CrmTeam(name="Fallback", is_active=True)
    db_session.add(team)
    db_session.commit()
    widget_id = str(uuid.uuid4())
    _make_config(db_session, scope_key=f"widget:{widget_id}", team_id=team.id)
    conversation.status = ConversationStatus.pending
    conversation.metadata_ = {
        AI_INTAKE_METADATA_KEY: {
            "status": "awaiting_timeout",
            "scope_key": f"widget:{widget_id}",
            "started_at": (datetime.now(UTC) - timedelta(minutes=10)).isoformat(),
            "escalate_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
            "turn_count": 1,
        }
    }
    db_session.commit()

    monkeypatch.setenv("CRM_AI_PENDING_INTAKE_ENABLED", "1")

    result = escalate_expired_pending_intakes(db_session, limit=20)

    db_session.refresh(conversation)
    assignment = (
        db_session.query(ConversationAssignment)
        .filter(ConversationAssignment.conversation_id == conversation.id)
        .filter(ConversationAssignment.is_active.is_(True))
        .first()
    )
    assert result["escalated"] == 1
    assert conversation.status == ConversationStatus.open
    assert conversation.metadata_[AI_INTAKE_METADATA_KEY]["status"] == "escalated"
    assert assignment is not None
    assert assignment.team_id == team.id


def test_save_ai_intake_config_requires_fallback_team_when_enabled(db_session):
    with pytest.raises(ValueError, match="requires a team_id when AI intake is enabled"):
        save_ai_intake_config(
            db_session,
            scope_key="widget:test",
            channel_type=ChannelType.chat_widget,
            enabled=True,
            confidence_threshold="0.75",
            allow_followup_questions=True,
            max_clarification_turns="1",
            escalate_after_minutes="5",
            exclude_campaign_attribution=True,
            fallback_team_id="",
            instructions="Ask enough to identify intent.",
            department_mappings_json='[{"key":"support","label":"Support","team_id":null}]',
        )


def test_save_ai_intake_config_accepts_split_billing_keys(db_session):
    config = save_ai_intake_config(
        db_session,
        scope_key="widget:split-billing",
        channel_type=ChannelType.chat_widget,
        enabled=True,
        confidence_threshold="0.75",
        allow_followup_questions=True,
        max_clarification_turns="1",
        escalate_after_minutes="5",
        exclude_campaign_attribution=True,
        fallback_team_id=str(uuid.uuid4()),
        instructions="Route billing precisely.",
        department_mappings_json=(
            '[{"key":"billing_payment","label":"Billing Payment","team_id":"'
            + str(uuid.uuid4())
            + '"},{"key":"billing_renewal","label":"Billing Renewal","team_id":"'
            + str(uuid.uuid4())
            + '"}]'
        ),
    )

    keys = {item["key"] for item in config.department_mappings or []}
    assert keys == {"billing_payment", "billing_renewal"}
