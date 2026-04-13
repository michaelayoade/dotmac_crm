"""Hardening test: a failed CSAT message send must not roll back the
SurveyInvitation. The invitation needs to persist so a future retry can find
it. This guards the regression that produced the WhatsApp #131000 → CSAT
cascade incident.
"""

from app.models.comms import (
    CustomerSurveyStatus,
    Survey,
    SurveyInvitation,
    SurveyInvitationStatus,
    SurveyTriggerType,
)
from app.models.crm.conversation import Conversation
from app.models.crm.enums import ChannelType, ConversationStatus
from app.services.crm.inbox import csat


def _make_conversation(db_session, person):
    convo = Conversation(
        person_id=person.id,
        status=ConversationStatus.resolved,
        subject="Test",
    )
    db_session.add(convo)
    db_session.commit()
    db_session.refresh(convo)
    return convo


def _make_survey(db_session):
    survey = Survey(
        name="CSAT",
        is_active=True,
        status=CustomerSurveyStatus.active,
        trigger_type=SurveyTriggerType.ticket_closed,
    )
    db_session.add(survey)
    db_session.commit()
    db_session.refresh(survey)
    return survey


def _stub_csat_lookups(monkeypatch):
    monkeypatch.setattr(csat, "_resolve_latest_inbound_message", lambda db, cid: None)
    monkeypatch.setattr(csat, "_resolve_target_id", lambda db, cid: "00000000-0000-0000-0000-000000000001")
    monkeypatch.setattr(csat, "_resolve_latest_channel_type", lambda db, cid: ChannelType.whatsapp)
    monkeypatch.setattr(
        csat,
        "get_enabled_map",
        lambda db: {"00000000-0000-0000-0000-000000000001": True, "channel:whatsapp": True},
    )


def test_send_failure_keeps_invitation_for_retry(monkeypatch, db_session, crm_contact):
    convo = _make_conversation(db_session, crm_contact)
    survey = _make_survey(db_session)
    _stub_csat_lookups(monkeypatch)

    class _BoomInbox:
        def send_message_with_retry(self, *args, **kwargs):
            raise RuntimeError("WhatsApp error #131000 — Something went wrong")

    class _StubCrmService:
        inbox = _BoomInbox()

    monkeypatch.setattr(csat, "crm_service", _StubCrmService)

    result = csat.queue_for_resolved_conversation(db_session, conversation_id=str(convo.id))

    assert result.kind == "send_failed"
    assert "131000" in (result.detail or "")

    invitation = (
        db_session.query(SurveyInvitation)
        .filter(SurveyInvitation.survey_id == survey.id)
        .filter(SurveyInvitation.person_id == crm_contact.id)
        .one()
    )
    # Critical: the invitation must NOT have been rolled back, and must still
    # be pending so a retry job can pick it up.
    assert invitation.status == SurveyInvitationStatus.pending
    assert invitation.sent_at is None


def test_successful_send_marks_invitation_sent(monkeypatch, db_session, crm_contact):
    convo = _make_conversation(db_session, crm_contact)
    survey = _make_survey(db_session)
    _stub_csat_lookups(monkeypatch)

    class _OkInbox:
        def send_message_with_retry(self, *args, **kwargs):
            return None

    class _StubCrmService:
        inbox = _OkInbox()

    monkeypatch.setattr(csat, "crm_service", _StubCrmService)

    result = csat.queue_for_resolved_conversation(db_session, conversation_id=str(convo.id))

    assert result.kind == "queued"
    invitation = (
        db_session.query(SurveyInvitation)
        .filter(SurveyInvitation.survey_id == survey.id)
        .filter(SurveyInvitation.person_id == crm_contact.id)
        .one()
    )
    assert invitation.status == SurveyInvitationStatus.sent
    assert invitation.sent_at is not None
    assert invitation.conversation_id == convo.id


def test_already_sent_invitation_short_circuits(monkeypatch, db_session, crm_contact):
    """A pre-existing sent/opened/completed invitation must not be re-sent or
    have its status rewritten back to `sent`. Guards data corruption flagged
    in code review."""
    from datetime import UTC, datetime

    convo = _make_conversation(db_session, crm_contact)
    survey = _make_survey(db_session)
    _stub_csat_lookups(monkeypatch)

    pre_existing_sent_at = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)
    invitation = SurveyInvitation(
        survey_id=survey.id,
        person_id=crm_contact.id,
        token="pre-existing-token",
        email=crm_contact.email,
        status=SurveyInvitationStatus.completed,
        sent_at=pre_existing_sent_at,
    )
    db_session.add(invitation)
    db_session.commit()

    send_calls = []

    class _RecordingInbox:
        def send_message_with_retry(self, *args, **kwargs):
            send_calls.append((args, kwargs))

    class _StubCrmService:
        inbox = _RecordingInbox()

    monkeypatch.setattr(csat, "crm_service", _StubCrmService)

    result = csat.queue_for_resolved_conversation(db_session, conversation_id=str(convo.id))

    assert result.kind == "already_invited"
    assert send_calls == []  # never attempted re-send
    db_session.refresh(invitation)
    assert invitation.status == SurveyInvitationStatus.completed
    # SQLite drops tzinfo on roundtrip; compare naive
    assert invitation.sent_at.replace(tzinfo=None) == pre_existing_sent_at.replace(tzinfo=None)


def test_retry_pending_invitation_succeeds(monkeypatch, db_session, crm_contact):
    """The retry helper resends a pending invitation through the same outbound
    path and marks it sent on success."""
    convo = _make_conversation(db_session, crm_contact)
    survey = _make_survey(db_session)
    _stub_csat_lookups(monkeypatch)

    invitation = SurveyInvitation(
        survey_id=survey.id,
        person_id=crm_contact.id,
        token="retry-token",
        email=crm_contact.email,
        status=SurveyInvitationStatus.pending,
        conversation_id=convo.id,
    )
    db_session.add(invitation)
    db_session.commit()

    class _OkInbox:
        def send_message_with_retry(self, *args, **kwargs):
            return None

    class _StubCrmService:
        inbox = _OkInbox()

    monkeypatch.setattr(csat, "crm_service", _StubCrmService)

    result = csat.retry_pending_invitation(db_session, invitation=invitation)

    assert result.kind == "queued"
    db_session.refresh(invitation)
    assert invitation.status == SurveyInvitationStatus.sent
    assert invitation.sent_at is not None


def test_retry_pending_invitation_send_failure_keeps_pending(monkeypatch, db_session, crm_contact):
    convo = _make_conversation(db_session, crm_contact)
    survey = _make_survey(db_session)
    _stub_csat_lookups(monkeypatch)

    invitation = SurveyInvitation(
        survey_id=survey.id,
        person_id=crm_contact.id,
        token="retry-token-2",
        email=crm_contact.email,
        status=SurveyInvitationStatus.pending,
        conversation_id=convo.id,
    )
    db_session.add(invitation)
    db_session.commit()

    class _BoomInbox:
        def send_message_with_retry(self, *args, **kwargs):
            raise RuntimeError("still broken")

    class _StubCrmService:
        inbox = _BoomInbox()

    monkeypatch.setattr(csat, "crm_service", _StubCrmService)

    result = csat.retry_pending_invitation(db_session, invitation=invitation)

    assert result.kind == "send_failed"
    db_session.refresh(invitation)
    assert invitation.status == SurveyInvitationStatus.pending
