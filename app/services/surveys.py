import contextlib
import logging
import secrets
from collections import Counter
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.models.comms import (
    CustomerSurveyStatus,
    Survey,
    SurveyInvitation,
    SurveyInvitationStatus,
    SurveyQuestionType,
    SurveyResponse,
    SurveyTriggerType,
)
from app.models.notification import Notification, NotificationChannel, NotificationStatus
from app.models.person import PartyStatus, Person
from app.models.subscriber import Organization
from app.schemas.comms import SurveyCreate, SurveyUpdate
from app.services.common import apply_ordering, apply_pagination, coerce_uuid, validate_enum

logger = logging.getLogger(__name__)


# ── Segment Query Builder ─────────────────────────────────────────


def _build_segment_query(db: Session, segment_filter: dict | None):
    """Build a Person query filtered by segment_filter criteria.

    Reuses the campaign segment pattern: party_status, regions, tags, created_after/before.
    """
    query = db.query(Person).filter(
        Person.is_active.is_(True),
        Person.email.isnot(None),
        Person.email != "",
    )

    if not segment_filter:
        return query

    if segment_filter.get("party_status"):
        statuses = []
        for s in segment_filter["party_status"]:
            if s:
                with contextlib.suppress(ValueError):
                    statuses.append(PartyStatus(s))
        if statuses:
            query = query.filter(Person.party_status.in_(statuses))

    if segment_filter.get("organization_ids"):
        org_ids = [coerce_uuid(oid) for oid in segment_filter["organization_ids"] if oid]
        if org_ids:
            query = query.filter(Person.organization_id.in_(org_ids))

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

    return query


# ── NPS Calculation ───────────────────────────────────────────────


def _calculate_nps(nps_values: list[int]) -> float | None:
    """Calculate Net Promoter Score from a list of 0-10 values.

    Promoters (9-10), Passives (7-8), Detractors (0-6).
    NPS = (promoters/total * 100) - (detractors/total * 100)
    """
    if not nps_values:
        return None
    total = len(nps_values)
    promoters = sum(1 for v in nps_values if v >= 9)
    detractors = sum(1 for v in nps_values if v <= 6)
    return round((promoters / total * 100) - (detractors / total * 100))


# ── Survey Manager ────────────────────────────────────────────────


