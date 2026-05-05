"""Campaign management service.

Provides CRUD for campaigns, nurture steps, and recipients.
Handles audience segmentation, variable substitution, and
campaign lifecycle (draft -> scheduled -> sending -> completed).
"""

from __future__ import annotations

import contextlib
import logging
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from typing import cast as type_cast

from fastapi import HTTPException
from sqlalchemy import String, cast, false, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models.connector import ConnectorConfig, ConnectorType
from app.models.crm.campaign import Campaign, CampaignRecipient, CampaignStep
from app.models.crm.campaign_smtp import CampaignSmtpConfig
from app.models.crm.conversation import ConversationTag, Message
from app.models.crm.enums import (
    CampaignChannel,
    CampaignRecipientStatus,
    CampaignStatus,
    CampaignType,
    ChannelType,
    MessageDirection,
    MessageStatus,
)
from app.models.crm.sales import Lead
from app.models.notification import Notification, NotificationChannel, NotificationStatus
from app.models.person import ChannelType as PersonChannelType
from app.models.person import PartyStatus, Person, PersonChannel
from app.models.subscriber import Organization, Subscriber
from app.schemas.crm.conversation import ConversationCreate
from app.schemas.crm.inbox import InboxSendRequest
from app.services.common import apply_ordering, apply_pagination, coerce_uuid, validate_enum
from app.services.crm import conversation as conversation_service
from app.services.crm.inbox import outbound as inbox_outbound_service
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)

# Variable pattern for template substitution
_VAR_PATTERN = re.compile(r"\{\{(\w+)\}\}")
_MANUAL_AUDIENCE_MODE = "manual_snapshot"
_OUTREACH_KIND = "outreach"


def _substitute_variables(template: str | None, person: Person, org_map: dict | None = None) -> str | None:
    """Replace {{first_name}}, {{last_name}}, {{email}}, {{organization_name}} in template."""
    if not template:
        return template

    org_name = ""
    if person.organization_id and org_map:
        org = org_map.get(person.organization_id)
        if org:
            org_name = org.name or ""

    replacements = {
        "first_name": person.first_name or "",
        "last_name": person.last_name or "",
        "email": person.email or "",
        "organization_name": org_name,
    }

    def _replace(match):
        key = match.group(1)
        return replacements.get(key, match.group(0))

    return _VAR_PATTERN.sub(_replace, template)


def _normalize_whatsapp_address(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) < 8 or len(digits) > 15:
        return None
    return f"+{digits}"


def _coerce_whatsapp_template_components(value: object) -> list[dict[Any, Any]] | None:
    if not isinstance(value, list):
        return None
    components: list[dict[Any, Any]] = []
    for item in value:
        if isinstance(item, dict):
            components.append(item)
    return components or None


def _resolve_or_create_whatsapp_channel(db: Session, person: Person) -> PersonChannel | None:
    for channel in person.channels or []:
        if channel.channel_type == PersonChannelType.whatsapp and channel.address:
            normalized = _normalize_whatsapp_address(channel.address)
            if normalized and channel.address != normalized:
                channel.address = normalized
            return channel if normalized else None

    normalized_phone = _normalize_whatsapp_address(person.phone)
    if not normalized_phone:
        return None

    existing = (
        db.query(PersonChannel)
        .filter(
            PersonChannel.person_id == person.id,
            PersonChannel.channel_type == PersonChannelType.whatsapp,
            PersonChannel.address == normalized_phone,
        )
        .first()
    )
    if existing:
        return existing

    channel = PersonChannel(
        person_id=person.id,
        channel_type=PersonChannelType.whatsapp,
        address=normalized_phone,
        label="Primary WhatsApp",
        is_primary=True,
        metadata_={
            "whatsapp_validation": {
                "status": "unknown",
                "source": "billing_risk_outreach",
                "updated_at": datetime.now(UTC).isoformat(),
            }
        },
    )
    db.add(channel)
    db.flush()
    person.channels.append(channel)
    return channel


def _whatsapp_channel_validation_status(channel: PersonChannel | None) -> str:
    if not channel or not isinstance(channel.metadata_, dict):
        return ""
    validation = channel.metadata_.get("whatsapp_validation")
    if not isinstance(validation, dict):
        return ""
    return str(validation.get("status") or "").strip().lower()


def _whatsapp_preflight_failure_reason(db: Session, person: Person) -> str | None:
    channel = _resolve_or_create_whatsapp_channel(db, person)
    if not channel or not _normalize_whatsapp_address(channel.address):
        return "Invalid WhatsApp number format"
    validation_status = _whatsapp_channel_validation_status(channel)
    if validation_status == "invalid":
        return "Previously rejected by WhatsApp"
    return None


