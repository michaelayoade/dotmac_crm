from types import SimpleNamespace

from app.services import ticket_mentions, web_admin, web_admin_dashboard


def test_get_sidebar_stats_uses_short_ttl_cache(monkeypatch):
    calls = {"count": 0}

    class FakeQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def scalar(self):
            calls["count"] += 1
            return 7

    monkeypatch.setattr(web_admin, "_workqueue_attention", lambda _db, _current_user: 3)
    db = SimpleNamespace(query=lambda *_args, **_kwargs: FakeQuery())
    current_user = {"person_id": "person-1", "permissions": ["workqueue:view"]}
    web_admin._SIDEBAR_STATS_CACHE.clear()

    first = web_admin.get_sidebar_stats(db, current_user)
    second = web_admin.get_sidebar_stats(db, current_user)

    assert first == second
    assert first["open_tickets"] == 7
    assert first["workqueue_attention"] == 3
    assert calls["count"] == 1


def test_build_live_stats_context_uses_short_ttl_cache(monkeypatch):
    calls = {"count": 0}

    def fake_high_priority_stats(_db):
        calls["count"] += 1
        return {"waiting_queue_count": 5}

    monkeypatch.setattr(web_admin_dashboard, "get_high_priority_stats", fake_high_priority_stats)
    web_admin_dashboard._DASHBOARD_LIVE_STATS_CACHE = None

    first = web_admin_dashboard._build_live_stats_context(SimpleNamespace())
    second = web_admin_dashboard._build_live_stats_context(SimpleNamespace())

    assert first == second
    assert first["high_priority_stats"]["waiting_queue_count"] == 5
    assert calls["count"] == 1


def test_ticket_mention_users_uses_short_ttl_cache(monkeypatch):
    calls = {"count": 0}

    def fake_list_active_users_for_mentions(_db, *, limit):
        calls["count"] += 1
        return [{"id": "person:1", "label": "Agent One"}]

    monkeypatch.setattr(ticket_mentions, "list_active_users_for_mentions", fake_list_active_users_for_mentions)
    ticket_mentions._TICKET_MENTION_USERS_CACHE = None

    first = ticket_mentions.list_ticket_mention_users(SimpleNamespace(), limit=200)
    second = ticket_mentions.list_ticket_mention_users(SimpleNamespace(), limit=200)

    assert first == second
    assert first[0]["label"] == "Agent One"
    assert calls["count"] == 1
