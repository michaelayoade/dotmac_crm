from types import SimpleNamespace

from app.services.crm.conversations import message_attachments
from app.services.crm.inbox import formatting


def test_save_message_attachments_uses_storage_proxy_for_s3(monkeypatch):
    class _FakeStorage:
        def put(self, key, _content, _mime):
            return f"http://localhost:9000/dotmac-uploads/{key}"

    monkeypatch.setattr(
        message_attachments,
        "settings",
        SimpleNamespace(storage_backend="s3", s3_bucket="dotmac-uploads"),
    )
    monkeypatch.setattr(message_attachments, "storage", _FakeStorage())
    monkeypatch.setenv("APP_URL", "https://crm.dotmac.io")

    saved = message_attachments.save_message_attachments(
        [
            {
                "stored_name": "abc.png",
                "file_name": "abc.png",
                "file_size": 10,
                "mime_type": "image/png",
                "content": b"x",
            }
        ]
    )

    assert saved[0]["url"] == "https://crm.dotmac.io/admin/storage/dotmac-uploads/uploads/messages/abc.png"


def test_normalize_storage_attachment_url_rewrites_localhost_minio_link(monkeypatch):
    monkeypatch.setattr(
        formatting,
        "settings",
        SimpleNamespace(storage_backend="s3", s3_bucket="dotmac-uploads"),
    )

    normalized = formatting._normalize_storage_attachment_url(
        "http://localhost:9000/dotmac-uploads/uploads/messages/test-image.jpg"
    )

    assert normalized == "/admin/storage/dotmac-uploads/uploads/messages/test-image.jpg"
