from __future__ import annotations

import json
import logging
import socket
import ssl
from dataclasses import asdict, dataclass
from time import perf_counter
from typing import Any, Literal
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.metrics import (
    observe_ai_provider_healthcheck,
    observe_ai_provider_healthcheck_failure,
    set_ai_provider_circuit_open,
)
from app.services.ai.client import AIClientError
from app.services.ai.gateway import AIEndpoint, AIEndpointConfig, ai_gateway

logger = logging.getLogger(__name__)

HealthMode = Literal["primary", "secondary", "fallback"]
SimulatedFailureMode = Literal["timeout", "auth", "none"]

_HEALTHCHECK_SYSTEM_PROMPT = "You are an AI provider health probe."
_HEALTHCHECK_USER_PROMPT = 'Return exactly this JSON and nothing else: {"ok":true}'


@dataclass(frozen=True)
class ProviderEndpointHealthResult:
    mode: str
    endpoint_name: str
    provider: str
    endpoint: str
    model: str
    configured: bool
    ready: bool
    success: bool
    latency_ms: float
    failure_type: str | None
    retry_count: int
    timeout_type: str | None
    http_status: int | None
    request_id: str | None
    circuit_open: bool
    circuit_consecutive_failures: int
    circuit_cooldown_remaining_seconds: float
    dns_ok: bool
    dns_addresses: list[str]
    tls_ok: bool
    auth_valid: bool | None
    model_available: bool | None
    used_fallback: bool = False
    simulated_failure: str | None = None
    response_preview: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProviderHealthReport:
    mode: str
    overall_success: bool
    fallback_used: bool
    results: list[ProviderEndpointHealthResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "overall_success": self.overall_success,
            "fallback_used": self.fallback_used,
            "results": [result.to_dict() for result in self.results],
        }


