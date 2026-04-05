"""Unit tests for the storage abstraction backends."""

import pytest

from app.services.storage import LocalBackend, MinioBackend


@pytest.fixture()
def backend(tmp_path):
    return LocalBackend(root=str(tmp_path), url_prefix="/static")


def test_put_and_get(backend):
    data = b"hello world"
    url = backend.put("test.txt", data)
    assert url == "/static/test.txt"
    assert backend.get("test.txt") == data


def test_put_creates_subdirs(backend):
    data = b"\x89PNG fake image"
    url = backend.put("avatars/user123.jpg", data, "image/jpeg")
    assert url == "/static/avatars/user123.jpg"
    assert backend.get("avatars/user123.jpg") == data


def test_delete_existing(backend):
    backend.put("to_delete.bin", b"data")
    assert backend.exists("to_delete.bin")
    backend.delete("to_delete.bin")
    assert not backend.exists("to_delete.bin")


def test_delete_nonexistent(backend):
    # Should not raise
    backend.delete("nonexistent_key.txt")


def test_url_format(backend):
    assert backend.url("uploads/branding/logo.png") == "/static/uploads/branding/logo.png"


def test_exists(backend):
    assert not backend.exists("nope.txt")
    backend.put("yep.txt", b"1")
    assert backend.exists("yep.txt")
    backend.delete("yep.txt")
    assert not backend.exists("yep.txt")


def test_get_missing_raises(backend):
    with pytest.raises(FileNotFoundError, match="Storage key not found"):
        backend.get("does_not_exist.txt")


def test_put_overwrites(backend):
    backend.put("file.txt", b"version1")
    backend.put("file.txt", b"version2")
    assert backend.get("file.txt") == b"version2"


def test_nested_key(backend):
    key = "uploads/messages/deep/nested/file.pdf"
    backend.put(key, b"pdf-content", "application/pdf")
    assert backend.exists(key)
    assert backend.get(key) == b"pdf-content"


def test_path_traversal_rejected(backend):
    with pytest.raises(ValueError, match="path traversal"):
        backend.put("../../etc/passwd", b"evil")

    with pytest.raises(ValueError, match="path traversal"):
        backend.get("../../../etc/shadow")

    with pytest.raises(ValueError, match="path traversal"):
        backend.delete("../../tmp/nuke")

    with pytest.raises(ValueError, match="path traversal"):
        backend.exists("../../etc/hosts")


def test_avatar_key_convention(backend):
    """Verify avatar keys map to the expected path structure."""
    url = backend.put("avatars/person123_abc.jpg", b"img", "image/jpeg")
    assert url == "/static/avatars/person123_abc.jpg"
    assert backend.get("avatars/person123_abc.jpg") == b"img"


def test_ticket_key_convention(backend):
    """Verify ticket attachment keys map to uploads/ subdirectory."""
    url = backend.put("uploads/tickets/abc123.pdf", b"pdf", "application/pdf")
    assert url == "/static/uploads/tickets/abc123.pdf"
    assert backend.get("uploads/tickets/abc123.pdf") == b"pdf"


class _FakeS3Error(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _FakeResponse:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self.closed = False
        self.released = False

    def read(self) -> bytes:
        return self._data

    def close(self) -> None:
        self.closed = True

    def release_conn(self) -> None:
        self.released = True


class _FakeMinioClient:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.bucket_exists_value = True
        self.created_buckets: list[str] = []
        self.last_put: tuple[str, str, str, int] | None = None

    def bucket_exists(self, bucket: str) -> bool:
        return self.bucket_exists_value

    def make_bucket(self, bucket: str) -> None:
        self.created_buckets.append(bucket)

    def put_object(self, bucket: str, key: str, data_stream, length: int, content_type: str = "") -> None:
        data = data_stream.read()
        self.objects[(bucket, key)] = data
        self.last_put = (bucket, key, content_type, length)

    def get_object(self, bucket: str, key: str):
        data = self.objects.get((bucket, key))
        if data is None:
            raise _FakeS3Error("NoSuchKey")
        return _FakeResponse(data)

    def remove_object(self, bucket: str, key: str) -> None:
        if (bucket, key) not in self.objects:
            raise _FakeS3Error("NoSuchKey")
        del self.objects[(bucket, key)]

    def stat_object(self, bucket: str, key: str) -> dict[str, str]:
        if (bucket, key) not in self.objects:
            raise _FakeS3Error("NoSuchKey")
        return {"key": key}


@pytest.fixture()
def minio_backend():
    client = _FakeMinioClient()
    backend = MinioBackend(
        endpoint_url="https://next.dotmac.ng",
        access_key="key",
        secret_key="secret",
        bucket="dotmac-uploads",
        public_url="https://next.dotmac.ng",
        region="us-east-1",
        client=client,
    )
    return backend, client


def test_minio_put_get_and_url(minio_backend):
    backend, _client = minio_backend
    data = b"hello minio"
    url = backend.put("uploads/messages/abc.txt", data, "text/plain")
    assert url == "https://next.dotmac.ng/dotmac-uploads/uploads/messages/abc.txt"
    assert backend.get("uploads/messages/abc.txt") == data
    assert backend.exists("uploads/messages/abc.txt")


def test_minio_put_uses_default_content_type(minio_backend):
    backend, client = minio_backend
    backend.put("uploads/messages/noctype.bin", b"x")
    assert client.last_put == (
        "dotmac-uploads",
        "uploads/messages/noctype.bin",
        "application/octet-stream",
        1,
    )


def test_minio_delete_existing_and_non_existing(minio_backend):
    backend, _client = minio_backend
    backend.put("uploads/messages/to-delete.txt", b"bye", "text/plain")
    backend.delete("uploads/messages/to-delete.txt")
    assert not backend.exists("uploads/messages/to-delete.txt")
    backend.delete("uploads/messages/to-delete.txt")


def test_minio_get_missing_raises(minio_backend):
    backend, _client = minio_backend
    with pytest.raises(FileNotFoundError, match="Storage key not found"):
        backend.get("uploads/messages/missing.txt")


def test_minio_ensure_bucket_only_when_missing():
    client = _FakeMinioClient()
    client.bucket_exists_value = False
    backend = MinioBackend(
        endpoint_url="http://minio:9000",
        access_key="key",
        secret_key="secret",
        bucket="dotmac-uploads",
        public_url="http://localhost:9000",
        region="us-east-1",
        client=client,
    )
    backend.ensure_bucket()
    assert client.created_buckets == ["dotmac-uploads"]


def test_minio_path_traversal_rejected(minio_backend):
    backend, _client = minio_backend
    with pytest.raises(ValueError, match="path traversal"):
        backend.put("../../etc/passwd", b"evil")
    with pytest.raises(ValueError, match="path traversal"):
        backend.get("../uploads/messages/file.txt")
    with pytest.raises(ValueError, match="path traversal"):
        backend.delete("uploads/../messages/file.txt")
    with pytest.raises(ValueError, match="path traversal"):
        backend.exists("uploads/messages/../../secret")
