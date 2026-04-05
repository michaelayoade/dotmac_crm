"""Centralised monitoring setup: Loki log shipping + GlitchTip (Sentry) errors.

Usage (CRM app — reads from env):
    from app.monitoring import setup_monitoring
    setup_monitoring()

Usage (remote apps — explicit config):
    from monitoring import setup_monitoring
    setup_monitoring(
        app_name="your-app-name",
        server="remote-1",
        loki_url="http://160.119.127.195:3100/loki/api/v1/push",
        glitchtip_dsn="http://<key>@160.119.127.195:8080/1",
    )
"""

import logging
import os
from queue import Queue
from urllib.parse import urlparse

_MONITORING_SERVER = os.getenv("MONITORING_SERVER", "160.119.127.195")


def _host_reachable(url: str, timeout: float = 3.0) -> bool:
    """Quick TCP connect check — avoids blocking the app if the target is down."""
    import socket

    parsed = urlparse(url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def setup_loki(app_name: str, server: str, environment: str, loki_url: str | None = None) -> None:
    """Add an async Loki handler to the root logger."""
    try:
        import logging_loki  # type: ignore[import-untyped]
    except ImportError:
        logging.getLogger(__name__).warning("python-logging-loki not installed — Loki handler skipped")
        return

    url = loki_url or os.getenv(
        "LOKI_URL",
        f"http://{_MONITORING_SERVER}:3100/loki/api/v1/push",
    )

    if not url or not _host_reachable(url):
        logging.getLogger(__name__).warning("Loki endpoint unreachable (%s) — handler skipped", url)
        return

    handler = logging_loki.LokiQueueHandler(
        Queue(-1),
        url=url,
        tags={"app": app_name, "server": server, "environment": environment},
        version="1",
    )
    logging.getLogger().addHandler(handler)


def setup_sentry(app_name: str, environment: str, glitchtip_dsn: str | None = None) -> None:
    """Initialise Sentry SDK pointing at the GlitchTip instance."""
    dsn = glitchtip_dsn or os.getenv("SENTRY_DSN", "")
    if not dsn:
        logging.getLogger(__name__).info("SENTRY_DSN not set — GlitchTip/Sentry disabled")
        return

    try:
        import sentry_sdk
    except ImportError:
        logging.getLogger(__name__).warning("sentry-sdk not installed — error tracking skipped")
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
    loki_url: str | None = None,
    glitchtip_dsn: str | None = None,
) -> None:
    """Wire up Loki log shipping and GlitchTip error tracking in one call.

    Parameters can be passed explicitly (for remote apps) or left as None
    to fall back to environment variables (for the CRM app).

    Call this after ``configure_logging()`` so the Loki handler inherits the
    existing formatter/filter configuration.
    """
    if environment is None:
        environment = os.getenv("APP_ENV", "production")

    setup_loki(app_name, server, environment, loki_url=loki_url)
    setup_sentry(app_name, environment, glitchtip_dsn=glitchtip_dsn)

    logging.getLogger(__name__).info(
        "Monitoring initialised: app=%s server=%s env=%s",
        app_name,
        server,
        environment,
    )
