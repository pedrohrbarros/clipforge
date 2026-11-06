from pathlib import Path

from app import jobs
from app.db import Db
from app.jobs import Job, Services, Status, run_job
from app.storage import Storage
from tests.conftest import FAKE_USER


def _services() -> Services:
    return Services(Db(""), Storage("", "", "shorts"))


async def test_run_job_rejects_videos_over_the_limit(monkeypatch, tmp_path: Path):
    async def long_duration(_path):
        return 601.0

    monkeypatch.setattr(jobs.ffmpeg, "duration", long_duration)
    job = Job(id="j", dir=tmp_path, captions=True, user=FAKE_USER)
    await run_job(job, groq=None, services=_services())
    assert job.status == Status.failed
    assert "limit is 10 minutes" in job.message
    assert job.code == "too_long"


async def test_sign_links_refreshes_urls_for_stored_jobs(monkeypatch):
    class FakeStorage:
        enabled = True

        async def sign(self, key, expires):
            return f"https://signed/{key}?e={expires}"

    public = {"id": "j", "status": "done", "stored": True, "shorts": [{"index": 1, "url": "old"}]}
    out = await jobs.sign_links(public, "u", FakeStorage())
    assert out["shorts"][0]["url"] == "https://signed/u/j/short-1.mp4?e=3600"
    assert out["zip_url"] == "https://signed/u/j/shorts.zip?e=3600"
    untouched = await jobs.sign_links({**public, "stored": False}, "u", FakeStorage())
    assert untouched["shorts"][0]["url"] == "old"
