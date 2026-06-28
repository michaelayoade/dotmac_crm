"""Public (no-auth) endpoints for campaign email open/click tracking.

The capability is the unguessable recipient UUID baked into the email; clicks
additionally carry an HMAC signature so the redirect cannot be tampered with.
Mounted at the root path (and under ``/api/v1``) without auth dependencies.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.crm.campaign_tracking import TRACKING_PIXEL_GIF, campaign_tracking

logger = logging.getLogger(__name__)

public_router = APIRouter(prefix="/track/email", tags=["crm-campaign-tracking"])

_NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, private",
    "Pragma": "no-cache",
    "Expires": "0",
}


@public_router.get("/o/{recipient_id}.gif")
def track_open(recipient_id: str, db: Session = Depends(get_db)) -> Response:
    """Open-tracking pixel. Records the open and always returns a 1x1 GIF.

    Always responds 200 with the pixel — even for unknown recipients or when
    recording fails — so a forged/stale id or a DB error never breaks the
    rendered email.
    """
    try:
        campaign_tracking.record_open(db, recipient_id)
    except Exception:
        # Recording is best-effort: never let a failure break the email image.
        db.rollback()
        logger.warning("Failed to record campaign open for %s", recipient_id, exc_info=True)
    return Response(content=TRACKING_PIXEL_GIF, media_type="image/gif", headers=_NO_CACHE_HEADERS)


@public_router.get("/c/{recipient_id}")
def track_click(
    recipient_id: str,
    u: str,
    s: str,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Click-tracking redirect. Verifies the HMAC signature over the destination
    URL, records the click, then 302-redirects to the original link.

    A bad/missing signature or undecodable URL is rejected (400) rather than
    redirected, so this endpoint can never be used as an open redirector.
    """
    url = campaign_tracking.decode_url(u)
    if not url:
        raise HTTPException(status_code=400, detail="Invalid tracking link")
    destination = campaign_tracking.record_click(db, recipient_id, url, s)
    if destination is None:
        raise HTTPException(status_code=400, detail="Invalid tracking link")
    return RedirectResponse(url=destination, status_code=302, headers=_NO_CACHE_HEADERS)
