from app.services.ai.client import AIResponse
from app.services.ai.use_cases import voice_sentence_suggestion


def test_suggest_voice_sentence_returns_structured_suggestion(db_session, monkeypatch):
    def _fake_generate_with_fallback(db, **kwargs):
        return (
            AIResponse(
                content='{"suggested_text":"Hello, I need an update on my ticket.","alternatives":["Hi, I need an update on my ticket.","Hello, I need an update on my ticket."]}',
                tokens_in=10,
                tokens_out=14,
                model="test-model",
                provider="test-provider",
            ),
            {"endpoint": "primary", "fallback_used": False},
        )

    monkeypatch.setattr(voice_sentence_suggestion.ai_gateway, "generate_with_fallback", _fake_generate_with_fallback)
    monkeypatch.setattr(voice_sentence_suggestion, "log_audit_event", lambda *args, **kwargs: None)

    result = voice_sentence_suggestion.suggest_voice_sentence(
        db_session,
        request=None,
        text="hello i need update on my ticket",
        actor_person_id=None,
        context="ticket_comment",
    )

    assert result.suggested_text == "Hello, I need an update on my ticket."
    assert result.alternatives == ["Hi, I need an update on my ticket."]
    assert result.meta["provider"] == "test-provider"
    assert result.meta["endpoint"] == "primary"


def test_suggest_voice_sentence_rejects_blank_input(db_session):
    try:
        voice_sentence_suggestion.suggest_voice_sentence(
            db_session,
            request=None,
            text="   ",
            actor_person_id=None,
            context="crm_reply",
        )
    except ValueError as exc:
        assert str(exc) == "Voice text is required"
    else:
        raise AssertionError("Expected ValueError for blank voice input")
