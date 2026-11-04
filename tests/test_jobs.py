from pathlib import Path

from app import jobs
from app.jobs import Job, Status, run_job


async def test_run_job_rejects_videos_over_the_limit(monkeypatch, tmp_path: Path):
    async def long_duration(_path):
        return 601.0

    monkeypatch.setattr(jobs.ffmpeg, "duration", long_duration)
    job = Job(id="j", dir=tmp_path, captions=True)
    await run_job(job, groq=None)
    assert job.status == Status.failed
    assert "limit is 10 minutes" in job.message
    assert job.code == "too_long"
