"""Unit tests for the storage abstraction (LocalBackend)."""

import pytest

from app.services.storage import LocalBackend


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
