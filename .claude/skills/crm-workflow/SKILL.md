---
name: crm-workflow
description: Build CRM workflow logic for lead/quote/project conversions, pipeline automation, and state machine transitions
arguments:
  - name: workflow_info
    description: "Workflow to build (e.g. 'lead won → auto-create project with tasks from template')"
---

# CRM Workflow

Build or extend CRM workflow logic in the DotMac Omni CRM sales pipeline.

## Steps

### 1. Understand the request
Parse `$ARGUMENTS` to determine:
- **Workflow type**: lead conversion, quote acceptance, stage transition, automation rule, bulk operation
- **Entities involved**: Lead, Quote, Pipeline, PipelineStage, Project, SalesOrder, Person
- **Trigger**: status change, stage move, manual action, automation rule
- **Side effects**: person party_status upgrade, project creation, notification, work order creation

### 2. Study the existing patterns
Read these reference files:

- **Sales service**: `app/services/crm/sales/service.py` — `Leads`, `Quotes`, `Pipelines`, `PipelineStages`, `CrmQuoteLineItems` manager classes
- **CRM enums**: `app/models/crm/enums.py` — `LeadStatus`, `QuoteStatus`, `CampaignStatus`, etc.
- **CRM models**: `app/models/crm/sales.py` — `Lead`, `Quote`, `Pipeline`, `PipelineStage`, `CrmQuoteLineItem`
- **CRM schemas**: `app/schemas/crm/sales.py` — Pydantic schemas for validation
- **Automation actions**: `app/services/automation_actions.py` — action executor, entity resolvers, whitelisted fields
- **Sales web routes**: `app/web/admin/crm/sales.py` or equivalent — thin route wrappers
- **Projects service**: `app/services/projects.py` — project creation patterns

### 3. Understand the state machines

**Lead lifecycle:**
```
new → contacted → qualified → proposal → negotiation → won / lost
```
- `won`: auto-upgrades `person.party_status` to `customer`
- `won`/`lost`: sets `closed_at` timestamp, resolves `owner_agent_id`
- Pipeline stage moves auto-update `probability` from stage defaults

**Quote lifecycle:**
```
draft → sent → accepted / rejected / expired
```
- `accepted`: upgrades person to `customer`, creates `SalesOrder`, may trigger project creation via `_ensure_project_from_quote()`
- Quote totals auto-recalculated when line items change via `_recalculate_quote_totals()`

**Lead ↔ Quote linkage:**
- Quote status changes propagate to lead via `_apply_lead_status_from_quote()`:
  - quote `sent` → lead `proposal`
  - quote `accepted` → lead `won`
  - quote `rejected` → lead `lost`

### 4. Build the workflow

**For status transition side effects**, add to the existing `update()` method:

```python
# In Leads.update() or Quotes.update()
@staticmethod
def update(db: Session, entity_id: str, payload):
    entity = db.get(Model, coerce_uuid(entity_id))
    if not entity:
        raise HTTPException(status_code=404, detail="Not found")

    previous_status = entity.status
    data = payload.model_dump(exclude_unset=True)

    if "status" in data:
        data["status"] = validate_enum(data["status"], StatusEnum, "status")

    for key, value in data.items():
        setattr(entity, key, value)

    # === Side effects on status change ===
    if data.get("status") and data["status"] != previous_status:
        _on_status_change(db, entity, previous_status, data["status"])

    db.commit()
    db.refresh(entity)
    return entity


def _on_status_change(db: Session, entity, old_status, new_status):
    """Handle side effects when entity status changes."""
    if new_status == StatusEnum.won:
        # Upgrade person party_status
        if entity.person and entity.person.party_status in (PartyStatus.lead, PartyStatus.contact):
            entity.person.party_status = PartyStatus.customer

        # Create downstream entities
        _create_project_from_lead(db, entity)

    if new_status in (StatusEnum.won, StatusEnum.lost):
        entity.closed_at = datetime.now(UTC)
        if not entity.owner_agent_id:
            entity.owner_agent_id = _resolve_owner_agent_id(db, entity.person_id)
```

**For automation rule actions**, add to `app/services/automation_actions.py`:

```python
# In _dispatch_action()
elif action_type == "convert_lead_to_project":
    _execute_convert_lead_to_project(db, params, event)

def _execute_convert_lead_to_project(db: Session, params: dict, event: Event) -> None:
    """Convert a won lead into a project with template tasks."""
    lead = _resolve_entity(db, "lead", event)
    if not lead:
        raise ValueError("Cannot resolve lead from event")

    template_id = params.get("project_template_id")
    project_type = params.get("project_type", "installation")

    from app.services.projects import projects as project_service
    project = project_service.create(db, ProjectCreate(
        name=f"Installation - {lead.title}",
        project_type=project_type,
        owner_person_id=lead.person_id,
        # Copy relevant fields from lead
    ))

    if template_id:
        project_service.apply_template(db, str(project.id), template_id)

    lead.metadata_ = {**(lead.metadata_ or {}), "project_id": str(project.id)}
    db.commit()
```

