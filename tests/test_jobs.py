from pathlib import Path

from app import jobs
from app.db import Db
from app.jobs import Job, Services, Status, run_job
from app.mailer import Mailer
from app.storage import Storage
from tests.conftest import FAKE_USER


def _services() -> Services:
    return Services(Db(""), Storage("", "", "shorts"), Mailer("", "x", "http://app", 24))


async def test_run_job_rejects_videos_over_the_limit(monkeypatch, tmp_path: Path):
    async def long_duration(_path):
        return 601.0

    monkeypatch.setattr(jobs.ffmpeg, "duration", long_duration)
    job = Job(id="j", dir=tmp_path, captions=True, user=FAKE_USER)
    await run_job(job, groq=None, services=_services())
    assert job.status == Status.failed
    assert "limit is 10 minutes" in job.message
    assert job.code == "too_long"
