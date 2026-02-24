from datetime import UTC, datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import String, cast, func, nullslast, or_
from sqlalchemy.orm import Session, selectinload

from app.models.crm.conversation import Conversation, ConversationAssignment, Message
from app.models.crm.enums import ChannelType, LeadStatus, MessageDirection, QuoteStatus
from app.models.crm.sales import CrmQuoteLineItem, Lead, Pipeline, PipelineStage, Quote
from app.models.crm.team import CrmAgent
from app.models.domain_settings import SettingDomain
from app.models.inventory import InventoryItem
from app.models.person import ChannelType as PersonChannelType
from app.models.person import PartyStatus, Person, PersonChannel
from app.models.projects import Project, ProjectStatus, ProjectTemplate, ProjectType
from app.schemas.projects import ProjectCreate
from app.services import projects as projects_service
from app.services import settings_spec
from app.services.common import apply_ordering, apply_pagination, coerce_uuid, validate_enum
from app.services.response import ListResponseMixin

LEAD_SOURCE_OPTIONS = (
    "Facebook",
    "Instagram",
    "Whatsapp",
    "Email",
    "Referrer",
    "Instagram Ads",
    "Facebook Ads",
    "Google",
    "Website",
)

_LEAD_SOURCE_NORMALIZED_MAP = {
    "facebook": "Facebook",
    "facebook messenger": "Facebook",
    "facebook_messenger": "Facebook",
    "instagram": "Instagram",
    "instagram dm": "Instagram",
    "instagram_dm": "Instagram",
    "whatsapp": "Whatsapp",
    "wa": "Whatsapp",
    "email": "Email",
    "referrer": "Referrer",
    "referral": "Referrer",
    "instagram ads": "Instagram Ads",
    "instagram ad": "Instagram Ads",
    "ig ads": "Instagram Ads",
    "ig ad": "Instagram Ads",
    "facebook ads": "Facebook Ads",
    "facebook ad": "Facebook Ads",
    "fb ads": "Facebook Ads",
    "fb ad": "Facebook Ads",
    "meta ads": "Facebook Ads",
    "meta ad": "Facebook Ads",
    "google": "Google",
    "google ads": "Google",
    "google ad": "Google",
    "adwords": "Google",
    "website": "Website",
    "web": "Website",
    "chat widget": "Website",
    "chat_widget": "Website",
}


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


def _resolve_owner_agent_id_from_last_agent_message(db: Session, person_id):
    row = (
        db.query(CrmAgent.id)
        .join(Message, Message.author_id == CrmAgent.person_id)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .filter(Conversation.person_id == person_id)
        .filter(Message.author_id.isnot(None))
        .order_by(Message.created_at.desc())
        .first()
    )
    return row[0] if row else None


def _resolve_owner_agent_id(db: Session, person_id):
    # Prefer explicit inbox assignment; fall back to last agent-authored message.
    owner = _resolve_owner_agent_id_for_person(db, person_id)
    if owner:
        return owner
    return _resolve_owner_agent_id_from_last_agent_message(db, person_id)


def _lead_title_from_person(person: Person) -> str | None:
    if not person:
        return None
    if person.display_name:
        return person.display_name.strip() or None
    name = " ".join([part for part in [person.first_name, person.last_name] if part]).strip()
    if name:
        return name
    if person.email:
        return person.email.strip() or None
    if person.phone:
        return person.phone.strip() or None
    return None


def _is_placeholder_lead_title(value: str | None) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.strip().lower()
    return normalized in {"website chat", "website chat lead"}


def _normalize_lead_source(value: str | None) -> str | None:
    if value is None:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    mapped = _LEAD_SOURCE_NORMALIZED_MAP.get(candidate.lower())
    if mapped:
        return mapped
    if candidate in LEAD_SOURCE_OPTIONS:
        return candidate
    return None


def _normalize_lead_source_or_400(value: str | None) -> str | None:
    normalized = _normalize_lead_source(value)
    if value and value.strip() and not normalized:
        raise HTTPException(status_code=400, detail="Invalid lead_source")
    return normalized


