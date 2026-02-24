from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.models.ai_insight import AIInsight, AIInsightStatus, InsightSeverity
from app.models.domain_settings import SettingDomain
from app.services.ai.client import AIClientError
from app.services.ai.gateway import ai_gateway
from app.services.ai.insights import ai_insights
from app.services.ai.output_parsers import parse_json_object, require_keys
from app.services.ai.personas import persona_registry
from app.services.audit_helpers import log_audit_event
from app.services.common import coerce_uuid
from app.services.settings_spec import resolve_value
from app.telemetry import get_tracer


def _bool_value(value: object | None, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _coerce_int(value: object | None, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int | float):
        return int(value)
    if isinstance(value, str):
        with_value = value.strip()
        if not with_value:
            return default
        try:
            return int(with_value)
        except ValueError:
            return default
    return default


class IntelligenceEngine:
    def enabled(self, db: Session, *, trigger: str) -> bool:
        if not ai_gateway.enabled(db):
            return False
        # Scheduled/batch operation is additionally gated.
        if trigger == "scheduled":
            return _bool_value(resolve_value(db, SettingDomain.integration, "intelligence_enabled"), False)
        return True

    def _persona_enabled(self, db: Session, setting_key: str | None) -> bool:
        if not setting_key:
            return True
        return _bool_value(resolve_value(db, SettingDomain.integration, setting_key), True)

    def _within_budget(self, db: Session) -> bool:
        budget = _coerce_int(resolve_value(db, SettingDomain.integration, "intelligence_daily_token_budget"), 0)
        if budget <= 0:
            return True
        used = ai_insights.tokens_used_today(db)
        return used < budget

    def invoke(
        self,
        db: Session,
        *,
        persona_key: str,
        params: dict[str, Any],
        entity_type: str,
        entity_id: str | None,
        trigger: str,
        triggered_by_person_id: str | None = None,
    ) -> AIInsight:
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span(
            "ai.invoke",
            attributes={
                "ai.persona_key": persona_key,
                "ai.entity_type": entity_type,
                "ai.trigger": trigger,
            },
        ) as span:
            return self._invoke_inner(
                db,
                span=span,
                persona_key=persona_key,
                params=params,
                entity_type=entity_type,
                entity_id=entity_id,
                trigger=trigger,
                triggered_by_person_id=triggered_by_person_id,
            )

    def _invoke_inner(
        self,
        db: Session,
        *,
        span,
        persona_key: str,
        params: dict[str, Any],
        entity_type: str,
        entity_id: str | None,
        trigger: str,
        triggered_by_person_id: str | None = None,
    ) -> AIInsight:
        if not self.enabled(db, trigger=trigger):
            span.set_attribute("ai.status", "disabled")
            raise AIClientError("Intelligence Engine is disabled")
        if not self._within_budget(db):
            span.set_attribute("ai.status", "budget_exceeded")
            raise AIClientError("Daily AI token budget exceeded")

        spec = persona_registry.get(persona_key)
        if not self._persona_enabled(db, spec.setting_key):
            raise AIClientError(f"Persona disabled: {persona_key}")

        # Data readiness check
        quality = spec.context_quality_scorer(db, params or {})
        quality_score = round(max(0.0, min(1.0, quality.score)), 2)

        if spec.min_context_quality > 0 and quality.score < spec.min_context_quality and spec.skip_on_low_quality:
            span.set_attribute("ai.status", "skipped")
            span.set_attribute("ai.quality_score", quality_score)
            missing_str = ", ".join(quality.missing_fields[:5])
            insight = AIInsight(
                persona_key=spec.key,
                domain=spec.domain,
                severity=InsightSeverity.info,
                status=AIInsightStatus.skipped,
                entity_type=entity_type,
                entity_id=entity_id,
                title=f"{spec.name}: insufficient data",
                summary=(
                    f"Skipped — context quality {quality.score:.0%} "
                    f"below threshold {spec.min_context_quality:.0%}. "
                    f"Missing: {missing_str}."
                ),
                structured_output={"quality": quality.field_scores, "missing": quality.missing_fields},
                context_quality_score=quality_score,
                confidence_score=None,
                recommendations=None,
                llm_provider="n/a",
                llm_model="n/a",
                llm_tokens_in=0,
                llm_tokens_out=0,
                generation_time_ms=0,
                trigger=trigger,
                triggered_by_person_id=coerce_uuid(triggered_by_person_id) if triggered_by_person_id else None,
                acknowledged_at=None,
                acknowledged_by_person_id=None,
                expires_at=None,
            )
            db.add(insight)
            db.commit()
            db.refresh(insight)
            return insight

        started = time.monotonic()
        context = spec.context_builder(db, params or {})
        output_instructions = spec.output_schema.to_instruction()
        system = spec.system_prompt.format(output_instructions=output_instructions)

        # Call the gateway using the persona's preferred endpoint, with global fallback policy.
        # We still allow the gateway to route primary->secondary on failures.
        primary_endpoint: Literal["primary", "secondary"] = (
            "secondary" if spec.default_endpoint == "secondary" else "primary"
        )
        result, routing = ai_gateway.generate_with_fallback(
            db,
            primary=primary_endpoint,
            fallback="secondary",
            system=system,
            prompt=context,
            max_tokens=spec.default_max_tokens,
        )

        span.set_attribute("ai.provider", result.provider or "unknown")
        span.set_attribute("ai.model", result.model or "unknown")
        span.set_attribute("ai.tokens_in", result.tokens_in or 0)
        span.set_attribute("ai.tokens_out", result.tokens_out or 0)
        span.set_attribute("ai.quality_score", quality_score)
        span.set_attribute("ai.status", "completed")

        parsed = parse_json_object(result.content)
        require_keys(parsed, spec.output_schema.required_keys())

        title = str(parsed.get("title") or spec.name).strip()[:300]
        summary = str(parsed.get("summary") or "").strip()[:5000]
        recommendations = parsed.get("recommended_actions") or parsed.get("recommendations") or []
        if not isinstance(recommendations, list):
            recommendations = []

        confidence = parsed.get("confidence")
        try:
            confidence_score = max(0.0, min(1.0, float(confidence))) if confidence is not None else None
        except (TypeError, ValueError):
            confidence_score = None

        severity_value = "info"
        if spec.severity_classifier:
            try:
                severity_value = str(spec.severity_classifier(parsed) or "info").strip().lower()
            except Exception:
                severity_value = "info"
        severity = InsightSeverity.info
        if severity_value in {s.value for s in InsightSeverity}:
            severity = InsightSeverity(severity_value)

        expires_at = (
            datetime.now(UTC) + timedelta(hours=max(int(spec.insight_ttl_hours or 0), 0))
            if spec.insight_ttl_hours
            else None
        )

        insight = AIInsight(
            persona_key=spec.key,
            domain=spec.domain,
            severity=severity,
            status=AIInsightStatus.completed,
            entity_type=entity_type,
            entity_id=entity_id,
            title=title or spec.name,
            summary=summary or "No summary generated.",
            structured_output=parsed,
            confidence_score=confidence_score,
            context_quality_score=quality_score,
            recommendations=recommendations[:10] if isinstance(recommendations, list) else None,
            llm_provider=result.provider,
            llm_model=result.model,
            llm_tokens_in=result.tokens_in,
            llm_tokens_out=result.tokens_out,
            llm_endpoint=str(routing.get("endpoint")) if isinstance(routing, dict) else None,
            generation_time_ms=int((time.monotonic() - started) * 1000),
            trigger=trigger,
            triggered_by_person_id=coerce_uuid(triggered_by_person_id) if triggered_by_person_id else None,
            acknowledged_at=None,
            acknowledged_by_person_id=None,
            expires_at=expires_at,
        )
        db.add(insight)
        db.commit()
        db.refresh(insight)

        # Audit without storing prompt/context.
        log_audit_event(
            db,
            request=None,
            action="ai_insight_generated",
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id else None,
            actor_id=triggered_by_person_id,
            metadata={
                "persona_key": spec.key,
                "domain": spec.domain.value,
                "llm_provider": result.provider,
                "llm_model": result.model,
                "llm_endpoint": str(routing.get("endpoint")) if isinstance(routing, dict) else None,
            },
            status_code=200,
            is_success=True,
        )
        return insight


intelligence_engine = IntelligenceEngine()
