from app.services.crm.inbox import queries


class _DBStub:
    def __init__(self):
        self.info = {}


def test_get_assignment_counts_uses_session_cache(monkeypatch):
    db = _DBStub()
    calls: list[str] = []

    def _fake_count(_db, *, assignment_filter, assigned_person_id=None):
        calls.append(assignment_filter)
        return len(calls)

    monkeypatch.setattr(
        queries,
        "_get_base_assignment_counts",
        lambda _db: {"all": 9, "unassigned": 4, "unreplied": 2, "needs_attention": 1},
    )
    monkeypatch.setattr(queries, "_count_active_conversations_for_filter", _fake_count)

    first = queries.get_assignment_counts(db, assigned_person_id="user-1")
    second = queries.get_assignment_counts(db, assigned_person_id="user-1")

    assert first == second
    assert first["all"] == 9
    assert first["unassigned"] == 4
    assert first["unreplied"] == 2
    assert first["needs_attention"] == 1
    assert calls == ["assigned", "my_team"]


def test_get_resolved_today_count_uses_session_cache(monkeypatch):
    db = _DBStub()

    class _QueryStub:
        def filter(self, *args, **kwargs):
            return self

        def scalar(self):
            return 4

    calls = {"query": 0}

    def _fake_query(*args, **kwargs):
        calls["query"] += 1
        return _QueryStub()

    db.query = _fake_query

    first = queries.get_resolved_today_count(db, timezone="UTC")
    second = queries.get_resolved_today_count(db, timezone="UTC")

    assert first == 4
    assert second == 4
    assert calls["query"] == 1
