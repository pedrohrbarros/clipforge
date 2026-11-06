import asyncio
import datetime as dt
import logging
import shutil
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from app import ffmpeg
from app.auth import User
from app.config import settings
from app.db import Db
from app.ffmpeg import FfmpegError
from app.groq_client import GroqAuthError, GroqClient, GroqQuotaError, GroqRequestError
from app.render import ASPECTS, DEFAULT_ASPECT, build_ass, render_args
from app.selector import SelectionError, select_clips
from app.storage import Storage
from app.transcribe import transcribe_video

log = logging.getLogger("clipforge")


class VideoTooLong(Exception):
    code = "too_long"

    def __init__(self, seconds: float):
        minutes = settings.max_video_seconds // 60
        super().__init__(
            f"This video is {seconds / 60:.0f} minutes long; the limit is {minutes} minutes."
        )


def check_duration(seconds: float | None) -> None:
    if seconds is not None and seconds > settings.max_video_seconds:
        raise VideoTooLong(seconds)


class Status(StrEnum):
    queued = "queued"
    transcribing = "transcribing"
    selecting = "selecting"
    rendering = "rendering"
    done = "done"
    failed = "failed"


@dataclass
class Short:
    index: int
    title: str
    start: float
    end: float
    path: Path | None = None
    url: str | None = None  # signed storage URL once uploaded
    aspect: str = DEFAULT_ASPECT

    def public(self) -> dict:
        return {
            "index": self.index,
            "title": self.title,
            "start": self.start,
            "end": self.end,
            "duration": round(self.end - self.start, 1),
            "url": self.url,
            "aspect": self.aspect,
        }


@dataclass
class Job:
    id: str
    dir: Path
    captions: bool
    user: User
    min_clips: int = 1
    aspect: str = DEFAULT_ASPECT
    lang: str = "en"
    created_at: float = field(default_factory=time.time)
    status: Status = Status.queued
    progress: int = 0
    message: str = ""
    code: str = ""
    shorts: list[Short] = field(default_factory=list)
    zip_url: str | None = None
    stored: bool = False  # files are in the bucket (keys derive from user and job ids)

    @property
    def video_path(self) -> Path:
        return self.dir / "input"

    def public(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "code": self.code,
            "shorts": [s.public() for s in self.shorts],
            "zip_url": self.zip_url,
            "stored": self.stored,
            "expired": False,
            "created_at": dt.datetime.fromtimestamp(self.created_at, dt.UTC).isoformat(),
            "finished_at": None,
        }

    def row(self) -> dict:
        """The persisted shape (Supabase `jobs` table)."""
        return {
            "id": self.id,
            "user_id": self.user.id,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "code": self.code,
            "captions": self.captions,
            "lang": self.lang,
            "shorts": [s.public() for s in self.shorts],
            "zip_url": self.zip_url if self.stored else None,
        }


def public_from_row(row: dict) -> dict:
    """A job loaded from the database, in the same shape as Job.public()."""
    return {
        "id": row["id"],
        "status": row["status"],
        "progress": row.get("progress", 0),
        "message": row.get("message", ""),
        "code": row.get("code", ""),
        "shorts": row.get("shorts") or [],
        "zip_url": row.get("zip_url"),
        "stored": bool(row.get("zip_url")),
        "expired": _expired(row.get("created_at")),
        "created_at": row.get("created_at"),
        "finished_at": row.get("finished_at"),
    }


def _expired(created_at: str | None) -> bool:
    if not created_at:
        return False
    created = dt.datetime.fromisoformat(created_at)
    return dt.datetime.now(dt.UTC) - created > dt.timedelta(days=settings.retention_days)


@dataclass
class Services:
    db: Db
    storage: Storage


class JobStore:
    def __init__(self, root: Path, ttl_seconds: int):
        self.root = root
        self.ttl = ttl_seconds
        self._jobs: dict[str, Job] = {}

    def create(
        self, captions: bool, user: User, min_clips: int = 1, aspect: str = DEFAULT_ASPECT
    ) -> Job:
        job_id = uuid.uuid4().hex
        job = Job(
            id=job_id,
            dir=self.root / job_id,
            captions=captions,
            user=user,
            min_clips=min_clips,
            aspect=aspect if aspect in ASPECTS else DEFAULT_ASPECT,
        )
        job.dir.mkdir(parents=True, exist_ok=True)
        self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def delete(self, job_id: str) -> None:
        job = self._jobs.pop(job_id, None)
        if job:
            shutil.rmtree(job.dir, ignore_errors=True)

    def sweep(self, now: float | None = None) -> list[str]:
        now = now or time.time()
        expired = [j.id for j in self._jobs.values() if now - j.created_at > self.ttl]
        for job_id in expired:
            self.delete(job_id)
        return expired

    async def sweeper(self, interval: int = 300) -> None:
        while True:
            await asyncio.sleep(interval)
            removed = self.sweep()
            if removed:
                log.info("swept %d expired jobs", len(removed))


