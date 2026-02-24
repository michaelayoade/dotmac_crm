"""Public signed media endpoints for outbound channel providers."""

from __future__ import annotations

import mimetypes

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from app.services import public_media
from app.services.storage import storage

router = APIRouter(prefix="/public/media", tags=["web-public-media"])


@router.get("/messages/{stored_name}")
def get_public_message_media(
    stored_name: str,
    exp: int = Query(...),
    sig: str = Query(...),
):
    if not public_media.is_valid_stored_name(stored_name):
        raise HTTPException(status_code=404, detail="Not found")
    if not public_media.verify_media_signature(stored_name, exp, sig):
        raise HTTPException(status_code=403, detail="Invalid media signature")

    key = f"uploads/messages/{stored_name}"
    try:
        data = storage.get(key)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Not found")

    ctype, _enc = mimetypes.guess_type(stored_name)
    return Response(content=data, media_type=ctype or "application/octet-stream")