def _ensure_conversation_tag(db: Session, *, conversation_id, tag: str) -> None:
    clean_tag = str(tag or "").strip()
    if not clean_tag:
        return
    exists = (
        db.query(ConversationTag)
        .filter(
            ConversationTag.conversation_id == conversation_id,
            ConversationTag.tag == clean_tag,
        )
        .first()
    )
    if not exists:
        db.add(ConversationTag(conversation_id=conversation_id, tag=clean_tag))


def _whatsapp_address_for_person(db: Session, person: Person) -> str | None:
    channel = _resolve_or_create_whatsapp_channel(db, person)
    if channel and channel.address:
        return _normalize_whatsapp_address(channel.address)
    return _normalize_whatsapp_address(person.phone)


def _campaign_metadata(campaign: Campaign | None) -> dict:
    metadata = getattr(campaign, "metadata_", None)
    return metadata if isinstance(metadata, dict) else {}


def _campaign_audience_mode(campaign: Campaign | None) -> str:
    return str(_campaign_metadata(campaign).get("audience_mode") or "").strip().lower()


def _is_manual_snapshot_campaign(campaign: Campaign | None) -> bool:
    return _campaign_audience_mode(campaign) == _MANUAL_AUDIENCE_MODE


def _is_outreach_campaign(campaign: Campaign | None) -> bool:
    return str(_campaign_metadata(campaign).get("kind") or "").strip().lower() == _OUTREACH_KIND


def _outreach_channel_target_id(campaign: Campaign | None) -> str | None:
    value = str(_campaign_metadata(campaign).get("channel_target_id") or "").strip()
    return value or None


def _audience_snapshot_row_for_person(campaign: Campaign | None, person_id: str) -> dict | None:
    snapshot = _campaign_metadata(campaign).get("audience_snapshot")
    if not isinstance(snapshot, list):
        return None
    normalized_person_id = str(person_id or "").strip()
    if not normalized_person_id:
        return None
    for row in snapshot:
        if not isinstance(row, dict):
            continue
        if str(row.get("person_id") or "").strip() == normalized_person_id:
            return row
    return None


def _outreach_message_campaign_id(message: Message | None) -> str | None:
    metadata = getattr(message, "metadata_", None)
    if not isinstance(metadata, dict):
        return None
    value = str(metadata.get("campaign_id") or "").strip()
    return value or None


def _outreach_message_campaign_recipient_id(message: Message | None) -> str | None:
    metadata = getattr(message, "metadata_", None)
    if not isinstance(metadata, dict):
        return None
    value = str(metadata.get("campaign_recipient_id") or "").strip()
    return value or None


def _build_segment_query(db: Session, segment_filter: dict | None, channel: CampaignChannel):
    """Build a Person query filtered by segment_filter criteria."""
    query = db.query(Person)
    if channel == CampaignChannel.whatsapp:
        query = query.filter(
            Person.phone.isnot(None),
            Person.phone != "",
        )
    else:
        query = query.filter(
            Person.email.isnot(None),
            Person.email != "",
        )

    if not segment_filter:
        return query.filter(Person.is_active.is_(True))

    active_status = str(segment_filter.get("active_status") or "").strip().lower()
    if active_status == "inactive":
        query = query.filter(Person.is_active.is_(False))
    elif active_status == "all":
        pass
    else:
        query = query.filter(Person.is_active.is_(True))

    if segment_filter.get("party_status"):
        statuses = []
        for s in segment_filter["party_status"]:
            with contextlib.suppress(ValueError):
                statuses.append(PartyStatus(s))
        if statuses:
            query = query.filter(Person.party_status.in_(statuses))

    if segment_filter.get("organization_ids"):
        org_ids = [coerce_uuid(oid) for oid in segment_filter["organization_ids"] if oid]
        if org_ids:
            query = query.filter(Person.organization_id.in_(org_ids))

    # Regions and tags both require Organization join — do it once
    needs_org_join = bool(segment_filter.get("regions") or segment_filter.get("tags"))
    if needs_org_join:
        query = query.join(Organization, Person.organization_id == Organization.id)

    if segment_filter.get("regions"):
        regions = segment_filter["regions"]
        if regions:
            query = query.filter(Organization.region.in_(regions))

    if segment_filter.get("tags"):
        tags = segment_filter["tags"]
        if tags:
            # Organization.tags is a JSON array — match any tag
            tag_conditions = [Organization.tags.op("@>")(f'["{tag}"]') for tag in tags]
            query = query.filter(or_(*tag_conditions))

    if segment_filter.get("created_after"):
        try:
            dt = datetime.fromisoformat(str(segment_filter["created_after"]))
            query = query.filter(Person.created_at >= dt)
        except (ValueError, TypeError):
            pass

    if segment_filter.get("created_before"):
        try:
            dt = datetime.fromisoformat(str(segment_filter["created_before"]))
            query = query.filter(Person.created_at <= dt)
        except (ValueError, TypeError):
            pass

    if segment_filter.get("pipeline_ids") or segment_filter.get("stage_ids"):
        lead_query = db.query(Lead.person_id).filter(Lead.is_active.is_(True))

        if segment_filter.get("pipeline_ids"):
            pipeline_ids = [coerce_uuid(pid) for pid in segment_filter["pipeline_ids"] if pid]
            if pipeline_ids:
                lead_query = lead_query.filter(Lead.pipeline_id.in_(pipeline_ids))
            else:
                lead_query = lead_query.filter(false())

        if segment_filter.get("stage_ids"):
            stage_ids = [coerce_uuid(sid) for sid in segment_filter["stage_ids"] if sid]
            if stage_ids:
                lead_query = lead_query.filter(Lead.stage_id.in_(stage_ids))
            else:
                lead_query = lead_query.filter(false())

        lead_person_ids = lead_query.distinct().subquery()
        query = query.filter(Person.id.in_(select(lead_person_ids.c.person_id)))

    return query


