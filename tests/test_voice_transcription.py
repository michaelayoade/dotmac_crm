from app.services.ai.use_cases import voice_transcription


def test_transcribe_voice_audio_posts_openai_compatible_request(db_session, monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"text": "Hello from mobile voice."}

    class FakeClient:
        def __init__(self, timeout):
            captured["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, headers, data, files):
            captured["url"] = url
            captured["headers"] = headers
            captured["data"] = data
            captured["files"] = files
            return FakeResponse()

    monkeypatch.setattr(
        voice_transcription,
        "resolve_values_atomic",
        lambda db, domain, keys: {
            "ai_enabled": True,
            "voice_transcription_base_url": "https://api.example.com/v1",
            "voice_transcription_model": "test-transcribe",
            "voice_transcription_api_key": "secret",
            "voice_transcription_timeout_seconds": 12,
            "voice_transcription_max_retries": 0,
        },
    )
    monkeypatch.setattr(voice_transcription.httpx, "Client", FakeClient)
    monkeypatch.setattr(voice_transcription, "log_audit_event", lambda *args, **kwargs: None)

    result = voice_transcription.transcribe_voice_audio(
        db_session,
        request=None,
        audio=b"webm-bytes",
        filename="voice.webm",
        content_type="audio/webm",
        actor_person_id=None,
        context="crm_reply",
    )

    assert result.text == "Hello from mobile voice."
    assert result.meta == {"provider": "voice_transcription", "model": "test-transcribe"}
    assert captured["url"] == "https://api.example.com/v1/audio/transcriptions"
    assert captured["headers"] == {"authorization": "Bearer secret"}
    assert captured["data"] == {"model": "test-transcribe", "response_format": "json"}
    assert captured["files"]["file"] == ("voice.webm", b"webm-bytes", "audio/webm")
    assert captured["timeout"] == 12.0


def test_transcribe_voice_audio_rejects_oversized_audio(db_session, monkeypatch):
    monkeypatch.setattr(voice_transcription, "MAX_AUDIO_BYTES", 4)

    try:
        voice_transcription.transcribe_voice_audio(
            db_session,
            request=None,
            audio=b"too-large",
            filename="voice.webm",
            content_type="audio/webm",
            actor_person_id=None,
            context="crm_reply",
        )
    except ValueError as exc:
        assert str(exc) == "Voice audio is too large"
    else:
        raise AssertionError("Expected ValueError for oversized audio")
