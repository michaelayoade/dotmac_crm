import httpx
import pytest
from fastapi import FastAPI

from app.errors import register_error_handlers
from app.schemas.nextcloud_talk import NextcloudTalkLoginRequest


@pytest.fixture
async def client():
    app = FastAPI()
    register_error_handlers(app)

    @app.post("/nextcloud-talk/me/login")
    def login(payload: NextcloudTalkLoginRequest):
        return {"ok": True}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client


@pytest.mark.anyio
async def test_validation_handler_serializes_ctx_exceptions(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/nextcloud-talk/me/login",
        json={
            "base_url": "next.example.com",
            "username": "user@example.com",
            "app_password": "secret-pass",
        },
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


@pytest.mark.anyio
async def test_validation_handler_redacts_sensitive_input_fields(client: httpx.AsyncClient) -> None:
    secret = "test-secret-value"

    response = await client.post(
        "/nextcloud-talk/me/login",
        json={
            "base_url": "next.example.com",
            "username": "user@example.com",
            "app_password": secret,
        },
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
