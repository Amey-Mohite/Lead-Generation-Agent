from app.api.jobs import JobStore


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
