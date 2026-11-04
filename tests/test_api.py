from fastapi.testclient import TestClient

from app.main import app


def test_health_and_unknown_job():
    with TestClient(app) as client:
        assert client.get("/health").json() == {"ok": True}
        assert client.get("/jobs/nope").status_code == 404
