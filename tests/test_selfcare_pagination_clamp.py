"""P2: selfcare per_page is clamped to sub's real cap (500).

sub clamps per_page to 500. Requesting more meant a full-but-clamped 500-row
page read as "short" (500 < requested), tripping the short-page heuristic and
silently dropping every subscriber past the first page when metadata was absent.
"""

from __future__ import annotations

from app.services import selfcare
from app.services.selfcare import _SUB_MAX_PER_PAGE


def test_over_cap_request_is_clamped_and_pagination_completes(monkeypatch):
    sent: list[dict] = []
    pages = {
        1: [{"id": i} for i in range(500)],  # full page
        2: [{"id": 500 + i} for i in range(500)],  # full page
        3: [{"id": 1000 + i} for i in range(200)],  # short -> last page
    }

    def fake_request(db, method, path, *, params=None, **_kw):
        sent.append(dict(params or {}))
        page = (params or {}).get("page", 1)
        # No meta.total / last_page -> termination is driven by the short-page
        # heuristic, which is exactly what the clamp protects.
        return {"data": pages.get(page, [])}

    monkeypatch.setattr(selfcare, "_request_json", fake_request)

    rows = selfcare.fetch_customers(None, per_page=1000)  # ask for more than sub allows

    assert len(rows) == 1200  # nothing dropped past page 1
    assert len(sent) == 3  # stopped on the genuinely short third page
    assert all(p.get("per_page") == _SUB_MAX_PER_PAGE for p in sent)


def test_list_paginated_clamps_per_page_param(monkeypatch):
    sent: list[dict] = []

    def fake_request(db, method, path, *, params=None, **_kw):
        sent.append(dict(params or {}))
        return {"data": [], "meta": {"total": 0}}

    monkeypatch.setattr(selfcare, "_request_json", fake_request)
    selfcare._list_paginated(None, "/subscribers", {"per_page": 5000})

    assert sent[0]["per_page"] == _SUB_MAX_PER_PAGE
