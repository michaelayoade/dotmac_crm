from types import SimpleNamespace
from uuid import uuid4

from app.models.auth import ApiKey
from app.models.person import Person
from app.web.admin import system


def _person() -> Person:
    unique = uuid4().hex
    return Person(
        first_name="Api",
        last_name="User",
        display_name="Api User",
        email=f"api-user-{unique}@example.com",
    )


def test_api_key_create_redirects_with_flash_token_not_raw_key(db_session, monkeypatch):
    person = _person()
    db_session.add(person)
    db_session.commit()

    request = SimpleNamespace()
    monkeypatch.setattr(
        "app.web.admin._auth_helpers.get_current_user",
        lambda _request: {"person_id": str(person.id), "roles": ["admin"], "scopes": []},
    )
    monkeypatch.setattr("app.web.admin._auth_helpers.get_sidebar_stats", lambda _db: {})
    monkeypatch.setattr(system.secrets, "token_urlsafe", lambda _size: "raw-api-key-secret")

    captured: dict[str, str] = {}

    def _store(raw_key: str, person_id: str) -> str:
        captured["raw_key"] = raw_key
        captured["person_id"] = person_id
        return "flash-token"

    monkeypatch.setattr(system, "_store_api_key_flash", _store)

    response = system.api_key_create(request, label="Integration", expires_in=None, db=db_session)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/system/api-keys?flash_token=flash-token"
    assert "raw-api-key-secret" not in response.headers["location"]
    assert "new_key" not in response.headers["location"]
    assert captured == {"raw_key": "raw-api-key-secret", "person_id": str(person.id)}
    assert db_session.query(ApiKey).filter(ApiKey.person_id == person.id).count() == 1


def test_api_keys_list_consumes_flash_token_for_current_user(db_session, monkeypatch):
    person = _person()
    db_session.add(person)
    db_session.commit()

    request = SimpleNamespace()
    monkeypatch.setattr(
        "app.web.admin._auth_helpers.get_current_user",
        lambda _request: {"person_id": str(person.id), "roles": ["admin"], "scopes": []},
    )
    monkeypatch.setattr("app.web.admin._auth_helpers.get_sidebar_stats", lambda _db: {})

    consumed: dict[str, str | None] = {}

    def _consume(token: str | None, person_id: str | None) -> str | None:
        consumed["token"] = token
        consumed["person_id"] = person_id
        return "raw-api-key-secret"

    monkeypatch.setattr(system, "_consume_api_key_flash", _consume)

    captured_context: dict = {}

    def _capture_template(template_name: str, context: dict):
        captured_context["template_name"] = template_name
        captured_context.update(context)
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(system.templates, "TemplateResponse", _capture_template)

    response = system.api_keys_list(request, flash_token="flash-token", db=db_session)

    assert response.status_code == 200
    assert captured_context["template_name"] == "admin/system/api_keys.html"
    assert captured_context["new_key"] == "raw-api-key-secret"
    assert consumed == {"token": "flash-token", "person_id": str(person.id)}


def test_api_key_flash_memory_fallback_is_one_time_and_person_scoped(monkeypatch):
    class _BrokenRedis:
        def setex(self, *_args, **_kwargs):
            raise system.redis.RedisError("redis down")

        def pipeline(self):
            raise system.redis.RedisError("redis down")

    monkeypatch.setattr(system, "get_settings_redis", lambda: _BrokenRedis())
    system._API_KEY_FLASH_MEMORY.clear()

    token = system._store_api_key_flash("raw-api-key-secret", "person-1")

    assert system._consume_api_key_flash(token, "person-2") is None
    assert system._consume_api_key_flash(token, "person-1") is None

    token = system._store_api_key_flash("raw-api-key-secret", "person-1")

    assert system._consume_api_key_flash(token, "person-1") == "raw-api-key-secret"
    assert system._consume_api_key_flash(token, "person-1") is None
