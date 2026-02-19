"""Storage abstraction for local filesystem backend.

Usage::

    from app.services.storage import storage

    url = storage.put("avatars/photo.jpg", data, "image/jpeg")
    data = storage.get("avatars/photo.jpg")
    storage.delete("avatars/photo.jpg")
"""

from __future__ import annotations

import logging
from pathlib import Path
from threading import Lock

from app.config import settings

logger = logging.getLogger(__name__)


class StorageBackend:
    """Base interface for storage backends."""

    def put(self, key: str, data: bytes, content_type: str = "") -> str:
        """Store *data* under *key* and return its public URL."""
        raise NotImplementedError

    def get(self, key: str) -> bytes:
        """Return raw bytes for *key*. Raises ``FileNotFoundError`` if missing."""
        raise NotImplementedError

    def delete(self, key: str) -> None:
        """Remove *key*. Silently ignores missing keys."""
        raise NotImplementedError

    def url(self, key: str) -> str:
        """Return the public URL for *key* (without fetching)."""
        raise NotImplementedError

    def exists(self, key: str) -> bool:
        """Return ``True`` if *key* exists in storage."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Local filesystem backend
# ---------------------------------------------------------------------------


class LocalBackend(StorageBackend):
    """Writes files under a local directory (default: ``static``)."""

    def __init__(
        self,
        root: str | None = None,
        url_prefix: str | None = None,
    ) -> None:
        self._root = Path(root or settings.storage_local_root).resolve()
        self._url_prefix = (url_prefix or settings.storage_local_url_prefix).rstrip("/")

    def _safe_path(self, key: str) -> Path:
        """Resolve *key* within root, rejecting traversal attempts."""
        dest = (self._root / key).resolve()
        try:
            dest.relative_to(self._root)
        except ValueError:
            raise ValueError(f"Invalid storage key (path traversal): {key}")
        return dest

    def put(self, key: str, data: bytes, content_type: str = "") -> str:
        dest = self._safe_path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return f"{self._url_prefix}/{key}"

    def get(self, key: str) -> bytes:
        dest = self._safe_path(key)
        if not dest.exists():
            raise FileNotFoundError(f"Storage key not found: {key}")
        return dest.read_bytes()

    def delete(self, key: str) -> None:
        dest = self._safe_path(key)
        if dest.exists():
            dest.unlink()

    def url(self, key: str) -> str:
        return f"{self._url_prefix}/{key}"

    def exists(self, key: str) -> bool:
        return self._safe_path(key).exists()


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


def _build_backend() -> StorageBackend:
    backend = settings.storage_backend
    if backend == "s3":
        logger.warning("S3 storage backend is not enabled in this build; falling back to local storage.")
        return LocalBackend()
    logger.info("Using local storage backend (root=%s)", settings.storage_local_root)
    return LocalBackend()


class LazyStorage(StorageBackend):
    """Lazy backend wrapper to avoid heavy imports during module import."""

    def __init__(self) -> None:
        self._backend: StorageBackend | None = None
        self._lock = Lock()

    def _get_backend(self) -> StorageBackend:
        if self._backend is not None:
            return self._backend
        with self._lock:
            if self._backend is None:
                self._backend = _build_backend()
        return self._backend

    def put(self, key: str, data: bytes, content_type: str = "") -> str:
        return self._get_backend().put(key, data, content_type)

    def get(self, key: str) -> bytes:
        return self._get_backend().get(key)

    def delete(self, key: str) -> None:
        self._get_backend().delete(key)

    def url(self, key: str) -> str:
        return self._get_backend().url(key)

    def exists(self, key: str) -> bool:
        return self._get_backend().exists(key)

    def ensure_bucket(self) -> None:
        backend = self._get_backend()
        if hasattr(backend, "ensure_bucket"):
            backend.ensure_bucket()


storage: StorageBackend = LazyStorage()
