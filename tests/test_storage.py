"""Unit tests for the storage abstraction."""

import pytest

from app.services.storage import LocalBackend, S3Backend


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


class _NoSuchKey(Exception):
    pass


class _ClientError(Exception):
    def __init__(self, code):
        self.response = {"Error": {"Code": code}}


class _Body:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data


class _FakeS3Client:
    class exceptions:
        NoSuchKey = _NoSuchKey

    def __init__(self):
        self.objects = {}
        self.put_calls = []
        self.deleted = []
        self.head_bucket_calls = 0
        self.create_bucket_calls = []
        self.bucket_exists = True

    def put_object(self, **kwargs):
        self.put_calls.append(kwargs)
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = kwargs["Body"]

    def get_object(self, *, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise self.exceptions.NoSuchKey()
        return {"Body": _Body(self.objects[(Bucket, Key)])}

    def delete_object(self, *, Bucket, Key):
        self.deleted.append((Bucket, Key))
        self.objects.pop((Bucket, Key), None)

    def head_object(self, *, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise _ClientError("404")
        return {}

    def head_bucket(self, *, Bucket):
        self.head_bucket_calls += 1
        if not self.bucket_exists:
            raise _ClientError("404")
        return {"Bucket": Bucket}

    def create_bucket(self, **kwargs):
        self.create_bucket_calls.append(kwargs)
        self.bucket_exists = True


@pytest.fixture()
def s3_client():
    return _FakeS3Client()


@pytest.fixture()
def s3_backend(s3_client):
    return S3Backend(bucket="dotmac-uploads", public_url="http://minio:9000", client=s3_client)


def test_s3_put_and_get(s3_backend, s3_client):
    url = s3_backend.put("uploads/messages/test.png", b"img", "image/png")

    assert url == "http://minio:9000/dotmac-uploads/uploads/messages/test.png"
    assert s3_backend.get("uploads/messages/test.png") == b"img"
    assert s3_client.put_calls == [
        {
            "Bucket": "dotmac-uploads",
            "Key": "uploads/messages/test.png",
            "Body": b"img",
            "ContentType": "image/png",
        }
    ]


def test_s3_delete_existing_and_missing_is_safe(s3_backend, s3_client):
    s3_backend.put("uploads/messages/test.png", b"img", "image/png")

    s3_backend.delete("uploads/messages/test.png")
    s3_backend.delete("uploads/messages/missing.png")

    assert ("dotmac-uploads", "uploads/messages/test.png") in s3_client.deleted
    assert ("dotmac-uploads", "uploads/messages/missing.png") in s3_client.deleted


def test_s3_exists(s3_backend):
    assert not s3_backend.exists("uploads/messages/missing.png")
    s3_backend.put("uploads/messages/test.png", b"img")
    assert s3_backend.exists("uploads/messages/test.png")


def test_s3_get_missing_raises(s3_backend):
    with pytest.raises(FileNotFoundError, match="Storage key not found"):
        s3_backend.get("uploads/messages/missing.png")


def test_s3_ensure_bucket_noop_when_present(s3_backend, s3_client):
    s3_backend.ensure_bucket()

    assert s3_client.head_bucket_calls == 1
    assert s3_client.create_bucket_calls == []


def test_s3_ensure_bucket_creates_when_missing(s3_client):
    s3_client.bucket_exists = False
    backend = S3Backend(bucket="dotmac-uploads", public_url="http://minio:9000", client=s3_client)

    backend.ensure_bucket()

    assert s3_client.create_bucket_calls == [{"Bucket": "dotmac-uploads"}]


def test_s3_path_traversal_rejected(s3_backend):
    with pytest.raises(ValueError, match="path traversal"):
        s3_backend.put("../../etc/passwd", b"evil")

    with pytest.raises(ValueError, match="path traversal"):
        s3_backend.get("../../../etc/shadow")

    with pytest.raises(ValueError, match="path traversal"):
        s3_backend.delete("../../tmp/nuke")

    with pytest.raises(ValueError, match="path traversal"):
        s3_backend.exists("../../etc/hosts")
