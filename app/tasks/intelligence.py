from __future__ import annotations

import logging
from typing import Any

from app.celery_app import celery_app
from app.db import SessionLocal
from app.models.domain_settings import SettingDomain
from app.services.ai.engine import intelligence_engine
from app.services.ai.insights import ai_insights
from app.services.ai.personas import persona_registry
from app.services.settings_spec import resolve_value

logger = logging.getLogger(__name__)


def _bool(value: object | None, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


@celery_app.task(name="app.tasks.intelligence.run_scheduled_analysis")
def run_scheduled_analysis(persona_key: str | None = None) -> dict:
    session = SessionLocal()
    try:
        if not _bool(resolve_value(session, SettingDomain.integration, "ai_enabled"), False):
            return {"enabled": False, "reason": "ai_disabled"}
        if not _bool(resolve_value(session, SettingDomain.integration, "intelligence_enabled"), False):
            return {"enabled": False, "reason": "intelligence_disabled"}

        max_per_run = resolve_value(session, SettingDomain.integration, "intelligence_max_insights_per_run")
        try:
            max_per_run_int = int(max_per_run) if max_per_run is not None else 50
        except (TypeError, ValueError):
            max_per_run_int = 50
        max_per_run_int = max(1, min(max_per_run_int, 500))

        if persona_key:
            specs = [persona_registry.get(persona_key)]
        else:
            specs = [s for s in persona_registry.list_all() if s.supports_scheduled]

        generated_total = 0
        results: dict[str, dict] = {}
        for spec in specs:
            if generated_total >= max_per_run_int:
                results[spec.key] = {"generated": 0, "skipped": True, "reason": "max_per_run_reached"}
                continue

            try:
                from app.services.ai.context_builders.batch_scanners import batch_scanners

                scanner = batch_scanners.get(spec.domain.value)
                if not scanner:
                    results[spec.key] = {"generated": 0, "skipped": True, "reason": "no_scanner"}
                    continue

                entity_params_list = scanner(session, spec.key, limit=max_per_run_int - generated_total)
                count = 0
                for entity_type, entity_id, params in entity_params_list:
                    intelligence_engine.invoke(
                        session,
                        persona_key=spec.key,
                        params=params,
                        entity_type=entity_type,
                        entity_id=entity_id,
                        trigger="scheduled",
                        triggered_by_person_id=None,
                    )
                    count += 1
                    generated_total += 1
                results[spec.key] = {"generated": count}
            except Exception:
                session.rollback()
                logger.exception("Scheduled analysis failed for persona=%s", spec.key)
                results[spec.key] = {"error": True}

        return {"generated_total": generated_total, "results": results}
    finally:
        session.close()


@celery_app.task(name="app.tasks.intelligence.expire_stale_insights")
def expire_stale_insights() -> dict:
    session = SessionLocal()
    try:
        expired = ai_insights.expire_stale(session)
        return {"expired": expired}
    except Exception:
        session.rollback()
        logger.exception("Failed to expire stale insights")
        raise
    finally:
        session.close()


@celery_app.task(name="app.tasks.intelligence.invoke_persona_async")
def invoke_persona_async(
    persona_key: str,
    *,
    params: dict[str, Any] | None = None,
    entity_type: str = "unknown",
    entity_id: str | None = None,
    trigger: str = "on_demand",
    triggered_by_person_id: str | None = None,
) -> dict:
    session = SessionLocal()
    try:
        insight = intelligence_engine.invoke(
            session,
            persona_key=persona_key,
            params=params or {},
            entity_type=entity_type,
            entity_id=entity_id,
            trigger=trigger,
            triggered_by_person_id=triggered_by_person_id,
        )
        return {"insight_id": str(insight.id), "status": insight.status.value}
    except Exception:
        session.rollback()
        logger.exception("Async persona invocation failed for %s", persona_key)
        raise
    finally:
        session.close()
