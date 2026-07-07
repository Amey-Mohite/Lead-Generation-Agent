from fastapi.testclient import TestClient

import app.api.health as health_module
from app.main import create_app


class _FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, *_a, **_k):
        return None


class _UpEngine:
    def connect(self):
        return _FakeConn()


class _DownEngine:
    def connect(self):
        raise RuntimeError("db down")


def test_ready_up(monkeypatch):
    monkeypatch.setattr(health_module, "get_engine", lambda: _UpEngine())
    client = TestClient(create_app())
    resp = client.get("/ready")
    assert resp.status_code == 200
    assert resp.json()["database"] == "up"


def test_ready_down(monkeypatch):
    monkeypatch.setattr(health_module, "get_engine", lambda: _DownEngine())
    client = TestClient(create_app())
    resp = client.get("/ready")
    assert resp.status_code == 503
    assert resp.json()["database"] == "down"
