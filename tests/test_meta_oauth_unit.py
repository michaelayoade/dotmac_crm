from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import meta_oauth


def test_build_authorization_url_defaults_to_v19():
    url = meta_oauth.build_authorization_url(
        app_id="app-id",
        redirect_uri="https://example.com/callback",
        state="state",
    )
    assert "https://www.facebook.com/v19.0/dialog/oauth" in url
    assert "client_id=app-id" in url


@pytest.mark.asyncio
async def test_exchange_code_for_token_success():
    mock_response = MagicMock()
    mock_response.json.return_value = {"access_token": "token"}
    mock_response.raise_for_status = MagicMock()

    with patch("app.services.meta_oauth.httpx.AsyncClient") as mock_client:
        mock_instance = AsyncMock()
        mock_instance.get = AsyncMock(return_value=mock_response)
        mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_client.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await meta_oauth.exchange_code_for_token(
            app_id="123",
            app_secret="secret",
            redirect_uri="https://example.com/callback",
            code="auth",
        )

    assert result["access_token"] == "token"


@pytest.mark.asyncio
async def test_get_user_pages_success():
    mock_response = MagicMock()
    mock_response.json.return_value = {"data": [{"id": "page_1"}]}
    mock_response.raise_for_status = MagicMock()

    with patch("app.services.meta_oauth.httpx.AsyncClient") as mock_client:
        mock_instance = AsyncMock()
        mock_instance.get = AsyncMock(return_value=mock_response)
        mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_client.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await meta_oauth.get_user_pages("user_access_token")

    assert result == [{"id": "page_1"}]
