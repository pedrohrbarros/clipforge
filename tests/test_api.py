from fastapi.testclient import TestClient

from app.main import app


def test_health_and_unknown_job():
    with TestClient(app) as client:
        assert client.get("/health").json() == {"ok": True}
        assert client.get("/jobs/nope").status_code == 404


def test_create_job_requires_a_video_or_a_youtube_link():
    with TestClient(app) as client:
        assert client.post("/jobs", data={"captions": "true"}).status_code == 400
        res = client.post("/jobs", data={"url": "https://vimeo.com/1"})
        assert res.status_code == 400
        assert "YouTube" in res.json()["detail"]