class Campaigns(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload, created_by_id=None):
        data = payload.model_dump()
        if created_by_id:
            data["created_by_id"] = coerce_uuid(created_by_id)
        campaign = Campaign(**data)
        db.add(campaign)
        db.commit()
        db.refresh(campaign)
        return campaign

    @staticmethod
    def get(db: Session, campaign_id: str):
        campaign = db.get(Campaign, coerce_uuid(campaign_id))
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")
        return campaign

    @staticmethod
    def list(
        db: Session,
        status: str | None = None,
        campaign_type: str | None = None,
        search: str | None = None,
        is_active: bool | None = None,
        order_by: str = "created_at",
        order_dir: str = "desc",
        limit: int = 50,
        offset: int = 0,
    ):
        query = db.query(Campaign)
        if status:
            status_value = validate_enum(status, CampaignStatus, "status")
            query = query.filter(Campaign.status == status_value)
        if campaign_type:
            type_value = validate_enum(campaign_type, CampaignType, "campaign_type")
            query = query.filter(Campaign.campaign_type == type_value)
        if search:
            like = f"%{search.strip()}%"
            query = query.filter(
                or_(
                    Campaign.name.ilike(like),
                    Campaign.subject.ilike(like),
                    cast(Campaign.id, String).ilike(like),
                )
            )
        if is_active is None:
            query = query.filter(Campaign.is_active.is_(True))
        else:
            query = query.filter(Campaign.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": Campaign.created_at, "updated_at": Campaign.updated_at, "name": Campaign.name},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, campaign_id: str, payload):
        campaign = db.get(Campaign, coerce_uuid(campaign_id))
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")
        if campaign.status != CampaignStatus.draft:
            raise HTTPException(status_code=400, detail="Only draft campaigns can be edited")
        data = payload.model_dump(exclude_unset=True)
        for key, value in data.items():
            setattr(campaign, key, value)
        db.commit()
        db.refresh(campaign)
        return campaign

    @staticmethod
    def delete(db: Session, campaign_id: str):
        campaign = db.get(Campaign, coerce_uuid(campaign_id))
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")
        campaign.is_active = False
        db.commit()

    @staticmethod
    def schedule(db: Session, campaign_id: str, scheduled_at: datetime):
        campaign = db.get(Campaign, coerce_uuid(campaign_id))
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")
        if campaign.status != CampaignStatus.draft:
            raise HTTPException(status_code=400, detail="Only draft campaigns can be scheduled")
        campaign.status = CampaignStatus.scheduled
        campaign.scheduled_at = scheduled_at
        db.commit()
        db.refresh(campaign)
        return campaign

    @staticmethod
    def send_now(db: Session, campaign_id: str):
        campaign = db.get(Campaign, coerce_uuid(campaign_id))
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")
        if campaign.status not in (CampaignStatus.draft, CampaignStatus.scheduled):
            raise HTTPException(status_code=400, detail="Campaign cannot be sent in its current state")
        if campaign.channel == CampaignChannel.email:
            if not campaign.campaign_smtp_config_id:
                raise HTTPException(status_code=400, detail="Campaign SMTP profile is required to send")
            smtp_profile = db.get(CampaignSmtpConfig, campaign.campaign_smtp_config_id)
            if not smtp_profile or not smtp_profile.is_active:
                raise HTTPException(status_code=400, detail="Selected campaign SMTP profile is inactive")
        elif campaign.channel == CampaignChannel.whatsapp:
            if not campaign.connector_config_id:
                raise HTTPException(status_code=400, detail="WhatsApp connector is required to send")
            connector = db.get(ConnectorConfig, campaign.connector_config_id)
            if not connector or not connector.is_active:
                raise HTTPException(status_code=400, detail="Selected WhatsApp connector is inactive")
            if connector.connector_type != ConnectorType.whatsapp:
                raise HTTPException(status_code=400, detail="Selected connector is not a WhatsApp connector")

        # Build recipient list
        Campaigns.build_recipient_list(db, str(campaign.id))

        campaign.status = CampaignStatus.sending
        campaign.sending_started_at = datetime.now(UTC)
        db.commit()
        db.refresh(campaign)

        # Dispatch Celery task
        from app.tasks.campaigns import execute_campaign

        execute_campaign.delay(str(campaign.id))

        return campaign

    @staticmethod
    def cancel(db: Session, campaign_id: str):
        campaign = db.get(Campaign, coerce_uuid(campaign_id))
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")
        if campaign.status not in (CampaignStatus.scheduled, CampaignStatus.sending):
            raise HTTPException(status_code=400, detail="Only scheduled or sending campaigns can be cancelled")
        campaign.status = CampaignStatus.cancelled
        db.commit()
        db.refresh(campaign)
        return campaign

    @staticmethod
    def build_recipient_list(db: Session, campaign_id: str):
        campaign = db.get(Campaign, coerce_uuid(campaign_id))
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")
        if _is_manual_snapshot_campaign(campaign):
            return 0

        persons = _build_segment_query(db, campaign.segment_filter, campaign.channel).all()

        # Batch-check existing recipients to avoid N+1
        existing_person_ids = set(
            pid
            for (pid,) in db.query(CampaignRecipient.person_id)
            .filter(
                CampaignRecipient.campaign_id == campaign.id,
                CampaignRecipient.step_id.is_(None),
            )
            .all()
        )

        count = 0
        for person in persons:
            address = person.email
            if campaign.channel == CampaignChannel.whatsapp:
                if _whatsapp_preflight_failure_reason(db, person):
                    continue
                address = _whatsapp_address_for_person(db, person)
            if not address:
                continue
            if person.id in existing_person_ids:
                continue
            recipient = CampaignRecipient(
                campaign_id=campaign.id,
                person_id=person.id,
                address=address,
                email=person.email if campaign.channel == CampaignChannel.email else None,
                status=CampaignRecipientStatus.pending,
            )
            db.add(recipient)
            count += 1

        # Accumulate rather than overwrite to handle re-runs
        campaign.total_recipients = (campaign.total_recipients or 0) + count
        db.commit()
        return count

    @staticmethod
    def seed_manual_snapshot_recipients(
        db: Session,
        *,
        campaign_id: str,
        subscriber_ids: Sequence[str],
        snapshot_context_by_subscriber_id: dict[str, dict] | None = None,
    ) -> dict[str, int]:
        campaign = db.get(Campaign, coerce_uuid(campaign_id))
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")

        normalized_subscriber_ids = [coerce_uuid(subscriber_id) for subscriber_id in subscriber_ids if subscriber_id]
        normalized_subscriber_ids = [subscriber_id for subscriber_id in normalized_subscriber_ids if subscriber_id]
        if not normalized_subscriber_ids:
            return {"selected": 0, "seeded": 0, "skipped": 0}

        subscribers = (
            db.query(Subscriber)
            .options(joinedload(Subscriber.person))
            .filter(Subscriber.id.in_(normalized_subscriber_ids))
            .all()
        )
        subscriber_by_id = {subscriber.id: subscriber for subscriber in subscribers}
        existing_person_ids = set(
            pid
            for (pid,) in db.query(CampaignRecipient.person_id)
            .filter(
                CampaignRecipient.campaign_id == campaign.id,
                CampaignRecipient.step_id.is_(None),
            )
            .all()
        )

        seeded = 0
        skipped = 0
        whatsapp_validation_skipped = 0
        snapshot_rows: list[dict[str, str]] = []
        for subscriber_id in normalized_subscriber_ids:
            subscriber = subscriber_by_id.get(subscriber_id)
            if not subscriber or not subscriber.person:
                skipped += 1
                continue

            person = subscriber.person
            address = person.email
            if campaign.channel == CampaignChannel.whatsapp:
                if _whatsapp_preflight_failure_reason(db, person):
                    whatsapp_validation_skipped += 1
                    skipped += 1
                    continue
                address = _whatsapp_address_for_person(db, person)
            if not address or person.id in existing_person_ids:
                skipped += 1
                continue

            db.add(
                CampaignRecipient(
                    campaign_id=campaign.id,
                    person_id=person.id,
                    address=address,
                    email=person.email if campaign.channel == CampaignChannel.email else None,
                    status=CampaignRecipientStatus.pending,
                )
            )
            existing_person_ids.add(person.id)
            seeded += 1
            snapshot_rows.append(
                {
                    "subscriber_id": str(subscriber.id),
                    "person_id": str(person.id),
                    "name": (
                        person.display_name
                        or f"{person.first_name or ''} {person.last_name or ''}".strip()
                        or subscriber.subscriber_number
                        or str(subscriber.id)
                    ),
                    "subscriber_number": subscriber.subscriber_number or "",
                    "email": person.email or "",
                    "phone": person.phone or "",
                    **(
                        snapshot_context_by_subscriber_id.get(str(subscriber.id), {})
                        if snapshot_context_by_subscriber_id
                        else {}
                    ),
                }
            )

        metadata = dict(_campaign_metadata(campaign))
        metadata["audience_mode"] = _MANUAL_AUDIENCE_MODE
        metadata["kind"] = metadata.get("kind") or _OUTREACH_KIND
        if campaign.channel == CampaignChannel.whatsapp:
            metadata["whatsapp_validation_summary"] = {
                "selected": len(normalized_subscriber_ids),
                "seeded": seeded,
                "skipped": skipped,
                "quarantined_or_invalid": whatsapp_validation_skipped,
                "updated_at": datetime.now(UTC).isoformat(),
            }
        snapshot = metadata.get("audience_snapshot")
        existing_snapshot_rows = snapshot if isinstance(snapshot, list) else []
        metadata["audience_snapshot"] = existing_snapshot_rows + snapshot_rows
        metadata["audience_snapshot_count"] = len(metadata["audience_snapshot"])
        campaign.metadata_ = metadata
        campaign.total_recipients = len(metadata["audience_snapshot"])
        db.commit()
        return {"selected": len(normalized_subscriber_ids), "seeded": seeded, "skipped": skipped}

    @staticmethod
    def preview_audience(db: Session, segment_filter: dict | None, channel: CampaignChannel):
        query = _build_segment_query(db, segment_filter, channel)
        total = query.count()
        sample = query.limit(10).all()
        return {
            "total": total,
            "sample": [
                {
                    "id": str(p.id),
                    "name": p.display_name or f"{p.first_name or ''} {p.last_name or ''}".strip(),
                    "address": _whatsapp_address_for_person(db, p) if channel == CampaignChannel.whatsapp else p.email,
                }
                for p in sample
            ],
        }

    @staticmethod
    def preview_seeded_audience(db: Session, *, campaign_id: str):
        campaign = db.get(Campaign, coerce_uuid(campaign_id))
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")

        recipients = (
            db.query(CampaignRecipient)
            .options(joinedload(CampaignRecipient.person))
            .filter(CampaignRecipient.campaign_id == campaign.id)
            .filter(CampaignRecipient.step_id.is_(None))
            .order_by(CampaignRecipient.created_at.asc())
            .limit(10)
            .all()
        )
        snapshot_count = int(
            _campaign_metadata(campaign).get("audience_snapshot_count") or campaign.total_recipients or 0
        )
        return {
            "total": snapshot_count,
            "sample": [
                {
                    "id": str(recipient.person_id),
                    "name": (recipient.person.display_name if recipient.person else None)
                    or (
                        f"{recipient.person.first_name or ''} {recipient.person.last_name or ''}".strip()
                        if recipient.person
                        else ""
                    ),
                    "address": recipient.address,
                }
                for recipient in recipients
            ],
        }

    @staticmethod
    def analytics(db: Session, campaign_id: str):
        campaign = db.get(Campaign, coerce_uuid(campaign_id))
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")

        status_counts = (
            db.query(CampaignRecipient.status, func.count(CampaignRecipient.id))
            .filter(CampaignRecipient.campaign_id == campaign.id)
            .group_by(CampaignRecipient.status)
            .all()
        )
        counts = {s.value: 0 for s in CampaignRecipientStatus}
        for status_val, count in status_counts:
            if status_val:
                counts[status_val.value] = count

        metadata = _campaign_metadata(campaign)
        return {
            "campaign_id": str(campaign.id),
            "status": campaign.status.value,
            "total_recipients": campaign.total_recipients,
            "sent_count": campaign.sent_count,
            "delivered_count": campaign.delivered_count,
            "failed_count": campaign.failed_count,
            "opened_count": campaign.opened_count,
            "clicked_count": campaign.clicked_count,
            "replied_conversations_count": int(metadata.get("replied_conversations_count") or 0),
            "inbound_replies_count": int(metadata.get("inbound_replies_count") or 0),
            "recipient_status_breakdown": counts,
        }

    @staticmethod
    def count_by_status(db: Session) -> dict:
        results = (
            db.query(Campaign.status, func.count(Campaign.id))
            .filter(Campaign.is_active.is_(True))
            .group_by(Campaign.status)
            .all()
        )
        counts = {s.value: 0 for s in CampaignStatus}
        for status_val, count in results:
            if status_val:
                counts[status_val.value] = count
        counts["total"] = sum(counts.values())
        return counts


class CampaignSteps(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload):
        campaign = db.get(Campaign, payload.campaign_id)
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")
        if campaign.campaign_type != CampaignType.nurture:
            raise HTTPException(status_code=400, detail="Steps can only be added to nurture campaigns")
        step = CampaignStep(**payload.model_dump())
        db.add(step)
        db.commit()
        db.refresh(step)
        return step

    @staticmethod
    def get(db: Session, step_id: str):
        step = db.get(CampaignStep, coerce_uuid(step_id))
        if not step:
            raise HTTPException(status_code=404, detail="Campaign step not found")
        return step

    @staticmethod
    def list(
        db: Session,
        campaign_id: str,
        order_by: str = "step_index",
        order_dir: str = "asc",
        limit: int = 50,
        offset: int = 0,
    ):
        query = db.query(CampaignStep).filter(CampaignStep.campaign_id == coerce_uuid(campaign_id))
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"step_index": CampaignStep.step_index, "created_at": CampaignStep.created_at},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, step_id: str, payload):
        step = db.get(CampaignStep, coerce_uuid(step_id))
        if not step:
            raise HTTPException(status_code=404, detail="Campaign step not found")
        data = payload.model_dump(exclude_unset=True)
        for key, value in data.items():
            setattr(step, key, value)
        db.commit()
        db.refresh(step)
        return step

    @staticmethod
    def delete(db: Session, step_id: str):
        step = db.get(CampaignStep, coerce_uuid(step_id))
        if not step:
            raise HTTPException(status_code=404, detail="Campaign step not found")
        db.delete(step)
        db.commit()


