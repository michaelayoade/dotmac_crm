"""Authenticated file proxy for attachments stored in S3/MinIO.

We store attachment URLs in DB metadata. In some environments MinIO is not
publicly reachable (or S3_PUBLIC_URL accidentally points at localhost). This
route allows the web UI to download objects through the app using the
configured storage backend.
"""

from __future__ import annotations

import mimetypes

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.config import settings
from app.services.storage import storage

router = APIRouter(prefix="/storage", tags=["web-admin-storage"])


@router.get("/{bucket}/{key:path}")
def get_object(bucket: str, key: str):
    # Prevent fetching from unexpected buckets.
    if bucket != settings.s3_bucket:
        raise HTTPException(status_code=404, detail="Not found")

    # Basic safety: only allow fetching uploaded content.
    if not key or not (key.startswith("uploads/") or key.startswith("/uploads/")):
        raise HTTPException(status_code=404, detail="Not found")
    key = key.lstrip("/")

    try:
        data = storage.get(key)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Not found")

    ctype, _enc = mimetypes.guess_type(key)
    return Response(content=data, media_type=ctype or "application/octet-stream")
