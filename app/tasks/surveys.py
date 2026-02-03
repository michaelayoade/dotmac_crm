import logging
from datetime import UTC, datetime, timedelta

from app.celery_app import celery_app
from app.db import SessionLocal
from app.models.comms import (
    CustomerSurveyStatus,
    Survey,
    SurveyInvitation,
    SurveyTriggerType,
)
from app.models.person import Person
from app.models.subscriber import Subscriber
from app.models.tickets import Ticket, TicketStatus
from app.models.workforce import WorkOrder, WorkOrderStatus
from app.services.surveys import send_survey, survey_invitations

logger = logging.getLogger(__name__)

BATCH_SIZE = 50


@celery_app.task(name="app.tasks.surveys.distribute_survey")
def distribute_survey(survey_id: str):
    """Build invitations + notifications for a survey in batches."""
    session = SessionLocal()
    try:
        count = send_survey(session, survey_id)
        logger.info("distribute_survey survey_id=%s invitations=%d", survey_id, count)
    except Exception:
        session.rollback()
        logger.exception("Error distributing survey %s", survey_id)
        raise
    finally:
        session.close()


@celery_app.task(name="app.tasks.surveys.process_survey_triggers")
def process_survey_triggers():
    """Periodic task: check for ticket_closed and work_order_completed triggers.

    Looks for tickets closed or work orders completed in the last 2 minutes
    and creates survey invitations for matching active surveys.
    """
    session = SessionLocal()
    try:
        now = datetime.now(UTC)
        since = now - timedelta(minutes=2)

        # Find active surveys with automatic triggers
        active_surveys = (
            session.query(Survey)
            .filter(
                Survey.status == CustomerSurveyStatus.active,
                Survey.is_active.is_(True),
                Survey.trigger_type.in_(
                    [
                        SurveyTriggerType.ticket_closed,
                        SurveyTriggerType.work_order_completed,
                    ]
                ),
            )
            .all()
        )

        if not active_surveys:
            return

        ticket_surveys = [s for s in active_surveys if s.trigger_type == SurveyTriggerType.ticket_closed]
        wo_surveys = [s for s in active_surveys if s.trigger_type == SurveyTriggerType.work_order_completed]

        # Process ticket_closed triggers
        if ticket_surveys:
            recently_closed = (
                session.query(Ticket)
                .filter(
                    Ticket.status == TicketStatus.closed,
                    Ticket.updated_at >= since,
                    Ticket.customer_person_id.isnot(None),
                )
                .all()
            )

            if recently_closed:
                # Batch load persons
                person_ids = [t.customer_person_id for t in recently_closed if t.customer_person_id]
                persons = session.query(Person).filter(Person.id.in_(person_ids)).all() if person_ids else []
                person_map = {p.id: p for p in persons}

                for survey in ticket_surveys:
                    for ticket in recently_closed:
                        person = person_map.get(ticket.customer_person_id)
                        if not person or not person.email:
                            continue

                        # Check if invitation already exists
                        existing = (
                            session.query(SurveyInvitation.id)
                            .filter(
                                SurveyInvitation.survey_id == survey.id,
                                SurveyInvitation.person_id == person.id,
                            )
                            .first()
                        )
                        if existing:
                            continue

                        try:
                            survey_invitations.create_for_person(
                                session,
                                survey_id=str(survey.id),
                                person_id=str(person.id),
                                email=person.email,
                                ticket_id=str(ticket.id),
                                expires_at=survey.expires_at,
                            )
                            survey.total_invited = (survey.total_invited or 0) + 1
                        except Exception:
                            logger.exception(
                                "Failed creating invitation survey=%s person=%s ticket=%s",
                                survey.id,
                                person.id,
                                ticket.id,
                            )

        # Process work_order_completed triggers
        if wo_surveys:
            recently_completed = (
                session.query(WorkOrder)
                .filter(
                    WorkOrder.status == WorkOrderStatus.completed,
                    WorkOrder.updated_at >= since,
                    WorkOrder.subscriber_id.isnot(None),
                )
                .all()
            )

            if recently_completed:
                # Batch load subscribers to get person_ids
                sub_ids = [wo.subscriber_id for wo in recently_completed if wo.subscriber_id]
                subs = session.query(Subscriber).filter(Subscriber.id.in_(sub_ids)).all() if sub_ids else []
                sub_map = {s.id: s for s in subs}

                # Batch load persons
                person_ids = [s.person_id for s in subs if s.person_id]
                persons = session.query(Person).filter(Person.id.in_(person_ids)).all() if person_ids else []
                person_map = {p.id: p for p in persons}

                for survey in wo_surveys:
                    for wo in recently_completed:
                        sub = sub_map.get(wo.subscriber_id)
                        if not sub or not sub.person_id:
                            continue
                        person = person_map.get(sub.person_id)
                        if not person or not person.email:
                            continue

                        existing = (
                            session.query(SurveyInvitation.id)
                            .filter(
                                SurveyInvitation.survey_id == survey.id,
                                SurveyInvitation.person_id == person.id,
                            )
                            .first()
                        )
                        if existing:
                            continue

                        try:
                            survey_invitations.create_for_person(
                                session,
                                survey_id=str(survey.id),
                                person_id=str(person.id),
                                email=person.email,
                                work_order_id=str(wo.id),
                                expires_at=survey.expires_at,
                            )
                            survey.total_invited = (survey.total_invited or 0) + 1
                        except Exception:
                            logger.exception(
                                "Failed creating invitation survey=%s person=%s wo=%s",
                                survey.id,
                                person.id,
                                wo.id,
                            )

        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Error processing survey triggers")
        raise
    finally:
        session.close()