class CampaignRecipients(ListResponseMixin):
    @staticmethod
    def list(
        db: Session,
        campaign_id: str,
        status: str | None = None,
        order_by: str = "created_at",
        order_dir: str = "desc",
        limit: int = 50,
        offset: int = 0,
    ):
        query = db.query(CampaignRecipient).filter(CampaignRecipient.campaign_id == coerce_uuid(campaign_id))
        if status:
            status_value = validate_enum(status, CampaignRecipientStatus, "status")
            query = query.filter(CampaignRecipient.status == status_value)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": CampaignRecipient.created_at},
        )
        return apply_pagination(query, limit, offset).all()


def reconcile_outreach_tracking(db: Session, *, campaign_id: str) -> None:
    campaign = db.get(Campaign, coerce_uuid(campaign_id))
    if not campaign or not _is_outreach_campaign(campaign):
        return

    recipients = (
        db.query(CampaignRecipient)
        .filter(CampaignRecipient.campaign_id == campaign.id)
        .filter(CampaignRecipient.step_id.is_(None))
        .all()
    )
    outbound_messages = (
        db.query(Message)
        .filter(
            Message.direction == MessageDirection.outbound,
            Message.metadata_["campaign_id"].astext == str(campaign.id),
        )
        .all()
    )
    conversation_ids = sorted({message.conversation_id for message in outbound_messages if message.conversation_id})
    inbound_messages = []
    if conversation_ids:
        inbound_messages = (
            db.query(Message)
            .filter(
                Message.direction == MessageDirection.inbound,
                Message.conversation_id.in_(conversation_ids),
            )
            .all()
        )

    campaign.total_recipients = len(recipients)
    campaign.sent_count = sum(
        1
        for recipient in recipients
        if recipient.status in {CampaignRecipientStatus.sent, CampaignRecipientStatus.delivered}
    )
    campaign.delivered_count = sum(
        1 for recipient in recipients if recipient.status == CampaignRecipientStatus.delivered
    )
    campaign.failed_count = sum(1 for recipient in recipients if recipient.status == CampaignRecipientStatus.failed)
    campaign.opened_count = sum(1 for message in outbound_messages if message.status == MessageStatus.read)
    metadata = _campaign_metadata(campaign)
    metadata["replied_conversations_count"] = len(
        {message.conversation_id for message in inbound_messages if message.conversation_id}
    )
    metadata["inbound_replies_count"] = len(inbound_messages)
    metadata["tracking_updated_at"] = datetime.now(UTC).isoformat()
    campaign.metadata_ = metadata
    db.commit()


