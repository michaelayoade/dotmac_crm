"""Tests for CRM sales service."""

import uuid
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.models.crm.enums import LeadStatus, QuoteStatus
from app.schemas.crm.sales import (
    LeadCreate,
    LeadUpdate,
    PipelineCreate,
    PipelineStageCreate,
    PipelineStageUpdate,
    PipelineUpdate,
    QuoteCreate,
    QuoteLineItemCreate,
    QuoteLineItemUpdate,
    QuoteUpdate,
)
from app.services.crm import sales as sales_service

# =============================================================================
# Pipelines CRUD Tests
# =============================================================================


def test_create_pipeline(db_session):
    """Test creating a pipeline."""
    pipeline = sales_service.Pipelines.create(
        db_session,
        PipelineCreate(name="Sales Pipeline"),
    )
    assert pipeline.name == "Sales Pipeline"
    assert pipeline.is_active is True


def test_create_pipeline_inactive(db_session):
    """Test creating an inactive pipeline."""
    pipeline = sales_service.Pipelines.create(
        db_session,
        PipelineCreate(name="Inactive Pipeline", is_active=False),
    )
    assert pipeline.name == "Inactive Pipeline"
    assert pipeline.is_active is False


def test_get_pipeline(db_session):
    """Test getting a pipeline by ID."""
    pipeline = sales_service.Pipelines.create(
        db_session,
        PipelineCreate(name="Get Test Pipeline"),
    )
    fetched = sales_service.Pipelines.get(db_session, str(pipeline.id))
    assert fetched.id == pipeline.id
    assert fetched.name == "Get Test Pipeline"


def test_get_pipeline_not_found(db_session):
    """Test getting non-existent pipeline raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        sales_service.Pipelines.get(db_session, str(uuid.uuid4()))
    assert exc_info.value.status_code == 404
    assert "Pipeline not found" in exc_info.value.detail


def test_list_pipelines(db_session):
    """Test listing pipelines."""
    sales_service.Pipelines.create(db_session, PipelineCreate(name="List Test 1"))
    sales_service.Pipelines.create(db_session, PipelineCreate(name="List Test 2"))

    pipelines = sales_service.Pipelines.list(
        db_session,
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert len(pipelines) >= 2


def test_list_pipelines_filter_inactive(db_session):
    """Test listing only inactive pipelines."""
    pipeline = sales_service.Pipelines.create(db_session, PipelineCreate(name="Inactive Pipeline", is_active=False))

    pipelines = sales_service.Pipelines.list(
        db_session,
        is_active=False,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert any(p.id == pipeline.id for p in pipelines)


def test_list_pipelines_order_by_name(db_session):
    """Test listing pipelines ordered by name."""
    pipelines = sales_service.Pipelines.list(
        db_session,
        is_active=None,
        order_by="name",
        order_dir="desc",
        limit=10,
        offset=0,
    )
    assert isinstance(pipelines, list)


def test_list_pipelines_invalid_order_by(db_session):
    """Test listing pipelines with invalid order_by raises 400."""
    with pytest.raises(HTTPException) as exc_info:
        sales_service.Pipelines.list(
            db_session,
            is_active=None,
            order_by="invalid_column",
            order_dir="asc",
            limit=10,
            offset=0,
        )
    assert exc_info.value.status_code == 400


def test_update_pipeline(db_session):
    """Test updating a pipeline."""
    pipeline = sales_service.Pipelines.create(db_session, PipelineCreate(name="Original Name"))
    updated = sales_service.Pipelines.update(
        db_session,
        str(pipeline.id),
        PipelineUpdate(name="Updated Name"),
    )
    assert updated.name == "Updated Name"


def test_update_pipeline_not_found(db_session):
    """Test updating non-existent pipeline raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        sales_service.Pipelines.update(db_session, str(uuid.uuid4()), PipelineUpdate(name="New"))
    assert exc_info.value.status_code == 404


def test_delete_pipeline(db_session):
    """Test deleting (soft delete) a pipeline."""
    pipeline = sales_service.Pipelines.create(db_session, PipelineCreate(name="To Delete"))
    sales_service.Pipelines.delete(db_session, str(pipeline.id))
    db_session.refresh(pipeline)
    assert pipeline.is_active is False


