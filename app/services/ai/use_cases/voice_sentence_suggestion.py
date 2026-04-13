from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.services.ai.client import AIClientError
from app.services.ai.gateway import ai_gateway
from app.services.ai.output_parsers import parse_json_object, require_keys
from app.services.audit_helpers import log_audit_event

_DEFAULT_CONTEXT = "general_message"
_CONTEXT_LABELS = {
    "crm_reply": "CRM reply",
    "crm_new_conversation": "CRM new conversation",
    "ticket_comment": "ticket comment",
    _DEFAULT_CONTEXT: "general message",
}

_VOICE_SENTENCE_SYSTEM_PROMPT = """You rewrite short speech-to-text transcripts into clean, natural sentences.

Rules:
- Preserve the user's meaning.
- Do not add facts, promises, names, greetings, or apologies that are not already implied.
- Keep the output concise and in the same language as the input.
- Fix capitalization, punctuation, spacing, and obvious grammar.
- Return strict JSON only.

JSON shape:
{
  "suggested_text": "best cleaned sentence",
  "alternatives": ["optional alternative 1", "optional alternative 2"]
}
"""


@dataclass(frozen=True)
class VoiceSentenceSuggestion:
    suggested_text: str
    alternatives: list[str]
    meta: dict[str, str]


def _normalize_text(value: str) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalize_alternatives(value: object, *, primary: str) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    seen = {primary.casefold()}
    for item in value:
        text = _normalize_text(str(item or ""))
        if not text:
            continue
        lowered = text.casefold()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(text)
        if len(normalized) >= 2:
            break
    return normalized


def suggest_voice_sentence(
    db: Session,
    *,
    request,
    text: str,
    actor_person_id: str | None,
    context: str | None = None,
) -> VoiceSentenceSuggestion:
    normalized_text = _normalize_text(text)
    if not normalized_text:
        raise ValueError("Voice text is required")

    normalized_context = str(context or _DEFAULT_CONTEXT).strip().lower() or _DEFAULT_CONTEXT
    context_label = _CONTEXT_LABELS.get(normalized_context, _CONTEXT_LABELS[_DEFAULT_CONTEXT])
    prompt = (
        f"Context: {context_label}\n"
        f"Transcript:\n{normalized_text}\n\n"
        "Return JSON only."
    )

    result, routing = ai_gateway.generate_with_fallback(
        db,
        primary="primary",
        fallback="secondary",
        system=_VOICE_SENTENCE_SYSTEM_PROMPT,
        prompt=prompt,
        max_tokens=180,
    )
    parsed = parse_json_object(result.content)
    require_keys(parsed, ["suggested_text"])

    suggested_text = _normalize_text(str(parsed.get("suggested_text") or ""))
    if not suggested_text:
        raise AIClientError("AI output missing suggested_text")
    alternatives = _normalize_alternatives(parsed.get("alternatives"), primary=suggested_text)

    log_audit_event(
        db,
        request,
        action="ai_voice_sentence_suggestion",
        entity_type="voice_input",
        entity_id=None,
        actor_id=actor_person_id,
        metadata={
            "context": normalized_context,
            "input_length": len(normalized_text),
            "llm_provider": result.provider,
            "llm_model": result.model,
            "llm_endpoint": str(routing.get("endpoint")) if isinstance(routing, dict) else None,
        },
        status_code=200,
        is_success=True,
    )

    return VoiceSentenceSuggestion(
        suggested_text=suggested_text,
        alternatives=alternatives,
        meta={
            "provider": result.provider,
            "model": result.model,
            "endpoint": str(routing.get("endpoint")) if isinstance(routing, dict) else "",
        },
    )
