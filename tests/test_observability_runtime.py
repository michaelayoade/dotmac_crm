import asyncio
from contextlib import suppress

from starlette.requests import Request
from starlette.responses import PlainTextResponse

from app.observability import ObservabilityMiddleware


async def _receive():
    return {"type": "http.request", "body": b"", "more_body": False}


def _request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        },
        receive=_receive,
    )


def _middleware() -> ObservabilityMiddleware:
    return ObservabilityMiddleware(app=lambda scope, receive, send: None)


def test_observability_logs_actor_id_after_downstream_auth(caplog):
    async def call_next(request):
        request.state.actor_id = "user-123"
        return PlainTextResponse("ok")

    with caplog.at_level("INFO", logger="app.observability"):
        response = asyncio.run(_middleware().dispatch(_request("/ok"), call_next))

    assert response.status_code == 200
    record = next(record for record in caplog.records if record.message == "request_completed")
    assert record.actor_id == "user-123"


def test_observability_logs_actor_id_for_exceptions(caplog):
    async def call_next(request):
        request.state.actor_id = "user-500"
        raise RuntimeError("boom")

    with caplog.at_level("ERROR", logger="app.observability"), suppress(RuntimeError):
        asyncio.run(_middleware().dispatch(_request("/boom"), call_next))

    record = next(record for record in caplog.records if record.message == "request_failed")
    assert record.actor_id == "user-500"
