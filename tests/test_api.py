from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import KEY

AUTH = {"Authorization": f"Bearer {KEY}"}


def test_health_and_unknown_job():
    with TestClient(app) as client:
        assert client.get("/health").json() == {"ok": True}
        assert client.get("/jobs/nope", headers=AUTH).status_code == 404


def test_every_endpoint_but_health_needs_the_key():
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/usage").status_code == 401
        assert client.get("/jobs/x", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert client.get(f"/jobs/x?token={KEY}").status_code == 404


def test_create_job_requires_a_video():
    with TestClient(app) as client:
        assert client.post("/jobs", data={"captions": "true"}, headers=AUTH).status_code == 422
