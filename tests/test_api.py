from fastapi.testclient import TestClient

from app import main
from app.main import app


def test_health_and_unknown_job():
    with TestClient(app) as client:
        assert client.get("/health").json() == {"ok": True}
        assert client.get("/jobs/nope").status_code == 404


def test_docs_are_public_and_advertise_the_bearer_scheme():
    with TestClient(app) as client:
        assert client.get("/docs").status_code == 200
        spec = client.get("/openapi.json").json()
        assert "HTTPBearer" in spec["components"]["securitySchemes"]
        assert spec["paths"]["/usage"]["get"]["security"] == [{"HTTPBearer": []}]


def test_endpoints_need_a_google_token(anonymous):
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        res = client.get("/me")
        assert res.status_code == 401
        assert res.json()["detail"]["code"] == "unauthorized"


def test_me_reports_daily_quota():
    with TestClient(app) as client:
        body = client.get("/me").json()
        assert body["email"] == "pedro@example.com"
        assert body["used_today"] == 0
        assert body["limit"] == 3


def test_create_job_enforces_the_daily_limit(monkeypatch):
    async def three(_user_id):
        return 3

    monkeypatch.setattr(main.db, "jobs_today", three)
    with TestClient(app) as client:
        res = client.post("/jobs", files={"video": ("v.mp4", b"x", "video/mp4")})
        assert res.status_code == 429
        assert res.json()["detail"]["code"] == "daily_limit"


def test_access_log_filter_redacts_tokens_in_urls():
    import logging

    from app.main import RedactTokens

    record = logging.LogRecord(
        "uvicorn.access", 20, "", 0, '%s "%s"', ("1.2.3.4", "GET /x?token=abc.def HTTP/1.1"), None
    )
    assert RedactTokens().filter(record)
    assert record.args[1] == "GET /x?token=[redacted] HTTP/1.1"


def test_list_jobs_is_empty_offline():
    with TestClient(app) as client:
        assert client.get("/jobs").json() == []
