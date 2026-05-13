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

DB_SESSIONS_CREATED = Counter(
    "db_sessions_created_total",
    "Total SQLAlchemy sessions created",
    ["scope"],
)

DB_SESSIONS_OPEN = Gauge(
    "db_sessions_open",
    "Current number of open SQLAlchemy sessions",
    ["scope"],
)

DB_TRANSACTION_DURATION = Histogram(
    "db_transaction_duration_seconds",
    "Database transaction duration in seconds",
    ["scope", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)

DB_POOL_CHECKED_OUT = Gauge(
    "db_pool_checked_out",
    "Current number of checked out DB connections in the SQLAlchemy pool",
    [],
)

DB_POOL_SIZE = Gauge(
    "db_pool_size",
    "Configured DB connection pool size when available",
    [],
)

DB_POOL_OVERFLOW = Gauge(
    "db_pool_overflow",
    "Current SQLAlchemy pool overflow when available",
    [],
)

DB_RUNTIME_SESSIONS = Gauge(
    "db_runtime_sessions",
    "Current live Postgres session count by state for the application database",
    ["state"],
)

DB_OLDEST_TRANSACTION_AGE = Gauge(
    "db_oldest_transaction_age_seconds",
    "Age in seconds of the oldest open transaction observed in Postgres",
    [],
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

AI_PROVIDER_CIRCUIT_OPEN_DURATION = Gauge(
    "ai_provider_circuit_open_duration_seconds",
    "Current AI provider circuit open duration in seconds",
    ["provider", "model", "endpoint"],
)

AI_PROVIDER_RETRY_EXHAUSTIONS = Counter(
    "ai_provider_retry_exhaustions_total",
    "Total AI provider requests that exhausted retries",
    ["provider", "model", "endpoint", "failure_type"],
)

AI_INTAKE_RESULTS = Counter(
    "ai_intake_results_total",
    "Total AI intake results by outcome/channel/failure type",
    ["outcome", "channel", "failure_type"],
)

AI_QUEUE_DEPTH = Gauge(
    "ai_queue_depth",
    "Current queue depth for AI-relevant queues",
    ["queue_name"],
)

AI_QUEUE_OLDEST_TASK_AGE = Gauge(
    "ai_queue_oldest_task_age_seconds",
    "Age of the oldest visible task in seconds when available",
    ["queue_name"],
)

AI_WORKER_UP = Gauge(
    "ai_worker_up",
    "Current Celery worker reachability from inspect ping (1=up, 0=down)",
    ["worker_name"],
)

AI_WORKER_ACTIVE_TASKS = Gauge(
    "ai_worker_active_tasks",
    "Current number of active tasks per Celery worker",
    ["worker_name"],
)

AI_WORKER_RESERVED_TASKS = Gauge(
    "ai_worker_reserved_tasks",
    "Current number of reserved tasks per Celery worker",
    ["worker_name"],
)

AI_WORKER_SCHEDULED_TASKS = Gauge(
    "ai_worker_scheduled_tasks",
    "Current number of scheduled tasks per Celery worker",
    ["worker_name"],
)

AI_INTAKE_LAST_SUCCESS_AGE = Gauge(
    "ai_intake_last_success_age_seconds",
    "Seconds since the last resolved AI intake",
    [],
)


def observe_job(task_name: str, status: str, duration: float) -> None:
    JOB_DURATION.labels(task=task_name, status=status).observe(duration)


def observe_db_session_created(*, scope: str) -> None:
    DB_SESSIONS_CREATED.labels(scope=scope).inc()
    DB_SESSIONS_OPEN.labels(scope=scope).inc()


def observe_db_session_closed(*, scope: str) -> None:
    DB_SESSIONS_OPEN.labels(scope=scope).dec()


def observe_db_transaction_duration(*, scope: str, path: str, duration_seconds: float) -> None:
    DB_TRANSACTION_DURATION.labels(scope=scope, path=path).observe(max(float(duration_seconds), 0.0))


def set_db_pool_state(*, checked_out: int | None = None, size: int | None = None, overflow: int | None = None) -> None:
    if checked_out is not None:
        DB_POOL_CHECKED_OUT.set(max(int(checked_out), 0))
    if size is not None:
        DB_POOL_SIZE.set(max(int(size), 0))
    if overflow is not None:
        DB_POOL_OVERFLOW.set(int(overflow))


def set_db_runtime_sessions(*, active: int, idle: int, idle_in_transaction: int, total: int) -> None:
    DB_RUNTIME_SESSIONS.labels(state="active").set(max(int(active), 0))
    DB_RUNTIME_SESSIONS.labels(state="idle").set(max(int(idle), 0))
    DB_RUNTIME_SESSIONS.labels(state="idle_in_transaction").set(max(int(idle_in_transaction), 0))
    DB_RUNTIME_SESSIONS.labels(state="total").set(max(int(total), 0))


def set_db_oldest_transaction_age(*, duration_seconds: float) -> None:
    DB_OLDEST_TRANSACTION_AGE.set(max(float(duration_seconds), 0.0))


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


def set_ai_provider_circuit_open_duration(*, provider: str, model: str, endpoint: str, duration_seconds: float) -> None:
    AI_PROVIDER_CIRCUIT_OPEN_DURATION.labels(
        provider=provider,
        model=model,
        endpoint=endpoint,
    ).set(max(float(duration_seconds), 0.0))


def observe_ai_provider_retry_exhaustion(*, provider: str, model: str, endpoint: str, failure_type: str) -> None:
    AI_PROVIDER_RETRY_EXHAUSTIONS.labels(
        provider=provider,
        model=model,
        endpoint=endpoint,
        failure_type=failure_type,
    ).inc()


def observe_ai_intake_result(*, outcome: str, channel: str, failure_type: str = "none") -> None:
    AI_INTAKE_RESULTS.labels(
        outcome=outcome,
        channel=channel,
        failure_type=failure_type,
    ).inc()


def set_ai_queue_depth(*, queue_name: str, depth: int) -> None:
    AI_QUEUE_DEPTH.labels(queue_name=queue_name).set(max(int(depth), 0))


def set_ai_queue_oldest_task_age(*, queue_name: str, age_seconds: float | None) -> None:
    AI_QUEUE_OLDEST_TASK_AGE.labels(queue_name=queue_name).set(max(float(age_seconds or 0.0), 0.0))


def set_ai_worker_health(
    *,
    worker_name: str,
    is_up: bool,
    active_tasks: int,
    reserved_tasks: int,
    scheduled_tasks: int,
) -> None:
    AI_WORKER_UP.labels(worker_name=worker_name).set(1 if is_up else 0)
    AI_WORKER_ACTIVE_TASKS.labels(worker_name=worker_name).set(max(int(active_tasks), 0))
    AI_WORKER_RESERVED_TASKS.labels(worker_name=worker_name).set(max(int(reserved_tasks), 0))
    AI_WORKER_SCHEDULED_TASKS.labels(worker_name=worker_name).set(max(int(scheduled_tasks), 0))


def set_ai_intake_last_success_age(*, age_seconds: float | None) -> None:
    AI_INTAKE_LAST_SUCCESS_AGE.set(max(float(age_seconds or 0.0), 0.0))


# ---------------------------------------------------------------------------
# Workqueue metrics
# ---------------------------------------------------------------------------

WORKQUEUE_RENDER_MS = Histogram(
    "workqueue_render_ms",
    "Workqueue page/partial render latency in milliseconds.",
    ["audience", "view"],
    buckets=(10, 25, 50, 100, 150, 250, 400, 800, 1500),
)

WORKQUEUE_ACTION_TOTAL = Counter(
    "workqueue_action_total",
    "Workqueue inline-action invocations.",
    ["kind", "action"],
)

WORKQUEUE_WS_EVENT_TOTAL = Counter(
    "workqueue_ws_event_total",
    "Workqueue WebSocket events emitted (per channel target).",
    ["kind", "change"],
)


def observe_workqueue_render(*, audience: str, view: str, duration_ms: float) -> None:
    WORKQUEUE_RENDER_MS.labels(audience=audience, view=view).observe(max(float(duration_ms), 0.0))


def observe_workqueue_action(*, kind: str, action: str) -> None:
    WORKQUEUE_ACTION_TOTAL.labels(kind=kind, action=action).inc()


def observe_workqueue_ws_event(*, kind: str, change: str, count: int = 1) -> None:
    if count <= 0:
        return
    WORKQUEUE_WS_EVENT_TOTAL.labels(kind=kind, change=change).inc(count)