def _derive_lead_source_from_attribution(attribution: dict | None) -> str | None:
    if not isinstance(attribution, dict):
        return None

    keys = (
        "source",
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "referer_uri",
        "ref",
        "campaign_id",
        "ad_id",
        "adgroup_id",
        "adset_id",
    )
    values: list[str] = []
    for key in keys:
        raw = attribution.get(key)
        if raw is None:
            continue
        if isinstance(raw, str):
            candidate = raw.strip().lower()
        else:
            candidate = str(raw).strip().lower()
        if candidate:
            values.append(candidate)

    combined = " ".join(values)
    if not combined:
        return None
    if "google" in combined or "adwords" in combined or "gclid" in combined:
        return "Google"
    if "instagram" in combined or "ig_" in combined or " ig " in f" {combined} ":
        return "Instagram Ads"
    if "facebook" in combined or "fb" in combined or "meta" in combined:
        return "Facebook Ads"
    if "referrer" in combined or "referral" in combined or "referer" in combined or "ref=" in combined:
        return "Referrer"
    if "website" in combined or "web" in combined:
        return "Website"
    return None


def _derive_lead_source_from_person_channels(db: Session, person_id) -> str | None:
    channels = db.query(PersonChannel.channel_type).filter(PersonChannel.person_id == person_id).all()
    channel_types = {row[0] for row in channels}
    if PersonChannelType.instagram_dm in channel_types:
        return "Instagram"
    if PersonChannelType.facebook_messenger in channel_types:
        return "Facebook"
    if PersonChannelType.whatsapp in channel_types:
        return "Whatsapp"
    if PersonChannelType.email in channel_types:
        return "Email"
    if PersonChannelType.chat_widget in channel_types:
        return "Website"
    return None


def _derive_lead_source_from_recent_inbound_messages(db: Session, person_id) -> str | None:
    rows = (
        db.query(Message.channel_type, Message.metadata_)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .filter(Conversation.person_id == person_id)
        .filter(Message.direction == MessageDirection.inbound)
        .order_by(Message.created_at.desc())
        .limit(20)
        .all()
    )
    for channel_type, metadata in rows:
        msg_meta = metadata if isinstance(metadata, dict) else {}
        msg_attr = msg_meta.get("attribution") if isinstance(msg_meta, dict) else None
        inferred = _derive_lead_source_from_attribution(msg_attr if isinstance(msg_attr, dict) else None)
        if inferred:
            return inferred
        if channel_type == ChannelType.instagram_dm:
            return "Instagram"
        if channel_type == ChannelType.facebook_messenger:
            return "Facebook"
        if channel_type == ChannelType.whatsapp:
            return "Whatsapp"
        if channel_type == ChannelType.email:
            return "Email"
        if channel_type == ChannelType.chat_widget:
            return "Website"
    return None


def _infer_lead_source(db: Session, person: Person | None, metadata: dict | None) -> str | None:
    metadata_attr = metadata.get("attribution") if isinstance(metadata, dict) else None
    inferred = _derive_lead_source_from_attribution(metadata_attr if isinstance(metadata_attr, dict) else None)
    if inferred:
        return inferred
    person_meta = person.metadata_ if person and isinstance(person.metadata_, dict) else {}
    person_attr = person_meta.get("attribution") if isinstance(person_meta, dict) else None
    inferred = _derive_lead_source_from_attribution(person_attr if isinstance(person_attr, dict) else None)
    if inferred:
        return inferred
    if person:
        inferred = _derive_lead_source_from_recent_inbound_messages(db, person.id)
        if inferred:
            return inferred
        inferred = _derive_lead_source_from_person_channels(db, person.id)
        if inferred:
            return inferred
    return None


def _apply_lead_closed_at(
    lead: Lead,
    status: LeadStatus | None,
    *,
    previous_status: LeadStatus | None = None,
) -> None:
    closed_statuses = (LeadStatus.won, LeadStatus.lost)
    if status in closed_statuses:
        # Stamp close time when transitioning from open -> closed, or backfill if missing.
        if previous_status not in closed_statuses or lead.closed_at is None:
            lead.closed_at = datetime.now(UTC)
        return

    # Clear close timestamp if a previously closed lead is reopened.
    if previous_status in closed_statuses:
        lead.closed_at = None


def _apply_lead_status_from_quote(db: Session, quote: Quote, status: QuoteStatus | None):
    if not quote or not status or not quote.lead_id:
        return
    lead = db.get(Lead, quote.lead_id)
    if not lead:
        return
    previous_status = lead.status
    if status == QuoteStatus.accepted:
        lead.status = LeadStatus.won
    elif status == QuoteStatus.rejected:
        lead.status = LeadStatus.lost
    else:
        return
    if lead.owner_agent_id is None:
        lead.owner_agent_id = _resolve_owner_agent_id(db, lead.person_id)
    _apply_lead_closed_at(lead, lead.status, previous_status=previous_status)
    db.commit()


def _recalculate_quote_totals(db: Session, quote: Quote) -> None:
    items = db.query(CrmQuoteLineItem).filter(CrmQuoteLineItem.quote_id == quote.id).all()
    subtotal = Decimal("0.00")
    for item in items:
        subtotal += Decimal(item.amount or 0)
    quote.subtotal = subtotal
    quote.total = subtotal + Decimal(quote.tax_total or 0)
    db.commit()


