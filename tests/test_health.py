import pytest
from fastapi.testclient import TestClient

import app.api.leads as leads_module
from app.config import Settings, get_settings
from app.main import create_app
from app.observability.metrics import REQUEST_COUNT


def test_health_ok():
    client = TestClient(create_app())
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_health_ok_with_request_id_middleware_active():
    client = TestClient(create_app())
    resp = client.get("/health")
    assert resp.status_code == 200


def test_metrics_endpoint_returns_prometheus_format():
    client = TestClient(create_app())
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "http_requests_total" in resp.text
    assert "job_outcomes_total" in resp.text


def test_metrics_record_route_template_not_concrete_path_for_parameterized_route(monkeypatch):
    class _FakeRepo:
        def get_by_domain(self, domain):
            return None

    monkeypatch.setattr(leads_module, "build_lead_repository", lambda settings: _FakeRepo())

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)
    client = TestClient(app)

    resp1 = client.get("/v1/leads/acme.com")
    resp2 = client.get("/v1/leads/other.com")
    assert resp1.status_code == 404
    assert resp2.status_code == 404

    metrics_resp = client.get("/metrics")
    assert metrics_resp.status_code == 200
    assert 'path="/v1/leads/{domain}"' in metrics_resp.text
    assert "acme.com" not in metrics_resp.text
    assert "other.com" not in metrics_resp.text


def test_exception_in_handler_still_records_500_metric_and_reraises(monkeypatch):
    class _RaisingRepo:
        def get_by_domain(self, domain):
            raise RuntimeError("boom")

    monkeypatch.setattr(leads_module, "build_lead_repository", lambda settings: _RaisingRepo())

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)
    client = TestClient(app)

    before = REQUEST_COUNT.labels(
        method="GET", path="/v1/leads/{domain}", status="500"
    )._value.get()

    with pytest.raises(RuntimeError):
        client.get("/v1/leads/boom.com")

    after = REQUEST_COUNT.labels(
        method="GET", path="/v1/leads/{domain}", status="500"
    )._value.get()
    assert after == before + 1
