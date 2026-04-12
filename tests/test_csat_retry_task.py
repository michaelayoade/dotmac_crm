"""Tests for the periodic CSAT retry task and the webhook_health push
fan-out for expired OAuth tokens."""

from datetime import UTC, datetime, timedelta

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


def _build_pending_invitation(db_session, person, conversation, *, age_minutes: int):
    survey = Survey(
        name="CSAT",
        is_active=True,
        status=CustomerSurveyStatus.active,
        trigger_type=SurveyTriggerType.ticket_closed,
    )
    db_session.add(survey)
    db_session.commit()
    invitation = SurveyInvitation(
        survey_id=survey.id,
        person_id=person.id,
        token=f"tok-{age_minutes}",
        email=person.email,
        status=SurveyInvitationStatus.pending,
        conversation_id=conversation.id,
        created_at=datetime.now(UTC) - timedelta(minutes=age_minutes),
    )
    db_session.add(invitation)
    db_session.commit()
    return invitation


def test_retry_task_skips_invitations_younger_than_5_minutes(monkeypatch, db_session, crm_contact):
    convo = Conversation(person_id=crm_contact.id, status=ConversationStatus.resolved)
    db_session.add(convo)
    db_session.commit()
    _build_pending_invitation(db_session, crm_contact, convo, age_minutes=2)

    monkeypatch.setattr(
        csat,
        "_resolve_target_id",
        lambda db, cid: "00000000-0000-0000-0000-000000000001",
    )
    monkeypatch.setattr(csat, "_resolve_latest_channel_type", lambda db, cid: ChannelType.whatsapp)
    monkeypatch.setattr(csat, "_resolve_latest_inbound_message", lambda db, cid: None)

    sent = []

    def _fake_retry(_db, *, invitation):
        sent.append(invitation.id)
        return csat.CsatQueueResult(kind="queued")

    monkeypatch.setattr("app.services.crm.inbox.csat.retry_pending_invitation", _fake_retry)
    # The task imports inside the function, so monkeypatch the import target.
    from app.tasks import crm_inbox as crm_inbox_tasks

    # Inject our patched session
    monkeypatch.setattr(crm_inbox_tasks, "SessionLocal", lambda: db_session)

    result = crm_inbox_tasks.retry_pending_csat_invitations_task(limit=10)

    assert result["retried"] == 0
    assert sent == []


def test_retry_task_picks_up_eligible_invitations(monkeypatch, db_session, crm_contact):
    convo = Conversation(person_id=crm_contact.id, status=ConversationStatus.resolved)
    db_session.add(convo)
    db_session.commit()
    invitation = _build_pending_invitation(db_session, crm_contact, convo, age_minutes=30)

    sent = []

    def _fake_retry(_db, *, invitation):
        sent.append(invitation.id)
        return csat.CsatQueueResult(kind="queued")

    from app.tasks import crm_inbox as crm_inbox_tasks

    monkeypatch.setattr("app.services.crm.inbox.csat.retry_pending_invitation", _fake_retry)
    monkeypatch.setattr(crm_inbox_tasks, "SessionLocal", lambda: db_session)

    # Don't actually close our test session at the end of the task.
    monkeypatch.setattr(db_session, "close", lambda: None)

    result = crm_inbox_tasks.retry_pending_csat_invitations_task(limit=10)

    assert result["retried"] == 1
    assert result["succeeded"] == 1
    assert sent == [invitation.id]


def test_retry_task_skips_invitations_older_than_24h(monkeypatch, db_session, crm_contact):
    convo = Conversation(person_id=crm_contact.id, status=ConversationStatus.resolved)
    db_session.add(convo)
    db_session.commit()
    _build_pending_invitation(db_session, crm_contact, convo, age_minutes=60 * 25)

    from app.tasks import crm_inbox as crm_inbox_tasks

    sent = []
    monkeypatch.setattr(
        "app.services.crm.inbox.csat.retry_pending_invitation",
        lambda _db, *, invitation: sent.append(invitation.id) or csat.CsatQueueResult(kind="queued"),
    )
    monkeypatch.setattr(crm_inbox_tasks, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    result = crm_inbox_tasks.retry_pending_csat_invitations_task(limit=10)

    assert result["retried"] == 0
    assert sent == []
