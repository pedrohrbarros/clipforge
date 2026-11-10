from pathlib import Path

from app import jobs
from app.db import Db
from app.jobs import Job, Services, Short, Status, run_job
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

    public = {
        "id": "j",
        "status": "done",
        "stored": True,
        "shorts": [{"index": 1, "url": "old"}],
        "zip_url": "old",
    }
    out = await jobs.sign_links(public, "u", FakeStorage())
    assert out["shorts"][0]["url"] == "https://signed/u/j/short-1.mp4?e=3600"
    assert out["zip_url"] == "https://signed/u/j/shorts.zip?e=3600"
    untouched = await jobs.sign_links({**public, "stored": False}, "u", FakeStorage())
    assert untouched["shorts"][0]["url"] == "old"


async def test_publish_skips_the_zip_when_it_exceeds_the_object_limit(monkeypatch, tmp_path):
    class FakeStorage:
        enabled = True
        uploaded: list[str] = []

        async def upload(self, path, key, content_type):
            self.uploaded.append(key)

        async def sign(self, key, expires):
            return f"https://signed/{key}"

    monkeypatch.setattr(jobs.settings, "max_object_mb", 1)
    job = Job(id="j", dir=tmp_path, captions=True, user=FAKE_USER)
    for i in (1, 2):
        path = tmp_path / f"short-{i}.mp4"
        path.write_bytes(b"\0" * 600_000)  # two shorts: each fits, the zip of both does not
        job.shorts.append(Short(index=i, title="t", start=0, end=1, path=path))
    await jobs._publish(job, FakeStorage())
    assert FakeStorage.uploaded == ["u1/j/short-1.mp4", "u1/j/short-2.mp4"]
    assert job.stored and job.zip_url is None


async def test_sign_links_skips_the_zip_when_none_was_stored():
    class FakeStorage:
        enabled = True

        async def sign(self, key, expires):
            assert not key.endswith(".zip")
            return f"https://signed/{key}"

    row = {"id": "j", "status": "done", "shorts": [{"index": 1, "url": "old"}], "zip_url": None}
    public = jobs.public_from_row(row)
    assert public["stored"]
    out = await jobs.sign_links(public, "u", FakeStorage())
    assert out["shorts"][0]["url"] == "https://signed/u/j/short-1.mp4"
    assert out["zip_url"] is None