def test_delete_pipeline_not_found(db_session):
    """Test deleting non-existent pipeline raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        sales_service.Pipelines.delete(db_session, str(uuid.uuid4()))
    assert exc_info.value.status_code == 404


# =============================================================================
# Pipeline Stages CRUD Tests
# =============================================================================


def test_create_pipeline_stage(db_session):
    """Test creating a pipeline stage."""
    pipeline = sales_service.Pipelines.create(db_session, PipelineCreate(name="Stage Test Pipeline"))
    stage = sales_service.PipelineStages.create(
        db_session,
        PipelineStageCreate(
            pipeline_id=pipeline.id,
            name="Qualification",
            order_index=1,
        ),
    )
    assert stage.pipeline_id == pipeline.id
    assert stage.name == "Qualification"
    assert stage.order_index == 1
    assert stage.is_active is True


def test_create_pipeline_stage_pipeline_not_found(db_session):
    """Test creating stage with non-existent pipeline raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        sales_service.PipelineStages.create(
            db_session,
            PipelineStageCreate(pipeline_id=uuid.uuid4(), name="Test Stage"),
        )
    assert exc_info.value.status_code == 404
    assert "Pipeline not found" in exc_info.value.detail


def test_list_pipeline_stages(db_session):
    """Test listing pipeline stages."""
    pipeline = sales_service.Pipelines.create(db_session, PipelineCreate(name="Stages List Pipeline"))
    sales_service.PipelineStages.create(
        db_session,
        PipelineStageCreate(pipeline_id=pipeline.id, name="Stage 1", order_index=1),
    )
    sales_service.PipelineStages.create(
        db_session,
        PipelineStageCreate(pipeline_id=pipeline.id, name="Stage 2", order_index=2),
    )

    stages = sales_service.PipelineStages.list(
        db_session,
        pipeline_id=str(pipeline.id),
        is_active=None,
        order_by="order_index",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert len(stages) >= 2


def test_list_pipeline_stages_filter_inactive(db_session):
    """Test listing only inactive pipeline stages."""
    pipeline = sales_service.Pipelines.create(db_session, PipelineCreate(name="Inactive Stages Pipeline"))
    stage = sales_service.PipelineStages.create(
        db_session,
        PipelineStageCreate(pipeline_id=pipeline.id, name="Inactive Stage", is_active=False),
    )

    stages = sales_service.PipelineStages.list(
        db_session,
        pipeline_id=None,
        is_active=False,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert any(s.id == stage.id for s in stages)


def test_list_pipeline_stages_invalid_order_by(db_session):
    """Test listing stages with invalid order_by raises 400."""
    with pytest.raises(HTTPException) as exc_info:
        sales_service.PipelineStages.list(
            db_session,
            pipeline_id=None,
            is_active=None,
            order_by="invalid_column",
            order_dir="asc",
            limit=10,
            offset=0,
        )
    assert exc_info.value.status_code == 400


def test_update_pipeline_stage(db_session):
    """Test updating a pipeline stage."""
    pipeline = sales_service.Pipelines.create(db_session, PipelineCreate(name="Update Stage Pipeline"))
    stage = sales_service.PipelineStages.create(
        db_session,
        PipelineStageCreate(pipeline_id=pipeline.id, name="Original Stage"),
    )
    updated = sales_service.PipelineStages.update(
        db_session,
        str(stage.id),
        PipelineStageUpdate(name="Updated Stage", order_index=5),
    )
    assert updated.name == "Updated Stage"
    assert updated.order_index == 5


def test_update_pipeline_stage_not_found(db_session):
    """Test updating non-existent stage raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        sales_service.PipelineStages.update(db_session, str(uuid.uuid4()), PipelineStageUpdate(name="New"))
    assert exc_info.value.status_code == 404
    assert "Pipeline stage not found" in exc_info.value.detail


# =============================================================================
# Leads CRUD Tests
# =============================================================================


def test_create_lead(db_session, person):
    """Test creating a lead."""
    lead = sales_service.Leads.create(
        db_session,
        LeadCreate(title="New Business Opportunity", person_id=person.id),
    )
    assert lead.title == "New Business Opportunity"
    assert lead.status == LeadStatus.new
    assert lead.is_active is True
    assert lead.person_id == person.id


def test_create_lead_with_pipeline_and_stage(db_session, person):
    """Test creating a lead with pipeline and stage."""
    pipeline = sales_service.Pipelines.create(db_session, PipelineCreate(name="Lead Pipeline"))
    stage = sales_service.PipelineStages.create(
        db_session,
        PipelineStageCreate(pipeline_id=pipeline.id, name="Initial"),
    )
    lead = sales_service.Leads.create(
        db_session,
        LeadCreate(
            title="Pipeline Lead",
            person_id=person.id,
            pipeline_id=pipeline.id,
            stage_id=stage.id,
            estimated_value=Decimal("10000.00"),
            currency="USD",
        ),
    )
    assert lead.pipeline_id == pipeline.id
    assert lead.stage_id == stage.id
    assert lead.estimated_value == Decimal("10000.00")


def test_lead_create_sets_person(db_session, person):
    """Test creating a lead links the provided person."""
    lead = sales_service.Leads.create(
        db_session,
        LeadCreate(person_id=person.id),
    )

    assert lead.person_id == person.id


def test_create_lead_with_status(db_session, person):
    """Test creating a lead with specific status."""
    lead = sales_service.Leads.create(
        db_session,
        LeadCreate(title="Qualified Lead", person_id=person.id, status=LeadStatus.qualified),
    )
    assert lead.status == LeadStatus.qualified


def test_get_lead(db_session, person):
    """Test getting a lead by ID."""
    lead = sales_service.Leads.create(
        db_session,
        LeadCreate(title="Get Test Lead", person_id=person.id),
    )
    fetched = sales_service.Leads.get(db_session, str(lead.id))
    assert fetched.id == lead.id
    assert fetched.title == "Get Test Lead"


def test_get_lead_not_found(db_session):
    """Test getting non-existent lead raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        sales_service.Leads.get(db_session, str(uuid.uuid4()))
    assert exc_info.value.status_code == 404
    assert "Lead not found" in exc_info.value.detail


def test_list_leads(db_session, person):
    """Test listing leads."""
    sales_service.Leads.create(db_session, LeadCreate(title="List Lead 1", person_id=person.id))
    sales_service.Leads.create(db_session, LeadCreate(title="List Lead 2", person_id=person.id))

    leads = sales_service.Leads.list(
        db_session,
        pipeline_id=None,
        stage_id=None,
        owner_agent_id=None,
        status=None,
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert len(leads) >= 2


def test_list_leads_filter_by_pipeline(db_session, person):
    """Test listing leads filtered by pipeline."""
    pipeline = sales_service.Pipelines.create(db_session, PipelineCreate(name="Filter Pipeline"))
    lead = sales_service.Leads.create(
        db_session,
        LeadCreate(title="Pipeline Lead", person_id=person.id, pipeline_id=pipeline.id),
    )

    leads = sales_service.Leads.list(
        db_session,
        pipeline_id=str(pipeline.id),
        stage_id=None,
        owner_agent_id=None,
        status=None,
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert any(item.id == lead.id for item in leads)


def test_list_leads_filter_by_stage(db_session, person):
    """Test listing leads filtered by stage."""
    pipeline = sales_service.Pipelines.create(db_session, PipelineCreate(name="Stage Filter Pipeline"))
    stage = sales_service.PipelineStages.create(
        db_session,
        PipelineStageCreate(pipeline_id=pipeline.id, name="Filter Stage"),
    )
    lead = sales_service.Leads.create(
        db_session,
        LeadCreate(title="Stage Lead", person_id=person.id, stage_id=stage.id),
    )

    leads = sales_service.Leads.list(
        db_session,
        pipeline_id=None,
        stage_id=str(stage.id),
        owner_agent_id=None,
        status=None,
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert any(item.id == lead.id for item in leads)


def test_list_leads_filter_by_status(db_session, person):
    """Test listing leads filtered by status."""
    lead = sales_service.Leads.create(
        db_session,
        LeadCreate(title="Won Lead", person_id=person.id, status=LeadStatus.won),
    )

    leads = sales_service.Leads.list(
        db_session,
        pipeline_id=None,
        stage_id=None,
        owner_agent_id=None,
        status="won",
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert any(item.id == lead.id for item in leads)


def test_list_leads_filter_inactive(db_session, person):
    """Test listing only inactive leads."""
    lead = sales_service.Leads.create(
        db_session,
        LeadCreate(title="Inactive Lead", person_id=person.id, is_active=False),
    )

    leads = sales_service.Leads.list(
        db_session,
        pipeline_id=None,
        stage_id=None,
        owner_agent_id=None,
        status=None,
        is_active=False,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert any(item.id == lead.id for item in leads)


def test_list_leads_filter_by_owner_agent(db_session, person, crm_agent):
    """Test listing leads filtered by owner agent."""
    lead = sales_service.Leads.create(
        db_session,
        LeadCreate(title="Owned Lead", person_id=person.id, owner_agent_id=crm_agent.id),
    )

    leads = sales_service.Leads.list(
        db_session,
        pipeline_id=None,
        stage_id=None,
        owner_agent_id=str(crm_agent.id),
        status=None,
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert any(item.id == lead.id for item in leads)


def test_list_leads_invalid_status(db_session):
    """Test listing leads with invalid status raises 400."""
    with pytest.raises(HTTPException) as exc_info:
        sales_service.Leads.list(
            db_session,
            pipeline_id=None,
            stage_id=None,
            owner_agent_id=None,
            status="invalid_status",
            is_active=None,
            order_by="created_at",
            order_dir="asc",
            limit=10,
            offset=0,
        )
    assert exc_info.value.status_code == 400


def test_list_leads_invalid_order_by(db_session):
    """Test listing leads with invalid order_by raises 400."""
    with pytest.raises(HTTPException) as exc_info:
        sales_service.Leads.list(
            db_session,
            pipeline_id=None,
            stage_id=None,
            owner_agent_id=None,
            status=None,
            is_active=None,
            order_by="invalid_column",
            order_dir="asc",
            limit=10,
            offset=0,
        )
    assert exc_info.value.status_code == 400


def test_update_lead(db_session, person):
    """Test updating a lead."""
    lead = sales_service.Leads.create(
        db_session,
        LeadCreate(title="Original Title", person_id=person.id),
    )
    updated = sales_service.Leads.update(
        db_session,
        str(lead.id),
        LeadUpdate(title="Updated Title", status=LeadStatus.qualified),
    )
    assert updated.title == "Updated Title"
    assert updated.status == LeadStatus.qualified


def test_lead_update_changes_person(db_session, person):
    """Test updating a lead can change the linked person."""
    from app.models.person import Person

    new_person = Person(
        first_name="Lead",
        last_name="Update",
        email=f"lead-update-{uuid.uuid4().hex}@example.com",
    )
    db_session.add(new_person)
    db_session.commit()
    db_session.refresh(new_person)

    lead = sales_service.Leads.create(
        db_session,
        LeadCreate(title="Needs link", person_id=person.id),
    )

    updated = sales_service.Leads.update(
        db_session,
        str(lead.id),
        LeadUpdate(person_id=new_person.id),
    )

    assert updated.person_id == new_person.id


def test_update_lead_not_found(db_session):
    """Test updating non-existent lead raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        sales_service.Leads.update(db_session, str(uuid.uuid4()), LeadUpdate(title="New"))
    assert exc_info.value.status_code == 404


def test_delete_lead(db_session, person):
    """Test deleting (soft delete) a lead."""
    lead = sales_service.Leads.create(
        db_session,
        LeadCreate(title="To Delete Lead", person_id=person.id),
    )
    sales_service.Leads.delete(db_session, str(lead.id))
    db_session.refresh(lead)
    assert lead.is_active is False


def test_delete_lead_not_found(db_session):
    """Test deleting non-existent lead raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        sales_service.Leads.delete(db_session, str(uuid.uuid4()))
    assert exc_info.value.status_code == 404


# =============================================================================
# Quotes CRUD Tests
# =============================================================================


def test_create_quote(db_session, person):
    """Test creating a quote."""
    quote = sales_service.Quotes.create(
        db_session,
        QuoteCreate(person_id=person.id, notes="Initial quote"),
    )
    assert quote.notes == "Initial quote"
    assert quote.status == QuoteStatus.draft
    assert quote.is_active is True


def test_create_quote_with_lead(db_session, person):
    """Test creating a quote linked to a lead."""
    lead = sales_service.Leads.create(
        db_session,
        LeadCreate(title="Quote Lead", person_id=person.id),
    )
    quote = sales_service.Quotes.create(
        db_session,
        QuoteCreate(person_id=person.id, lead_id=lead.id),
    )
    assert quote.lead_id == lead.id


def test_create_quote_sets_person(db_session, person):
    """Test creating a quote links the provided person."""
    quote = sales_service.Quotes.create(
        db_session,
        QuoteCreate(person_id=person.id),
    )

    assert quote.person_id == person.id


def test_create_quote_with_status(db_session, person):
    """Test creating a quote with specific status."""
    quote = sales_service.Quotes.create(
        db_session,
        QuoteCreate(person_id=person.id, status=QuoteStatus.sent),
    )
    assert quote.status == QuoteStatus.sent


def test_get_quote(db_session, person):
    """Test getting a quote by ID."""
    quote = sales_service.Quotes.create(
        db_session,
        QuoteCreate(person_id=person.id, notes="Get Test Quote"),
    )
    fetched = sales_service.Quotes.get(db_session, str(quote.id))
    assert fetched.id == quote.id
    assert fetched.notes == "Get Test Quote"


def test_get_quote_not_found(db_session):
    """Test getting non-existent quote raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        sales_service.Quotes.get(db_session, str(uuid.uuid4()))
    assert exc_info.value.status_code == 404
    assert "Quote not found" in exc_info.value.detail


def test_list_quotes(db_session, person):
    """Test listing quotes."""
    sales_service.Quotes.create(db_session, QuoteCreate(person_id=person.id, notes="List Quote 1"))
    sales_service.Quotes.create(db_session, QuoteCreate(person_id=person.id, notes="List Quote 2"))

    quotes = sales_service.Quotes.list(
        db_session,
        lead_id=None,
        status=None,
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert len(quotes) >= 2


def test_list_quotes_filter_by_lead(db_session, person):
    """Test listing quotes filtered by lead."""
    lead = sales_service.Leads.create(
        db_session,
        LeadCreate(title="Filter Lead", person_id=person.id),
    )
    quote = sales_service.Quotes.create(
        db_session,
        QuoteCreate(person_id=person.id, lead_id=lead.id),
    )

    quotes = sales_service.Quotes.list(
        db_session,
        lead_id=str(lead.id),
        status=None,
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert any(q.id == quote.id for q in quotes)


def test_list_quotes_filter_by_status(db_session, person):
    """Test listing quotes filtered by status."""
    quote = sales_service.Quotes.create(
        db_session,
        QuoteCreate(person_id=person.id, status=QuoteStatus.accepted),
    )

    quotes = sales_service.Quotes.list(
        db_session,
        lead_id=None,
        status="accepted",
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert any(q.id == quote.id for q in quotes)


def test_list_quotes_filter_inactive(db_session, person):
    """Test listing only inactive quotes."""
    quote = sales_service.Quotes.create(
        db_session,
        QuoteCreate(person_id=person.id, is_active=False),
    )

    quotes = sales_service.Quotes.list(
        db_session,
        lead_id=None,
        status=None,
        is_active=False,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert any(q.id == quote.id for q in quotes)


def test_list_quotes_invalid_status(db_session):
    """Test listing quotes with invalid status raises 400."""
    with pytest.raises(HTTPException) as exc_info:
        sales_service.Quotes.list(
            db_session,
            lead_id=None,
            status="invalid_status",
            is_active=None,
            order_by="created_at",
            order_dir="asc",
            limit=10,
            offset=0,
        )
    assert exc_info.value.status_code == 400


def test_list_quotes_invalid_order_by(db_session):
    """Test listing quotes with invalid order_by raises 400."""
    with pytest.raises(HTTPException) as exc_info:
        sales_service.Quotes.list(
            db_session,
            lead_id=None,
            status=None,
            is_active=None,
            order_by="invalid_column",
            order_dir="asc",
            limit=10,
            offset=0,
        )
    assert exc_info.value.status_code == 400


def test_update_quote(db_session, person):
    """Test updating a quote."""
    quote = sales_service.Quotes.create(
        db_session,
        QuoteCreate(person_id=person.id, notes="Original Notes"),
    )
    updated = sales_service.Quotes.update(
        db_session,
        str(quote.id),
        QuoteUpdate(notes="Updated Notes", status=QuoteStatus.sent),
    )
    assert updated.notes == "Updated Notes"
    assert updated.status == QuoteStatus.sent


def test_update_quote_not_found(db_session):
    """Test updating non-existent quote raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        sales_service.Quotes.update(db_session, str(uuid.uuid4()), QuoteUpdate(notes="New"))
    assert exc_info.value.status_code == 404


def test_delete_quote(db_session, person):
    """Test deleting (soft delete) a quote."""
    quote = sales_service.Quotes.create(
        db_session,
        QuoteCreate(person_id=person.id, notes="To Delete Quote"),
    )
    sales_service.Quotes.delete(db_session, str(quote.id))
    db_session.refresh(quote)
    assert quote.is_active is False


def test_delete_quote_not_found(db_session):
    """Test deleting non-existent quote raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        sales_service.Quotes.delete(db_session, str(uuid.uuid4()))
    assert exc_info.value.status_code == 404


# =============================================================================
# Quote Line Items CRUD Tests
# =============================================================================


def test_create_quote_line_item(db_session, person):
    """Test creating a quote line item."""
    quote = sales_service.Quotes.create(
        db_session,
        QuoteCreate(person_id=person.id, notes="Line Items Quote"),
    )
    item = sales_service.CrmQuoteLineItems.create(
        db_session,
        QuoteLineItemCreate(
            quote_id=quote.id,
            description="Consulting Services",
            quantity=Decimal("10.000"),
            unit_price=Decimal("150.00"),
        ),
    )
    assert item.quote_id == quote.id
    assert item.description == "Consulting Services"
    assert item.quantity == Decimal("10.000")
    assert item.unit_price == Decimal("150.00")
    # Amount calculated automatically
    assert item.amount == Decimal("1500.00")


def test_create_quote_line_item_quote_not_found(db_session):
    """Test creating line item with non-existent quote raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        sales_service.CrmQuoteLineItems.create(
            db_session,
            QuoteLineItemCreate(
                quote_id=uuid.uuid4(),
                description="Test Item",
            ),
        )
    assert exc_info.value.status_code == 404
    assert "Quote not found" in exc_info.value.detail


def test_create_quote_line_item_recalculates_quote_totals(db_session, person):
    """Test creating line item recalculates quote totals."""
    quote = sales_service.Quotes.create(
        db_session,
        QuoteCreate(person_id=person.id, notes="Totals Quote"),
    )
    sales_service.CrmQuoteLineItems.create(
        db_session,
        QuoteLineItemCreate(
            quote_id=quote.id,
            description="Item 1",
            quantity=Decimal("1.000"),
            unit_price=Decimal("100.00"),
        ),
    )
    sales_service.CrmQuoteLineItems.create(
        db_session,
        QuoteLineItemCreate(
            quote_id=quote.id,
            description="Item 2",
            quantity=Decimal("2.000"),
            unit_price=Decimal("50.00"),
        ),
    )

    db_session.refresh(quote)
    assert quote.subtotal == Decimal("200.00")
    assert quote.total == Decimal("200.00")


def test_list_quote_line_items(db_session, person):
    """Test listing quote line items."""
    quote = sales_service.Quotes.create(
        db_session,
        QuoteCreate(person_id=person.id, notes="List Items Quote"),
    )
    sales_service.CrmQuoteLineItems.create(
        db_session,
        QuoteLineItemCreate(
            quote_id=quote.id,
            description="List Item 1",
        ),
    )
    sales_service.CrmQuoteLineItems.create(
        db_session,
        QuoteLineItemCreate(
            quote_id=quote.id,
            description="List Item 2",
        ),
    )

    items = sales_service.CrmQuoteLineItems.list(
        db_session,
        quote_id=str(quote.id),
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert len(items) >= 2


def test_list_quote_line_items_no_filter(db_session):
    """Test listing all quote line items without filter."""
    items = sales_service.CrmQuoteLineItems.list(
        db_session,
        quote_id=None,
        order_by="created_at",
        order_dir="desc",
        limit=10,
        offset=0,
    )
    assert isinstance(items, list)


def test_list_quote_line_items_invalid_order_by(db_session):
    """Test listing line items with invalid order_by raises 400."""
    with pytest.raises(HTTPException) as exc_info:
        sales_service.CrmQuoteLineItems.list(
            db_session,
            quote_id=None,
            order_by="invalid_column",
            order_dir="asc",
            limit=10,
            offset=0,
        )
    assert exc_info.value.status_code == 400


def test_update_quote_line_item(db_session, person):
    """Test updating a quote line item."""
    quote = sales_service.Quotes.create(
        db_session,
        QuoteCreate(person_id=person.id, notes="Update Items Quote"),
    )
    item = sales_service.CrmQuoteLineItems.create(
        db_session,
        QuoteLineItemCreate(
            quote_id=quote.id,
            description="Original Description",
            quantity=Decimal("1.000"),
            unit_price=Decimal("100.00"),
        ),
    )
    updated = sales_service.CrmQuoteLineItems.update(
        db_session,
        str(item.id),
        QuoteLineItemUpdate(description="Updated Description", quantity=Decimal("5.000")),
    )
    assert updated.description == "Updated Description"
    assert updated.quantity == Decimal("5.000")
    # Amount recalculated
    assert updated.amount == Decimal("500.00")


def test_update_quote_line_item_recalculates_quote_totals(db_session, person):
    """Test updating line item recalculates quote totals."""
    quote = sales_service.Quotes.create(
        db_session,
        QuoteCreate(person_id=person.id, notes="Update Totals Quote"),
    )
    item = sales_service.CrmQuoteLineItems.create(
        db_session,
        QuoteLineItemCreate(
            quote_id=quote.id,
            description="Recalc Item",
            quantity=Decimal("1.000"),
            unit_price=Decimal("100.00"),
        ),
    )

    db_session.refresh(quote)
    assert quote.subtotal == Decimal("100.00")

    sales_service.CrmQuoteLineItems.update(
        db_session,
        str(item.id),
        QuoteLineItemUpdate(unit_price=Decimal("200.00")),
    )

    db_session.refresh(quote)
    assert quote.subtotal == Decimal("200.00")


def test_update_quote_line_item_not_found(db_session):
    """Test updating non-existent line item raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        sales_service.CrmQuoteLineItems.update(db_session, str(uuid.uuid4()), QuoteLineItemUpdate(description="New"))
    assert exc_info.value.status_code == 404
    assert "Quote line item not found" in exc_info.value.detail


# =============================================================================
# Lead Sales Fields Tests
# =============================================================================


def test_create_lead_with_probability(db_session, person):
    """Test creating a lead with probability."""
    from datetime import date

    lead = sales_service.Leads.create(
        db_session,
        LeadCreate(
            title="Probability Test Lead",
            person_id=person.id,
            estimated_value=Decimal("10000.00"),
            probability=75,
            expected_close_date=date(2026, 3, 15),
        ),
    )
    assert lead.probability == 75
    assert lead.expected_close_date == date(2026, 3, 15)
    assert lead.weighted_value == Decimal("7500.00")


def test_lead_weighted_value_none_when_no_probability(db_session, person):
    """Test weighted_value is None when probability is not set."""
    lead = sales_service.Leads.create(
        db_session,
        LeadCreate(
            title="No Probability Lead",
            person_id=person.id,
            estimated_value=Decimal("10000.00"),
        ),
    )
    assert lead.probability is None
    assert lead.weighted_value is None


def test_update_lead_with_sales_fields(db_session, person):
    """Test updating lead with sales fields."""
    from datetime import date

    lead = sales_service.Leads.create(
        db_session,
        LeadCreate(title="Update Sales Fields Lead", person_id=person.id),
    )

    updated = sales_service.Leads.update(
        db_session,
        str(lead.id),
        LeadUpdate(
            estimated_value=Decimal("50000.00"),
            probability=60,
            expected_close_date=date(2026, 6, 1),
            status=LeadStatus.lost,
            lost_reason="Competitor chosen",
        ),
    )

    assert updated.estimated_value == Decimal("50000.00")
    assert updated.probability == 60
    assert updated.expected_close_date == date(2026, 6, 1)
    assert updated.status == LeadStatus.lost
    assert updated.lost_reason == "Competitor chosen"
    assert updated.weighted_value == Decimal("30000.00")


# =============================================================================
# Pipeline Stage Default Probability Tests
# =============================================================================


def test_create_pipeline_stage_with_default_probability(db_session):
    """Test creating a pipeline stage with default probability."""
    pipeline = sales_service.Pipelines.create(db_session, PipelineCreate(name="Probability Stage Pipeline"))
    stage = sales_service.PipelineStages.create(
        db_session,
        PipelineStageCreate(
            pipeline_id=pipeline.id,
            name="Negotiation",
            order_index=3,
            default_probability=80,
        ),
    )
    assert stage.default_probability == 80


def test_create_pipeline_stage_default_probability_default(db_session):
    """Test pipeline stage default probability defaults to 50."""
    pipeline = sales_service.Pipelines.create(db_session, PipelineCreate(name="Default Prob Pipeline"))
    stage = sales_service.PipelineStages.create(
        db_session,
        PipelineStageCreate(
            pipeline_id=pipeline.id,
            name="Initial",
        ),
    )
    assert stage.default_probability == 50


# =============================================================================
# Kanban View Tests
# =============================================================================


def test_kanban_view_empty(db_session):
    """Test kanban view with no data."""
    result = sales_service.Leads.kanban_view(db_session, pipeline_id=None)
    assert "columns" in result
    assert "records" in result
    assert isinstance(result["columns"], list)
    assert isinstance(result["records"], list)


def test_kanban_view_with_pipeline(db_session, person):
    """Test kanban view filtered by pipeline."""
    pipeline = sales_service.Pipelines.create(db_session, PipelineCreate(name="Kanban Test Pipeline"))
    stage1 = sales_service.PipelineStages.create(
        db_session,
        PipelineStageCreate(
            pipeline_id=pipeline.id,
            name="Stage 1",
            order_index=1,
            default_probability=20,
        ),
    )
    sales_service.PipelineStages.create(
        db_session,
        PipelineStageCreate(
            pipeline_id=pipeline.id,
            name="Stage 2",
            order_index=2,
            default_probability=50,
        ),
    )

    lead = sales_service.Leads.create(
        db_session,
        LeadCreate(
            title="Kanban Lead",
            person_id=person.id,
            pipeline_id=pipeline.id,
            stage_id=stage1.id,
            estimated_value=Decimal("5000.00"),
            probability=25,
        ),
    )

    result = sales_service.Leads.kanban_view(db_session, pipeline_id=str(pipeline.id))

    assert len(result["columns"]) == 2
    assert result["columns"][0]["id"] == str(stage1.id)
    assert result["columns"][0]["default_probability"] == 20

    assert len(result["records"]) >= 1
    lead_record = next((r for r in result["records"] if r["id"] == str(lead.id)), None)
    assert lead_record is not None
    assert lead_record["stage"] == str(stage1.id)
    assert lead_record["estimated_value"] == 5000.0
    assert lead_record["probability"] == 25
    assert lead_record["url"] == f"/admin/crm/leads/{lead.id}"


def test_update_stage_moves_lead(db_session, person):
    """Test update_stage moves lead to new stage."""
    pipeline = sales_service.Pipelines.create(db_session, PipelineCreate(name="Stage Move Pipeline"))
    stage1 = sales_service.PipelineStages.create(
        db_session,
        PipelineStageCreate(
            pipeline_id=pipeline.id,
            name="First",
            order_index=1,
            default_probability=10,
        ),
    )
    stage2 = sales_service.PipelineStages.create(
        db_session,
        PipelineStageCreate(
            pipeline_id=pipeline.id,
            name="Second",
            order_index=2,
            default_probability=60,
        ),
    )

    lead = sales_service.Leads.create(
        db_session,
        LeadCreate(
            title="Move Stage Lead",
            person_id=person.id,
            pipeline_id=pipeline.id,
            stage_id=stage1.id,
        ),
    )

    result = sales_service.Leads.update_stage(db_session, str(lead.id), str(stage2.id))

    assert result["stage_id"] == str(stage2.id)
    assert result["pipeline_id"] == str(pipeline.id)

    # Probability should be set from stage default since lead didn't have one
    db_session.refresh(lead)
    assert lead.stage_id == stage2.id
    assert lead.probability == 60


def test_update_stage_preserves_existing_probability(db_session, person):
    """Test update_stage preserves existing probability."""
    pipeline = sales_service.Pipelines.create(db_session, PipelineCreate(name="Preserve Prob Pipeline"))
    stage1 = sales_service.PipelineStages.create(
        db_session,
        PipelineStageCreate(
            pipeline_id=pipeline.id,
            name="First",
            default_probability=10,
        ),
    )
    stage2 = sales_service.PipelineStages.create(
        db_session,
        PipelineStageCreate(
            pipeline_id=pipeline.id,
            name="Second",
            default_probability=60,
        ),
    )

    lead = sales_service.Leads.create(
        db_session,
        LeadCreate(
            title="Keep Prob Lead",
            person_id=person.id,
            pipeline_id=pipeline.id,
            stage_id=stage1.id,
            probability=45,  # Custom probability
        ),
    )

    sales_service.Leads.update_stage(db_session, str(lead.id), str(stage2.id))

    db_session.refresh(lead)
    # Probability should remain 45, not be overwritten by stage default
    assert lead.probability == 45


def test_update_stage_not_found(db_session):
    """Test update_stage with non-existent lead raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        sales_service.Leads.update_stage(db_session, str(uuid.uuid4()), str(uuid.uuid4()))
    assert exc_info.value.status_code == 404