def reconcile_outreach_message_status(db: Session, *, message_id: str) -> None:
    message = db.get(Message, coerce_uuid(message_id))
    if not message:
        return
    campaign_id = _outreach_message_campaign_id(message)
    if not campaign_id:
        return
    recipient_id = _outreach_message_campaign_recipient_id(message)
    if recipient_id:
        recipient = db.get(CampaignRecipient, coerce_uuid(recipient_id))
        if recipient:
            if message.status == MessageStatus.failed:
                recipient.status = CampaignRecipientStatus.failed
                recipient.failed_reason = "Inbox delivery failed"
            elif message.status in {MessageStatus.delivered, MessageStatus.read}:
                recipient.status = CampaignRecipientStatus.delivered
                recipient.delivered_at = message.read_at or message.sent_at or datetime.now(UTC)
            elif message.status == MessageStatus.sent:
                recipient.status = CampaignRecipientStatus.sent
                recipient.sent_at = message.sent_at or datetime.now(UTC)
            db.flush()
    reconcile_outreach_tracking(db, campaign_id=campaign_id)


def reconcile_outreach_inbound_reply(db: Session, *, message_id: str) -> None:
    message = db.get(Message, coerce_uuid(message_id))
    if not message or message.direction != MessageDirection.inbound:
        return

    outbound_context = (
        db.query(Message)
        .filter(
            Message.conversation_id == message.conversation_id,
            Message.direction == MessageDirection.outbound,
            Message.metadata_["campaign_id"].astext.isnot(None),
        )
        .order_by(func.coalesce(Message.sent_at, Message.created_at).desc())
        .first()
    )
    if not outbound_context:
        return
    campaign_id = _outreach_message_campaign_id(outbound_context)
    if not campaign_id:
        return
    reconcile_outreach_tracking(db, campaign_id=campaign_id)


