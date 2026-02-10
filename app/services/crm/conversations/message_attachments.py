import uuid
from pathlib import Path
from typing import cast

from fastapi import HTTPException, UploadFile

from app.config import settings


def _allowed_types() -> set[str]:
    raw = settings.message_attachment_allowed_types
    return {item.strip() for item in raw.split(",") if item.strip()}


def _validate_attachment(file: UploadFile, content: bytes) -> None:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Attachment filename required")
    if len(content) > settings.message_attachment_max_size_bytes:
        raise HTTPException(status_code=413, detail="Attachment too large")
    allowed = _allowed_types()
    if allowed and file.content_type not in allowed:
        raise HTTPException(status_code=400, detail="Unsupported attachment type")


def _is_upload_like(item: object) -> bool:
    return hasattr(item, "filename") and hasattr(item, "file")


def _coerce_upload_files(files: UploadFile | list[UploadFile] | tuple[UploadFile, ...] | None) -> list[UploadFile]:
    if files is None:
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
    raise HTTPException(
        status_code=400,
        detail="Attachment upload failed. Please refresh and try again.",
    )


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
        await file.close()
    return prepared


def save_message_attachments(prepared: list[dict]) -> list[dict]:
    if not prepared:
        return []
    upload_dir = Path(settings.message_attachment_upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    saved: list[dict] = []
    for item in prepared:
        file_path = upload_dir / item["stored_name"]
        with open(file_path, "wb") as handle:
            handle.write(item["content"])
        saved.append(
            {
                "stored_name": item["stored_name"],
                "file_name": item["file_name"],
                "file_size": item["file_size"],
                "mime_type": item["mime_type"],
                "url": f"{settings.message_attachment_url_prefix}/{item['stored_name']}",
            }
        )
    return saved


class MessageAttachments:
    @staticmethod
    async def prepare(
        files: UploadFile | list[UploadFile] | tuple[UploadFile, ...] | None,
    ) -> list[dict]:
        return await prepare_message_attachments(files)

    @staticmethod
    def save(prepared: list[dict]) -> list[dict]:
        return save_message_attachments(prepared)


# Singleton instance
message_attachments = MessageAttachments()
