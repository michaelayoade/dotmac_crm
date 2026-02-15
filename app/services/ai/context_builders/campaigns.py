from __future__ import annotations

from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.crm.campaign import Campaign, CampaignRecipient, CampaignStep
from app.models.crm.enums import CampaignRecipientStatus, CampaignStatus
from app.services.ai.redaction import redact_text
from app.services.common import coerce_uuid


def gather_campaign_context(db: Session, params: dict[str, Any]) -> str:
    campaign_id = params.get("campaign_id")
    if not campaign_id:
        raise ValueError("campaign_id is required")

    campaign = db.get(Campaign, coerce_uuid(campaign_id))
    if not campaign:
        raise ValueError("Campaign not found")

    max_steps = min(int(params.get("max_steps", 6)), 20)
    max_failed_recipients = min(int(params.get("max_failed_recipients", 8)), 30)
    max_chars = int(params.get("max_chars", 800))

    lines: list[str] = [
        f"Campaign ID: {str(campaign.id)[:8]}",
        f"Name: {redact_text(campaign.name or '', max_chars=200)}",
        f"Type: {campaign.campaign_type.value}",
        f"Channel: {campaign.channel.value}",
        f"Status: {campaign.status.value if isinstance(campaign.status, CampaignStatus) else str(campaign.status)}",
        f"Scheduled at: {campaign.scheduled_at.isoformat() if campaign.scheduled_at else 'not scheduled'}",
        f"Sending started: {campaign.sending_started_at.isoformat() if campaign.sending_started_at else 'not started'}",
        f"Completed: {campaign.completed_at.isoformat() if campaign.completed_at else 'not completed'}",
        f"Subject: {redact_text(campaign.subject or '', max_chars=200)}",
        f"From: {redact_text((campaign.from_name or '') + ' <' + (campaign.from_email or '') + '>', max_chars=160)}",
        f"Reply-to: {redact_text(campaign.reply_to or '', max_chars=160)}",
        "Counters: "
        + ", ".join(
            [
                f"total={campaign.total_recipients}",
                f"sent={campaign.sent_count}",
                f"delivered={campaign.delivered_count}",
                f"failed={campaign.failed_count}",
                f"opened={campaign.opened_count}",
                f"clicked={campaign.clicked_count}",
            ]
        ),
    ]

    steps = (
        db.query(CampaignStep)
        .filter(CampaignStep.campaign_id == campaign.id)
        .order_by(CampaignStep.step_index.asc())
        .limit(max(1, max_steps))
        .all()
    )
    if steps:
        lines.append("Steps:")
        for s in steps:
            name = redact_text(s.name or f"Step {s.step_index}", max_chars=140)
            subj = redact_text(s.subject or "", max_chars=180)
            delay = int(s.delay_days or 0)
            lines.append(f"  - index={s.step_index} delay_days={delay} name={name} subject={subj}")

    # Aggregate recipient statuses for quick health checks.
    status_counts = (
        db.query(CampaignRecipient.status, func.count(CampaignRecipient.id))
        .filter(CampaignRecipient.campaign_id == campaign.id)
        .group_by(CampaignRecipient.status)
        .all()
    )
    if status_counts:
        formatted = []
        for status, count in status_counts:
            key = status.value if isinstance(status, CampaignRecipientStatus) else str(status)
            formatted.append(f"{key}={int(count)}")
        lines.append("Recipient status counts: " + ", ".join(sorted(formatted)))

    # Sample failed recipients to debug content/targeting issues without leaking addresses.
    failed = (
        db.query(CampaignRecipient)
        .filter(CampaignRecipient.campaign_id == campaign.id)
        .filter(CampaignRecipient.status == CampaignRecipientStatus.failed)
        .order_by(CampaignRecipient.created_at.desc())
        .limit(max(0, max_failed_recipients))
        .all()
    )
    if failed:
        lines.append("Recent failures (sample):")
        for r in failed:
            reason = redact_text(r.failed_reason or "", max_chars=240)
            addr_hint = redact_text((r.address or "")[:40], max_chars=60)
            lines.append(f"  - address_hint={addr_hint} reason={reason}")

    # Include body preview but aggressively capped.
    body_preview = campaign.body_text or campaign.body_html or ""
    if body_preview:
        lines.append("Body preview:")
        lines.append(redact_text(body_preview, max_chars=max_chars))

    return "\n".join([line for line in lines if line.strip()])
