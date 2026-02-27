import contextlib
import os
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from fastapi import HTTPException
from starlette.datastructures import UploadFile

from app.config import settings
from app.services.common import validate_upload_mime
from app.services.storage import storage

_IMAGE_UPLOAD_MIMES = [
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
]
_DOCUMENT_UPLOAD_MIMES = [*_IMAGE_UPLOAD_MIMES, "application/pdf"]


def _validate_attachment(file: UploadFile, content: bytes) -> str:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Attachment filename required")
    if len(content) > settings.ticket_attachment_max_size_bytes:
        raise HTTPException(status_code=413, detail="Attachment too large")
    return validate_upload_mime(content, _DOCUMENT_UPLOAD_MIMES, label="attachment")


def _is_upload_like(item: object) -> bool:
    return hasattr(item, "filename") and hasattr(item, "file")


def _coerce_upload_files(
    files: UploadFile | Sequence[UploadFile | str] | str | bytes | None,
) -> list[UploadFile]:
    if files is None:
        return []
    if isinstance(files, UploadFile):
        if not files.filename:
            return []
        return [files]
    if isinstance(files, str | bytes):
        return []
    if isinstance(files, Sequence):
        uploads: list[UploadFile] = []
        for item in files:
            if isinstance(item, UploadFile):
                if item.filename:
                    uploads.append(item)
            elif _is_upload_like(item) and getattr(item, "filename", None):
                uploads.append(cast(UploadFile, item))
        return uploads
    # Be permissive with unexpected types (e.g., stray form fields) to avoid 400s.
    return []


def prepare_ticket_attachments(
    files: UploadFile | Sequence[UploadFile | str] | str | bytes | None,
) -> list[dict]:
    uploads = _coerce_upload_files(files)
    if not uploads:
        return []
    prepared: list[dict] = []
    for file in uploads:
        if not file.filename:
            continue
        with contextlib.suppress(Exception):
            file.file.seek(0)
        content = file.file.read()
        if content is None:
            content = b""
        detected_mime = _validate_attachment(file, content)
        stored_name = f"{uuid.uuid4().hex}{Path(file.filename).suffix}"
        prepared.append(
            {
                "stored_name": stored_name,
                "file_name": file.filename,
                "file_size": len(content),
                "mime_type": detected_mime,
                "content": content,
            }
        )
        file.file.close()
    return prepared


def save_ticket_attachments(prepared: list[dict]) -> list[dict]:
    if not prepared:
        return []
    saved: list[dict] = []
    # app.config.Settings doesn't currently expose APP_URL; fall back to env.
    app_url = (getattr(settings, "app_url", None) or os.getenv("APP_URL") or "").rstrip("/")
    for item in prepared:
        key = f"uploads/tickets/{item['stored_name']}"
        storage.put(key, item["content"], item["mime_type"])
        # Serve through the authenticated app route so we don't depend on MinIO
        # being publicly reachable (and avoid localhost links in prod).
        url = (
            f"{app_url}/admin/storage/{settings.s3_bucket}/{key}"
            if app_url
            else f"/admin/storage/{settings.s3_bucket}/{key}"
        )
        saved.append(
            {
                "file_name": item["file_name"],
                "file_size": item["file_size"],
                "mime_type": item["mime_type"],
                "key": key,
                "url": url,
            }
        )
    return saved


def delete_ticket_attachments(prepared: list[dict]) -> None:
    if not prepared:
        return
    for item in prepared:
        key = f"uploads/tickets/{item['stored_name']}"
        storage.delete(key)