def _resolve_project_type(value: str | None) -> ProjectType | None:
    if not value:
        return None
    legacy_map = {
        "radio_installation": ProjectType.air_fiber_installation,
        "radio_fiber_relocation": ProjectType.air_fiber_relocation,
    }
    if value in legacy_map:
        return legacy_map[value]
    try:
        return ProjectType(value)
    except ValueError:
        return None


def _find_existing_project_for_quote(db: Session, quote_id) -> Project | None:
    return (
        db.query(Project)
        .filter(Project.is_active.is_(True))
        .filter(cast(Project.metadata_["quote_id"], String) == str(quote_id))
        .first()
    )


def _find_template_for_project_type(db: Session, project_type: ProjectType) -> ProjectTemplate | None:
    return (
        db.query(ProjectTemplate)
        .filter(ProjectTemplate.is_active.is_(True))
        .filter(ProjectTemplate.project_type == project_type)
        .order_by(ProjectTemplate.created_at.desc())
        .first()
    )


def _build_project_name_for_quote(
    *,
    base_name: str,
    owner_label: str | None,
    quote_id: object,
    max_length: int = 160,
) -> str:
    if owner_label:
        candidate = f"{base_name} - {owner_label}"
    else:
        candidate = f"{base_name} - Quote {str(quote_id)[:8].upper()}"
    cleaned = " ".join(candidate.split()).strip()
    if len(cleaned) <= max_length:
        return cleaned
    return cleaned[:max_length].rstrip()


