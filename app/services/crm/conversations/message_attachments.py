import logging
import os
import uuid
from pathlib import Path
from typing import cast

from fastapi import HTTPException, UploadFile

from app.config import settings
from app.services.common import validate_upload_mime
from app.services.storage import storage

logger = logging.getLogger(__name__)


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
    if len(content) > settings.message_attachment_max_size_bytes:
        raise HTTPException(status_code=413, detail="Attachment too large")
    return validate_upload_mime(content, _DOCUMENT_UPLOAD_MIMES, label="attachment")


def _is_upload_like(item: object) -> bool:
    return hasattr(item, "filename") and hasattr(item, "file")


def _coerce_upload_files(files: UploadFile | list[UploadFile] | tuple[UploadFile, ...] | None) -> list[UploadFile]:
    if files is None:
        return []
    if isinstance(files, str | bytes | bytearray):
        # Some multipart parsers can surface an empty scalar for an untouched
        # file input; treat that as "no attachments".
        raw = str(files).strip()
        if raw:
            logger.warning(
                "Ignoring non-file attachment scalar payload type=%s value=%r", type(files).__name__, raw[:120]
            )
        return []
    if isinstance(files, UploadFile):
        if not files.filename:
            return []
        return [files]
    if _is_upload_like(files) and not isinstance(files, list | tuple):
        upload = cast(UploadFile, files)
        if getattr(upload, "filename", None):
            return [upload]  # Accept UploadFile-like objects
    if isinstance(files, list | tuple):
        uploads: list[UploadFile] = []
        for item in files:
            if isinstance(item, UploadFile):
                if item.filename:
                    uploads.append(item)
            elif _is_upload_like(item) and getattr(item, "filename", None):
                uploads.append(item)
        return uploads
    logger.warning("Ignoring unsupported attachment payload type=%s", type(files).__name__)
    return []


async def prepare_message_attachments(
    files: UploadFile | list[UploadFile] | tuple[UploadFile, ...] | None,
) -> list[dict]:
    uploads = _coerce_upload_files(files)
    if not uploads:
        return []
    prepared: list[dict] = []
    for file in uploads:
        if not file.filename:
            continue
        content = await file.read()
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
        await file.close()
    return prepared


def save_message_attachments(prepared: list[dict]) -> list[dict]:
    if not prepared:
        return []
    saved: list[dict] = []
    app_url = (os.getenv("APP_URL") or "").rstrip("/")
    for item in prepared:
        key = f"uploads/messages/{item['stored_name']}"
        url = storage.put(key, item["content"], item["mime_type"])
        if settings.storage_backend == "s3":
            proxy_path = f"/admin/storage/{settings.s3_bucket}/{key}"
            url = f"{app_url}{proxy_path}" if app_url else proxy_path
        saved.append(
            {
                "stored_name": item["stored_name"],
                "file_name": item["file_name"],
                "file_size": item["file_size"],
                "mime_type": item["mime_type"],
                "url": url,
            }
        )
    return saved


class MessageAttachments:
    settings = settings
    storage = storage

    @staticmethod
    async def prepare(
        files: UploadFile | list[UploadFile] | tuple[UploadFile, ...] | None,
    ) -> list[dict]:
        return await prepare_message_attachments(files)

    def save_message_attachments(self, prepared: list[dict]) -> list[dict]:
        if not prepared:
            return []
        saved: list[dict] = []
        app_url = (os.getenv("APP_URL") or "").rstrip("/")
        for item in prepared:
            key = f"uploads/messages/{item['stored_name']}"
            url = self.storage.put(key, item["content"], item["mime_type"])
            if self.settings.storage_backend == "s3":
                proxy_path = f"/admin/storage/{self.settings.s3_bucket}/{key}"
                url = f"{app_url}{proxy_path}" if app_url else proxy_path
            saved.append(
                {
                    "stored_name": item["stored_name"],
                    "file_name": item["file_name"],
                    "file_size": item["file_size"],
                    "mime_type": item["mime_type"],
                    "url": url,
                }
            )
        return saved

    def save(self, prepared: list[dict]) -> list[dict]:
        return self.save_message_attachments(prepared)


# Singleton instance
message_attachments = MessageAttachments()