class SurveyManager:
    @staticmethod
    def create(db: Session, payload: SurveyCreate, created_by_id: str | None = None) -> Survey:
        data = payload.model_dump()
        # Convert SurveyQuestion objects to dicts for JSON storage
        if data.get("questions"):
            data["questions"] = list(data["questions"])
        if created_by_id:
            data["created_by_id"] = coerce_uuid(created_by_id)
        survey = Survey(**data)
        db.add(survey)
        db.commit()
        db.refresh(survey)
        return survey

    @staticmethod
    def get(db: Session, survey_id: str) -> Survey:
        survey = db.get(Survey, coerce_uuid(survey_id))
        if not survey:
            raise HTTPException(status_code=404, detail="Survey not found")
        return survey

    @staticmethod
    def get_by_slug(db: Session, slug: str) -> Survey | None:
        return db.query(Survey).filter(Survey.public_slug == slug).first()

    @staticmethod
    def list(
        db: Session,
        status: str | None = None,
        trigger_type: str | None = None,
        search: str | None = None,
        is_active: bool | None = None,
        order_by: str = "created_at",
        order_dir: str = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> list[Survey]:
        query = db.query(Survey)
        if status:
            status_value = validate_enum(status, CustomerSurveyStatus, "status")
            query = query.filter(Survey.status == status_value)
        if trigger_type:
            trigger_value = validate_enum(trigger_type, SurveyTriggerType, "trigger_type")
            query = query.filter(Survey.trigger_type == trigger_value)
        if search:
            like = f"%{search.strip()}%"
            query = query.filter(or_(Survey.name.ilike(like), Survey.description.ilike(like)))
        if is_active is None:
            query = query.filter(Survey.is_active.is_(True))
        else:
            query = query.filter(Survey.is_active == is_active)
        allowed = {
            "created_at": Survey.created_at,
            "updated_at": Survey.updated_at,
            "name": Survey.name,
            "total_responses": Survey.total_responses,
        }
        query = apply_ordering(query, order_by, order_dir, allowed)
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, survey_id: str, payload: SurveyUpdate) -> Survey:
        survey = db.get(Survey, coerce_uuid(survey_id))
        if not survey:
            raise HTTPException(status_code=404, detail="Survey not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(survey, key, value)
        db.commit()
        db.refresh(survey)
        return survey

    @staticmethod
    def delete(db: Session, survey_id: str) -> None:
        survey = db.get(Survey, coerce_uuid(survey_id))
        if not survey:
            raise HTTPException(status_code=404, detail="Survey not found")
        survey.is_active = False
        db.commit()

    @staticmethod
    def activate(db: Session, survey_id: str) -> Survey:
        survey = db.get(Survey, coerce_uuid(survey_id))
        if not survey:
            raise HTTPException(status_code=404, detail="Survey not found")
        if survey.status not in (CustomerSurveyStatus.draft, CustomerSurveyStatus.paused):
            raise HTTPException(status_code=400, detail="Only draft or paused surveys can be activated")
        survey.status = CustomerSurveyStatus.active
        db.commit()
        db.refresh(survey)
        return survey

    @staticmethod
    def pause(db: Session, survey_id: str) -> Survey:
        survey = db.get(Survey, coerce_uuid(survey_id))
        if not survey:
            raise HTTPException(status_code=404, detail="Survey not found")
        if survey.status != CustomerSurveyStatus.active:
            raise HTTPException(status_code=400, detail="Only active surveys can be paused")
        survey.status = CustomerSurveyStatus.paused
        db.commit()
        db.refresh(survey)
        return survey

    @staticmethod
    def close(db: Session, survey_id: str) -> Survey:
        survey = db.get(Survey, coerce_uuid(survey_id))
        if not survey:
            raise HTTPException(status_code=404, detail="Survey not found")
        survey.status = CustomerSurveyStatus.closed
        db.commit()
        db.refresh(survey)
        return survey

    @staticmethod
    def preview_audience(db: Session, segment_filter: dict | None) -> dict:
        query = _build_segment_query(db, segment_filter)
        total = query.count()
        sample = query.limit(5).all()
        return {
            "total": total,
            "sample": [{"id": str(p.id), "name": p.display_name, "email": p.email} for p in sample],
        }

    @staticmethod
    def count_by_status(db: Session) -> dict[str, int]:
        rows = (
            db.query(Survey.status, func.count(Survey.id))
            .filter(Survey.is_active.is_(True))
            .group_by(Survey.status)
            .all()
        )
        return {row[0].value if row[0] else "unknown": row[1] for row in rows}

    @staticmethod
    def analytics(db: Session, survey_id: str) -> dict:
        sid = coerce_uuid(survey_id)
        survey = db.get(Survey, sid)
        if not survey:
            raise HTTPException(status_code=404, detail="Survey not found")

        responses = db.query(SurveyResponse).filter(SurveyResponse.survey_id == sid).all()
        total_responses = len(responses)
        total_invited = survey.total_invited or 0
        response_rate = round(total_responses / total_invited * 100, 1) if total_invited > 0 else 0.0

        # Calculate overall avg rating (from rating column)
        ratings = [r.rating for r in responses if r.rating is not None]
        avg_rating = round(sum(ratings) / len(ratings), 2) if ratings else None

        # Per-question breakdown
        questions = survey.questions or []
        question_breakdown = []
        for q in questions:
            qkey = q.get("key", "")
            qtype = q.get("type", "")
            qlabel = q.get("label", "")
            values = []
            for r in responses:
                ans = (r.responses or {}).get(qkey)
                if ans is not None:
                    values.append(ans)

            breakdown = {"key": qkey, "label": qlabel, "type": qtype, "response_count": len(values)}

            if qtype == SurveyQuestionType.rating.value:
                numeric = [int(v) for v in values if str(v).isdigit()]
                breakdown["avg_value"] = round(sum(numeric) / len(numeric), 2) if numeric else None
                breakdown["distribution"] = dict(Counter(str(v) for v in numeric))

            elif qtype == SurveyQuestionType.nps.value:
                numeric = [int(v) for v in values if str(v).isdigit()]
                breakdown["avg_value"] = _calculate_nps(numeric)
                breakdown["distribution"] = dict(Counter(str(v) for v in numeric))

            elif qtype == SurveyQuestionType.multiple_choice.value:
                breakdown["distribution"] = dict(Counter(str(v) for v in values))

            question_breakdown.append(breakdown)

        # NPS from any NPS-type question
        nps_score = None
        for q in questions:
            if q.get("type") == SurveyQuestionType.nps.value:
                nps_values = []
                for r in responses:
                    v = (r.responses or {}).get(q.get("key", ""))
                    if v is not None and str(v).isdigit():
                        nps_values.append(int(v))
                nps_score = _calculate_nps(nps_values)
                break

        return {
            "total_invited": total_invited,
            "total_responses": total_responses,
            "response_rate": response_rate,
            "avg_rating": avg_rating,
            "nps_score": nps_score,
            "question_breakdown": question_breakdown,
        }


# ── Survey Invitation Manager ─────────────────────────────────────


class SurveyInvitationManager:
    @staticmethod
    def create_for_person(
        db: Session,
        survey_id: str,
        person_id: str,
        email: str,
        ticket_id: str | None = None,
        work_order_id: str | None = None,
        expires_at: datetime | None = None,
    ) -> SurveyInvitation:
        invitation = SurveyInvitation(
            survey_id=coerce_uuid(survey_id),
            person_id=coerce_uuid(person_id),
            token=secrets.token_urlsafe(32),
            email=email,
            ticket_id=coerce_uuid(ticket_id) if ticket_id else None,
            work_order_id=coerce_uuid(work_order_id) if work_order_id else None,
            expires_at=expires_at,
        )
        db.add(invitation)
        db.flush()
        return invitation

    @staticmethod
    def create_batch(
        db: Session,
        survey: Survey,
        persons: list[Person],
        ticket_id: str | None = None,
        work_order_id: str | None = None,
    ) -> list[SurveyInvitation]:
        """Create invitations for a batch of persons, skipping duplicates."""
        existing_person_ids = set(
            pid for (pid,) in db.query(SurveyInvitation.person_id).filter(SurveyInvitation.survey_id == survey.id).all()
        )

        invitations = []
        for person in persons:
            if person.id in existing_person_ids:
                continue
            if not person.email:
                continue
            inv = SurveyInvitation(
                survey_id=survey.id,
                person_id=person.id,
                token=secrets.token_urlsafe(32),
                email=person.email,
                ticket_id=coerce_uuid(ticket_id) if ticket_id else None,
                work_order_id=coerce_uuid(work_order_id) if work_order_id else None,
                expires_at=survey.expires_at,
            )
            db.add(inv)
            invitations.append(inv)
        db.flush()
        return invitations

    @staticmethod
    def get_by_token(db: Session, token: str) -> SurveyInvitation | None:
        return (
            db.query(SurveyInvitation)
            .options(joinedload(SurveyInvitation.survey))
            .filter(SurveyInvitation.token == token)
            .first()
        )

    @staticmethod
    def list(
        db: Session,
        survey_id: str | None = None,
        status: str | None = None,
        order_by: str = "created_at",
        order_dir: str = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> list[SurveyInvitation]:
        query = db.query(SurveyInvitation)
        if survey_id:
            query = query.filter(SurveyInvitation.survey_id == coerce_uuid(survey_id))
        if status:
            status_value = validate_enum(status, SurveyInvitationStatus, "status")
            query = query.filter(SurveyInvitation.status == status_value)
        allowed = {"created_at": SurveyInvitation.created_at, "sent_at": SurveyInvitation.sent_at}
        query = apply_ordering(query, order_by, order_dir, allowed)
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def mark_sent(db: Session, invitation: SurveyInvitation) -> None:
        invitation.status = SurveyInvitationStatus.sent
        invitation.sent_at = datetime.now(UTC)

    @staticmethod
    def mark_opened(db: Session, invitation: SurveyInvitation) -> None:
        if invitation.status in (SurveyInvitationStatus.pending, SurveyInvitationStatus.sent):
            invitation.status = SurveyInvitationStatus.opened
            invitation.opened_at = datetime.now(UTC)

    @staticmethod
    def mark_completed(db: Session, invitation: SurveyInvitation) -> None:
        invitation.status = SurveyInvitationStatus.completed
        invitation.completed_at = datetime.now(UTC)


# ── Survey Response Manager ───────────────────────────────────────


class SurveyResponseManager:
    @staticmethod
    def submit(
        db: Session,
        survey_id: str,
        answers: dict,
        invitation_id: str | None = None,
        person_id: str | None = None,
    ) -> SurveyResponse:
        """Submit a survey response, validate answers, update counters."""
        sid = coerce_uuid(survey_id)
        survey = db.get(Survey, sid)
        if not survey:
            raise HTTPException(status_code=404, detail="Survey not found")

        now = datetime.now(UTC)

        # Extract overall rating from answers if present (first rating-type question)
        rating = None
        questions = survey.questions or []
        for q in questions:
            if q.get("type") == SurveyQuestionType.rating.value:
                val = answers.get(q.get("key", ""))
                if val is not None:
                    with contextlib.suppress(ValueError, TypeError):
                        rating = max(1, min(5, int(val)))
                break

        response = SurveyResponse(
            survey_id=sid,
            responses=answers,
            rating=rating,
            completed_at=now,
            invitation_id=coerce_uuid(invitation_id) if invitation_id else None,
            person_id=coerce_uuid(person_id) if person_id else None,
        )
        db.add(response)
        db.flush()

        # Mark invitation completed
        if invitation_id:
            invitation = db.get(SurveyInvitation, coerce_uuid(invitation_id))
            if invitation:
                SurveyInvitationManager.mark_completed(db, invitation)

        # Update survey counters
        survey.total_responses = (survey.total_responses or 0) + 1

        # Recalculate avg_rating
        all_ratings = [
            r.rating
            for r in db.query(SurveyResponse.rating)
            .filter(
                SurveyResponse.survey_id == sid,
                SurveyResponse.rating.isnot(None),
            )
            .all()
        ]
        if all_ratings:
            survey.avg_rating = round(sum(r[0] for r in all_ratings) / len(all_ratings), 2)

        # Recalculate NPS from first NPS-type question
        for q in questions:
            if q.get("type") == SurveyQuestionType.nps.value:
                qkey = q.get("key", "")
                nps_values = []
                for resp_row in db.query(SurveyResponse.responses).filter(SurveyResponse.survey_id == sid).all():
                    val = (resp_row[0] or {}).get(qkey)
                    if val is not None and str(val).isdigit():
                        nps_values.append(int(val))
                survey.nps_score = _calculate_nps(nps_values)
                break

        db.commit()
        db.refresh(response)
        return response

    @staticmethod
    def list(
        db: Session,
        survey_id: str | None = None,
        order_by: str = "created_at",
        order_dir: str = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> list[SurveyResponse]:
        query = db.query(SurveyResponse)
        if survey_id:
            query = query.filter(SurveyResponse.survey_id == coerce_uuid(survey_id))
        allowed = {"created_at": SurveyResponse.created_at}
        query = apply_ordering(query, order_by, order_dir, allowed)
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def get(db: Session, response_id: str) -> SurveyResponse:
        response = db.get(SurveyResponse, coerce_uuid(response_id))
        if not response:
            raise HTTPException(status_code=404, detail="Survey response not found")
        return response


# ── Send Survey Function ──────────────────────────────────────────


def send_survey(db: Session, survey_id: str, base_url: str = "") -> int:
    """Evaluate segment → create invitations → queue notifications.

    Returns the number of invitations created.
    """
    sid = coerce_uuid(survey_id)
    survey = db.query(Survey).filter(Survey.id == sid).with_for_update(skip_locked=True).first()
    if not survey:
        return 0
    if survey.status != CustomerSurveyStatus.active:
        logger.warning("send_survey called on non-active survey %s (status=%s)", survey_id, survey.status)
        return 0

    # Build audience
    persons_query = _build_segment_query(db, survey.segment_filter)
    persons = persons_query.all()

    # Create invitations in batch
    invitations = SurveyInvitationManager.create_batch(db, survey, persons)
    if not invitations:
        return 0

    # Create notification records for delivery
    for inv in invitations:
        survey_url = f"{base_url}/s/t/{inv.token}"
        notification = Notification(
            channel=NotificationChannel.email,
            recipient=inv.email,
            subject=f"We'd love your feedback: {survey.name}",
            body=f"Please take a moment to complete our survey: {survey_url}",
            status=NotificationStatus.queued,
        )
        db.add(notification)
        db.flush()
        inv.notification_id = notification.id
        SurveyInvitationManager.mark_sent(db, inv)

    survey.total_invited = (survey.total_invited or 0) + len(invitations)
    db.commit()

    logger.info("send_survey survey_id=%s invitations_created=%d", survey_id, len(invitations))
    return len(invitations)


# Singleton instances
survey_manager = SurveyManager()
survey_invitations = SurveyInvitationManager()
survey_responses = SurveyResponseManager()
