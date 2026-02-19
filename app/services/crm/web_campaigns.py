"""Service helpers for campaign web route form parsing and validation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.connector import ConnectorConfig, ConnectorType
from app.models.crm.enums import CampaignChannel, CampaignType
from app.schemas.crm.campaign import CampaignCreate, CampaignUpdate
from app.services.crm.campaign_senders import campaign_senders
from app.services.crm.campaign_smtp_configs import campaign_smtp_configs


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
    sender: object | None
    smtp_profile: object | None
    whatsapp_connector: ConnectorConfig | None
    errors: list[str]


def _form_str_opt(value: str) -> str | None:
    value_str = (value or "").strip()
    return value_str or None


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
