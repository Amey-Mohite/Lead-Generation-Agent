from app.api.jobs import JobStore
from app.config import Settings
from app.observability.metrics import JOB_OUTCOMES


def test_create_returns_a_queued_job():
    store = JobStore()
    job = store.create(kind="lead")
    assert job.status == "queued"
    assert job.kind == "lead"
    assert job.result is None
    assert job.error is None


def test_mark_running_updates_status():
    store = JobStore()
    job = store.create(kind="lead")
    store.mark_running(job.job_id)
    assert store.get(job.job_id).status == "running"


def test_mark_done_sets_result_and_finished_at():
    store = JobStore()
    job = store.create(kind="discovery")
    store.mark_done(job.job_id, {"some": "result"})
    updated = store.get(job.job_id)
    assert updated.status == "done"
    assert updated.result == {"some": "result"}
    assert updated.finished_at is not None


def test_mark_failed_sets_error_and_finished_at():
    store = JobStore()
    job = store.create(kind="lead")
    store.mark_failed(job.job_id, "boom")
    updated = store.get(job.job_id)
    assert updated.status == "failed"
    assert updated.error == "boom"
    assert updated.finished_at is not None


def test_get_returns_none_for_unknown_job_id():
    store = JobStore()
    assert store.get("nonexistent") is None


def test_mark_done_records_job_outcome_metric():
    store = JobStore()
    job = store.create(kind="lead")
    before = JOB_OUTCOMES.labels(kind="lead", status="done")._value.get()
    store.mark_done(job.job_id, "result")
    after = JOB_OUTCOMES.labels(kind="lead", status="done")._value.get()
    assert after == before + 1


def test_mark_failed_records_job_outcome_metric():
    store = JobStore()
    job = store.create(kind="discovery")
    before = JOB_OUTCOMES.labels(kind="discovery", status="failed")._value.get()
    store.mark_failed(job.job_id, "boom")
    after = JOB_OUTCOMES.labels(kind="discovery", status="failed")._value.get()
    assert after == before + 1


def test_mark_failed_triggers_alert_when_settings_configured(monkeypatch):
    import app.api.jobs as jobs_module
    calls = []
    monkeypatch.setattr(jobs_module, "send_alert", lambda settings, **kw: calls.append(kw))

    store = JobStore()
    job = store.create(kind="lead")
    settings = Settings(
        _env_file=None, n8n_alert_webhook_url="https://n8n.example.com/webhook/alert"
    )

    store.mark_failed(job.job_id, "boom", settings)

    assert calls == [{"kind": "lead", "status": "failed", "error": "boom"}]


def test_mark_failed_skips_alert_when_settings_omitted(monkeypatch):
    import app.api.jobs as jobs_module
    calls = []
    monkeypatch.setattr(jobs_module, "send_alert", lambda settings, **kw: calls.append(kw))

    store = JobStore()
    job = store.create(kind="lead")

    store.mark_failed(job.job_id, "boom")

    assert calls == []