def send_campaign_batch(db: Session, campaign_id: str, batch_size: int = 50) -> int:
    """Send a batch of pending recipients for a campaign.

    Creates Notification records (queued) for each recipient. The existing
    deliver_notification_queue task handles actual SMTP delivery.

    Returns the number of recipients processed in this batch.
    """
    cid = coerce_uuid(campaign_id)
    # Lock campaign row to prevent concurrent batch processing
    campaign = db.query(Campaign).filter(Campaign.id == cid).with_for_update(skip_locked=True).first()
    if not campaign or campaign.status != CampaignStatus.sending:
        return 0

    pending = (
        db.query(CampaignRecipient)
        .options(joinedload(CampaignRecipient.step))
        .filter(
            CampaignRecipient.campaign_id == campaign.id,
            CampaignRecipient.status == CampaignRecipientStatus.pending,
        )
        .limit(batch_size)
        .all()
    )

    if not pending:
        # All done
        campaign.status = CampaignStatus.completed
        campaign.completed_at = datetime.now(UTC)
        db.commit()
        return 0

    # Batch load persons for variable substitution
    person_ids = [r.person_id for r in pending]
    persons = db.query(Person).options(joinedload(Person.channels)).filter(Person.id.in_(person_ids)).all()
    person_map = {p.id: p for p in persons}

    # Batch load organizations for variable substitution
    org_ids = [p.organization_id for p in persons if p.organization_id]
    org_map = {}
    if org_ids:
        orgs = db.query(Organization).filter(Organization.id.in_(org_ids)).all()
        org_map = {o.id: o for o in orgs}

    processed = 0
    for recipient in pending:
        person = person_map.get(recipient.person_id)
        if not person:
            recipient.status = CampaignRecipientStatus.failed
            recipient.failed_reason = "Person not found"
            campaign.failed_count += 1
            processed += 1
            continue

        # Determine subject and body (use step overrides for nurture)
        subject = campaign.subject
        body_html = campaign.body_html
        body_text = campaign.body_text
        if recipient.step_id and recipient.step:
            step = recipient.step
            if step.subject:
                subject = step.subject
            if step.body_html:
                body_html = step.body_html
            if step.body_text:
                body_text = step.body_text

        # Variable substitution
        subject = _substitute_variables(subject, person, org_map)
        body = _substitute_variables(body_html or body_text, person, org_map)

        if campaign.channel == CampaignChannel.whatsapp:
            preflight_failure = _whatsapp_preflight_failure_reason(db, person)
            if preflight_failure:
                recipient.status = CampaignRecipientStatus.failed
                recipient.failed_reason = preflight_failure
                campaign.failed_count += 1
                processed += 1
                continue

        if _is_outreach_campaign(campaign) and _is_manual_snapshot_campaign(campaign):
            try:
                snapshot_row = _audience_snapshot_row_for_person(campaign, str(person.id)) or {}
                retention_customer_id = str(
                    snapshot_row.get("retention_customer_id") or snapshot_row.get("subscriber_id") or ""
                ).strip()
                campaign_source_report = str(_campaign_metadata(campaign).get("source_report") or "").strip() or None
                conversation = conversation_service.resolve_open_conversation_for_channel(
                    db,
                    str(person.id),
                    ChannelType(campaign.channel.value),
                )
                if conversation:
                    existing_metadata = conversation.metadata_ if isinstance(conversation.metadata_, dict) else {}
                    existing_kind = str(existing_metadata.get("campaign_kind") or "").strip().lower()
                    existing_source_report = str(existing_metadata.get("source_report") or "").strip().lower()
                    existing_retention_customer_id = str(existing_metadata.get("retention_customer_id") or "").strip()
                    if (
                        existing_kind != _OUTREACH_KIND
                        or existing_source_report != str(campaign_source_report or "").strip().lower()
                        or (retention_customer_id and existing_retention_customer_id != retention_customer_id)
                    ):
                        conversation = None
                if not conversation:
                    conversation = conversation_service.Conversations.create(
                        db,
                        ConversationCreate(
                            person_id=person.id,
                            subject=subject if campaign.channel == CampaignChannel.email else None,
                            metadata_={
                                "campaign_id": str(campaign.id),
                                "campaign_kind": _OUTREACH_KIND,
                                "source_report": campaign_source_report,
                                "retention_customer_id": retention_customer_id or None,
                                "outreach_owner_person_id": str(campaign.created_by_id) if campaign.created_by_id else None,
                                "preferred_channel_target_id": _outreach_channel_target_id(campaign),
                            },
                        ),
                    )
                    _ensure_conversation_tag(db, conversation_id=conversation.id, tag="Retention")
                    _ensure_conversation_tag(db, conversation_id=conversation.id, tag="Billing Risk")
                else:
                    metadata = conversation.metadata_ if isinstance(conversation.metadata_, dict) else {}
                    metadata["campaign_id"] = str(campaign.id)
                    metadata["campaign_kind"] = _OUTREACH_KIND
                    if campaign_source_report:
                        metadata["source_report"] = campaign_source_report
                    if retention_customer_id:
                        metadata["retention_customer_id"] = retention_customer_id
                    if campaign.created_by_id:
                        metadata["outreach_owner_person_id"] = str(campaign.created_by_id)
                    if _outreach_channel_target_id(campaign):
                        metadata["preferred_channel_target_id"] = _outreach_channel_target_id(campaign)
                    conversation.metadata_ = metadata
                    _ensure_conversation_tag(db, conversation_id=conversation.id, tag="Retention")
                    _ensure_conversation_tag(db, conversation_id=conversation.id, tag="Billing Risk")
                    db.flush()
                message = inbox_outbound_service.send_message(
                    db,
                    InboxSendRequest(
                        conversation_id=conversation.id,
                        channel_type=ChannelType(campaign.channel.value),
                        channel_target_id=coerce_uuid(_outreach_channel_target_id(campaign)),
                        subject=subject,
                        body=body,
                        whatsapp_template_name=campaign.whatsapp_template_name,
                        whatsapp_template_language=campaign.whatsapp_template_language,
                        whatsapp_template_components=_coerce_whatsapp_template_components(
                            type_cast(object, campaign.whatsapp_template_components)
                        ),
                    ),
                    author_id=str(campaign.created_by_id) if campaign.created_by_id else None,
                )
                message_metadata = message.metadata_ if isinstance(message.metadata_, dict) else {}
                message_metadata.update(
                    {
                        "campaign_id": str(campaign.id),
                        "campaign_kind": _OUTREACH_KIND,
                        "campaign_recipient_id": str(recipient.id),
                    }
                )
                message.metadata_ = message_metadata
                if message.status == MessageStatus.failed:
                    recipient.status = CampaignRecipientStatus.failed
                    recipient.failed_reason = "Inbox delivery failed"
                    campaign.failed_count += 1
                else:
                    recipient.status = CampaignRecipientStatus.sent
                    recipient.sent_at = message.sent_at or datetime.now(UTC)
                    campaign.sent_count += 1
                    if message.status in {MessageStatus.delivered, MessageStatus.read}:
                        campaign.delivered_count += 1
                    if message.status == MessageStatus.read:
                        campaign.opened_count += 1
                processed += 1
                continue
            except Exception as exc:
                logger.exception("Outreach inbox send failed for campaign %s recipient %s", campaign.id, recipient.id)
                recipient.status = CampaignRecipientStatus.failed
                recipient.failed_reason = str(exc)
                campaign.failed_count += 1
                processed += 1
                continue

        if campaign.channel == CampaignChannel.whatsapp:
            # Store WhatsApp template name in subject for delivery lookup
            notification = Notification(
                channel=NotificationChannel.whatsapp,
                recipient=recipient.address,
                subject=campaign.whatsapp_template_name,
                body=body,
                connector_config_id=campaign.connector_config_id,
                status=NotificationStatus.queued,
            )
        else:
            notification = Notification(
                channel=NotificationChannel.email,
                recipient=recipient.address,
                subject=subject,
                body=body,
                from_name=campaign.from_name,
                from_email=campaign.from_email,
                reply_to=campaign.reply_to,
                smtp_config_id=campaign.campaign_smtp_config_id,
                status=NotificationStatus.queued,
            )
        db.add(notification)
        db.flush()

        recipient.notification_id = notification.id
        recipient.status = CampaignRecipientStatus.sent
        recipient.sent_at = datetime.now(UTC)
        campaign.sent_count += 1
        processed += 1

    db.commit()
    return processed


# Singleton instances
campaigns = Campaigns()
campaign_steps = CampaignSteps()
campaign_recipients = CampaignRecipients()
