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
from urllib.parse import quote

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
# S3-compatible backend
# ---------------------------------------------------------------------------


class S3Backend(StorageBackend):
    """Stores files in an S3-compatible object store such as MinIO."""

    def __init__(
        self,
        *,
        endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        bucket: str | None = None,
        region: str | None = None,
        public_url: str | None = None,
        client=None,
    ) -> None:
        self._bucket = (bucket or settings.s3_bucket).strip()
        self._public_url = (public_url or settings.s3_public_url).rstrip("/")
        self._client = client or self._build_client(
            endpoint_url=endpoint_url or settings.s3_endpoint_url,
            access_key=access_key or settings.s3_access_key,
            secret_key=secret_key or settings.s3_secret_key,
            region=region or settings.s3_region,
        )

    def _build_client(self, *, endpoint_url: str, access_key: str, secret_key: str, region: str):
        import boto3  # type: ignore[import-untyped]

        return boto3.client(
            "s3",
            endpoint_url=endpoint_url or None,
            aws_access_key_id=access_key or None,
            aws_secret_access_key=secret_key or None,
            region_name=region or None,
        )

    def _normalize_key(self, key: str) -> str:
        normalized = key.lstrip("/")
        if not normalized or normalized in {".", ".."}:
            raise ValueError("Invalid storage key")
        parts = Path(normalized).parts
        if any(part == ".." for part in parts):
            raise ValueError(f"Invalid storage key (path traversal): {key}")
        return normalized

    def put(self, key: str, data: bytes, content_type: str = "") -> str:
        key = self._normalize_key(key)
        extra_args = {}
        if content_type:
            extra_args["ContentType"] = content_type
        self._client.put_object(Bucket=self._bucket, Key=key, Body=data, **extra_args)
        return self.url(key)

    def get(self, key: str) -> bytes:
        key = self._normalize_key(key)
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
        except self._client.exceptions.NoSuchKey as exc:
            raise FileNotFoundError(f"Storage key not found: {key}") from exc
        return response["Body"].read()

    def delete(self, key: str) -> None:
        key = self._normalize_key(key)
        self._client.delete_object(Bucket=self._bucket, Key=key)

    def url(self, key: str) -> str:
        key = self._normalize_key(key)
        quoted_key = quote(key, safe="/")
        if self._public_url:
            return f"{self._public_url}/{self._bucket}/{quoted_key}"
        return f"/admin/storage/{self._bucket}/{quoted_key}"

    def exists(self, key: str) -> bool:
        key = self._normalize_key(key)
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except self._client.exceptions.NoSuchKey:
            return False
        except Exception as exc:
            error_code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            if error_code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    def ensure_bucket(self) -> None:
        try:
            self._client.head_bucket(Bucket=self._bucket)
            return
        except Exception as exc:
            error_code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            if error_code not in {"404", "NoSuchBucket", "NotFound"}:
                raise

        params: dict[str, object] = {"Bucket": self._bucket}
        region = (settings.s3_region or "").strip()
        if region and region != "us-east-1":
            params["CreateBucketConfiguration"] = {"LocationConstraint": region}
        self._client.create_bucket(**params)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


def _build_backend() -> StorageBackend:
    backend = settings.storage_backend
    if backend == "s3":
        try:
            logger.info(
                "Using S3 storage backend (bucket=%s, endpoint=%s)", settings.s3_bucket, settings.s3_endpoint_url
            )
            return S3Backend()
        except ImportError:
            logger.warning("S3 storage backend requested but boto3 is not installed; falling back to local storage.")
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
