import contextlib
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from fastapi import HTTPException
from starlette.datastructures import UploadFile

from app.config import settings


def _allowed_types() -> set[str]:
    raw = settings.ticket_attachment_allowed_types
    return {item.strip() for item in raw.split(",") if item.strip()}


def _validate_attachment(file: UploadFile, content: bytes) -> None:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Attachment filename required")
    if len(content) > settings.ticket_attachment_max_size_bytes:
        raise HTTPException(status_code=413, detail="Attachment too large")
    allowed = _allowed_types()
    if allowed and file.content_type not in allowed:
        raise HTTPException(status_code=400, detail="Unsupported attachment type")


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
        _validate_attachment(file, content)
        stored_name = f"{uuid.uuid4().hex}{Path(file.filename).suffix}"
        prepared.append(
            {
                "stored_name": stored_name,
                "file_name": file.filename,
                "file_size": len(content),
                "mime_type": file.content_type or "application/octet-stream",
                "content": content,
            }
        )
        file.file.close()
    return prepared


def save_ticket_attachments(prepared: list[dict]) -> list[dict]:
    if not prepared:
        return []
    upload_dir = Path(settings.ticket_attachment_upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    saved: list[dict] = []
    for item in prepared:
        file_path = upload_dir / item["stored_name"]
        with open(file_path, "wb") as handle:
            handle.write(item["content"])
        saved.append(
            {
                "file_name": item["file_name"],
                "file_size": item["file_size"],
                "mime_type": item["mime_type"],
                "url": f"{settings.ticket_attachment_url_prefix}/{item['stored_name']}",
            }
        )
    return saved


def delete_ticket_attachments(prepared: list[dict]) -> None:
    if not prepared:
        return
    upload_dir = Path(settings.ticket_attachment_upload_dir)
    for item in prepared:
        file_path = upload_dir / item["stored_name"]
        if file_path.exists():
            file_path.unlink()
