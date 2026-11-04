import asyncio
import logging
import shutil
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol

import httpx

from app import ffmpeg
from app.ffmpeg import FfmpegError
from app.groq_client import GroqAuthError, GroqClient, GroqQuotaError
from app.render import build_ass, render_args
from app.selector import SelectionError, select_clips
from app.transcribe import transcribe_video

log = logging.getLogger("clipforge")


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
    path: Path

    def public(self) -> dict:
        return {
            "index": self.index,
            "title": self.title,
            "start": self.start,
            "end": self.end,
            "duration": round(self.end - self.start, 1),
        }


@dataclass
class Job:
    id: str
    dir: Path
    captions: bool
    created_at: float = field(default_factory=time.time)
    status: Status = Status.queued
    progress: int = 0
    message: str = ""
    shorts: list[Short] = field(default_factory=list)

    @property
    def video_path(self) -> Path:
        return self.dir / "input"

    def public(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "shorts": [s.public() for s in self.shorts],
        }


class Expirable(Protocol):
    id: str
    dir: Path
    created_at: float


class JobStore:
    """In-memory registry of shorts jobs and download jobs, each owning a temp dir."""

    def __init__(self, root: Path, ttl_seconds: int):
        self.root = root
        self.ttl = ttl_seconds
        self._jobs: dict[str, Expirable] = {}

    def new_dir(self) -> Path:
        path = self.root / uuid.uuid4().hex
        path.mkdir(parents=True, exist_ok=True)
        return path

    def add(self, job: Expirable) -> None:
        self._jobs[job.id] = job

    def create(self, captions: bool) -> Job:
        job_dir = self.new_dir()
        job = Job(id=job_dir.name, dir=job_dir, captions=captions)
        self.add(job)
        return job

    def get(self, job_id: str) -> Expirable | None:
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


async def run_job(job: Job, groq: GroqClient) -> None:
    try:
        job.status, job.progress = Status.transcribing, 10
        words, segments = await transcribe_video(job.video_path, job.dir, groq)
        if not segments:
            raise SelectionError("No speech found in this video")
        job.status, job.progress = Status.selecting, 40
        clips = await select_clips(segments, groq)
        job.status, job.progress = Status.rendering, 50
        for i, clip in enumerate(clips, start=1):
            out = job.dir / f"short-{i}.mp4"
            ass = None
            if job.captions:
                ass = job.dir / f"short-{i}.ass"
                ass.write_text(build_ass(words, clip.start, clip.end), encoding="utf-8")
            await ffmpeg.run(render_args(job.video_path, out, clip, ass))
            job.shorts.append(Short(i, clip.title, clip.start, clip.end, out))
            job.progress = 50 + int(50 * i / len(clips))
        job.status, job.progress = Status.done, 100
    except (GroqAuthError, GroqQuotaError) as e:
        _fail(job, str(e))
    except SelectionError as e:
        _fail(job, str(e))
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (401, 403):
            _fail(job, "Groq rejected the API key. Set GROQ_API_KEY on the server.")
        else:
            log.warning("groq error for %s: %s", job.id, e)
            _fail(job, f"Groq answered {e.response.status_code}, try again")
    except FfmpegError as e:
        log.warning("ffmpeg failed for %s: %s", job.id, e)
        _fail(job, "Could not read this video")
    except Exception:
        log.exception("job %s crashed", job.id)
        _fail(job, "Something went wrong, try again")


def _fail(job: Job, message: str) -> None:
    job.status, job.message = Status.failed, message
