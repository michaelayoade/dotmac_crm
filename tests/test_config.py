from app.config import Settings


def test_cookie_secure_defaults_false(monkeypatch):
    monkeypatch.delenv("COOKIE_SECURE", raising=False)

    settings = Settings()

    assert settings.cookie_secure is False


def test_cookie_secure_false_string_parses_false(monkeypatch):
    monkeypatch.setenv("COOKIE_SECURE", "false")

    settings = Settings()

    assert settings.cookie_secure is False


def test_cookie_secure_true_string_parses_true(monkeypatch):
    monkeypatch.setenv("COOKIE_SECURE", "true")

    settings = Settings()

    assert settings.cookie_secure is True
