"""Tests for CRM reports service."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models.crm.conversation import Conversation, ConversationAssignment, Message
from app.models.crm.enums import ChannelType, ConversationStatus, MessageDirection
from app.models.crm.sales import Lead, Pipeline, PipelineStage
from app.models.crm.enums import LeadStatus
from app.models.projects import Project, ProjectTask, ProjectStatus, TaskStatus
from app.models.tickets import Ticket, TicketStatus, TicketPriority, TicketSlaEvent
from app.models.workforce import WorkOrder, WorkOrderStatus
from app.services.crm import reports


# =============================================================================
# Helper Function Tests
# =============================================================================


def test_status_group_open():
    """Test status group for open statuses."""
    assert reports._status_group(TicketStatus.open) == "open"
    assert reports._status_group(TicketStatus.new) == "open"
    assert reports._status_group(TicketStatus.pending) == "open"


def test_status_group_closed():
    """Test status group for closed statuses."""
    assert reports._status_group(TicketStatus.resolved) == "closed"
    assert reports._status_group(TicketStatus.closed) == "closed"
    assert reports._status_group(TicketStatus.canceled) == "closed"


def test_agent_person_ids_with_agent_id(db_session, crm_agent):
    """Test getting person IDs for specific agent."""
    result = reports._agent_person_ids(db_session, str(crm_agent.id), None)
    assert result == [str(crm_agent.person_id)]


def test_agent_person_ids_agent_not_found(db_session):
    """Test getting person IDs for non-existent agent returns empty list."""
    import uuid
    result = reports._agent_person_ids(db_session, str(uuid.uuid4()), None)
    assert result == []


def test_agent_person_ids_with_team_id(db_session, crm_team, crm_agent, crm_agent_team):
    """Test getting person IDs for team."""
    result = reports._agent_person_ids(db_session, None, str(crm_team.id))
    assert str(crm_agent.person_id) in result


def test_agent_person_ids_none(db_session):
    """Test getting person IDs with no filters returns None."""
    result = reports._agent_person_ids(db_session, None, None)
    assert result is None


# =============================================================================
# Ticket Support Metrics Tests
# =============================================================================


def test_ticket_support_metrics_empty(db_session):
    """Test ticket support metrics with no tickets."""
    result = reports.ticket_support_metrics(db_session, None, None, None, None)
    assert result["tickets"]["total"] == 0
    assert result["avg_resolution_hours"] is None


def test_ticket_support_metrics_with_tickets(db_session, subscriber_account):
    """Test ticket support metrics with tickets."""
    # Create tickets
    ticket1 = Ticket(
        account_id=subscriber_account.id,
        title="Open Ticket",
        status=TicketStatus.open,
    )
    db_session.add(ticket1)

    now = datetime.now(timezone.utc)
    ticket2 = Ticket(
        account_id=subscriber_account.id,
        title="Resolved Ticket",
        status=TicketStatus.resolved,
        created_at=now - timedelta(hours=5),
        resolved_at=now,
    )
    db_session.add(ticket2)
    db_session.commit()

    result = reports.ticket_support_metrics(db_session, None, None, None, None)

    assert result["tickets"]["total"] >= 2
    assert result["tickets"]["open"] >= 1
    assert result["tickets"]["closed"] >= 1
    assert result["avg_resolution_hours"] is not None


def test_ticket_support_metrics_with_date_filter(db_session, subscriber_account):
    """Test ticket support metrics with date filters."""
    now = datetime.now(timezone.utc)
    start_at = now - timedelta(days=7)
    end_at = now

    result = reports.ticket_support_metrics(db_session, start_at, end_at, None, None)

    assert "tickets" in result


def test_ticket_support_metrics_with_agent_filter(db_session, subscriber_account, crm_agent):
    """Test ticket support metrics filtered by agent."""
    # Create ticket assigned to agent's person
    ticket = Ticket(
        account_id=subscriber_account.id,
        title="Agent Ticket",
        status=TicketStatus.open,
        assigned_to_person_id=crm_agent.person_id,
    )
    db_session.add(ticket)
    db_session.commit()

    result = reports.ticket_support_metrics(
        db_session, None, None, str(crm_agent.id), None
    )

    assert result["tickets"]["total"] >= 1


def test_ticket_support_metrics_empty_agent(db_session):
    """Test ticket support metrics with non-existent agent returns zero counts."""
    import uuid
    result = reports.ticket_support_metrics(
        db_session, None, None, str(uuid.uuid4()), None
    )

    assert result["tickets"]["total"] == 0
    assert result["sla"]["total"] == 0


def test_ticket_support_metrics_with_sla_events(db_session, ticket):
    """Test ticket support metrics with SLA events."""
    now = datetime.now(timezone.utc)

    # Create SLA event that was met
    sla_event_met = TicketSlaEvent(
        ticket_id=ticket.id,
        event_type="first_response",
        expected_at=now + timedelta(hours=2),
        actual_at=now + timedelta(hours=1),  # Met (before expected)
    )
    db_session.add(sla_event_met)

    # Create SLA event that was breached
    sla_event_breached = TicketSlaEvent(
        ticket_id=ticket.id,
        event_type="resolution",
        expected_at=now + timedelta(hours=4),
        actual_at=now + timedelta(hours=6),  # Breached (after expected)
    )
    db_session.add(sla_event_breached)
    db_session.commit()

    result = reports.ticket_support_metrics(db_session, None, None, None, None)

    assert result["sla"]["total"] >= 2
    assert result["sla"]["met"] >= 1
    assert result["sla"]["breached"] >= 1
    assert result["sla"]["compliance_percent"] is not None


# =============================================================================
# Inbox KPIs Tests
# =============================================================================


def test_inbox_kpis_empty(db_session):
    """Test inbox KPIs with no data."""
    result = reports.inbox_kpis(db_session, None, None, None, None, None)

    assert result["messages"]["total"] == 0
    assert result["avg_response_minutes"] is None


def test_inbox_kpis_with_messages(db_session, crm_contact):
    """Test inbox KPIs with messages."""
    conversation = Conversation(
        person_id=crm_contact.id,
        status=ConversationStatus.open,
    )
    db_session.add(conversation)
    db_session.commit()

    # Create inbound message
    inbound = Message(
        conversation_id=conversation.id,
        channel_type=ChannelType.email,
        direction=MessageDirection.inbound,
        body="Customer question",
    )
    db_session.add(inbound)

    # Create outbound message
    outbound = Message(
        conversation_id=conversation.id,
        channel_type=ChannelType.email,
        direction=MessageDirection.outbound,
        body="Agent response",
    )
    db_session.add(outbound)
    db_session.commit()

    result = reports.inbox_kpis(db_session, None, None, None, None, None)

    assert result["messages"]["total"] >= 2
    assert result["messages"]["inbound"] >= 1
    assert result["messages"]["outbound"] >= 1


def test_inbox_kpis_with_channel_filter(db_session, crm_contact):
    """Test inbox KPIs filtered by channel type."""
    conversation = Conversation(
        person_id=crm_contact.id,
        status=ConversationStatus.open,
    )
    db_session.add(conversation)
    db_session.commit()

    message = Message(
        conversation_id=conversation.id,
        channel_type=ChannelType.whatsapp,
        direction=MessageDirection.inbound,
        body="WhatsApp message",
    )
    db_session.add(message)
    db_session.commit()

    result = reports.inbox_kpis(db_session, None, None, "whatsapp", None, None)

    # Should filter to only WhatsApp messages
    assert "messages" in result


def test_inbox_kpis_invalid_channel_type(db_session):
    """Test inbox KPIs with invalid channel type."""
    result = reports.inbox_kpis(db_session, None, None, "invalid_channel", None, None)

    # Should still return results, just unfiltered
    assert "messages" in result


def test_inbox_kpis_with_agent_filter(db_session, crm_contact, crm_agent, crm_team):
    """Test inbox KPIs filtered by agent."""
    conversation = Conversation(
        person_id=crm_contact.id,
        status=ConversationStatus.open,
    )
    db_session.add(conversation)
    db_session.commit()

    # Assign conversation to agent
    assignment = ConversationAssignment(
        conversation_id=conversation.id,
        agent_id=crm_agent.id,
        is_active=True,
    )
    db_session.add(assignment)
    db_session.commit()

    result = reports.inbox_kpis(
        db_session, None, None, None, str(crm_agent.id), None
    )

    assert "messages" in result


def test_inbox_kpis_with_team_filter(db_session, crm_contact, crm_team):
    """Test inbox KPIs filtered by team."""
    conversation = Conversation(
        person_id=crm_contact.id,
        status=ConversationStatus.open,
    )
    db_session.add(conversation)
    db_session.commit()

    # Assign conversation to team
    assignment = ConversationAssignment(
        conversation_id=conversation.id,
        team_id=crm_team.id,
        is_active=True,
    )
    db_session.add(assignment)
    db_session.commit()

    result = reports.inbox_kpis(
        db_session, None, None, None, None, str(crm_team.id)
    )

    assert "messages" in result


def test_inbox_kpis_empty_assignments(db_session):
    """Test inbox KPIs when agent/team has no assignments."""
    import uuid
    result = reports.inbox_kpis(
        db_session, None, None, None, str(uuid.uuid4()), None
    )

    assert result["messages"]["total"] == 0
    assert result["avg_response_minutes"] is None


def test_inbox_kpis_with_response_times(db_session, crm_contact):
    """Test inbox KPIs calculates response times."""
    now = datetime.now(timezone.utc)

    conversation = Conversation(
        person_id=crm_contact.id,
        status=ConversationStatus.resolved,
        created_at=now - timedelta(hours=2),
        updated_at=now,
    )
    db_session.add(conversation)
    db_session.commit()

    # Inbound message first
    inbound = Message(
        conversation_id=conversation.id,
        channel_type=ChannelType.email,
        direction=MessageDirection.inbound,
        body="Question",
        created_at=now - timedelta(hours=2),
    )
    db_session.add(inbound)

    # Outbound response later
    outbound = Message(
        conversation_id=conversation.id,
        channel_type=ChannelType.email,
        direction=MessageDirection.outbound,
        body="Answer",
        created_at=now - timedelta(hours=1),
    )
    db_session.add(outbound)
    db_session.commit()

    result = reports.inbox_kpis(db_session, None, None, None, None, None)

    assert result["avg_response_minutes"] is not None
    assert result["avg_resolution_minutes"] is not None


# =============================================================================
# Pipeline Stage Metrics Tests
# =============================================================================


def test_pipeline_stage_metrics(db_session, crm_contact):
    """Test pipeline stage metrics."""
    # Create pipeline
    pipeline = Pipeline(name="Sales Pipeline", is_active=True)
    db_session.add(pipeline)
    db_session.commit()

    # Create stages
    stage1 = PipelineStage(
        pipeline_id=pipeline.id,
        name="Qualification",
        order_index=1,
        is_active=True,
    )
    stage2 = PipelineStage(
        pipeline_id=pipeline.id,
        name="Negotiation",
        order_index=2,
        is_active=True,
    )
    db_session.add(stage1)
    db_session.add(stage2)
    db_session.commit()

    # Create leads
    lead1 = Lead(
        person_id=crm_contact.id,
        pipeline_id=pipeline.id,
        stage_id=stage1.id,
        status=LeadStatus.new,
    )
    lead2 = Lead(
        person_id=crm_contact.id,
        pipeline_id=pipeline.id,
        stage_id=stage2.id,
        status=LeadStatus.won,
    )
    lead3 = Lead(
        person_id=crm_contact.id,
        pipeline_id=pipeline.id,
        stage_id=stage2.id,
        status=LeadStatus.lost,
    )
    db_session.add(lead1)
    db_session.add(lead2)
    db_session.add(lead3)
    db_session.commit()

    result = reports.pipeline_stage_metrics(db_session, str(pipeline.id))

    assert result["total_leads"] == 3
    assert result["won"] == 1
    assert result["lost"] == 1
    assert result["conversion_percent"] is not None
    assert len(result["stages"]) == 2


def test_pipeline_stage_metrics_empty(db_session):
    """Test pipeline stage metrics with no leads."""
    pipeline = Pipeline(name="Empty Pipeline", is_active=True)
    db_session.add(pipeline)
    db_session.commit()

    result = reports.pipeline_stage_metrics(db_session, str(pipeline.id))

    assert result["total_leads"] == 0
    assert result["won"] == 0
    assert result["lost"] == 0
    assert result["conversion_percent"] is None


# =============================================================================
# Field Service Metrics Tests
# =============================================================================


def test_field_service_metrics_empty(db_session):
    """Test field service metrics with no work orders."""
    result = reports.field_service_metrics(db_session, None, None, None, None)

    assert result["total"] == 0
    assert result["avg_completion_hours"] is None


def test_field_service_metrics_with_orders(db_session, subscriber_account):
    """Test field service metrics with work orders."""
    now = datetime.now(timezone.utc)

    order1 = WorkOrder(
        account_id=subscriber_account.id,
        title="Installation",
        status=WorkOrderStatus.scheduled,
    )
    db_session.add(order1)

    order2 = WorkOrder(
        account_id=subscriber_account.id,
        title="Repair",
        status=WorkOrderStatus.completed,
        started_at=now - timedelta(hours=3),
        completed_at=now,
    )
    db_session.add(order2)
    db_session.commit()

    result = reports.field_service_metrics(db_session, None, None, None, None)

    assert result["total"] >= 2
    assert "scheduled" in result["status"]
    assert "completed" in result["status"]


def test_field_service_metrics_with_agent_filter(db_session, subscriber_account, crm_agent):
    """Test field service metrics filtered by agent."""
    order = WorkOrder(
        account_id=subscriber_account.id,
        title="Agent Order",
        status=WorkOrderStatus.scheduled,
        assigned_to_person_id=crm_agent.person_id,
    )
    db_session.add(order)
    db_session.commit()

    result = reports.field_service_metrics(
        db_session, None, None, str(crm_agent.id), None
    )

    assert result["total"] >= 1


def test_field_service_metrics_empty_agent(db_session):
    """Test field service metrics with non-existent agent returns zero."""
    import uuid
    result = reports.field_service_metrics(
        db_session, None, None, str(uuid.uuid4()), None
    )

    assert result["total"] == 0


# =============================================================================
# Project Metrics Tests
# =============================================================================


def test_project_metrics_empty(db_session):
    """Test project metrics with no projects."""
    result = reports.project_metrics(db_session, None, None, None, None)

    assert result["projects"]["total"] == 0
    assert result["tasks"]["total"] == 0


def test_project_metrics_with_projects(db_session, subscriber_account):
    """Test project metrics with projects and tasks."""
    project = Project(
        name="Test Project",
        account_id=subscriber_account.id,
        status=ProjectStatus.active,
    )
    db_session.add(project)
    db_session.commit()

    task1 = ProjectTask(
        project_id=project.id,
        title="Task 1",
        status=TaskStatus.todo,
    )
    task2 = ProjectTask(
        project_id=project.id,
        title="Task 2",
        status=TaskStatus.done,
    )
    db_session.add(task1)
    db_session.add(task2)
    db_session.commit()

    result = reports.project_metrics(db_session, None, None, None, None)

    assert result["projects"]["total"] >= 1
    assert result["tasks"]["total"] >= 2


def test_project_metrics_with_agent_filter(db_session, subscriber_account, crm_agent):
    """Test project metrics filtered by agent."""
    project = Project(
        name="Agent Project",
        account_id=subscriber_account.id,
        status=ProjectStatus.active,
        owner_person_id=crm_agent.person_id,
    )
    db_session.add(project)
    db_session.commit()

    task = ProjectTask(
        project_id=project.id,
        title="Agent Task",
        status=TaskStatus.todo,
        assigned_to_person_id=crm_agent.person_id,
    )
    db_session.add(task)
    db_session.commit()

    result = reports.project_metrics(
        db_session, None, None, str(crm_agent.id), None
    )

    assert result["projects"]["total"] >= 1
    assert result["tasks"]["total"] >= 1


def test_project_metrics_empty_agent(db_session):
    """Test project metrics with non-existent agent returns zero."""
    import uuid
    result = reports.project_metrics(
        db_session, None, None, str(uuid.uuid4()), None
    )

    assert result["projects"]["total"] == 0
    assert result["tasks"]["total"] == 0


def test_project_metrics_with_date_filter(db_session, subscriber_account):
    """Test project metrics with date filters."""
    now = datetime.now(timezone.utc)
    start_at = now - timedelta(days=30)
    end_at = now

    result = reports.project_metrics(db_session, start_at, end_at, None, None)

    assert "projects" in result
    assert "tasks" in result


# =============================================================================
# Sales Pipeline Metrics Tests
# =============================================================================


def test_sales_pipeline_metrics_empty(db_session):
    """Test sales pipeline metrics with no leads."""
    result = reports.sales_pipeline_metrics(db_session, None, None, None, None)

    assert result["pipeline_value"] == 0.0
    assert result["weighted_value"] == 0.0
    assert result["open_deals"] == 0
    assert result["won_deals"] == 0
    assert result["lost_deals"] == 0
    assert result["win_rate"] is None


def test_sales_pipeline_metrics_with_leads(db_session, crm_contact):
    """Test sales pipeline metrics with leads."""
    pipeline = Pipeline(name="Sales Metrics Pipeline", is_active=True)
    db_session.add(pipeline)
    db_session.commit()

    stage = PipelineStage(
        pipeline_id=pipeline.id,
        name="Negotiation",
        order_index=1,
        is_active=True,
    )
    db_session.add(stage)
    db_session.commit()

    lead1 = Lead(
        person_id=crm_contact.id,
        pipeline_id=pipeline.id,
        stage_id=stage.id,
        status=LeadStatus.qualified,
        estimated_value=Decimal("10000.00"),
        probability=60,
        is_active=True,
    )
    lead2 = Lead(
        person_id=crm_contact.id,
        pipeline_id=pipeline.id,
        stage_id=stage.id,
        status=LeadStatus.won,
        estimated_value=Decimal("5000.00"),
        probability=100,
        is_active=True,
    )
    lead3 = Lead(
        person_id=crm_contact.id,
        pipeline_id=pipeline.id,
        stage_id=stage.id,
        status=LeadStatus.lost,
        estimated_value=Decimal("3000.00"),
        is_active=True,
    )
    db_session.add(lead1)
    db_session.add(lead2)
    db_session.add(lead3)
    db_session.commit()

    result = reports.sales_pipeline_metrics(db_session, str(pipeline.id), None, None, None)

    assert result["pipeline_value"] == 18000.0
    assert result["weighted_value"] == 11000.0  # 6000 + 5000
    assert result["open_deals"] == 1
    assert result["won_deals"] == 1
    assert result["lost_deals"] == 1
    assert result["win_rate"] == 50.0
    assert result["avg_deal_size"] == 5000.0
    assert len(result["stages"]) == 1
    assert result["stages"][0]["count"] == 3


def test_sales_pipeline_metrics_with_owner_filter(db_session, crm_contact, crm_agent):
    """Test sales pipeline metrics filtered by owner agent."""
    lead = Lead(
        person_id=crm_contact.id,
        owner_agent_id=crm_agent.id,
        status=LeadStatus.new,
        estimated_value=Decimal("7500.00"),
        probability=40,
        is_active=True,
    )
    db_session.add(lead)
    db_session.commit()

    result = reports.sales_pipeline_metrics(
        db_session, None, None, None, str(crm_agent.id)
    )

    assert result["open_deals"] >= 1
    assert result["weighted_value"] >= 3000.0


# =============================================================================
# Sales Forecast Tests
# =============================================================================


def test_sales_forecast_empty(db_session):
    """Test sales forecast with no leads."""
    result = reports.sales_forecast(db_session, None, months_ahead=3)

    assert len(result) == 3
    for month in result:
        assert month["expected_value"] == 0.0
        assert month["weighted_value"] == 0.0
        assert month["deal_count"] == 0


def test_sales_forecast_with_leads(db_session, crm_contact):
    """Test sales forecast with leads having expected close dates."""
    from datetime import date
    from calendar import monthrange

    today = datetime.now().date()
    # Get first day of current month
    month_start = today.replace(day=1)
    _, last_day = monthrange(month_start.year, month_start.month)
    close_date = month_start.replace(day=15)  # Mid-month

    lead = Lead(
        person_id=crm_contact.id,
        status=LeadStatus.proposal,
        estimated_value=Decimal("20000.00"),
        probability=75,
        expected_close_date=close_date,
        is_active=True,
    )
    db_session.add(lead)
    db_session.commit()

    result = reports.sales_forecast(db_session, None, months_ahead=3)

    # First month should have the lead
    assert result[0]["deal_count"] >= 1
    assert result[0]["expected_value"] >= 20000.0
    assert result[0]["weighted_value"] >= 15000.0


def test_sales_forecast_excludes_won_and_lost(db_session, crm_contact):
    """Test sales forecast excludes won and lost leads."""
    from datetime import date

    today = datetime.now().date()
    close_date = today.replace(day=15)

    won_lead = Lead(
        person_id=crm_contact.id,
        status=LeadStatus.won,
        estimated_value=Decimal("10000.00"),
        expected_close_date=close_date,
        is_active=True,
    )
    lost_lead = Lead(
        person_id=crm_contact.id,
        status=LeadStatus.lost,
        estimated_value=Decimal("5000.00"),
        expected_close_date=close_date,
        is_active=True,
    )
    db_session.add(won_lead)
    db_session.add(lost_lead)
    db_session.commit()

    result = reports.sales_forecast(db_session, None, months_ahead=1)

    # Won and lost leads should not be counted in forecast
    assert result[0]["deal_count"] == 0


# =============================================================================
# Agent Sales Performance Tests
# =============================================================================


def test_agent_sales_performance_empty(db_session):
    """Test agent sales performance with no leads."""
    result = reports.agent_sales_performance(db_session, None, None, None)

    assert isinstance(result, list)
    assert len(result) == 0


def test_agent_sales_performance_with_leads(db_session, crm_contact, crm_agent):
    """Test agent sales performance with agent-owned leads."""
    won_lead = Lead(
        person_id=crm_contact.id,
        owner_agent_id=crm_agent.id,
        status=LeadStatus.won,
        estimated_value=Decimal("8000.00"),
        is_active=True,
    )
    lost_lead = Lead(
        person_id=crm_contact.id,
        owner_agent_id=crm_agent.id,
        status=LeadStatus.lost,
        estimated_value=Decimal("2000.00"),
        is_active=True,
    )
    db_session.add(won_lead)
    db_session.add(lost_lead)
    db_session.commit()

    result = reports.agent_sales_performance(db_session, None, None, None)

    assert len(result) >= 1
    agent_result = next(
        (r for r in result if r["agent_id"] == str(crm_agent.id)), None
    )
    assert agent_result is not None
    assert agent_result["deals_won"] == 1
    assert agent_result["deals_lost"] == 1
    assert agent_result["won_value"] == 8000.0
    assert agent_result["win_rate"] == 50.0


def test_agent_sales_performance_sorted_by_value(db_session, crm_contact, person):
    """Test agent sales performance is sorted by won value descending."""
    from app.models.crm.team import CrmAgent

    # Create two agents with different values
    agent1 = CrmAgent(person_id=crm_contact.id, is_active=True)
    agent2 = CrmAgent(person_id=person.id, is_active=True)
    db_session.add(agent1)
    db_session.add(agent2)
    db_session.commit()

    # Agent 1 wins more value
    lead1 = Lead(
        person_id=crm_contact.id,
        owner_agent_id=agent1.id,
        status=LeadStatus.won,
        estimated_value=Decimal("50000.00"),
        is_active=True,
    )
    # Agent 2 wins less
    lead2 = Lead(
        person_id=person.id,
        owner_agent_id=agent2.id,
        status=LeadStatus.won,
        estimated_value=Decimal("10000.00"),
        is_active=True,
    )
    db_session.add(lead1)
    db_session.add(lead2)
    db_session.commit()

    result = reports.agent_sales_performance(db_session, None, None, None)

    # Should be sorted by won_value descending
    if len(result) >= 2:
        assert result[0]["won_value"] >= result[1]["won_value"]
