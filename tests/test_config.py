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


def test_request_shared_db_session_defaults_disabled(monkeypatch):
    monkeypatch.delenv("REQUEST_SHARED_DB_SESSION_ENABLED", raising=False)
    monkeypatch.delenv("REQUEST_SHARED_DB_SESSION_PATH_PREFIXES", raising=False)

    settings = Settings()

    assert settings.request_shared_db_session_enabled is False
    assert settings.request_shared_db_session_path_prefixes == ()


def test_request_shared_db_session_path_prefixes_parse(monkeypatch):
    monkeypatch.setenv("REQUEST_SHARED_DB_SESSION_ENABLED", "true")
    monkeypatch.setenv("REQUEST_SHARED_DB_SESSION_PATH_PREFIXES", "/admin/crm, /admin/dashboard ")

    settings = Settings()

    assert settings.request_shared_db_session_enabled is True
    assert settings.request_shared_db_session_path_prefixes == ("/admin/crm", "/admin/dashboard")