def _host_and_port(base_url: str) -> tuple[str | None, int]:
    parsed = urlparse(base_url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return parsed.hostname, port


def _dns_check(base_url: str) -> tuple[bool, list[str], str | None]:
    host, port = _host_and_port(base_url)
    if not host:
        return False, [], "missing_host"
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        return False, [], str(exc)
    addresses = sorted({str(info[4][0]) for info in infos if info[4]})
    return bool(addresses), addresses, None


def _tls_check(base_url: str, timeout_seconds: float) -> tuple[bool, str | None]:
    host, port = _host_and_port(base_url)
    if not host:
        return False, "missing_host"
    context = ssl.create_default_context()
    try:
        with (
            socket.create_connection((host, port), timeout=timeout_seconds) as sock,
            context.wrap_socket(
                sock,
                server_hostname=host,
            ),
        ):
            return True, None
    except TimeoutError as exc:
        return False, f"timeout:{exc}"
    except OSError as exc:
        return False, str(exc)
    except ssl.SSLError as exc:
        return False, str(exc)


def _model_available(error: AIClientError | None) -> bool | None:
    if error is None:
        return True
    if error.failure_type == "model_unavailable":
        return False
    return None


def _auth_valid(error: AIClientError | None) -> bool | None:
    if error is None:
        return True
    if error.failure_type == "auth":
        return False
    return None


def _simulate_primary_failure(
    mode: SimulatedFailureMode, *, cfg: AIEndpointConfig, endpoint_name: str
) -> AIClientError | None:
    if mode == "none":
        return None
    if mode == "timeout":
        return AIClientError(
            "Simulated primary timeout for fallback health check",
            provider=cfg.label,
            model=cfg.model,
            endpoint=endpoint_name,
            failure_type="timeout",
            transient=True,
            timeout_type="read",
        )
    if mode == "auth":
        return AIClientError(
            "Simulated primary auth failure for fallback health check",
            provider=cfg.label,
            model=cfg.model,
            endpoint=endpoint_name,
            failure_type="auth",
            transient=False,
            status_code=401,
        )
    return None


def _log_healthcheck(result: ProviderEndpointHealthResult) -> None:
    status = "success" if result.success else "failure"
    parts = [
        "ai_provider_healthcheck",
        f"mode={result.mode}",
        f"endpoint_name={result.endpoint_name}",
        f"provider={result.provider}",
        f"model={result.model}",
        f"status={status}",
        f"latency_ms={result.latency_ms:.1f}",
        f"retry_count={result.retry_count}",
        f"circuit_open={result.circuit_open}",
        f"dns_ok={result.dns_ok}",
        f"tls_ok={result.tls_ok}",
    ]
    if result.failure_type:
        parts.append(f"failure_type={result.failure_type}")
    if result.timeout_type:
        parts.append(f"timeout_type={result.timeout_type}")
    if result.http_status is not None:
        parts.append(f"http_status={result.http_status}")
    if result.request_id:
        parts.append(f"request_id={result.request_id}")
    if result.simulated_failure:
        parts.append(f"simulated_failure={result.simulated_failure}")
    if result.response_preview:
        parts.append(f"response_preview={result.response_preview!r}")
    logger.info(" ".join(parts))


def _result_from_error(
    *,
    mode: str,
    endpoint_name: str,
    cfg: AIEndpointConfig,
    dns_ok: bool,
    dns_addresses: list[str],
    tls_ok: bool,
    circuit_state: dict[str, Any],
    error: AIClientError,
    latency_ms: float,
    used_fallback: bool = False,
    simulated_failure: str | None = None,
) -> ProviderEndpointHealthResult:
    set_ai_provider_circuit_open(
        provider=cfg.label,
        model=cfg.model,
        endpoint=endpoint_name,
        is_open=bool(circuit_state.get("is_open")),
    )
    result = ProviderEndpointHealthResult(
        mode=mode,
        endpoint_name=endpoint_name,
        provider=cfg.label,
        endpoint=cfg.base_url,
        model=cfg.model,
        configured=bool(cfg.base_url and cfg.model),
        ready=bool(cfg.base_url and cfg.model and not (cfg.require_api_key and not cfg.api_key)),
        success=False,
        latency_ms=latency_ms,
        failure_type=error.failure_type,
        retry_count=error.retry_count,
        timeout_type=error.timeout_type,
        http_status=error.status_code,
        request_id=error.request_id,
        circuit_open=bool(circuit_state.get("is_open")),
        circuit_consecutive_failures=int(circuit_state.get("consecutive_failures") or 0),
        circuit_cooldown_remaining_seconds=float(circuit_state.get("cooldown_remaining_seconds") or 0.0),
        dns_ok=dns_ok,
        dns_addresses=dns_addresses,
        tls_ok=tls_ok,
        auth_valid=_auth_valid(error),
        model_available=_model_available(error),
        used_fallback=used_fallback,
        simulated_failure=simulated_failure,
        response_preview=error.response_preview,
    )
    observe_ai_provider_healthcheck(
        provider=cfg.label,
        model=cfg.model,
        endpoint=endpoint_name,
        mode=mode,
        status="failure",
        latency_ms=latency_ms,
    )
    observe_ai_provider_healthcheck_failure(
        provider=cfg.label,
        model=cfg.model,
        endpoint=endpoint_name,
        mode=mode,
        failure_type=error.failure_type,
    )
    _log_healthcheck(result)
    return result


def _probe_endpoint(
    db: Session,
    *,
    endpoint_name: AIEndpoint,
    mode: str,
    respect_circuit: bool = True,
    simulated_failure: SimulatedFailureMode = "none",
    used_fallback: bool = False,
) -> ProviderEndpointHealthResult:
    cfg = ai_gateway.get_endpoint_config(db, endpoint_name)
    circuit_state = ai_gateway.circuit_state(db, endpoint_name)
    dns_ok, dns_addresses, dns_error = _dns_check(cfg.base_url)
    tls_ok, tls_error = _tls_check(cfg.base_url, cfg.timeout_seconds)

    if simulated_failure != "none":
        error = _simulate_primary_failure(simulated_failure, cfg=cfg, endpoint_name=endpoint_name)
        if error is None:
            raise RuntimeError("expected simulated error")
        return _result_from_error(
            mode=mode,
            endpoint_name=endpoint_name,
            cfg=cfg,
            dns_ok=dns_ok,
            dns_addresses=dns_addresses,
            tls_ok=tls_ok,
            circuit_state=circuit_state,
            error=error,
            latency_ms=0.0,
            used_fallback=used_fallback,
            simulated_failure=simulated_failure,
        )

    if not cfg.base_url or not cfg.model:
        error = AIClientError(
            f"AI endpoint not configured: {endpoint_name}",
            provider=cfg.label,
            model=cfg.model,
            endpoint=endpoint_name,
            failure_type="not_configured",
        )
        return _result_from_error(
            mode=mode,
            endpoint_name=endpoint_name,
            cfg=cfg,
            dns_ok=dns_ok,
            dns_addresses=dns_addresses,
            tls_ok=tls_ok,
            circuit_state=circuit_state,
            error=error,
            latency_ms=0.0,
            used_fallback=used_fallback,
        )

    if cfg.require_api_key and not cfg.api_key:
        error = AIClientError(
            f"AI endpoint requires an API key: {endpoint_name}",
            provider=cfg.label,
            model=cfg.model,
            endpoint=endpoint_name,
            failure_type="auth",
            status_code=401,
        )
        return _result_from_error(
            mode=mode,
            endpoint_name=endpoint_name,
            cfg=cfg,
            dns_ok=dns_ok,
            dns_addresses=dns_addresses,
            tls_ok=tls_ok,
            circuit_state=circuit_state,
            error=error,
            latency_ms=0.0,
            used_fallback=used_fallback,
        )

    if not dns_ok:
        error = AIClientError(
            f"DNS resolution failed for endpoint {endpoint_name}: {dns_error or 'dns_network'}",
            provider=cfg.label,
            model=cfg.model,
            endpoint=endpoint_name,
            failure_type="dns_network",
            transient=True,
        )
        return _result_from_error(
            mode=mode,
            endpoint_name=endpoint_name,
            cfg=cfg,
            dns_ok=dns_ok,
            dns_addresses=dns_addresses,
            tls_ok=tls_ok,
            circuit_state=circuit_state,
            error=error,
            latency_ms=0.0,
            used_fallback=used_fallback,
        )

    if not tls_ok:
        failure_type = "timeout" if tls_error and "timeout" in tls_error.lower() else "tls_handshake"
        error = AIClientError(
            f"TLS connectivity failed for endpoint {endpoint_name}: {tls_error or failure_type}",
            provider=cfg.label,
            model=cfg.model,
            endpoint=endpoint_name,
            failure_type=failure_type,
            timeout_type="connect" if failure_type == "timeout" else None,
            transient=True,
        )
        return _result_from_error(
            mode=mode,
            endpoint_name=endpoint_name,
            cfg=cfg,
            dns_ok=dns_ok,
            dns_addresses=dns_addresses,
            tls_ok=tls_ok,
            circuit_state=circuit_state,
            error=error,
            latency_ms=0.0,
            used_fallback=used_fallback,
        )

    if respect_circuit and circuit_state.get("is_open"):
        error = AIClientError(
            f"AI circuit open provider={cfg.label} endpoint={endpoint_name}",
            provider=cfg.label,
            model=cfg.model,
            endpoint=endpoint_name,
            failure_type="circuit_open",
            transient=True,
        )
        return _result_from_error(
            mode=mode,
            endpoint_name=endpoint_name,
            cfg=cfg,
            dns_ok=dns_ok,
            dns_addresses=dns_addresses,
            tls_ok=tls_ok,
            circuit_state=circuit_state,
            error=error,
            latency_ms=0.0,
            used_fallback=used_fallback,
        )

    client = ai_gateway._client_for(cfg)
    start = perf_counter()
    try:
        client.generate(
            _HEALTHCHECK_SYSTEM_PROMPT,
            _HEALTHCHECK_USER_PROMPT,
            max_tokens=min(cfg.max_tokens, 16),
        )
    except AIClientError as exc:
        latency_ms = exc.latency_ms if exc.latency_ms is not None else (perf_counter() - start) * 1000.0
        return _result_from_error(
            mode=mode,
            endpoint_name=endpoint_name,
            cfg=cfg,
            dns_ok=dns_ok,
            dns_addresses=dns_addresses,
            tls_ok=tls_ok,
            circuit_state=circuit_state,
            error=exc,
            latency_ms=latency_ms,
            used_fallback=used_fallback,
        )

    latency_ms = (perf_counter() - start) * 1000.0
    set_ai_provider_circuit_open(
        provider=cfg.label,
        model=cfg.model,
        endpoint=endpoint_name,
        is_open=bool(circuit_state.get("is_open")),
    )
    result = ProviderEndpointHealthResult(
        mode=mode,
        endpoint_name=endpoint_name,
        provider=cfg.label,
        endpoint=cfg.base_url,
        model=cfg.model,
        configured=True,
        ready=True,
        success=True,
        latency_ms=latency_ms,
        failure_type=None,
        retry_count=0,
        timeout_type=None,
        http_status=None,
        request_id=None,
        circuit_open=bool(circuit_state.get("is_open")),
        circuit_consecutive_failures=int(circuit_state.get("consecutive_failures") or 0),
        circuit_cooldown_remaining_seconds=float(circuit_state.get("cooldown_remaining_seconds") or 0.0),
        dns_ok=dns_ok,
        dns_addresses=dns_addresses,
        tls_ok=tls_ok,
        auth_valid=True,
        model_available=True,
        used_fallback=used_fallback,
    )
    observe_ai_provider_healthcheck(
        provider=cfg.label,
        model=cfg.model,
        endpoint=endpoint_name,
        mode=mode,
        status="success",
        latency_ms=latency_ms,
    )
    _log_healthcheck(result)
    return result


def run_provider_healthcheck(
    db: Session,
    *,
    mode: HealthMode = "primary",
    respect_circuit: bool = True,
    simulate_primary_failure: SimulatedFailureMode = "none",
) -> ProviderHealthReport:
    if mode == "primary":
        primary_result = _probe_endpoint(
            db,
            endpoint_name="primary",
            mode=mode,
            respect_circuit=respect_circuit,
        )
        return ProviderHealthReport(
            mode=mode, overall_success=primary_result.success, fallback_used=False, results=[primary_result]
        )

    if mode == "secondary":
        secondary_result = _probe_endpoint(
            db,
            endpoint_name="secondary",
            mode=mode,
            respect_circuit=respect_circuit,
        )
        return ProviderHealthReport(
            mode=mode,
            overall_success=secondary_result.success,
            fallback_used=False,
            results=[secondary_result],
        )

    primary_result = _probe_endpoint(
        db,
        endpoint_name="primary",
        mode=mode,
        respect_circuit=respect_circuit,
        simulated_failure=simulate_primary_failure,
    )
    if primary_result.success:
        return ProviderHealthReport(mode=mode, overall_success=True, fallback_used=False, results=[primary_result])
    if primary_result.failure_type == "auth":
        return ProviderHealthReport(mode=mode, overall_success=False, fallback_used=False, results=[primary_result])
    secondary_result = _probe_endpoint(
        db,
        endpoint_name="secondary",
        mode=mode,
        respect_circuit=respect_circuit,
        used_fallback=True,
    )
    return ProviderHealthReport(
        mode=mode,
        overall_success=secondary_result.success,
        fallback_used=secondary_result.success,
        results=[primary_result, secondary_result],
    )


def render_health_report_text(report: ProviderHealthReport) -> str:
    lines = [
        f"mode={report.mode} overall_success={str(report.overall_success).lower()} fallback_used={str(report.fallback_used).lower()}"
    ]
    for result in report.results:
        lines.append(
            " ".join(
                [
                    f"endpoint_name={result.endpoint_name}",
                    f"provider={result.provider}",
                    f"model={result.model or '-'}",
                    f"success={str(result.success).lower()}",
                    f"latency_ms={result.latency_ms:.1f}",
                    f"failure_type={result.failure_type or '-'}",
                    f"circuit_open={str(result.circuit_open).lower()}",
                    f"dns_ok={str(result.dns_ok).lower()}",
                    f"tls_ok={str(result.tls_ok).lower()}",
                    f"auth_valid={json.dumps(result.auth_valid)}",
                    f"model_available={json.dumps(result.model_available)}",
                ]
            )
        )
    return "\n".join(lines)
