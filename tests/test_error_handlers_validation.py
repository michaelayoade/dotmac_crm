import asyncio
import concurrent.futures

import httpx
from fastapi import FastAPI

from app.errors import register_error_handlers
from app.schemas.nextcloud_talk import NextcloudTalkLoginRequest


def _run_async(coro):
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(coro)).result()


async def _post_login(payload: dict) -> httpx.Response:
    app = FastAPI()
    register_error_handlers(app)

    @app.post("/nextcloud-talk/me/login")
    def login(body: NextcloudTalkLoginRequest):
        return {"ok": True}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post("/nextcloud-talk/me/login", json=payload)


def test_validation_handler_serializes_ctx_exceptions() -> None:
    response = _run_async(
        _post_login(
            {
                "base_url": "next.example.com",
                "username": "user@example.com",
                "app_password": "secret-pass",
            }
        )
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload["code"] == "validation_error"
    details = payload["details"]
    assert isinstance(details, list)
    assert details

    # Regression guard: ctx.error must be JSON-serializable (string), not raw ValueError.
    ctx = details[0].get("ctx", {})
    if isinstance(ctx, dict) and "error" in ctx:
        assert isinstance(ctx["error"], str)


def test_validation_handler_redacts_sensitive_input_fields() -> None:
    secret = "test-secret-value"

    response = _run_async(
        _post_login(
            {
                "base_url": "next.example.com",
                "username": "user@example.com",
                "app_password": secret,
            }
        )
    )

    assert response.status_code == 422
    body = response.text
    assert secret not in body

    payload = response.json()
    details = payload["details"]
    assert isinstance(details, list)
    assert details

    # Pydantic may expose the full request body in `input` for model-level validation errors.
    # Ensure credential fields are redacted if echoed back.
    error_input = details[0].get("input")
    if isinstance(error_input, dict) and "app_password" in error_input:
        assert error_input["app_password"] == "[REDACTED]"
