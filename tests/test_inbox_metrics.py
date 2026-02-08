from app.models.crm.enums import ConversationStatus
from app.services.crm.inbox import outbox as outbox_service
from app.services.crm.inbox.metrics import (
    summarize_conversation_status_rows,
    summarize_outbox_status_rows,
)


def test_summarize_conversation_status_rows():
    rows = [
        (ConversationStatus.open, 2),
        (ConversationStatus.resolved, 1),
    ]
    summary = summarize_conversation_status_rows(rows)
    assert summary["open"] == 2
    assert summary["resolved"] == 1
    assert summary["pending"] == 0
    assert summary["total"] == 3


def test_summarize_outbox_status_rows():
    rows = [
        (outbox_service.STATUS_QUEUED, 3),
        (outbox_service.STATUS_FAILED, 1),
    ]
    summary = summarize_outbox_status_rows(rows)
    assert summary[outbox_service.STATUS_QUEUED] == 3
    assert summary[outbox_service.STATUS_FAILED] == 1
    assert summary["total"] == 4
