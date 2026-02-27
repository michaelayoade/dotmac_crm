import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

from app.config import settings
from app.services.storage import storage


def get_allowed_types() -> set[str]:
    return set(settings.avatar_allowed_types.split(","))


def validate_avatar(file: UploadFile) -> None:
    allowed_types = get_allowed_types()
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type. Allowed: {', '.join(allowed_types)}",
        )


async def save_avatar(file: UploadFile, person_id: str) -> str:
    validate_avatar(file)

    ext = _get_extension(file.content_type)
    filename = f"{person_id}_{uuid.uuid4().hex[:8]}{ext}"
    key = f"avatars/{filename}"

    content = await file.read()
    if len(content) > settings.avatar_max_size_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {settings.avatar_max_size_bytes // 1024 // 1024}MB",
        )

    upload_dir = getattr(settings, "avatar_upload_dir", None)
    url_prefix = (getattr(settings, "avatar_url_prefix", "") or "").rstrip("/")
    if upload_dir and url_prefix:
        dest = Path(upload_dir) / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        return f"{url_prefix}/{filename}"

    return storage.put(key, content, file.content_type or "")


def delete_avatar(avatar_url: str | None) -> None:
    if not avatar_url:
        return

    url_prefix = (getattr(settings, "avatar_url_prefix", "") or "").rstrip("/")
    upload_dir = getattr(settings, "avatar_upload_dir", None)
    if upload_dir and url_prefix and avatar_url.startswith(f"{url_prefix}/"):
        filename = avatar_url[len(url_prefix) + 1 :].strip()
        if filename:
            # Security check: prevent path traversal
            try:
                # Resolve both paths to absolute real paths
                upload_dir_path = Path(upload_dir).resolve()
                # Construct and resolve the target path
                target_path = (Path(upload_dir) / filename).resolve()
                # Ensure the target path stays within the upload directory
                if not target_path.is_relative_to(upload_dir_path):
                    # Path escapes the upload directory - potential path traversal attack
                    return
                # Use the resolved target path for existence check and deletion
                if target_path.exists():
                    target_path.unlink()
            except (ValueError, RuntimeError):
                # If resolution fails or paths can't be compared, abort deletion
                return
        return

    # Extract key from URL — works for both local (/static/uploads/avatars/...)
    # and S3 (.../bucket/avatars/...) URLs
    marker = "avatars/"
    idx = avatar_url.find(marker)
    if idx == -1:
        return
    key = avatar_url[idx:]
    storage.delete(key)


def _get_extension(content_type: str | None) -> str:
    extensions = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
    }
    return extensions.get(content_type or "", ".jpg")
