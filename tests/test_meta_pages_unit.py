import asyncio
import concurrent.futures
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.connector import ConnectorConfig, ConnectorType
from app.models.oauth_token import OAuthToken
from app.services import meta_pages


def _run_async(coro):
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(coro)).result()


def _create_page_token(db_session, page_id="page_1"):
    config = ConnectorConfig(name="Meta Test Connector", connector_type=ConnectorType.facebook, is_active=True)
    db_session.add(config)
    db_session.flush()
    token = OAuthToken(
        connector_config_id=config.id,
        provider="meta",
        account_type="page",
        external_account_id=page_id,
        external_account_name="Test Page",
        access_token="page-token",
        scopes=["pages_manage_posts", "pages_read_user_content"],
        token_expires_at=datetime.now(UTC) + timedelta(days=30),
        is_active=True,
        metadata_={"category": "Business", "picture": "https://pic"},
    )
    db_session.add(token)
    db_session.commit()
    return token


def test_create_page_post_success(db_session):
    _create_page_token(db_session, page_id="page_123")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": "post_1"}
    mock_response.raise_for_status = MagicMock()

    with patch("app.services.meta_pages.httpx.AsyncClient") as mock_client:
        mock_instance = AsyncMock()
        mock_instance.request = AsyncMock(return_value=mock_response)
        mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_client.return_value.__aexit__ = AsyncMock(return_value=None)

        result = _run_async(
            meta_pages.create_page_post(
                db_session,
                page_id="page_123",
                message="Hello",
            )
        )

    assert result["id"] == "post_1"


def test_create_instagram_carousel_post_invalid_count():
    with pytest.raises(ValueError):
        _run_async(
            meta_pages.create_instagram_carousel_post(
                None,  # type: ignore[arg-type]
                "ig_123",
                ["only-one"],
            )
        )


def test_get_connected_pages(db_session):
    _create_page_token(db_session, page_id="page_123")
    pages = meta_pages.get_connected_pages(db_session)
    assert pages
    assert pages[0]["page_id"] == "page_123"