async def _persist(job: Job, db: Db, **extra) -> None:
    """Best effort: a database hiccup must not fail the job."""
    try:
        await db.update_job(job.id, {**job.row(), **extra})
    except Exception:
        log.exception("could not persist job %s", job.id)


async def _set(job: Job, db: Db, status: Status, progress: int) -> None:
    job.status, job.progress = status, progress
    await _persist(job, db)


def short_key(user_id: str, job_id: str, index: int) -> str:
    return f"{user_id}/{job_id}/short-{index}.mp4"


def zip_key(user_id: str, job_id: str) -> str:
    return f"{user_id}/{job_id}/shorts.zip"


async def sign_links(public: dict, user_id: str, storage: Storage) -> dict:
    """Fresh signed links for a finished job's stored files (keys are deterministic)."""
    if not storage.enabled or public.get("status") != Status.done or not public.get("stored"):
        return public
    expires = settings.link_hours * 3600
    shorts = []
    for short in public["shorts"]:
        url = await storage.sign(short_key(user_id, public["id"], short["index"]), expires)
        shorts.append({**short, "url": url})
    zip_url = await storage.sign(zip_key(user_id, public["id"]), expires)
    return {**public, "shorts": shorts, "zip_url": zip_url}


async def _publish(job: Job, storage: Storage) -> None:
    """Upload shorts and a zip to the private bucket; links are signed on read."""
    if not storage.enabled:
        return
    expires = settings.link_hours * 3600
    zip_path = job.dir / "shorts.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for short in job.shorts:
            assert short.path is not None
            zf.write(short.path, f"short-{short.index}.mp4")
    for short in job.shorts:
        assert short.path is not None
        key = short_key(job.user.id, job.id, short.index)
        await storage.upload(short.path, key, "video/mp4")
        short.url = await storage.sign(key, expires)
    await storage.upload(zip_path, zip_key(job.user.id, job.id), "application/zip")
    job.zip_url = await storage.sign(zip_key(job.user.id, job.id), expires)
    job.stored = True
    shutil.rmtree(job.dir, ignore_errors=True)


async def run_job(job: Job, groq: GroqClient, services: Services) -> None:
    db, storage = services.db, services.storage
    try:
        video = job.video_path
        check_duration(await ffmpeg.duration(video))
        await _set(job, db, Status.transcribing, 10)
        words, segments = await transcribe_video(video, job.dir, groq)
        if not segments:
            raise SelectionError("No speech found in this video", "no_speech")
        await _set(job, db, Status.selecting, 40)
        clips = await select_clips(segments, groq, min_clips=job.min_clips)
        await _set(job, db, Status.rendering, 50)
        for i, clip in enumerate(clips, start=1):
            out = job.dir / f"short-{i}.mp4"
            ass = None
            if job.captions:
                ass = job.dir / f"short-{i}.ass"
                ass.write_text(
                    build_ass(words, clip.start, clip.end, aspect=job.aspect), encoding="utf-8"
                )
            await ffmpeg.run(render_args(video, out, clip, ass, job.aspect))
            job.shorts.append(Short(i, clip.title, clip.start, clip.end, out, aspect=job.aspect))
            job.progress = 50 + int(45 * i / len(clips))
        await _publish(job, storage)
        job.status, job.progress = Status.done, 100
        await _persist(job, db, finished_at=dt.datetime.now(dt.UTC).isoformat())
    except GroqAuthError as e:
        await _fail(job, services, str(e), "auth")
    except GroqQuotaError as e:
        await _fail(job, services, str(e), "quota")
    except (SelectionError, VideoTooLong) as e:
        await _fail(job, services, str(e), e.code)
    except GroqRequestError as e:
        log.warning("groq %s for %s: %s", e.status, job.id, e)
        if e.status in (401, 403):
            await _fail(job, services, "Groq rejected the API key. Set GROQ_API_KEY.", "auth")
        elif e.status == 413:
            await _fail(
                job, services, "This video's transcript is too long.", "transcript_too_long"
            )
        else:
            await _fail(job, services, f"The AI service answered {e.status}: {e}", "ai_error")
    except FfmpegError as e:
        log.warning("ffmpeg failed for %s: %s", job.id, e)
        await _fail(job, services, "Could not read this video", "unreadable")
    except Exception:
        log.exception("job %s crashed", job.id)
        await _fail(job, services, "Something went wrong, try again", "error")


async def _fail(job: Job, services: Services, message: str, code: str) -> None:
    """Mark the job failed with a human message and a stable code the UI can translate."""
    job.status, job.message, job.code = Status.failed, message, code
    await _persist(job, services.db, finished_at=dt.datetime.now(dt.UTC).isoformat())
