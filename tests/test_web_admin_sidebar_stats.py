from app.services import web_admin


class _QueryStub:
    def filter(self, *args, **kwargs):
        return self

    def scalar(self):
        return 3


class _DBStub:
    def query(self, *args, **kwargs):
        return _QueryStub()


def test_get_sidebar_stats_uses_workqueue_override(monkeypatch):
    def _unexpected(*args, **kwargs):
        raise AssertionError("_workqueue_attention should not be called when override is provided")

    monkeypatch.setattr(web_admin, "_workqueue_attention", _unexpected)

    stats = web_admin.get_sidebar_stats(_DBStub(), {"person_id": "abc"}, workqueue_attention_override=7)

    assert stats["open_tickets"] == 3
    assert stats["workqueue_attention"] == 7
