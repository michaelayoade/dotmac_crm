"""Campaign management service.

Provides CRUD for campaigns, nurture steps, and recipients.
Handles audience segmentation, variable substitution, and
campaign lifecycle (draft -> scheduled -> sending -> completed).
"""

import contextlib
import logging
import re
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import String, cast, false, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models.connector import ConnectorConfig, ConnectorType
from app.models.crm.campaign import Campaign, CampaignRecipient, CampaignStep
from app.models.crm.campaign_smtp import CampaignSmtpConfig
from app.models.crm.enums import CampaignChannel, CampaignRecipientStatus, CampaignStatus, CampaignType
from app.models.crm.sales import Lead
from app.models.notification import Notification, NotificationChannel, NotificationStatus
from app.models.person import PartyStatus, Person
from app.models.subscriber import Organization
from app.services.common import apply_ordering, apply_pagination, coerce_uuid, validate_enum
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)

# Variable pattern for template substitution
_VAR_PATTERN = re.compile(r"\{\{(\w+)\}\}")


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
    if len(digits) < 7:  # E.164 minimum: country code + subscriber number
        return None
    return f"+{digits}"


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
                address = _normalize_whatsapp_address(person.phone)
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
                    "address": _normalize_whatsapp_address(p.phone) if channel == CampaignChannel.whatsapp else p.email,
                }
                for p in sample
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

        return {
            "campaign_id": str(campaign.id),
            "status": campaign.status.value,
            "total_recipients": campaign.total_recipients,
            "sent_count": campaign.sent_count,
            "delivered_count": campaign.delivered_count,
            "failed_count": campaign.failed_count,
            "opened_count": campaign.opened_count,
            "clicked_count": campaign.clicked_count,
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
    persons = db.query(Person).filter(Person.id.in_(person_ids)).all()
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
