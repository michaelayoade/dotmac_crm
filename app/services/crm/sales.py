from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import cast, func, nullslast, or_, String
from sqlalchemy.orm import Session, selectinload

from app.models.crm.sales import Lead, Pipeline, PipelineStage, Quote, CrmQuoteLineItem
from app.models.inventory import InventoryItem
from app.models.crm.conversation import Conversation, ConversationAssignment
from app.models.crm.enums import LeadStatus, QuoteStatus
from app.models.domain_settings import SettingDomain
from app.models.person import PartyStatus, Person
from app.services import settings_spec
from app.services.common import apply_ordering, apply_pagination, coerce_uuid, validate_enum
from app.services.response import ListResponseMixin


def _resolve_owner_agent_id_for_person(db: Session, person_id):
    assignment = (
        db.query(ConversationAssignment)
        .join(Conversation, ConversationAssignment.conversation_id == Conversation.id)
        .filter(Conversation.person_id == person_id)
        .filter(ConversationAssignment.is_active.is_(True))
        .order_by(
            nullslast(ConversationAssignment.assigned_at.desc()),
            ConversationAssignment.created_at.desc(),
        )
        .first()
    )
    if assignment and assignment.agent_id:
        return assignment.agent_id
    return None


def _apply_lead_status_from_quote(db: Session, quote: Quote, status: QuoteStatus | None):
    if not quote or not status or not quote.lead_id:
        return
    lead = db.get(Lead, quote.lead_id)
    if not lead:
        return
    if status == QuoteStatus.accepted:
        lead.status = LeadStatus.won
    elif status == QuoteStatus.rejected:
        lead.status = LeadStatus.lost
    else:
        return
    db.commit()


def _recalculate_quote_totals(db: Session, quote: Quote) -> None:
    items = (
        db.query(CrmQuoteLineItem)
        .filter(CrmQuoteLineItem.quote_id == quote.id)
        .all()
    )
    subtotal = Decimal("0.00")
    for item in items:
        subtotal += Decimal(item.amount or 0)
    quote.subtotal = subtotal
    quote.total = subtotal + Decimal(quote.tax_total or 0)
    db.commit()


