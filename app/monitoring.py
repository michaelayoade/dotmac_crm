"""Centralised monitoring setup: Loki log shipping + GlitchTip (Sentry) errors.

Usage:
    from app.monitoring import setup_monitoring
    setup_monitoring(app_name="dotmac-crm", server="crm-1")
"""

import logging
import os

_MONITORING_SERVER = os.getenv("MONITORING_SERVER", "160.119.127.195")


def setup_loki(app_name: str, server: str, environment: str) -> None:
    """Add a Loki HTTP handler to the root logger."""
    try:
        import logging_loki
    except ImportError:
        logging.getLogger(__name__).warning(
            "python-logging-loki not installed — Loki handler skipped"
        )
        return

    loki_url = os.getenv(
        "LOKI_URL",
        f"http://{_MONITORING_SERVER}:3100/loki/api/v1/push",
    )

    handler = logging_loki.LokiHandler(
        url=loki_url,
        tags={"app": app_name, "server": server, "environment": environment},
        version="1",
    )
    logging.getLogger().addHandler(handler)


def setup_sentry(app_name: str, environment: str) -> None:
    """Initialise Sentry SDK pointing at the GlitchTip instance."""
    dsn = os.getenv("SENTRY_DSN", "")
    if not dsn:
        logging.getLogger(__name__).info(
            "SENTRY_DSN not set — GlitchTip/Sentry disabled"
        )
        return

    try:
        import sentry_sdk
    except ImportError:
        logging.getLogger(__name__).warning(
            "sentry-sdk not installed — error tracking skipped"
        )
        return

    traces_sample_rate = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1"))

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        traces_sample_rate=traces_sample_rate,
        server_name=app_name,
    )


def setup_monitoring(
    app_name: str = "dotmac-crm",
    server: str = "crm-1",
    environment: str | None = None,
) -> None:
    """Wire up Loki log shipping and GlitchTip error tracking in one call.

    Call this after ``configure_logging()`` so the Loki handler inherits the
    existing formatter/filter configuration.
    """
    if environment is None:
        environment = os.getenv("APP_ENV", "production")

    setup_loki(app_name, server, environment)
    setup_sentry(app_name, environment)

    logging.getLogger(__name__).info(
        "Monitoring initialised: app=%s server=%s env=%s",
        app_name,
        server,
        environment,
    )
