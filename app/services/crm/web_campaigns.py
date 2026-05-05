"""Service helpers for campaign web route form parsing and validation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import String, cast, select
from sqlalchemy.orm import Session

from app.models.connector import ConnectorConfig, ConnectorType
from app.models.crm.campaign_sender import CampaignSender
from app.models.crm.campaign_smtp import CampaignSmtpConfig
from app.models.crm.conversation import Message
from app.models.crm.enums import CampaignChannel, CampaignType, MessageDirection, MessageStatus
from app.models.crm.sales import Pipeline, PipelineStage
from app.models.customer_retention import CustomerRetentionEngagement
from app.models.integration import IntegrationTarget
from app.models.person import PartyStatus, Person
from app.schemas.crm.campaign import CampaignCreate, CampaignStepCreate, CampaignStepUpdate, CampaignUpdate
from app.services.crm.campaign_senders import campaign_senders
from app.services.crm.campaign_smtp_configs import campaign_smtp_configs
from app.services.crm.campaigns import Campaigns
from app.services.crm.campaigns import campaign_recipients as recipients_service
from app.services.crm.campaigns import campaign_steps as steps_service
from app.services.crm.campaigns import campaigns as campaigns_service
from app.services.crm.inbox.inboxes import list_channel_targets

OUTREACH_KIND = "outreach"
OUTREACH_SOURCE_BILLING_RISK = "billing_risk"
OUTREACH_SOURCE_ONLINE_LAST_24H = "online_last_24h"


@dataclass(slots=True)
class CampaignUpsertInput:
    campaign_type: str
    channel: str
    subject: str
    body_html: str
    body_text: str
    campaign_sender_id: str
    campaign_smtp_config_id: str
    whatsapp_connector_id: str
    seg_party_status: list[str]
    seg_regions: list[str]
    seg_pipeline_ids: list[str]
    seg_stage_ids: list[str]
    seg_active_status: str
    seg_created_after: str
    seg_created_before: str
    whatsapp_template_name: str
    whatsapp_template_language: str
    whatsapp_template_components: str


@dataclass(slots=True)
class CampaignUpsertResolution:
    campaign_type: CampaignType
    channel: CampaignChannel
    segment_filter: dict | None
    sender_id_value: str
    smtp_id_value: str
    whatsapp_connector_id_value: str
    whatsapp_template_name: str | None
    whatsapp_template_language: str | None
    whatsapp_template_components: dict | None
    sender: CampaignSender | None
    smtp_profile: CampaignSmtpConfig | None
    whatsapp_connector: ConnectorConfig | None
    errors: list[str]


def _form_str_opt(value: str) -> str | None:
    value_str = (value or "").strip()
    return value_str or None


def _campaign_metadata(campaign) -> dict:
    metadata = getattr(campaign, "metadata_", None)
    return dict(metadata) if isinstance(metadata, dict) else {}


def _campaign_kind(campaign) -> str:
    return str(_campaign_metadata(campaign).get("kind") or "campaign").strip().lower()


def _is_outreach(campaign) -> bool:
    return _campaign_kind(campaign) == OUTREACH_KIND


def _source_report(campaign) -> str:
    return str(_campaign_metadata(campaign).get("source_report") or "").strip()


def _is_billing_risk_outreach(campaign) -> bool:
    return _is_outreach(campaign) and _source_report(campaign) == OUTREACH_SOURCE_BILLING_RISK


def _normalize_retention_outcome(value: str | None) -> str:
    return str(value or "").strip().casefold()


def _is_do_not_reach_out_outcome(value: str | None) -> bool:
    outcome = _normalize_retention_outcome(value)
    return outcome in {"do not reach out", "do_not_reach_out", "do not contact"}


def _is_paid_or_resolved_outcome(value: str | None) -> bool:
    outcome = _normalize_retention_outcome(value)
    return outcome in {"paid", "renewing", "resolved"}


def _is_promised_outcome(value: str | None) -> bool:
    outcome = _normalize_retention_outcome(value)
    return "promise" in outcome or "promised" in outcome


def outreach_channel_target_options(db: Session) -> dict[str, list[dict[str, str | bool]]]:
    if not callable(getattr(db, "query", None)):
        return {"email": [], "whatsapp": []}

    def _serialize_target(target: dict) -> dict[str, str | bool]:
        return {
            "target_id": str(target.get("target_id") or "").strip(),
            "name": str(target.get("name") or "").strip(),
            "channel": str(target.get("channel") or "").strip(),
            "kind": str(target.get("kind") or "").strip(),
            "is_active": bool(target.get("is_active")),
            "connector_active": bool(target.get("connector_active")),
        }

    email_targets = [
        _serialize_target(target)
        for target in list_channel_targets(db, ConnectorType.email)
        if target.get("is_active") and target.get("connector_active")
    ]
    whatsapp_targets = [
        _serialize_target(target)
        for target in list_channel_targets(db, ConnectorType.whatsapp)
        if target.get("is_active") and target.get("connector_active")
    ]
    return {
        "email": email_targets,
        "whatsapp": whatsapp_targets,
    }


def _resolve_outreach_channel_target(
    db: Session,
    *,
    channel: CampaignChannel,
    channel_target_id: str | None,
) -> tuple[str | None, str | None]:
    target_id_value = str(channel_target_id or "").strip()
    if not target_id_value:
        return None, None
    target = db.get(IntegrationTarget, UUID(target_id_value))
    if not target or not target.is_active:
        raise HTTPException(status_code=400, detail="Selected inbox target is unavailable.")
    connector = target.connector_config
    if not connector or not connector.is_active:
        raise HTTPException(status_code=400, detail="Selected inbox connector is inactive.")
    expected = ConnectorType.whatsapp if channel == CampaignChannel.whatsapp else ConnectorType.email
    if connector.connector_type != expected:
        raise HTTPException(status_code=400, detail="Selected inbox target does not match the chosen channel.")
    return str(target.id), target.name or connector.name


def _build_segment_filter(
    party_statuses: list[str],
    regions: list[str],
    pipeline_ids: list[str],
    stage_ids: list[str],
    active_status: str | None,
    created_after: str | None,
    created_before: str | None,
) -> dict | None:
    sf: dict = {}
    if party_statuses:
        sf["party_status"] = [s for s in party_statuses if s]
    if regions:
        sf["regions"] = [r for r in regions if r]
    if pipeline_ids:
        sf["pipeline_ids"] = [pid for pid in pipeline_ids if pid]
    if stage_ids:
        sf["stage_ids"] = [sid for sid in stage_ids if sid]
    if active_status and active_status.strip():
        sf["active_status"] = active_status.strip()
    if created_after and created_after.strip():
        sf["created_after"] = created_after.strip()
    if created_before and created_before.strip():
        sf["created_before"] = created_before.strip()
    return sf or None


def resolve_campaign_upsert(db: Session, *, form: CampaignUpsertInput) -> CampaignUpsertResolution:
    try:
        campaign_type = CampaignType(form.campaign_type)
    except ValueError:
        campaign_type = CampaignType.one_time
    try:
        selected_channel = CampaignChannel(form.channel)
    except ValueError:
        selected_channel = CampaignChannel.email

    segment_filter = _build_segment_filter(
        form.seg_party_status,
        form.seg_regions,
        form.seg_pipeline_ids,
        form.seg_stage_ids,
        form.seg_active_status,
        form.seg_created_after,
        form.seg_created_before,
    )

    wa_template_name = _form_str_opt(form.whatsapp_template_name)
    wa_template_lang = _form_str_opt(form.whatsapp_template_language)
    wa_template_components: dict | None = None
    if (form.whatsapp_template_components or "").strip():
        try:
            wa_template_components = json.loads(form.whatsapp_template_components)
        except (json.JSONDecodeError, TypeError):
            wa_template_components = None

    errors: list[str] = []
    sender = None
    smtp_profile = None
    whatsapp_connector = None
    sender_id_value = (form.campaign_sender_id or "").strip()
    smtp_id_value = (form.campaign_smtp_config_id or "").strip()
    whatsapp_connector_id_value = (form.whatsapp_connector_id or "").strip()

    if selected_channel == CampaignChannel.email:
        if sender_id_value:
            try:
                sender = campaign_senders.get(db, sender_id_value)
            except HTTPException as exc:
                errors.append(str(exc.detail))
        else:
            errors.append("Campaign sender is required.")

        if sender and not sender.is_active:
            errors.append("Selected campaign sender is inactive.")

        if smtp_id_value:
            try:
                smtp_profile = campaign_smtp_configs.get(db, smtp_id_value)
            except HTTPException as exc:
                errors.append(str(exc.detail))
        else:
            errors.append("Campaign SMTP profile is required.")

        if smtp_profile and not smtp_profile.is_active:
            errors.append("Selected campaign SMTP profile is inactive.")
    else:
        if whatsapp_connector_id_value:
            try:
                whatsapp_connector = db.get(ConnectorConfig, UUID(whatsapp_connector_id_value))
            except ValueError:
                errors.append("Invalid WhatsApp connector.")
                whatsapp_connector = None
            if not whatsapp_connector and "Invalid WhatsApp connector." not in errors:
                errors.append("WhatsApp connector not found.")
            elif whatsapp_connector and whatsapp_connector.connector_type != ConnectorType.whatsapp:
                errors.append("Selected connector is not a WhatsApp connector.")
            elif whatsapp_connector and not whatsapp_connector.is_active:
                errors.append("Selected WhatsApp connector is inactive.")
        else:
            errors.append("WhatsApp connector is required.")

    return CampaignUpsertResolution(
        campaign_type=campaign_type,
        channel=selected_channel,
        segment_filter=segment_filter,
        sender_id_value=sender_id_value,
        smtp_id_value=smtp_id_value,
        whatsapp_connector_id_value=whatsapp_connector_id_value,
        whatsapp_template_name=wa_template_name,
        whatsapp_template_language=wa_template_lang,
        whatsapp_template_components=wa_template_components,
        sender=sender,
        smtp_profile=smtp_profile,
        whatsapp_connector=whatsapp_connector,
        errors=errors,
    )


def build_campaign_form_stub(
    *,
    campaign_id: str | None,
    name: str,
    subject: str,
    body_html: str,
    body_text: str,
    resolved: CampaignUpsertResolution,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=campaign_id,
        name=name,
        campaign_type=resolved.campaign_type,
        channel=resolved.channel,
        subject=subject or None,
        body_html=body_html or None,
        body_text=body_text or None,
        from_name=None,
        from_email=None,
        reply_to=None,
        segment_filter=resolved.segment_filter,
        campaign_sender_id=resolved.sender_id_value or None,
        campaign_smtp_config_id=resolved.smtp_id_value or None,
        connector_config_id=resolved.whatsapp_connector_id_value or None,
        whatsapp_template_name=resolved.whatsapp_template_name,
        whatsapp_template_language=resolved.whatsapp_template_language,
        whatsapp_template_components=resolved.whatsapp_template_components,
    )


def build_campaign_create_payload(
    *, name: str, subject: str, body_html: str, body_text: str, resolved: CampaignUpsertResolution
) -> CampaignCreate:
    sender = resolved.sender
    smtp_profile = resolved.smtp_profile
    whatsapp_connector = resolved.whatsapp_connector
    selected_channel = resolved.channel
    return CampaignCreate(
        name=name,
        campaign_type=resolved.campaign_type,
        channel=selected_channel,
        subject=_form_str_opt(subject),
        body_html=_form_str_opt(body_html),
        body_text=_form_str_opt(body_text),
        campaign_sender_id=sender.id if selected_channel == CampaignChannel.email and sender else None,
        campaign_smtp_config_id=smtp_profile.id if selected_channel == CampaignChannel.email and smtp_profile else None,
        connector_config_id=whatsapp_connector.id
        if selected_channel == CampaignChannel.whatsapp and whatsapp_connector
        else None,
        from_name=sender.from_name if selected_channel == CampaignChannel.email and sender else None,
        from_email=sender.from_email if selected_channel == CampaignChannel.email and sender else None,
        reply_to=sender.reply_to if selected_channel == CampaignChannel.email and sender else None,
        whatsapp_template_name=resolved.whatsapp_template_name
        if selected_channel == CampaignChannel.whatsapp
        else None,
        whatsapp_template_language=resolved.whatsapp_template_language
        if selected_channel == CampaignChannel.whatsapp
        else None,
        whatsapp_template_components=resolved.whatsapp_template_components
        if selected_channel == CampaignChannel.whatsapp
        else None,
        segment_filter=resolved.segment_filter,
    )


def build_campaign_update_payload(
    *, name: str, subject: str, body_html: str, body_text: str, resolved: CampaignUpsertResolution
) -> CampaignUpdate:
    sender = resolved.sender
    smtp_profile = resolved.smtp_profile
    whatsapp_connector = resolved.whatsapp_connector
    selected_channel = resolved.channel
    return CampaignUpdate(
        name=name,
        campaign_type=resolved.campaign_type,
        channel=selected_channel,
        segment_filter=resolved.segment_filter,
        subject=_form_str_opt(subject),
        body_html=_form_str_opt(body_html),
        body_text=_form_str_opt(body_text),
        campaign_sender_id=sender.id if selected_channel == CampaignChannel.email and sender else None,
        campaign_smtp_config_id=smtp_profile.id if selected_channel == CampaignChannel.email and smtp_profile else None,
        connector_config_id=whatsapp_connector.id
        if selected_channel == CampaignChannel.whatsapp and whatsapp_connector
        else None,
        from_name=sender.from_name if selected_channel == CampaignChannel.email and sender else None,
        from_email=sender.from_email if selected_channel == CampaignChannel.email and sender else None,
        reply_to=sender.reply_to if selected_channel == CampaignChannel.email and sender else None,
        whatsapp_template_name=resolved.whatsapp_template_name
        if selected_channel == CampaignChannel.whatsapp
        else None,
        whatsapp_template_language=resolved.whatsapp_template_language
        if selected_channel == CampaignChannel.whatsapp
        else None,
        whatsapp_template_components=resolved.whatsapp_template_components
        if selected_channel == CampaignChannel.whatsapp
        else None,
    )


def campaign_detail_page_data(db: Session, *, campaign_id: str) -> dict:
    campaign = campaigns_service.get(db, campaign_id)
    stats = Campaigns.analytics(db, campaign_id)
    recipients = recipients_service.list(db, campaign_id, limit=20, offset=0)
    person_ids = [r.person_id for r in recipients]
    persons = db.query(Person).filter(Person.id.in_(person_ids)).all() if person_ids else []
    person_map = {str(person.id): person for person in persons}
    steps = steps_service.list(db, campaign_id) if campaign.campaign_type == CampaignType.nurture else []
    metadata = _campaign_metadata(campaign)
    audience_snapshot = metadata.get("audience_snapshot")
    audience_snapshot_rows = audience_snapshot if isinstance(audience_snapshot, list) else []
    follow_up_hint = None
    if _is_billing_risk_outreach(campaign):
        follow_up_hint = summarize_billing_risk_follow_up_candidates(
            db,
            snapshot_rows=audience_snapshot_rows,
        )
    inbox_metrics = outreach_inbox_metrics(db, campaign_id=campaign_id) if _is_outreach(campaign) else None
    return {
        "campaign": campaign,
        "stats": stats,
        "recipients": recipients,
        "person_map": person_map,
        "steps": steps,
        "campaign_kind": _campaign_kind(campaign),
        "is_outreach": _is_outreach(campaign),
        "campaign_source": _source_report(campaign),
        "follow_up_hint": follow_up_hint,
        "outreach_channel_target_name": str(metadata.get("channel_target_name") or "").strip(),
        "whatsapp_validation_summary": (
            metadata.get("whatsapp_validation_summary")
            if isinstance(metadata.get("whatsapp_validation_summary"), dict)
            else None
        ),
        "outreach_inbox_metrics": inbox_metrics,
    }


def campaign_recipients_table_data(
    db: Session,
    *,
    campaign_id: str,
    status: str | None,
    offset: int,
) -> dict:
    campaign = campaigns_service.get(db, campaign_id)
    recipients = recipients_service.list(db, campaign_id, status=status, limit=50, offset=offset)
    person_ids = [recipient.person_id for recipient in recipients]
    persons = db.query(Person).filter(Person.id.in_(person_ids)).all() if person_ids else []
    person_map = {str(person.id): person for person in persons}
    retention_by_person_id: dict[str, dict[str, str]] = {}
    retention_profile_by_person_id: dict[str, str] = {}
    if _is_billing_risk_outreach(campaign):
        snapshot = _campaign_metadata(campaign).get("audience_snapshot")
        snapshot_rows = snapshot if isinstance(snapshot, list) else []
        retention_customer_by_person_id = {
            str(row.get("person_id") or ""): str(
                row.get("retention_customer_id") or row.get("subscriber_id") or ""
            ).strip()
            for row in snapshot_rows
            if isinstance(row, dict)
        }
        retention_profile_by_person_id = {
            person_id: f"/admin/customer-retention/{customer_id}"
            for person_id, customer_id in retention_customer_by_person_id.items()
            if customer_id
        }
        retention_customer_ids = [
            retention_customer_by_person_id.get(str(recipient.person_id), "")
            for recipient in recipients
            if retention_customer_by_person_id.get(str(recipient.person_id), "")
        ]
        latest_engagements = _latest_retention_engagement_by_customer_id(db, customer_ids=retention_customer_ids)
        for recipient in recipients:
            person_key = str(recipient.person_id)
            customer_id = retention_customer_by_person_id.get(person_key, "")
            engagement = latest_engagements.get(customer_id)
            if not engagement:
                continue
            retention_by_person_id[person_key] = {
                "outcome": engagement.outcome,
                "follow_up_date": engagement.follow_up_date.isoformat() if engagement.follow_up_date else "",
            }
    return {
        "recipients": recipients,
        "person_map": person_map,
        "campaign_id": campaign_id,
        "retention_by_person_id": retention_by_person_id,
        "retention_profile_by_person_id": retention_profile_by_person_id,
    }


def campaign_preview_audience_data(db: Session, *, campaign_id: str) -> dict:
    campaign = campaigns_service.get(db, campaign_id)
    is_manual_snapshot = (
        str(_campaign_metadata(campaign).get("audience_mode") or "").strip().lower() == "manual_snapshot"
    )
    if is_manual_snapshot:
        audience = Campaigns.preview_seeded_audience(db, campaign_id=campaign_id)
    else:
        audience = Campaigns.preview_audience(db, campaign.segment_filter, campaign.channel)
    audience_address_label = "Phone" if campaign.channel == CampaignChannel.whatsapp else "Email"
    return {
        "audience": audience,
        "campaign": campaign,
        "audience_address_label": audience_address_label,
        "is_manual_snapshot": is_manual_snapshot,
    }


def campaign_preview_page_data(db: Session, *, campaign_id: str) -> dict:
    return {
        "campaign": campaigns_service.get(db, campaign_id),
    }


def campaign_whatsapp_templates_payload(db: Session, *, connector_id: str | None) -> tuple[dict, int]:
    from app.services.crm.inbox.whatsapp_templates import list_whatsapp_templates

    if not connector_id:
        return {"templates": [], "error": "Connector is required"}, 400

    try:
        templates_payload = list_whatsapp_templates(db, connector_config_id=connector_id)
    except HTTPException as exc:
        return {"templates": [], "error": str(exc.detail)}, 400

    approved = [template for template in templates_payload if str(template.get("status", "")).lower() == "approved"]
    return {"templates": approved}, 200


def build_campaign_step_create_payload(
    *,
    campaign_id: str,
    name: str,
    subject: str,
    body_html: str,
    body_text: str,
    delay_days: int,
    step_index: int,
) -> CampaignStepCreate:
    return CampaignStepCreate(
        campaign_id=UUID(campaign_id),
        step_index=step_index,
        name=_form_str_opt(name),
        subject=_form_str_opt(subject),
        body_html=_form_str_opt(body_html),
        body_text=_form_str_opt(body_text),
        delay_days=delay_days,
    )


def build_campaign_step_update_payload(
    *,
    name: str,
    subject: str,
    body_html: str,
    body_text: str,
    delay_days: int,
    step_index: int,
) -> CampaignStepUpdate:
    return CampaignStepUpdate(
        step_index=step_index,
        name=_form_str_opt(name),
        subject=_form_str_opt(subject),
        body_html=_form_str_opt(body_html),
        body_text=_form_str_opt(body_text),
        delay_days=delay_days,
    )


def campaign_steps_page_data(db: Session, *, campaign_id: str) -> dict:
    campaign = campaigns_service.get(db, campaign_id)
    steps = steps_service.list(db, campaign_id)
    return {
        "campaign": campaign,
        "steps": steps,
    }


def campaign_list_page_data(
    db: Session,
    *,
    status: str | None,
    search: str | None,
    order_by: str,
    order_dir: str,
) -> dict:
    normalized_order_by = order_by if order_by in {"created_at", "updated_at", "name"} else "created_at"
    normalized_order_dir = order_dir if order_dir in {"asc", "desc"} else "desc"
    campaigns = campaigns_service.list(
        db,
        status=status,
        search=search,
        order_by=normalized_order_by,
        order_dir=normalized_order_dir,
    )
    status_counts = Campaigns.count_by_status(db)
    campaign_rows = []
    for campaign in campaigns:
        campaign_rows.append(
            {
                "campaign": campaign,
                "kind": _campaign_kind(campaign),
                "is_outreach": _is_outreach(campaign),
                "source_report": str(_campaign_metadata(campaign).get("source_report") or "").strip(),
            }
        )
    return {
        "campaigns": campaigns,
        "campaign_rows": campaign_rows,
        "status_counts": status_counts,
        "filter_status": status or "",
        "search": search or "",
        "order_by": normalized_order_by,
        "order_dir": normalized_order_dir,
    }


def campaign_form_page_data(
    db: Session,
    *,
    campaign,
    errors: list[str],
    region_options: list[str],
) -> dict:
    pipelines = db.query(Pipeline).filter(Pipeline.is_active.is_(True)).order_by(Pipeline.name.asc()).limit(200).all()
    pipeline_stages = (
        db.query(PipelineStage)
        .join(Pipeline, PipelineStage.pipeline_id == Pipeline.id)
        .filter(PipelineStage.is_active.is_(True))
        .filter(Pipeline.is_active.is_(True))
        .order_by(Pipeline.name.asc(), PipelineStage.order_index.asc(), PipelineStage.name.asc())
        .limit(500)
        .all()
    )
    campaign_senders_list = campaign_senders.list(
        db=db,
        is_active=None,
        order_by="name",
        order_dir="asc",
        limit=500,
        offset=0,
    )
    campaign_smtp_profiles = campaign_smtp_configs.list(
        db=db,
        is_active=None,
        order_by="name",
        order_dir="asc",
        limit=500,
        offset=0,
    )
    whatsapp_connectors = (
        db.query(ConnectorConfig)
        .filter(ConnectorConfig.connector_type == ConnectorType.whatsapp)
        .order_by(ConnectorConfig.name.asc())
        .limit(500)
        .all()
    )
    return {
        "campaign": campaign,
        "campaign_types": CampaignType,
        "campaign_channels": CampaignChannel,
        "party_statuses": PartyStatus,
        "region_options": region_options,
        "pipelines": pipelines,
        "pipeline_stages": pipeline_stages,
        "campaign_senders": campaign_senders_list,
        "campaign_smtp_profiles": campaign_smtp_profiles,
        "whatsapp_connectors": whatsapp_connectors,
        "errors": errors,
    }


def schedule_campaign_from_form(db: Session, *, campaign_id: str, scheduled_at: str) -> bool:
    if not scheduled_at:
        return False
    try:
        scheduled_dt = datetime.fromisoformat(scheduled_at)
    except ValueError:
        return False
    campaigns_service.schedule(db, campaign_id, scheduled_dt)
    return True


def send_campaign_now(db: Session, *, campaign_id: str) -> None:
    campaigns_service.send_now(db, campaign_id)


def cancel_campaign(db: Session, *, campaign_id: str) -> None:
    campaigns_service.cancel(db, campaign_id)


def delete_campaign(db: Session, *, campaign_id: str) -> None:
    campaigns_service.delete(db, campaign_id)


def create_campaign_step(
    db: Session,
    *,
    campaign_id: str,
    name: str,
    subject: str,
    body_html: str,
    body_text: str,
    delay_days: int,
    step_index: int,
) -> None:
    payload = build_campaign_step_create_payload(
        campaign_id=campaign_id,
        name=name,
        subject=subject,
        body_html=body_html,
        body_text=body_text,
        delay_days=delay_days,
        step_index=step_index,
    )
    steps_service.create(db, payload)


def update_campaign_step(
    db: Session,
    *,
    step_id: str,
    name: str,
    subject: str,
    body_html: str,
    body_text: str,
    delay_days: int,
    step_index: int,
) -> None:
    payload = build_campaign_step_update_payload(
        name=name,
        subject=subject,
        body_html=body_html,
        body_text=body_text,
        delay_days=delay_days,
        step_index=step_index,
    )
    steps_service.update(db, step_id, payload)


def delete_campaign_step(db: Session, *, step_id: str) -> None:
    steps_service.delete(db, step_id)


def create_campaign(
    db: Session,
    *,
    name: str,
    subject: str,
    body_html: str,
    body_text: str,
    resolved: CampaignUpsertResolution,
    created_by_id: str | None,
):
    payload = build_campaign_create_payload(
        name=name,
        subject=subject,
        body_html=body_html,
        body_text=body_text,
        resolved=resolved,
    )
    return campaigns_service.create(db, payload, created_by_id=created_by_id)


def create_retention_outreach_campaign(
    db: Session,
    *,
    name: str,
    channel: str,
    channel_target_id: str | None,
    subscriber_ids: list[str],
    retention_customer_ids: list[str] | None,
    created_by_id: str | None,
    source_report: str,
    source_filters: dict | None = None,
):
    try:
        selected_channel = CampaignChannel(channel)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid outreach channel")

    clean_name = (name or "").strip() or "Retention Outreach"
    resolved_target_id, resolved_target_name = _resolve_outreach_channel_target(
        db,
        channel=selected_channel,
        channel_target_id=channel_target_id,
    )
    metadata = {
        "kind": OUTREACH_KIND,
        "source_report": source_report,
        "audience_mode": "manual_snapshot",
        "source_filters": source_filters or {},
        "channel_target_id": resolved_target_id,
        "channel_target_name": resolved_target_name,
    }
    payload = CampaignCreate(
        name=clean_name,
        campaign_type=CampaignType.one_time,
        channel=selected_channel,
        metadata_=metadata,
    )
    campaign = campaigns_service.create(db, payload, created_by_id=created_by_id)
    if source_report == OUTREACH_SOURCE_ONLINE_LAST_24H:
        from app.services.subscriber_notifications import campaign_template_for_online_last_24h

        campaign_template = campaign_template_for_online_last_24h(db, channel=selected_channel.value)
        campaign.subject = campaign_template.subject
        campaign.body_text = campaign_template.body_text
        campaign.body_html = campaign_template.body_html
    snapshot_context_by_subscriber_id: dict[str, dict] = {}
    rendered_online_last_24h_messages: dict[str, object] = {}
    if source_report == OUTREACH_SOURCE_ONLINE_LAST_24H:
        from app.services.subscriber_notifications import render_online_last_24h_campaign_message

        for subscriber_id in subscriber_ids:
            normalized_subscriber_id = str(subscriber_id).strip()
            if not normalized_subscriber_id:
                continue
            try:
                rendered_online_last_24h_messages[normalized_subscriber_id] = render_online_last_24h_campaign_message(
                    db,
                    subscriber_id=UUID(normalized_subscriber_id),
                    channel=selected_channel.value,
                )
            except Exception:
                continue
    for index, subscriber_id in enumerate(subscriber_ids):
        normalized_subscriber_id = str(subscriber_id).strip()
        if not normalized_subscriber_id:
            continue
        retention_customer_id = ""
        if retention_customer_ids and index < len(retention_customer_ids):
            retention_customer_id = str(retention_customer_ids[index] or "").strip()
        snapshot_context_by_subscriber_id[normalized_subscriber_id] = {
            "retention_customer_id": retention_customer_id or normalized_subscriber_id,
        }
        rendered_message = rendered_online_last_24h_messages.get(normalized_subscriber_id)
        if rendered_message is not None:
            snapshot_context_by_subscriber_id[normalized_subscriber_id].update(
                {
                    "notification_template_key": getattr(rendered_message, "template_key", ""),
                    "campaign_subject": getattr(rendered_message, "subject", ""),
                    "campaign_body_text": getattr(rendered_message, "body_text", ""),
                    "campaign_body_html": getattr(rendered_message, "body_html", None) or "",
                }
            )
    Campaigns.seed_manual_snapshot_recipients(
        db,
        campaign_id=str(campaign.id),
        subscriber_ids=subscriber_ids,
        snapshot_context_by_subscriber_id=snapshot_context_by_subscriber_id,
    )
    return campaign


def create_billing_risk_outreach_campaign(
    db: Session,
    *,
    name: str,
    channel: str,
    channel_target_id: str | None,
    subscriber_ids: list[str],
    retention_customer_ids: list[str] | None,
    created_by_id: str | None,
    source_filters: dict | None = None,
):
    return create_retention_outreach_campaign(
        db,
        name=(name or "").strip() or "Billing Risk Outreach",
        channel=channel,
        channel_target_id=channel_target_id,
        subscriber_ids=subscriber_ids,
        retention_customer_ids=retention_customer_ids,
        created_by_id=created_by_id,
        source_report=OUTREACH_SOURCE_BILLING_RISK,
        source_filters=source_filters,
    )


def create_online_last_24h_outreach_campaign(
    db: Session,
    *,
    name: str,
    channel: str,
    channel_target_id: str | None,
    subscriber_ids: list[str],
    created_by_id: str | None,
    source_filters: dict | None = None,
):
    return create_retention_outreach_campaign(
        db,
        name=(name or "").strip() or "Online Last 24H Outreach",
        channel=channel,
        channel_target_id=channel_target_id,
        subscriber_ids=subscriber_ids,
        retention_customer_ids=subscriber_ids,
        created_by_id=created_by_id,
        source_report=OUTREACH_SOURCE_ONLINE_LAST_24H,
        source_filters=source_filters,
    )


def _latest_retention_engagement_by_customer_id(
    db: Session,
    *,
    customer_ids: list[str],
) -> dict[str, CustomerRetentionEngagement]:
    normalized_ids = [str(customer_id).strip() for customer_id in customer_ids if str(customer_id).strip()]
    if not normalized_ids:
        return {}
    rows = db.execute(
        select(CustomerRetentionEngagement)
        .where(
            CustomerRetentionEngagement.customer_external_id.in_(normalized_ids),
            CustomerRetentionEngagement.is_active.is_(True),
        )
        .order_by(CustomerRetentionEngagement.created_at.desc())
    ).scalars()
    latest_by_customer_id: dict[str, CustomerRetentionEngagement] = {}
    for row in rows:
        if row.customer_external_id not in latest_by_customer_id:
            latest_by_customer_id[row.customer_external_id] = row
    return latest_by_customer_id


def summarize_billing_risk_follow_up_candidates(
    db: Session,
    *,
    snapshot_rows: list[dict],
) -> dict[str, int]:
    today = datetime.now(UTC).date()
    customer_ids = [
        str(row.get("retention_customer_id") or row.get("subscriber_id") or "").strip()
        for row in snapshot_rows
        if isinstance(row, dict)
    ]
    latest_engagements = _latest_retention_engagement_by_customer_id(db, customer_ids=customer_ids)
    suppressed = 0
    eligible = 0
    for row in snapshot_rows:
        if not isinstance(row, dict):
            continue
        customer_id = str(row.get("retention_customer_id") or row.get("subscriber_id") or "").strip()
        engagement = latest_engagements.get(customer_id)
        follow_up_date = engagement.follow_up_date if engagement else None
        outcome = engagement.outcome if engagement else ""
        if (
            _is_do_not_reach_out_outcome(outcome)
            or _is_paid_or_resolved_outcome(outcome)
            or (
                follow_up_date
                and follow_up_date >= today
                and (
                    _is_promised_outcome(outcome)
                    or "follow-up" in _normalize_retention_outcome(outcome)
                    or "follow up" in _normalize_retention_outcome(outcome)
                )
            )
        ):
            suppressed += 1
        else:
            eligible += 1
    return {"total": len(snapshot_rows), "eligible": eligible, "suppressed": suppressed}


def outreach_inbox_metrics(
    db: Session,
    *,
    campaign_id: str,
) -> dict[str, int]:
    outbound_messages = (
        db.query(Message)
        .filter(
            Message.direction == MessageDirection.outbound,
            cast(Message.metadata_["campaign_id"], String) == str(campaign_id),
        )
        .all()
    )
    conversation_ids = sorted({message.conversation_id for message in outbound_messages if message.conversation_id})
    delivered_messages = sum(
        1 for message in outbound_messages if message.status in {MessageStatus.delivered, MessageStatus.read}
    )
    read_messages = sum(1 for message in outbound_messages if message.status == MessageStatus.read)
    inbound_replies = []
    if conversation_ids:
        inbound_replies = (
            db.query(Message)
            .filter(
                Message.conversation_id.in_(conversation_ids),
                Message.direction == MessageDirection.inbound,
            )
            .all()
        )
    return {
        "outbound_messages": len(outbound_messages),
        "delivered_messages": delivered_messages,
        "read_messages": read_messages,
        "replied_conversations": len(
            {message.conversation_id for message in inbound_replies if message.conversation_id}
        ),
        "inbound_replies": len(inbound_replies),
    }


def create_billing_risk_follow_up_campaign(
    db: Session,
    *,
    source_campaign_id: str,
    name: str | None,
    exclude_promised: bool = True,
    exclude_future_followups: bool = True,
    created_by_id: str | None,
):
    source_campaign = campaigns_service.get(db, source_campaign_id)
    if not _is_billing_risk_outreach(source_campaign):
        raise HTTPException(status_code=400, detail="Follow-up waves are only supported for billing risk outreach.")

    source_metadata = _campaign_metadata(source_campaign)
    audience_snapshot = source_metadata.get("audience_snapshot")
    snapshot_rows = audience_snapshot if isinstance(audience_snapshot, list) else []
    if not snapshot_rows:
        raise HTTPException(status_code=400, detail="Source outreach has no audience snapshot.")

    today = datetime.now(UTC).date()
    customer_ids = [
        str(row.get("retention_customer_id") or row.get("subscriber_id") or "").strip()
        for row in snapshot_rows
        if isinstance(row, dict)
    ]
    latest_engagements = _latest_retention_engagement_by_customer_id(db, customer_ids=customer_ids)

    filtered_subscriber_ids: list[str] = []
    filtered_retention_customer_ids: list[str] = []
    suppressed_count = 0
    for row in snapshot_rows:
        if not isinstance(row, dict):
            continue
        subscriber_id = str(row.get("subscriber_id") or "").strip()
        if not subscriber_id:
            continue
        customer_id = str(row.get("retention_customer_id") or subscriber_id).strip()
        engagement = latest_engagements.get(customer_id)
        follow_up_date = engagement.follow_up_date if engagement else None
        outcome = engagement.outcome if engagement else ""

        suppressed = False
        if _is_do_not_reach_out_outcome(outcome) or _is_paid_or_resolved_outcome(outcome):
            suppressed = True
        if exclude_future_followups and follow_up_date and follow_up_date >= today:
            suppressed = True
        if exclude_promised and follow_up_date and follow_up_date >= today and _is_promised_outcome(outcome):
            suppressed = True

        if suppressed:
            suppressed_count += 1
            continue
        filtered_subscriber_ids.append(subscriber_id)
        filtered_retention_customer_ids.append(customer_id)

    if not filtered_subscriber_ids:
        raise HTTPException(status_code=400, detail="No eligible recipients remain after follow-up exclusions.")

    source_filters = source_metadata.get("source_filters")
    filters_payload = source_filters if isinstance(source_filters, dict) else {}
    follow_up_name = (name or "").strip() or f"{source_campaign.name} Follow-up"
    follow_up_campaign = create_billing_risk_outreach_campaign(
        db,
        name=follow_up_name,
        channel=source_campaign.channel.value,
        channel_target_id=str(source_metadata.get("channel_target_id") or "").strip() or None,
        subscriber_ids=filtered_subscriber_ids,
        retention_customer_ids=filtered_retention_customer_ids,
        created_by_id=created_by_id,
        source_filters={
            **filters_payload,
            "source_campaign_id": str(source_campaign.id),
            "suppressed_count": suppressed_count,
            "follow_up_created_at": datetime.now(UTC).isoformat(),
        },
    )
    update_payload = CampaignUpdate(
        subject=source_campaign.subject,
        body_html=source_campaign.body_html,
        body_text=source_campaign.body_text,
        campaign_sender_id=source_campaign.campaign_sender_id,
        campaign_smtp_config_id=source_campaign.campaign_smtp_config_id,
        connector_config_id=source_campaign.connector_config_id,
        from_name=source_campaign.from_name,
        from_email=source_campaign.from_email,
        reply_to=source_campaign.reply_to,
        whatsapp_template_name=source_campaign.whatsapp_template_name,
        whatsapp_template_language=source_campaign.whatsapp_template_language,
        whatsapp_template_components=source_campaign.whatsapp_template_components,
    )
    campaigns_service.update(db, str(follow_up_campaign.id), update_payload)
    return follow_up_campaign


def update_campaign(
    db: Session,
    *,
    campaign_id: str,
    name: str,
    subject: str,
    body_html: str,
    body_text: str,
    resolved: CampaignUpsertResolution,
) -> None:
    payload = build_campaign_update_payload(
        name=name,
        subject=subject,
        body_html=body_html,
        body_text=body_text,
        resolved=resolved,
    )
    campaigns_service.update(db, campaign_id, payload)


def get_campaign(db: Session, *, campaign_id: str):
    return campaigns_service.get(db, campaign_id)
