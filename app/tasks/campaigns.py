import logging
from datetime import UTC, datetime, timedelta

from app.celery_app import celery_app
from app.db import SessionLocal
from app.models.crm.campaign import Campaign, CampaignRecipient, CampaignStep
from app.models.crm.enums import CampaignRecipientStatus, CampaignStatus, CampaignType
from app.services.crm.campaigns import send_campaign_batch

logger = logging.getLogger(__name__)

BATCH_SIZE = 50


@celery_app.task(name="app.tasks.campaigns.execute_campaign")
def execute_campaign(campaign_id: str):
    """Process a batch of campaign recipients.

    Sends BATCH_SIZE recipients per invocation, then re-queues itself
    if more are pending. Actual SMTP delivery is handled by the
    existing deliver_notification_queue task.
    """
    session = SessionLocal()
    try:
        processed = send_campaign_batch(session, campaign_id, batch_size=BATCH_SIZE)
        if processed > 0:
            # More recipients may be pending, re-queue
            execute_campaign.delay(campaign_id)
        else:
            logger.info("Campaign %s completed", campaign_id)
    except Exception:
        session.rollback()
        logger.exception("Error executing campaign %s", campaign_id)
        raise
    finally:
        session.close()


@celery_app.task(name="app.tasks.campaigns.process_scheduled_campaigns")
def process_scheduled_campaigns():
    """Find campaigns where scheduled_at <= now, build recipients, and trigger execution."""
    session = SessionLocal()
    try:
        now = datetime.now(UTC)
        scheduled = (
            session.query(Campaign)
            .filter(
                Campaign.status == CampaignStatus.scheduled,
                Campaign.scheduled_at <= now,
                Campaign.is_active.is_(True),
            )
            .all()
        )
        for campaign in scheduled:
            logger.info("Starting scheduled campaign %s", campaign.id)
            from app.services.crm.campaigns import Campaigns
            Campaigns.build_recipient_list(session, str(campaign.id))
            campaign.status = CampaignStatus.sending
            campaign.sending_started_at = now
            session.commit()
            execute_campaign.delay(str(campaign.id))
    except Exception:
        session.rollback()
        logger.exception("Error processing scheduled campaigns")
        raise
    finally:
        session.close()


@celery_app.task(name="app.tasks.campaigns.process_nurture_steps")
def process_nurture_steps():
    """Check due nurture steps based on delay_days and create recipient rows."""
    session = SessionLocal()
    try:
        now = datetime.now(UTC)

        # Find active nurture campaigns that are sending or completed
        nurture_campaigns = (
            session.query(Campaign)
            .filter(
                Campaign.campaign_type == CampaignType.nurture,
                Campaign.status.in_([CampaignStatus.sending, CampaignStatus.completed]),
                Campaign.is_active.is_(True),
            )
            .all()
        )

        for campaign in nurture_campaigns:
            if not campaign.sending_started_at:
                continue

            steps = (
                session.query(CampaignStep)
                .filter(CampaignStep.campaign_id == campaign.id)
                .order_by(CampaignStep.step_index.asc())
                .all()
            )

            for step in steps:
                step_due_date = campaign.sending_started_at + timedelta(days=step.delay_days)
                if now < step_due_date:
                    continue

                # Get original recipients (step_id is NULL = initial send)
                original_recipients = (
                    session.query(CampaignRecipient)
                    .filter(
                        CampaignRecipient.campaign_id == campaign.id,
                        CampaignRecipient.step_id.is_(None),
                        CampaignRecipient.status != CampaignRecipientStatus.unsubscribed,
                    )
                    .all()
                )

                # Batch-check which persons already have this step
                already_have_step = set(
                    pid
                    for (pid,) in session.query(CampaignRecipient.person_id)
                    .filter(
                        CampaignRecipient.campaign_id == campaign.id,
                        CampaignRecipient.step_id == step.id,
                    )
                    .all()
                )

                new_count = 0
                for orig in original_recipients:
                    if orig.person_id in already_have_step:
                        continue

                    recipient = CampaignRecipient(
                        campaign_id=campaign.id,
                        person_id=orig.person_id,
                        step_id=step.id,
                        email=orig.email,
                        status=CampaignRecipientStatus.pending,
                    )
                    session.add(recipient)
                    new_count += 1

                if new_count > 0:
                    session.commit()
                    # Re-trigger sending if campaign was completed
                    if campaign.status == CampaignStatus.completed:
                        campaign.status = CampaignStatus.sending
                        session.commit()
                    execute_campaign.delay(str(campaign.id))
                    logger.info(
                        "Created %d recipients for step %s of campaign %s",
                        new_count, step.id, campaign.id,
                    )
    except Exception:
        session.rollback()
        logger.exception("Error processing nurture steps")
        raise
    finally:
        session.close()