class Pipelines(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload):
        pipeline = Pipeline(**payload.model_dump())
        db.add(pipeline)
        db.commit()
        db.refresh(pipeline)
        return pipeline

    @staticmethod
    def get(db: Session, pipeline_id: str):
        pipeline = db.get(Pipeline, coerce_uuid(pipeline_id))
        if not pipeline:
            raise HTTPException(status_code=404, detail="Pipeline not found")
        return pipeline

    @staticmethod
    def list(
        db: Session,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(Pipeline)
        if is_active is None:
            query = query.filter(Pipeline.is_active.is_(True))
        else:
            query = query.filter(Pipeline.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": Pipeline.created_at, "name": Pipeline.name},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, pipeline_id: str, payload):
        pipeline = db.get(Pipeline, coerce_uuid(pipeline_id))
        if not pipeline:
            raise HTTPException(status_code=404, detail="Pipeline not found")
        data = payload.model_dump(exclude_unset=True)
        for key, value in data.items():
            setattr(pipeline, key, value)
        db.commit()
        db.refresh(pipeline)
        return pipeline

    @staticmethod
    def delete(db: Session, pipeline_id: str):
        pipeline = db.get(Pipeline, coerce_uuid(pipeline_id))
        if not pipeline:
            raise HTTPException(status_code=404, detail="Pipeline not found")
        pipeline.is_active = False
        db.commit()


class PipelineStages(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload):
        pipeline = db.get(Pipeline, payload.pipeline_id)
        if not pipeline:
            raise HTTPException(status_code=404, detail="Pipeline not found")
        stage = PipelineStage(**payload.model_dump())
        db.add(stage)
        db.commit()
        db.refresh(stage)
        return stage

    @staticmethod
    def list(
        db: Session,
        pipeline_id: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(PipelineStage)
        if pipeline_id:
            query = query.filter(PipelineStage.pipeline_id == coerce_uuid(pipeline_id))
        if is_active is None:
            query = query.filter(PipelineStage.is_active.is_(True))
        else:
            query = query.filter(PipelineStage.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"order_index": PipelineStage.order_index, "created_at": PipelineStage.created_at},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, stage_id: str, payload):
        stage = db.get(PipelineStage, coerce_uuid(stage_id))
        if not stage:
            raise HTTPException(status_code=404, detail="Pipeline stage not found")
        data = payload.model_dump(exclude_unset=True)
        for key, value in data.items():
            setattr(stage, key, value)
        db.commit()
        db.refresh(stage)
        return stage


class Leads(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload):
        data = payload.model_dump()
        if data.get("status"):
            data["status"] = validate_enum(data["status"], LeadStatus, "status")

        # Validate person_id is provided
        person_id = data.get("person_id")
        if not person_id:
            raise HTTPException(status_code=400, detail="person_id is required")

        person = db.get(Person, person_id)
        if not person:
            raise HTTPException(status_code=404, detail="Person not found")

        # Auto-upgrade person to at least 'contact' status if they're a lead
        if person.party_status == PartyStatus.lead:
            person.party_status = PartyStatus.contact

        if not data.get("owner_agent_id"):
            data["owner_agent_id"] = _resolve_owner_agent_id_for_person(db, person_id)
        if not data.get("currency"):
            default_currency = settings_spec.resolve_value(
                db, SettingDomain.billing, "default_currency"
            )
            if default_currency:
                data["currency"] = default_currency
        lead = Lead(**data)
        db.add(lead)
        db.commit()
        db.refresh(lead)
        return lead

    @staticmethod
    def get(db: Session, lead_id: str):
        lead = db.get(Lead, coerce_uuid(lead_id))
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found")
        return lead

    @staticmethod
    def list(
        db: Session,
        pipeline_id: str | None,
        stage_id: str | None,
        owner_agent_id: str | None,
        status: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(Lead)
        if pipeline_id:
            query = query.filter(Lead.pipeline_id == coerce_uuid(pipeline_id))
        if stage_id:
            query = query.filter(Lead.stage_id == coerce_uuid(stage_id))
        if owner_agent_id:
            query = query.filter(Lead.owner_agent_id == coerce_uuid(owner_agent_id))
        if status:
            status_value = validate_enum(status, LeadStatus, "status")
            query = query.filter(Lead.status == status_value)
        if is_active is None:
            query = query.filter(Lead.is_active.is_(True))
        else:
            query = query.filter(Lead.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": Lead.created_at, "updated_at": Lead.updated_at},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, lead_id: str, payload):
        lead = db.get(Lead, coerce_uuid(lead_id))
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found")
        data = payload.model_dump(exclude_unset=True)
        if "status" in data:
            data["status"] = validate_enum(data["status"], LeadStatus, "status")

        # If person_id is being changed, validate it exists
        if data.get("person_id"):
            person = db.get(Person, data["person_id"])
            if not person:
                raise HTTPException(status_code=404, detail="Person not found")

        for key, value in data.items():
            setattr(lead, key, value)

        # When lead is won, upgrade person to customer
        if data.get("status") == LeadStatus.won and lead.person:
            if lead.person.party_status in (PartyStatus.lead, PartyStatus.contact):
                lead.person.party_status = PartyStatus.customer

        db.commit()
        db.refresh(lead)
        return lead

    @staticmethod
    def delete(db: Session, lead_id: str):
        lead = db.get(Lead, coerce_uuid(lead_id))
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found")
        lead.is_active = False
        db.commit()

    @staticmethod
    def kanban_view(db: Session, pipeline_id: str | None = None) -> dict:
        """Return kanban board data with columns and records.

        Returns:
            dict with 'columns' (list of stage info) and 'records' (list of leads).
        """
        if pipeline_id:
            stages = (
                db.query(PipelineStage)
                .filter(PipelineStage.pipeline_id == coerce_uuid(pipeline_id))
                .filter(PipelineStage.is_active.is_(True))
                .order_by(PipelineStage.order_index.asc())
                .all()
            )
            leads = (
                db.query(Lead)
                .filter(Lead.pipeline_id == coerce_uuid(pipeline_id))
                .filter(Lead.is_active.is_(True))
                .all()
            )
        else:
            # Get all active stages grouped by pipeline
            stages = (
                db.query(PipelineStage)
                .filter(PipelineStage.is_active.is_(True))
                .order_by(PipelineStage.order_index.asc())
                .all()
            )
            leads = db.query(Lead).filter(Lead.is_active.is_(True)).all()

        columns = []
        for stage in stages:
            columns.append({
                "id": str(stage.id),
                "title": stage.name,
                "order_index": stage.order_index,
                "default_probability": stage.default_probability,
            })

        # Batch load all persons to avoid N+1 queries
        person_ids = [lead.person_id for lead in leads if lead.person_id]
        persons = db.query(Person).filter(Person.id.in_(person_ids)).all() if person_ids else []
        person_map = {p.id: p for p in persons}

        records = []
        for lead in leads:
            person = person_map.get(lead.person_id) if lead.person_id else None
            contact_name = ""
            if person:
                contact_name = person.display_name or f"{person.first_name or ''} {person.last_name or ''}".strip()

            records.append({
                "id": str(lead.id),
                "stage": str(lead.stage_id) if lead.stage_id else None,
                "title": lead.title or f"Lead #{str(lead.id)[:8]}",
                "contact_name": contact_name,
                "estimated_value": float(lead.estimated_value) if lead.estimated_value else None,
                "probability": lead.probability,
                "weighted_value": float(lead.weighted_value) if lead.weighted_value else None,
                "status": lead.status.value if lead.status else "new",
                "currency": lead.currency or "",
                "url": f"/admin/crm/leads/{lead.id}",
            })

        return {"columns": columns, "records": records}

    @staticmethod
    def update_stage(db: Session, lead_id: str, new_stage_id: str) -> dict:
        """Move lead to a new stage, auto-updating probability from stage default.

        Returns:
            dict with updated lead info.
        """
        lead = db.get(Lead, coerce_uuid(lead_id))
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found")

        stage = db.get(PipelineStage, coerce_uuid(new_stage_id))
        if not stage:
            raise HTTPException(status_code=404, detail="Stage not found")

        lead.stage_id = stage.id
        lead.pipeline_id = stage.pipeline_id

        # Auto-update probability from stage default if lead doesn't have one set
        if lead.probability is None:
            lead.probability = stage.default_probability

        db.commit()
        db.refresh(lead)

        return {
            "id": str(lead.id),
            "stage_id": str(lead.stage_id),
            "pipeline_id": str(lead.pipeline_id) if lead.pipeline_id else None,
            "probability": lead.probability,
        }


class Quotes(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload):
        data = payload.model_dump()
        if data.get("status"):
            data["status"] = validate_enum(data["status"], QuoteStatus, "status")

        # Validate person_id is provided
        person_id = data.get("person_id")
        if not person_id:
            raise HTTPException(status_code=400, detail="person_id is required")

        person = db.get(Person, person_id)
        if not person:
            raise HTTPException(status_code=404, detail="Person not found")

        # Set quote_name from person display name
        if not data.get("metadata_"):
            data["metadata_"] = {}
        if isinstance(data["metadata_"], dict):
            display_name = person.display_name or f"{person.first_name} {person.last_name}"
            data["metadata_"]["quote_name"] = display_name

        if not data.get("currency"):
            default_currency = settings_spec.resolve_value(
                db, SettingDomain.billing, "default_currency"
            )
            if default_currency:
                data["currency"] = default_currency
        quote = Quote(**data)
        db.add(quote)
        db.commit()
        db.refresh(quote)
        _apply_lead_status_from_quote(db, quote, quote.status)
        if quote.status == QuoteStatus.accepted:
            from app.services import sales_orders as sales_order_service

            sales_order_service.sales_orders.create_from_quote(db, str(quote.id))
        return quote

    @staticmethod
    def get(db: Session, quote_id: str):
        quote = db.get(
            Quote,
            coerce_uuid(quote_id),
            options=[selectinload(Quote.line_items)],
        )
        if not quote:
            raise HTTPException(status_code=404, detail="Quote not found")
        return quote

    @staticmethod
    def list(
        db: Session,
        lead_id: str | None,
        status: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
        search: str | None = None,
    ):
        query = db.query(Quote)
        if lead_id:
            query = query.filter(Quote.lead_id == coerce_uuid(lead_id))
        if status:
            status_value = validate_enum(status, QuoteStatus, "status")
            query = query.filter(Quote.status == status_value)
        if search:
            like = f"%{search.strip()}%"
            query = (
                query.outerjoin(Person, Quote.person_id == Person.id)
                .filter(
                    or_(
                        Person.display_name.ilike(like),
                        Person.first_name.ilike(like),
                        Person.last_name.ilike(like),
                        Person.email.ilike(like),
                        cast(Quote.id, String).ilike(like),
                    )
                )
                .distinct()
            )
        if is_active is None:
            query = query.filter(Quote.is_active.is_(True))
        else:
            query = query.filter(Quote.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": Quote.created_at, "updated_at": Quote.updated_at},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def count_by_status(db: Session) -> dict:
        """Return counts by quote status."""
        results = (
            db.query(Quote.status, func.count(Quote.id))
            .filter(Quote.is_active.is_(True))
            .group_by(Quote.status)
            .all()
        )
        counts = {s.value: 0 for s in QuoteStatus}
        for status_val, count in results:
            if status_val:
                counts[status_val.value] = count
        counts["total"] = sum(counts.values())
        return counts

    @staticmethod
    def update(db: Session, quote_id: str, payload):
        quote = db.get(Quote, coerce_uuid(quote_id))
        if not quote:
            raise HTTPException(status_code=404, detail="Quote not found")
        data = payload.model_dump(exclude_unset=True)
        if "status" in data:
            data["status"] = validate_enum(data["status"], QuoteStatus, "status")

        # If person_id is being changed, validate it exists
        if data.get("person_id"):
            person = db.get(Person, data["person_id"])
            if not person:
                raise HTTPException(status_code=404, detail="Person not found")

        for key, value in data.items():
            setattr(quote, key, value)

        # When quote is accepted, upgrade person to customer
        if data.get("status") == QuoteStatus.accepted and quote.person:
            if quote.person.party_status in (PartyStatus.lead, PartyStatus.contact):
                quote.person.party_status = PartyStatus.customer

        db.commit()
        db.refresh(quote)
        if "status" in data:
            _apply_lead_status_from_quote(db, quote, quote.status)
        if data.get("status") == QuoteStatus.accepted:
            from app.services import sales_orders as sales_order_service

            sales_order_service.sales_orders.create_from_quote(db, str(quote.id))
        return quote

    @staticmethod
    def delete(db: Session, quote_id: str):
        quote = db.get(Quote, coerce_uuid(quote_id))
        if not quote:
            raise HTTPException(status_code=404, detail="Quote not found")
        quote.is_active = False
        db.commit()


class CrmQuoteLineItems(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload):
        quote = db.get(Quote, payload.quote_id)
        if not quote:
            raise HTTPException(status_code=404, detail="Quote not found")
        data = payload.model_dump()
        if data.get("inventory_item_id"):
            if not db.get(InventoryItem, data["inventory_item_id"]):
                raise HTTPException(status_code=404, detail="Inventory item not found")
        if not data.get("amount"):
            data["amount"] = Decimal(data.get("quantity") or 0) * Decimal(
                data.get("unit_price") or 0
            )
        item = CrmQuoteLineItem(**data)
        db.add(item)
        db.commit()
        _recalculate_quote_totals(db, quote)
        db.refresh(item)
        return item

    @staticmethod
    def update(db: Session, item_id: str, payload):
        item = db.get(CrmQuoteLineItem, coerce_uuid(item_id))
        if not item:
            raise HTTPException(status_code=404, detail="Quote line item not found")
        data = payload.model_dump(exclude_unset=True)
        if "inventory_item_id" in data and data["inventory_item_id"]:
            if not db.get(InventoryItem, data["inventory_item_id"]):
                raise HTTPException(status_code=404, detail="Inventory item not found")
        for key, value in data.items():
            setattr(item, key, value)
        if "quantity" in data or "unit_price" in data:
            item.amount = Decimal(item.quantity or 0) * Decimal(item.unit_price or 0)
        db.commit()
        db.refresh(item)
        quote = db.get(Quote, item.quote_id)
        if quote:
            _recalculate_quote_totals(db, quote)
        return item

    @staticmethod
    def list(
        db: Session,
        quote_id: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(CrmQuoteLineItem)
        if quote_id:
            query = query.filter(CrmQuoteLineItem.quote_id == coerce_uuid(quote_id))
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": CrmQuoteLineItem.created_at},
        )
        return apply_pagination(query, limit, offset).all()
