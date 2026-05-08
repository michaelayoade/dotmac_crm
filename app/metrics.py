from prometheus_client import Counter, Gauge, Histogram

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["method", "path", "status"],
)
REQUEST_ERRORS = Counter(
    "http_request_errors_total",
    "Total HTTP 5xx responses",
    ["method", "path", "status"],
)

JOB_DURATION = Histogram(
    "job_duration_seconds",
    "Background job duration",
    ["task", "status"],
)

AI_PROVIDER_REQUESTS = Counter(
    "ai_provider_requests_total",
    "Total AI provider requests by provider/model/endpoint/outcome",
    ["provider", "model", "endpoint", "outcome"],
)

AI_PROVIDER_FAILURES = Counter(
    "ai_provider_failures_total",
    "Total AI provider failures by provider/model/endpoint/failure type",
    ["provider", "model", "endpoint", "failure_type"],
)

AI_PROVIDER_LATENCY = Histogram(
    "ai_provider_latency_ms",
    "AI provider request latency in milliseconds",
    ["provider", "model", "endpoint", "outcome"],
    buckets=(50, 100, 250, 500, 1000, 2500, 5000, 10000, 20000, 30000, 60000),
)

AI_PROVIDER_FALLBACKS = Counter(
    "ai_provider_fallback_total",
    "Total AI provider fallback activations",
    ["from_endpoint", "to_endpoint", "reason"],
)

AI_INTAKE_ESCALATIONS = Counter(
    "ai_intake_escalation_total",
    "Total CRM AI intake escalations by reason",
    ["reason"],
)

AI_PROVIDER_HEALTHCHECKS = Counter(
    "ai_provider_healthcheck_total",
    "Total AI provider health checks by provider/model/endpoint/mode/status",
    ["provider", "model", "endpoint", "mode", "status"],
)

AI_PROVIDER_HEALTHCHECK_FAILURES = Counter(
    "ai_provider_healthcheck_failures_total",
    "Total AI provider health check failures by provider/model/endpoint/mode/failure type",
    ["provider", "model", "endpoint", "mode", "failure_type"],
)

AI_PROVIDER_HEALTHCHECK_LATENCY = Histogram(
    "ai_provider_healthcheck_latency_ms",
    "AI provider health check latency in milliseconds",
    ["provider", "model", "endpoint", "mode", "status"],
    buckets=(25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 20000, 30000, 60000),
)

AI_PROVIDER_CIRCUIT_OPEN = Gauge(
    "ai_provider_circuit_open",
    "Current AI provider circuit state (1=open, 0=closed)",
    ["provider", "model", "endpoint"],
)


def observe_job(task_name: str, status: str, duration: float) -> None:
    JOB_DURATION.labels(task=task_name, status=status).observe(duration)


def observe_ai_provider_request(
    *,
    provider: str,
    model: str,
    endpoint: str,
    outcome: str,
    latency_ms: float,
) -> None:
    AI_PROVIDER_REQUESTS.labels(
        provider=provider,
        model=model,
        endpoint=endpoint,
        outcome=outcome,
    ).inc()
    AI_PROVIDER_LATENCY.labels(
        provider=provider,
        model=model,
        endpoint=endpoint,
        outcome=outcome,
    ).observe(max(float(latency_ms), 0.0))


def observe_ai_provider_failure(*, provider: str, model: str, endpoint: str, failure_type: str) -> None:
    AI_PROVIDER_FAILURES.labels(
        provider=provider,
        model=model,
        endpoint=endpoint,
        failure_type=failure_type,
    ).inc()


def observe_ai_provider_fallback(*, from_endpoint: str, to_endpoint: str, reason: str) -> None:
    AI_PROVIDER_FALLBACKS.labels(
        from_endpoint=from_endpoint,
        to_endpoint=to_endpoint,
        reason=reason,
    ).inc()


def observe_ai_intake_escalation(*, reason: str) -> None:
    AI_INTAKE_ESCALATIONS.labels(reason=reason).inc()


def observe_ai_provider_healthcheck(
    *,
    provider: str,
    model: str,
    endpoint: str,
    mode: str,
    status: str,
    latency_ms: float,
) -> None:
    AI_PROVIDER_HEALTHCHECKS.labels(
        provider=provider,
        model=model,
        endpoint=endpoint,
        mode=mode,
        status=status,
    ).inc()
    AI_PROVIDER_HEALTHCHECK_LATENCY.labels(
        provider=provider,
        model=model,
        endpoint=endpoint,
        mode=mode,
        status=status,
    ).observe(max(float(latency_ms), 0.0))


def observe_ai_provider_healthcheck_failure(
    *,
    provider: str,
    model: str,
    endpoint: str,
    mode: str,
    failure_type: str,
) -> None:
    AI_PROVIDER_HEALTHCHECK_FAILURES.labels(
        provider=provider,
        model=model,
        endpoint=endpoint,
        mode=mode,
        failure_type=failure_type,
    ).inc()


def set_ai_provider_circuit_open(*, provider: str, model: str, endpoint: str, is_open: bool) -> None:
    AI_PROVIDER_CIRCUIT_OPEN.labels(
        provider=provider,
        model=model,
        endpoint=endpoint,
    ).set(1 if is_open else 0)
