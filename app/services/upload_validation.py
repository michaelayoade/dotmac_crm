from __future__ import annotations

import re

from fastapi import HTTPException

_HEIF_BRANDS = {
    b"heic",
    b"heix",
    b"hevc",
    b"hevx",
    b"mif1",
    b"msf1",
}

_COMPATIBLE_MIME_TYPES = {
    "image/jpeg": {"image/jpeg", "image/jpg"},
    "image/png": {"image/png"},
    "image/gif": {"image/gif"},
    "image/webp": {"image/webp"},
    "image/heic": {"image/heic", "image/heif", "image/heic-sequence", "image/heif-sequence"},
    "image/svg+xml": {"image/svg+xml"},
    "image/x-icon": {"image/x-icon", "image/vnd.microsoft.icon"},
    "application/pdf": {"application/pdf"},
}


def detect_upload_mime(content: bytes) -> str | None:
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    if content.startswith(b"\x00\x00\x01\x00"):
        return "image/x-icon"
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    if len(content) >= 12 and content[4:8] == b"ftyp" and content[8:12] in _HEIF_BRANDS:
        return "image/heic"

    prefix = content[:512].lstrip(b"\xef\xbb\xbf\r\n\t ")
    if re.match(rb"^<\?xml\b", prefix, flags=re.IGNORECASE):
        prefix = re.sub(rb"^<\?xml[^>]*>\s*", b"", prefix, count=1, flags=re.IGNORECASE)
    if re.match(rb"^<svg(?:\s|>|:)", prefix, flags=re.IGNORECASE):
        return "image/svg+xml"
    return None


def validate_upload_mime(content: bytes, declared_mime: str | None, allowed_mimes: set[str], label: str) -> str:
    detected = detect_upload_mime(content)
    if not detected:
        raise HTTPException(status_code=400, detail=f"Invalid {label} content")
    if detected not in allowed_mimes:
        raise HTTPException(status_code=400, detail=f"Invalid {label} content type")

    declared = (declared_mime or "").strip().lower()
    compatible = _COMPATIBLE_MIME_TYPES.get(detected, {detected})
    if declared and declared not in compatible:
        raise HTTPException(status_code=400, detail=f"{label.title()} content does not match declared type")
    return detected