def _ensure_project_from_quote(db: Session, quote: Quote, sales_order_id: str | None) -> Project | None:
    existing = _find_existing_project_for_quote(db, quote.id)
    if existing:
        return existing

    lead = db.get(Lead, quote.lead_id) if quote.lead_id else None
    metadata = quote.metadata_ if isinstance(quote.metadata_, dict) else {}
    project_type_value = metadata.get("project_type") if isinstance(metadata, dict) else None
    project_type = _resolve_project_type(project_type_value if isinstance(project_type_value, str) else None)
    template = _find_template_for_project_type(db, project_type) if project_type else None

    person = db.get(Person, quote.person_id)
    owner_label = None
    if person:
        owner_label = person.display_name or person.email
    base_name = project_type.value.replace("_", " ").title() if project_type else "Project"
    project_name = _build_project_name_for_quote(
        base_name=base_name,
        owner_label=owner_label,
        quote_id=quote.id,
    )

    project_metadata = dict(metadata) if isinstance(metadata, dict) else {}
    project_metadata["quote_id"] = str(quote.id)
    if sales_order_id:
        project_metadata["sales_order_id"] = sales_order_id

    payload = ProjectCreate(
        name=project_name,
        project_type=project_type,
        project_template_id=template.id if template else None,
        status=ProjectStatus.active,
        lead_id=quote.lead_id,
        owner_person_id=quote.person_id,
        region=lead.region if lead else None,
        customer_address=lead.address if lead else None,
        metadata_=project_metadata or None,
    )
    return projects_service.projects.create(db, payload)


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
        if "lead_source" in data:
            data["lead_source"] = _normalize_lead_source_or_400(data.get("lead_source"))

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

        title_value = data.get("title")
        if (
            not title_value
            or (isinstance(title_value, str) and not title_value.strip())
            or _is_placeholder_lead_title(title_value)
        ):
            data["title"] = _lead_title_from_person(person)

        if not data.get("owner_agent_id"):
            data["owner_agent_id"] = _resolve_owner_agent_id(db, person_id)
        if not data.get("currency"):
            default_currency = settings_spec.resolve_value(db, SettingDomain.billing, "default_currency")
            if default_currency:
                data["currency"] = default_currency
        if not data.get("lead_source"):
            data["lead_source"] = _infer_lead_source(db, person, data.get("metadata_"))
        lead = Lead(**data)
        _apply_lead_closed_at(lead, lead.status)
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
        lead_source: str | None = None,
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
        if lead_source:
            query = query.filter(func.lower(Lead.lead_source) == lead_source.strip().lower())
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
        previous_status = lead.status
        data = payload.model_dump(exclude_unset=True)
        if "status" in data:
            data["status"] = validate_enum(data["status"], LeadStatus, "status")
        if "lead_source" in data:
            data["lead_source"] = _normalize_lead_source_or_400(data.get("lead_source"))

        # If person_id is being changed, validate it exists
        if data.get("person_id"):
            person = db.get(Person, data["person_id"])
            if not person:
                raise HTTPException(status_code=404, detail="Person not found")
        else:
            person = lead.person

        if "title" in data:
            title_value = data.get("title")
            if (
                not title_value
                or (isinstance(title_value, str) and not title_value.strip())
                or _is_placeholder_lead_title(title_value)
            ):
                inferred = _lead_title_from_person(person) if person else None
                data["title"] = inferred

        for key, value in data.items():
            setattr(lead, key, value)

        # When lead is won, upgrade person to customer
        if (
            data.get("status") == LeadStatus.won
            and lead.person
            and lead.person.party_status in (PartyStatus.lead, PartyStatus.contact)
        ):
            lead.person.party_status = PartyStatus.customer
        if "status" in data:
            if lead.owner_agent_id is None and lead.status in (LeadStatus.won, LeadStatus.lost):
                lead.owner_agent_id = _resolve_owner_agent_id(db, lead.person_id)
            _apply_lead_closed_at(lead, lead.status, previous_status=previous_status)

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
            columns.append(
                {
                    "id": str(stage.id),
                    "title": stage.name,
                    "order_index": stage.order_index,
                    "default_probability": stage.default_probability,
                }
            )

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

            records.append(
                {
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
                }
            )

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

    @staticmethod
    def bulk_assign_pipeline(
        db: Session,
        pipeline_id: str,
        stage_id: str | None = None,
        *,
        scope: str = "unassigned",
    ) -> int:
        pipeline = db.get(Pipeline, coerce_uuid(pipeline_id))
        if not pipeline:
            raise HTTPException(status_code=404, detail="Pipeline not found")

        resolved_stage_id = None
        if stage_id:
            stage = db.get(PipelineStage, coerce_uuid(stage_id))
            if not stage or stage.pipeline_id != pipeline.id:
                raise HTTPException(status_code=400, detail="Selected stage does not belong to this pipeline")
            resolved_stage_id = stage.id

        query = db.query(Lead).filter(Lead.is_active.is_(True))
        if scope == "unassigned":
            query = query.filter(Lead.pipeline_id.is_(None))
        elif scope != "all_active":
            raise HTTPException(status_code=400, detail="Unsupported bulk assign scope")

        count = query.update(
            {
                Lead.pipeline_id: pipeline.id,
                Lead.stage_id: resolved_stage_id,
            },
            synchronize_session=False,
        )
        db.commit()
        return int(count)


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
            default_currency = settings_spec.resolve_value(db, SettingDomain.billing, "default_currency")
            if default_currency:
                data["currency"] = default_currency
        quote = Quote(**data)
        db.add(quote)
        db.commit()
        db.refresh(quote)
        _apply_lead_status_from_quote(db, quote, quote.status)
        if quote.status == QuoteStatus.accepted:
            from app.services import sales_orders as sales_order_service

            if quote.person and quote.person.party_status in (PartyStatus.lead, PartyStatus.contact):
                quote.person.party_status = PartyStatus.customer
                db.commit()
                db.refresh(quote)

            sales_order = sales_order_service.sales_orders.create_from_quote(db, str(quote.id))
            _ensure_project_from_quote(db, quote, str(sales_order.id) if sales_order else None)
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
            db.query(Quote.status, func.count(Quote.id)).filter(Quote.is_active.is_(True)).group_by(Quote.status).all()
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
        if (
            data.get("status") == QuoteStatus.accepted
            and quote.person
            and quote.person.party_status in (PartyStatus.lead, PartyStatus.contact)
        ):
            quote.person.party_status = PartyStatus.customer

        db.commit()
        db.refresh(quote)
        if "status" in data:
            _apply_lead_status_from_quote(db, quote, quote.status)
        if data.get("status") == QuoteStatus.accepted:
            from app.services import sales_orders as sales_order_service

            sales_order = sales_order_service.sales_orders.create_from_quote(db, str(quote.id))
            _ensure_project_from_quote(db, quote, str(sales_order.id) if sales_order else None)
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
        if data.get("inventory_item_id") and not db.get(InventoryItem, data["inventory_item_id"]):
            raise HTTPException(status_code=404, detail="Inventory item not found")
        if not data.get("amount"):
            data["amount"] = Decimal(data.get("quantity") or 0) * Decimal(data.get("unit_price") or 0)
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
        if data.get("inventory_item_id") and not db.get(InventoryItem, data["inventory_item_id"]):
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


# Singleton instances
pipelines = Pipelines()
pipeline_stages = PipelineStages()
leads = Leads()
quotes = Quotes()
quote_line_items = CrmQuoteLineItems()
