import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from starlette.requests import Request

from app.services.crm.inbox import page_context
from app.services.crm.inbox.listing import DEFAULT_INBOX_PAGE_SIZE, InboxListResult
from app.web.admin import _auth_helpers
from app.web.admin import crm as crm_web


def _run_async(coro):
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, coro).result()


def test_inbox_route_defaults_to_smaller_page_size(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_build_inbox_page_context(_db, **kwargs):
        captured.update(kwargs)
        return {"pagination_limit": kwargs["limit"]}

    monkeypatch.setattr(_auth_helpers, "get_current_user", lambda _request: {"person_id": ""})
    monkeypatch.setattr(_auth_helpers, "get_sidebar_stats", lambda _db: {})
    monkeypatch.setattr(crm_web, "get_csrf_token", lambda _request: "csrf-token")
    monkeypatch.setattr(crm_web, "build_inbox_page_context", fake_build_inbox_page_context)
    monkeypatch.setattr(
        crm_web.templates,
        "TemplateResponse",
        lambda _template, context: SimpleNamespace(status_code=200, context=context),
    )

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin/crm/inbox",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )

    response = _run_async(crm_web.inbox(request=request, db=SimpleNamespace(), limit=None, page=None, offset=None))

    assert response.status_code == 200
    assert captured["limit"] == DEFAULT_INBOX_PAGE_SIZE
    assert response.context["pagination_limit"] == DEFAULT_INBOX_PAGE_SIZE


def test_inbox_partial_defaults_to_smaller_page_size(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_load_inbox_list(_db, **kwargs):
        captured.update(kwargs)
        return InboxListResult(
            conversations_raw=[],
            comment_items=[],
            channel_enum=None,
            status_enum=None,
            include_comments=False,
            target_is_comment=False,
            offset=0,
            limit=kwargs["limit"],
            has_more=False,
            next_offset=None,
        )

    monkeypatch.setattr(page_context, "resolve_company_time_prefs", lambda _db: ("UTC", "YYYY-MM-DD", "%H:%M", "mon"))
    monkeypatch.setattr(page_context, "load_inbox_list", fake_load_inbox_list)
    monkeypatch.setattr(page_context, "enrich_formatted_conversations_with_labels", lambda _db, _rows: None)

    template_name, context = _run_async(
        page_context.build_inbox_conversations_partial_context(
            SimpleNamespace(),
            limit=None,
            page=None,
            offset=None,
        )
    )

    assert template_name == "admin/crm/_conversation_list.html"
    assert captured["limit"] == DEFAULT_INBOX_PAGE_SIZE
    assert context["conversations_limit"] == DEFAULT_INBOX_PAGE_SIZE
