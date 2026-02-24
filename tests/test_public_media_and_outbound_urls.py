from app.services import public_media
from app.services.crm.inbox import outbound


def test_public_media_sign_and_verify_roundtrip(monkeypatch):
    monkeypatch.setenv("MEDIA_URL_SECRET", "test-secret")
    monkeypatch.setattr(public_media.time, "time", lambda: 1_700_000_000)
    monkeypatch.setattr(public_media.email_service, "get_app_url", lambda _db: "https://crm.dotmac.io")

    url = public_media.build_public_media_url(None, stored_name="abc123.png", ttl_seconds=300)

    assert "/public/media/messages/abc123.png?" in url
    exp = int(url.split("exp=")[1].split("&", 1)[0])
    sig = url.split("sig=")[1]
    assert public_media.verify_media_signature("abc123.png", exp, sig)


def test_outbound_resolve_meta_public_attachment_uses_signed_url(monkeypatch):
    monkeypatch.setattr(outbound.public_media, "is_valid_stored_name", lambda value: value == "abc123.png")
    monkeypatch.setattr(
        outbound.public_media,
        "build_public_media_url",
        lambda _db, *, stored_name, ttl_seconds=900: f"https://crm.dotmac.io/public/media/messages/{stored_name}",
    )

    url = outbound._resolve_meta_public_attachment_url(None, {"stored_name": "abc123.png", "url": "/x"})

    assert url == "https://crm.dotmac.io/public/media/messages/abc123.png"
