"""Tests for CRM inbox Celery tasks."""

from app.services.crm.inbox.outbound import TransientOutboundError
from app.tasks.crm_inbox import send_outbound_message_task


def test_send_outbound_message_task_retry_config():
    assert TransientOutboundError in send_outbound_message_task.autoretry_for
    assert send_outbound_message_task.retry_kwargs.get("max_retries") == 5
    assert send_outbound_message_task.retry_backoff
