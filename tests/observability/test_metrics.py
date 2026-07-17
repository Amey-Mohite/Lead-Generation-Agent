from app.observability.metrics import JOB_OUTCOMES, REQUEST_COUNT, record_job_outcome, record_request


def test_record_request_increments_counter():
    before = REQUEST_COUNT.labels(method="GET", path="/v1/leads", status="200")._value.get()
    record_request(method="GET", path="/v1/leads", status=200, duration_seconds=0.05)
    after = REQUEST_COUNT.labels(method="GET", path="/v1/leads", status="200")._value.get()
    assert after == before + 1


def test_record_job_outcome_increments_counter():
    before = JOB_OUTCOMES.labels(kind="lead", status="done")._value.get()
    record_job_outcome(kind="lead", status="done")
    after = JOB_OUTCOMES.labels(kind="lead", status="done")._value.get()
    assert after == before + 1
