import asyncio
import importlib
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.services import branding_assets, ticket_attachments
from app.services.upload_validation import detect_upload_mime, validate_upload_mime

message_attachments = importlib.import_module("app.services.crm.conversations.message_attachments")

JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x02"
PNG_BYTES = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
PDF_BYTES = b"%PDF-1.7\n1 0 obj\n"
SVG_BYTES = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"></svg>'


def _run_async(coro):
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, coro).result()


def _upload(name: str, content_type: str, content: bytes) -> StarletteUploadFile:
    return StarletteUploadFile(filename=name, file=BytesIO(content), headers={"content-type": content_type})


class _AsyncUpload:
    def __init__(self, name: str, content_type: str, content: bytes):
        self.filename = name
        self.content_type = content_type
        self.file = BytesIO(content)
        self._content = content

    async def read(self) -> bytes:
        return self._content

    async def close(self) -> None:
        self.file.close()


def test_detect_upload_mime_supported_types():
    assert detect_upload_mime(JPEG_BYTES) == "image/jpeg"
    assert detect_upload_mime(PNG_BYTES) == "image/png"
    assert detect_upload_mime(b"GIF89a......") == "image/gif"
    assert detect_upload_mime(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert detect_upload_mime(PDF_BYTES) == "application/pdf"
    assert detect_upload_mime(SVG_BYTES) == "image/svg+xml"
    assert detect_upload_mime(b"\x00\x00\x01\x00\x02\x00") == "image/x-icon"
    assert detect_upload_mime(b"\x00\x00\x00\x18ftypheic\x00\x00") == "image/heic"


def test_validate_upload_mime_rejects_declared_type_mismatch():
    with pytest.raises(HTTPException) as exc_info:
        validate_upload_mime(PDF_BYTES, "image/png", {"image/png", "application/pdf"}, "attachment")

    assert exc_info.value.status_code == 400
    assert "does not match declared type" in exc_info.value.detail


def test_branding_asset_rejects_spoofed_logo(monkeypatch):
    settings = MagicMock()
    settings.branding_logo_max_size_bytes = 1024
    settings.branding_favicon_max_size_bytes = 1024
    monkeypatch.setattr(branding_assets, "settings", settings)

    file = AsyncMock(spec=UploadFile)
    file.filename = "logo.png"
    file.content_type = "image/png"
    file.read.return_value = b"not a png"

    with pytest.raises(HTTPException) as exc_info:
        _run_async(branding_assets.save_branding_asset(file, "logo"))

    assert exc_info.value.status_code == 400
    assert "Invalid logo content" in exc_info.value.detail


def test_message_attachment_rejects_spoofed_pdf(monkeypatch):
    settings = SimpleNamespace(
        message_attachment_allowed_types="image/jpeg,image/png,application/pdf",
        message_attachment_max_size_bytes=1024,
    )
    monkeypatch.setattr(message_attachments, "settings", settings)

    file = _AsyncUpload("invoice.pdf", "application/pdf", b"not a pdf")

    with pytest.raises(HTTPException) as exc_info:
        _run_async(message_attachments.prepare_message_attachments(file))

    assert exc_info.value.status_code == 400
    assert "Invalid attachment content" in exc_info.value.detail


def test_ticket_attachment_rejects_spoofed_image(monkeypatch):
    settings = SimpleNamespace(
        ticket_attachment_allowed_types="image/jpeg,image/png,application/pdf",
        ticket_attachment_max_size_bytes=1024,
    )
    monkeypatch.setattr(ticket_attachments, "settings", settings)

    file = _upload("photo.jpg", "image/jpeg", b"not a jpeg")

    with pytest.raises(HTTPException) as exc_info:
        ticket_attachments.prepare_ticket_attachments(file)

    assert exc_info.value.status_code == 400
    assert "Invalid attachment content" in exc_info.value.detail
