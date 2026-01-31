from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

from app.config import settings

_LOGO_ALLOWED_TYPES = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/svg+xml",
}

_FAVICON_ALLOWED_TYPES = {
    "image/x-icon",
    "image/vnd.microsoft.icon",
    "image/png",
    "image/svg+xml",
}

_EXT_BY_TYPE = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/x-icon": ".ico",
    "image/vnd.microsoft.icon": ".ico",
}


def _validate_upload(file: UploadFile, content: bytes, kind: str) -> None:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required.")
    content_type = file.content_type or ""
    if kind == "logo":
        allowed = _LOGO_ALLOWED_TYPES
        max_size = settings.branding_logo_max_size_bytes
    else:
        allowed = _FAVICON_ALLOWED_TYPES
        max_size = settings.branding_favicon_max_size_bytes
    if content_type not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {kind} type. Allowed: {', '.join(sorted(allowed))}.",
        )
    if len(content) > max_size:
        raise HTTPException(
            status_code=400,
            detail=f"{kind.title()} file too large (max {max_size} bytes).",
        )


def _delete_previous(previous_url: str | None) -> None:
    if not previous_url:
        return
    prefix = settings.branding_url_prefix.rstrip("/") + "/"
    if not previous_url.startswith(prefix):
        return
    filename = previous_url.replace(prefix, "", 1)
    if not filename:
        return
    file_path = Path(settings.branding_upload_dir) / filename
    if file_path.exists():
        file_path.unlink()


async def save_branding_asset(
    file: UploadFile,
    kind: str,
    previous_url: str | None = None,
) -> str:
    content = await file.read()
    _validate_upload(file, content, kind)
    ext = _EXT_BY_TYPE.get(file.content_type or "", "")
    if not ext and file.filename:
        ext = Path(file.filename).suffix or ""
    filename = f"{kind}_{uuid.uuid4().hex}{ext}"
    upload_dir = Path(settings.branding_upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = upload_dir / filename
    file_path.write_bytes(content)
    _delete_previous(previous_url)
    return f"{settings.branding_url_prefix}/{filename}"
