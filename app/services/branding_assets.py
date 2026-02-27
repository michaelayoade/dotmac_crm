from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

from app.config import settings
from app.services.common import validate_upload_mime
from app.services.storage import storage

_IMAGE_UPLOAD_MIMES = [
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
]

_EXT_BY_TYPE = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


def _validate_upload(file: UploadFile, content: bytes, kind: str) -> str:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required.")
    if kind == "logo":
        max_size = settings.branding_logo_max_size_bytes
    else:
        max_size = settings.branding_favicon_max_size_bytes
    if len(content) > max_size:
        raise HTTPException(
            status_code=400,
            detail=f"{kind.title()} file too large (max {max_size} bytes).",
        )
    return validate_upload_mime(content, _IMAGE_UPLOAD_MIMES, label=kind)


def _delete_previous(previous_url: str | None) -> None:
    if not previous_url:
        return
    marker = "uploads/branding/"
    idx = previous_url.find(marker)
    if idx == -1:
        return
    key = previous_url[idx:]
    storage.delete(key)


async def save_branding_asset(
    file: UploadFile,
    kind: str,
    previous_url: str | None = None,
) -> str:
    content = await file.read()
    detected_mime = _validate_upload(file, content, kind)
    ext = _EXT_BY_TYPE.get(detected_mime, "")
    if not ext and file.filename:
        ext = Path(file.filename).suffix or ""
    filename = f"{kind}_{uuid.uuid4().hex}{ext}"
    key = f"uploads/branding/{filename}"
    url = storage.put(key, content, detected_mime)
    _delete_previous(previous_url)
    return url