### 5. Pipeline/stage configuration

**Kanban operations** (already in `Leads` service):
- `kanban_view(db, pipeline_id)` — returns `{columns, records}` for drag-and-drop board
- `update_stage(db, lead_id, new_stage_id)` — moves lead, auto-updates probability
- `bulk_assign_pipeline(db, pipeline_id, stage_id, scope)` — mass-assign unassigned leads

**Add new pipeline stages** via the existing `PipelineStages` manager:
```python
pipeline_stages.create(db, PipelineStageCreate(
    pipeline_id=pipeline_id,
    name="Stage Name",
    order_index=3,
    default_probability=50,
))
```

### 6. Quote → Project conversion pattern

The existing `_ensure_project_from_quote()` pattern:
```python
def _ensure_project_from_quote(db: Session, quote: Quote, sales_order_id: str | None) -> None:
    """When a quote is accepted, create a project if none exists."""
    if not quote.lead_id:
        return
    lead = db.get(Lead, quote.lead_id)
    if not lead:
        return

    # Check if project already exists for this lead
    existing = db.query(Project).filter(
        Project.metadata_["lead_id"].astext == str(lead.id)
    ).first()
    if existing:
        return

    from app.services.projects import projects as project_service
    project = project_service.create(db, ProjectCreate(
        name=f"Project - {lead.title}",
        project_type="installation",
        owner_person_id=lead.person_id,
        metadata_={"lead_id": str(lead.id), "quote_id": str(quote.id),
                    "sales_order_id": sales_order_id},
    ))
```

### 7. Owner resolution pattern
```python
def _resolve_owner_agent_id(db: Session, person_id) -> uuid.UUID | None:
    """Find the CRM agent linked to this person."""
    if not person_id:
        return None
    agent = (
        db.query(CrmAgent)
        .filter(CrmAgent.person_id == person_id, CrmAgent.is_active.is_(True))
        .first()
    )
    return agent.id if agent else None
```

### 8. Automation rules integration
Automation rules in `app/services/automation_actions.py` use:
- **Entity resolvers**: `_ENTITY_RESOLVERS` dict to get entity from event context
- **Whitelisted fields**: `_ALLOWED_FIELDS` dict limits which attributes can be mutated
- **Action types**: `assign_conversation`, `set_field`, `add_tag`, `send_notification`, `create_work_order`, `emit_event`, `reject_creation`

To add a new action type:
1. Add handler function `_execute_{action_type}(db, params, event)`
2. Add routing in `_dispatch_action()`
3. Add to `_ALLOWED_FIELDS` if it modifies entity attributes

### 9. Write tests
Create `tests/test_crm_workflow.py`:

```python
def test_lead_won_upgrades_person_to_customer(db_session):
    """When a lead is won, the linked person becomes a customer."""
    # Create person with lead status
    # Create lead linked to person
    # Update lead status to won
    # Assert person.party_status == PartyStatus.customer

def test_quote_accepted_creates_sales_order(db_session):
    """Accepted quote auto-creates a sales order."""
    # Create quote with line items
    # Update quote status to accepted
    # Assert SalesOrder exists

def test_lead_stage_move_updates_probability(db_session):
    """Moving a lead to a new stage updates its probability."""
    # Create pipeline with stages
    # Create lead in stage 1
    # Move to stage 2 (probability 60%)
    # Assert lead.probability == 60
```

### 10. Verify
```bash
ruff check app/services/crm/sales/service.py --fix
ruff format app/services/crm/sales/service.py
python3 -c "from app.services.crm.sales.service import leads, quotes"
pytest tests/test_crm_workflow.py -v
```

### 11. Checklist
- [ ] Status transitions validate enum values via `validate_enum()`
- [ ] Person party_status only upgraded (lead→customer), never downgraded
- [ ] `closed_at` set on terminal statuses (won/lost)
- [ ] Quote totals recalculated when line items change
- [ ] No N+1 queries in kanban_view (batch load persons)
- [ ] Side effects wrapped in the same transaction (single `db.commit()`)
- [ ] Automation actions use `_ALLOWED_FIELDS` whitelist
- [ ] Automation depth check: `_MAX_AUTOMATION_DEPTH = 3`
