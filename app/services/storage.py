"""Storage abstraction for local filesystem backend.

Usage::

    from app.services.storage import storage

    url = storage.put("avatars/photo.jpg", data, "image/jpeg")
    data = storage.get("avatars/photo.jpg")
    storage.delete("avatars/photo.jpg")
"""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path
from threading import Lock
from typing import BinaryIO, Protocol
from urllib.parse import urlparse

from app.config import settings

logger = logging.getLogger(__name__)


class _ObjectResponse(Protocol):
    def read(self) -> bytes: ...
    def close(self) -> None: ...
    def release_conn(self) -> None: ...


class _ObjectStorageClient(Protocol):
    def bucket_exists(self, bucket_name: str) -> bool: ...
    def make_bucket(self, bucket_name: str) -> None: ...
    def put_object(
        self,
        bucket_name: str,
        object_name: str,
        data: BinaryIO,
        length: int,
        content_type: str = "",
    ) -> object: ...
    def get_object(self, bucket_name: str, object_name: str) -> _ObjectResponse: ...
    def remove_object(self, bucket_name: str, object_name: str) -> object: ...
    def stat_object(self, bucket_name: str, object_name: str) -> object: ...


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
# S3/MinIO backend
# ---------------------------------------------------------------------------


class MinioBackend(StorageBackend):
    """Writes files to MinIO/S3-compatible object storage."""

    def __init__(
        self,
        endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        bucket: str | None = None,
        public_url: str | None = None,
        region: str | None = None,
        client: _ObjectStorageClient | None = None,
    ) -> None:
        self._endpoint_url = endpoint_url or settings.s3_endpoint_url
        self._bucket = (bucket or settings.s3_bucket).strip()
        self._public_url = (public_url or settings.s3_public_url).rstrip("/")
        self._region = region or settings.s3_region

        if not self._bucket:
            raise ValueError("S3 bucket is not configured.")

        client_obj = client
        if client_obj is None:
            client_obj = self._build_client(
                access_key=access_key or settings.s3_access_key,
                secret_key=secret_key or settings.s3_secret_key,
            )
        self._client: _ObjectStorageClient = client_obj

    def _build_client(self, access_key: str, secret_key: str) -> _ObjectStorageClient:
        if not access_key or not secret_key:
            raise ValueError("S3 credentials are not configured (S3_ACCESS_KEY / S3_SECRET_KEY).")
        try:
            from minio import Minio
        except ImportError as exc:
            raise RuntimeError(
                "MinIO SDK is not installed. Install package 'minio' to use STORAGE_BACKEND=s3."
            ) from exc

        endpoint, secure = self._parse_endpoint(self._endpoint_url)
        return Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
            region=self._region or None,
        )

    @staticmethod
    def _parse_endpoint(endpoint_url: str) -> tuple[str, bool]:
        parsed = urlparse(endpoint_url)
        if parsed.scheme:
            if not parsed.netloc:
                raise ValueError(f"Invalid S3 endpoint URL: {endpoint_url}")
            return parsed.netloc, parsed.scheme.lower() == "https"
        return endpoint_url, False

    @staticmethod
    def _normalize_key(key: str) -> str:
        normalized = key.strip().lstrip("/")
        if not normalized:
            raise ValueError("Storage key cannot be empty.")
        parts = [part for part in normalized.split("/") if part not in ("", ".")]
        if any(part == ".." for part in parts):
            raise ValueError(f"Invalid storage key (path traversal): {key}")
        return "/".join(parts)

    @staticmethod
    def _is_not_found_error(exc: Exception) -> bool:
        code = getattr(exc, "code", "") or ""
        return code in {"NoSuchKey", "NoSuchObject", "NoSuchBucket", "NoSuchVersion"}

    def ensure_bucket(self) -> None:
        if not self._client.bucket_exists(self._bucket):
            logger.info("Creating storage bucket '%s'", self._bucket)
            self._client.make_bucket(self._bucket)

    def put(self, key: str, data: bytes, content_type: str = "") -> str:
        normalized = self._normalize_key(key)
        self._client.put_object(
            self._bucket,
            normalized,
            BytesIO(data),
            length=len(data),
            content_type=content_type or "application/octet-stream",
        )
        return self.url(normalized)

    def get(self, key: str) -> bytes:
        normalized = self._normalize_key(key)
        try:
            response = self._client.get_object(self._bucket, normalized)
        except Exception as exc:
            if self._is_not_found_error(exc):
                raise FileNotFoundError(f"Storage key not found: {normalized}") from exc
            raise
        try:
            return response.read()
        finally:
            if hasattr(response, "close"):
                response.close()
            if hasattr(response, "release_conn"):
                response.release_conn()

    def delete(self, key: str) -> None:
        normalized = self._normalize_key(key)
        try:
            self._client.remove_object(self._bucket, normalized)
        except Exception as exc:
            if not self._is_not_found_error(exc):
                raise

    def url(self, key: str) -> str:
        normalized = self._normalize_key(key)
        return f"{self._public_url}/{self._bucket}/{normalized}"

    def exists(self, key: str) -> bool:
        normalized = self._normalize_key(key)
        try:
            self._client.stat_object(self._bucket, normalized)
            return True
        except Exception as exc:
            if self._is_not_found_error(exc):
                return False
            raise


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


def _build_backend() -> StorageBackend:
    backend = settings.storage_backend
    if backend == "s3":
        logger.info("Using S3/MinIO storage backend (endpoint=%s, bucket=%s)", settings.s3_endpoint_url, settings.s3_bucket)
        return MinioBackend()
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
