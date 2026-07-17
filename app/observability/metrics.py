from prometheus_client import CollectorRegistry, Counter, Histogram

registry = CollectorRegistry()

REQUEST_COUNT = Counter(
    "http_requests_total", "Total HTTP requests", ["method", "path", "status"],
    registry=registry,
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds", "HTTP request latency in seconds", ["method", "path"],
    registry=registry,
)
JOB_OUTCOMES = Counter(
    "job_outcomes_total", "Background job outcomes", ["kind", "status"],
    registry=registry,
)


def record_request(method: str, path: str, status: int, duration_seconds: float) -> None:
    REQUEST_COUNT.labels(method=method, path=path, status=str(status)).inc()
    REQUEST_LATENCY.labels(method=method, path=path).observe(duration_seconds)


def record_job_outcome(kind: str, status: str) -> None:
    JOB_OUTCOMES.labels(kind=kind, status=status).inc()
