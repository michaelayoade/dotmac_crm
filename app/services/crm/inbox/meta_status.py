"""Meta (Facebook/Instagram) connection status for admin UI."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.connector import ConnectorConfig, ConnectorType
from app.models.integration import IntegrationTarget, IntegrationTargetType
from app.models.oauth_token import OAuthToken


def get_meta_connection_status(db: Session) -> dict:
    """Get Meta connection status for admin UI."""
    target = (
        db.query(IntegrationTarget)
        .join(ConnectorConfig, ConnectorConfig.id == IntegrationTarget.connector_config_id)
        .filter(IntegrationTarget.target_type == IntegrationTargetType.crm)
        .filter(ConnectorConfig.connector_type == ConnectorType.facebook)
        .first()
    )
    if not target or not target.connector_config:
        return {"connected": False, "pages": [], "instagram_accounts": []}

    page_tokens = (
        db.query(OAuthToken)
        .filter(OAuthToken.connector_config_id == target.connector_config_id)
        .filter(OAuthToken.provider == "meta")
        .filter(OAuthToken.account_type == "page")
        .filter(OAuthToken.is_active.is_(True))
        .all()
    )

    instagram_tokens = (
        db.query(OAuthToken)
        .filter(OAuthToken.connector_config_id == target.connector_config_id)
        .filter(OAuthToken.provider == "meta")
        .filter(OAuthToken.account_type == "instagram_business")
        .filter(OAuthToken.is_active.is_(True))
        .all()
    )

    pages = []
    for token in page_tokens:
        metadata = token.metadata_ or {}
        pages.append(
            {
                "id": token.external_account_id,
                "name": token.external_account_name,
                "picture": metadata.get("picture"),
                "category": metadata.get("category"),
                "expires_at": token.token_expires_at,
                "needs_refresh": token.should_refresh(),
                "has_error": bool(token.refresh_error),
            }
        )

    instagram_accounts = []
    for token in instagram_tokens:
        metadata = token.metadata_ or {}
        instagram_accounts.append(
            {
                "id": token.external_account_id,
                "username": token.external_account_name,
                "profile_picture_url": metadata.get("profile_picture_url"),
                "expires_at": token.token_expires_at,
                "needs_refresh": token.should_refresh(),
                "has_error": bool(token.refresh_error),
            }
        )

    return {
        "connected": len(pages) > 0,
        "pages": pages,
        "instagram_accounts": instagram_accounts,
    }
