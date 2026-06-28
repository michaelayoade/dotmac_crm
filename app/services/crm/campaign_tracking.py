"""Per-recipient campaign open/click tracking.

Email campaigns can embed a 1x1 tracking pixel (open detection) and rewrite
outbound links through a signed redirect endpoint (click detection). All link
redirects are HMAC-signed to prevent the endpoint being abused as an open
redirector. Tracking is opt-in via the ``notification.campaign_tracking_enabled``
setting and requires ``notification.campaign_tracking_base_url`` to be configured.

The recipient id is an unguessable UUID, so the open pixel needs no signature;
clicks carry the (base64url) destination URL plus an HMAC over
``recipient_id + "\\n" + url`` so the destination cannot be tampered with.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import logging
import re
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.crm.campaign import Campaign, CampaignRecipient
from app.models.domain_settings import SettingDomain
from app.services import settings_spec
from app.services.common import coerce_uuid

logger = logging.getLogger(__name__)

# 1x1 transparent GIF (43 bytes) returned by the open pixel endpoint.
TRACKING_PIXEL_GIF: bytes = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")

# Matches href="http(s)://..." / href='http(s)://...' in campaign HTML bodies.
_HREF_RE = re.compile(r"""(href\s*=\s*)(["'])(https?://[^"']+)\2""", re.IGNORECASE)


class CampaignTracking:
    """Open/click tracking for email campaigns (manager singleton)."""

    @staticmethod
    def is_enabled(db: Session) -> bool:
        return bool(settings_spec.resolve_value(db, SettingDomain.notification, "campaign_tracking_enabled"))

    @staticmethod
    def _base_url(db: Session) -> str | None:
        value = settings_spec.resolve_value(db, SettingDomain.notification, "campaign_tracking_base_url")
        if not value:
            return None
        return str(value).rstrip("/")

    @staticmethod
    def _secret(db: Session) -> str | None:
        # Reuse the application JWT secret for HMAC signing of click URLs.
        value = settings_spec.resolve_value(db, SettingDomain.auth, "jwt_secret")
        if not value:
            return None
        return str(value)

    # --- signing -----------------------------------------------------------

    @staticmethod
    def sign(recipient_id: str, url: str, secret: str) -> str:
        message = f"{recipient_id}\n{url}".encode()
        digest = hmac.new(secret.encode(), message, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).decode().rstrip("=")

    @staticmethod
    def verify(recipient_id: str, url: str, signature: str, secret: str) -> bool:
        expected = CampaignTracking.sign(recipient_id, url, secret)
        return hmac.compare_digest(expected, signature or "")

    @staticmethod
    def _encode_url(url: str) -> str:
        return base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")

    @staticmethod
    def decode_url(token: str) -> str | None:
        try:
            padding = "=" * (-len(token) % 4)
            return base64.urlsafe_b64decode((token + padding).encode()).decode()
        except (binascii.Error, ValueError, UnicodeDecodeError):
            return None

    # --- injection ---------------------------------------------------------

    @staticmethod
    def inject_tracking(db: Session, html: str | None, *, recipient_id, campaign_id=None) -> str | None:
        """Rewrite links through the signed click endpoint and append the open pixel.

        Returns the body unchanged when tracking is disabled/unconfigured or the
        body is empty. Safe to call for every recipient in the send loop.
        """
        if not html:
            return html
        if not CampaignTracking.is_enabled(db):
            return html
        base_url = CampaignTracking._base_url(db)
        secret = CampaignTracking._secret(db)
        if not base_url or not secret:
            logger.warning("Campaign tracking enabled but base_url/secret missing; skipping injection")
            return html

        rid = str(recipient_id)

        def _rewrite(match: re.Match[str]) -> str:
            prefix, quote, url = match.group(1), match.group(2), match.group(3)
            signature = CampaignTracking.sign(rid, url, secret)
            token = CampaignTracking._encode_url(url)
            tracked = f"{base_url}/track/email/c/{rid}?u={token}&s={signature}"
            return f"{prefix}{quote}{tracked}{quote}"

        rewritten = _HREF_RE.sub(_rewrite, html)

        pixel = (
            f'<img src="{base_url}/track/email/o/{rid}.gif" width="1" height="1" '
            'alt="" style="display:none;border:0;width:1px;height:1px" />'
        )
        lower = rewritten.lower()
        idx = lower.rfind("</body>")
        if idx != -1:
            return rewritten[:idx] + pixel + rewritten[idx:]
        return rewritten + pixel

    # --- recording ---------------------------------------------------------

    @staticmethod
    def _get_recipient(db: Session, recipient_id) -> CampaignRecipient | None:
        try:
            rid = coerce_uuid(recipient_id)
        except (ValueError, AttributeError):
            return None
        if rid is None:
            return None
        return db.get(CampaignRecipient, rid)

    @staticmethod
    def _bump_open_count(db: Session, recipient_id) -> None:
        """Atomically increment the recipient open counter (avoids lost updates
        under concurrent pixel prefetch). First-open semantics are handled by the
        caller via the gated ``opened_at`` read."""
        db.execute(
            update(CampaignRecipient)
            .where(CampaignRecipient.id == recipient_id)
            .values(open_count=CampaignRecipient.open_count + 1)
        )

    @staticmethod
    def _bump_click_count(db: Session, recipient_id) -> None:
        """Atomically increment the recipient click counter."""
        db.execute(
            update(CampaignRecipient)
            .where(CampaignRecipient.id == recipient_id)
            .values(click_count=CampaignRecipient.click_count + 1)
        )

    @staticmethod
    def record_open(db: Session, recipient_id) -> bool:
        """Record an email open. Returns True if the recipient was found."""
        recipient = CampaignTracking._get_recipient(db, recipient_id)
        if recipient is None:
            return False
        first_open = recipient.opened_at is None
        CampaignTracking._bump_open_count(db, recipient.id)
        if first_open:
            recipient.opened_at = datetime.now(UTC)
            campaign = db.get(Campaign, recipient.campaign_id)
            if campaign is not None:
                campaign.opened_count = (campaign.opened_count or 0) + 1
        db.commit()
        return True

    @staticmethod
    def record_click(db: Session, recipient_id, url: str, signature: str) -> str | None:
        """Verify the signed click and record it. Returns the destination URL to
        redirect to, or ``None`` when the signature is invalid / recipient unknown.

        A click also counts as an open (the message was clearly rendered).
        """
        recipient = CampaignTracking._get_recipient(db, recipient_id)
        if recipient is None:
            return None
        secret = CampaignTracking._secret(db)
        if not secret:
            return None
        rid = str(recipient.id)
        if not CampaignTracking.verify(rid, url, signature, secret):
            logger.warning("Rejected campaign click with invalid signature for recipient %s", rid)
            return None

        first_click = recipient.clicked_at is None
        CampaignTracking._bump_click_count(db, recipient.id)
        now = datetime.now(UTC)
        campaign = db.get(Campaign, recipient.campaign_id)
        if first_click:
            recipient.clicked_at = now
            if campaign is not None:
                campaign.clicked_count = (campaign.clicked_count or 0) + 1
        if recipient.opened_at is None:
            recipient.opened_at = now
            CampaignTracking._bump_open_count(db, recipient.id)
            if campaign is not None:
                campaign.opened_count = (campaign.opened_count or 0) + 1
        db.commit()
        return url


campaign_tracking = CampaignTracking()
