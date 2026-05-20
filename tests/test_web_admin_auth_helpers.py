from app.web.admin import _auth_helpers


def test_get_sidebar_stats_forwards_workqueue_attention_override(monkeypatch):
    captured = {}

    def _fake_get_sidebar_stats(db, current_user=None, *, workqueue_attention_override=None):
        captured["db"] = db
        captured["current_user"] = current_user
        captured["workqueue_attention_override"] = workqueue_attention_override
        return {"workqueue_attention": workqueue_attention_override}

    monkeypatch.setattr(_auth_helpers.web_admin_service, "get_sidebar_stats", _fake_get_sidebar_stats)

    db = object()
    current_user = {"person_id": "abc"}
    result = _auth_helpers.get_sidebar_stats(db, current_user, workqueue_attention_override=7)

    assert result == {"workqueue_attention": 7}
    assert captured == {
        "db": db,
        "current_user": current_user,
        "workqueue_attention_override": 7,
    }
