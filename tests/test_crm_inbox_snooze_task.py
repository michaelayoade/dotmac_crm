from app.tasks import crm_inbox


def test_reopen_due_snoozed_conversations_task(monkeypatch):
    class _Session:
        def __init__(self):
            self.closed = False
            self.rolled_back = False

        def rollback(self):
            self.rolled_back = True

        def close(self):
            self.closed = True

    session = _Session()
    observed: list[tuple[str, str]] = []

    monkeypatch.setattr(crm_inbox, "SessionLocal", lambda: session)

    def _fake_observe_job(name, status, duration):
        observed.append((name, status))

    def _fake_reopen(_session):
        assert _session is session
        return 3

    import app.metrics as metrics_module
    import app.services.crm.inbox.conversation_status as status_module

    monkeypatch.setattr(metrics_module, "observe_job", _fake_observe_job)
    monkeypatch.setattr(status_module, "reopen_due_snoozed_conversations", _fake_reopen)

    result = crm_inbox.reopen_due_snoozed_conversations_task()

    assert result == {"reopened": 3}
    assert session.closed is True
    assert observed == [("crm_inbox_reopen_due_snoozed", "success")]
