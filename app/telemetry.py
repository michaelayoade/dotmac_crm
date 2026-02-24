import logging
import os

from opentelemetry import trace

logger = logging.getLogger(__name__)

_TRACER_NAME = "dotmac_crm"


def get_tracer(name: str | None = None) -> trace.Tracer:
    """Return an OTel tracer.

    When OTel is disabled (no TracerProvider configured), the default
    trace API returns no-op spans — so callers never need to check
    whether tracing is active.
    """
    return trace.get_tracer(name or _TRACER_NAME)


def setup_otel(app) -> None:
    """Configure OpenTelemetry tracing for the application.

    Instruments FastAPI, SQLAlchemy (cached engine), Celery, httpx,
    requests, Redis, and stdlib logging.  All instrumentors are
    optional — a missing package is logged and skipped.
    """
    enabled = os.getenv("OTEL_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return

    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except Exception:
        logger.exception("OpenTelemetry SDK not available — skipping setup.")
        return

    service_name = os.getenv("OTEL_SERVICE_NAME", "dotmac_crm")
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    trace.set_tracer_provider(provider)

    exporter = OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces") if endpoint else OTLPSpanExporter()
    processor = BatchSpanProcessor(exporter)
    provider.add_span_processor(processor)

    # --- FastAPI ---
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
        logger.info("OTel: FastAPI instrumented")
    except Exception:
        logger.warning("OTel: FastAPI instrumentation unavailable", exc_info=True)

    # --- SQLAlchemy (uses cached engine singleton) ---
    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        from app.db import get_engine

        SQLAlchemyInstrumentor().instrument(engine=get_engine())
        logger.info("OTel: SQLAlchemy instrumented")
    except Exception:
        logger.warning("OTel: SQLAlchemy instrumentation unavailable", exc_info=True)

    # --- Celery ---
    try:
        from opentelemetry.instrumentation.celery import CeleryInstrumentor

        CeleryInstrumentor().instrument()
        logger.info("OTel: Celery instrumented")
    except Exception:
        logger.warning("OTel: Celery instrumentation unavailable", exc_info=True)

    # --- httpx ---
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
        logger.info("OTel: httpx instrumented")
    except Exception:
        logger.warning("OTel: httpx instrumentation unavailable", exc_info=True)

    # --- requests ---
    try:
        from opentelemetry.instrumentation.requests import RequestsInstrumentor

        RequestsInstrumentor().instrument()
        logger.info("OTel: requests instrumented")
    except Exception:
        logger.warning("OTel: requests instrumentation unavailable", exc_info=True)

    # --- Redis ---
    try:
        from opentelemetry.instrumentation.redis import RedisInstrumentor

        RedisInstrumentor().instrument()
        logger.info("OTel: Redis instrumented")
    except Exception:
        logger.warning("OTel: Redis instrumentation unavailable", exc_info=True)

    # --- Logging (injects trace context into log records) ---
    try:
        from opentelemetry.instrumentation.logging import LoggingInstrumentor

        LoggingInstrumentor().instrument(set_logging_format=True)
        logger.info("OTel: logging instrumented")
    except Exception:
        logger.warning("OTel: logging instrumentation unavailable", exc_info=True)

    logger.info("OpenTelemetry tracing enabled (service=%s)", service_name)
