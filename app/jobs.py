import asyncio
import logging
import shutil
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from app import ffmpeg
from app.config import settings
from app.ffmpeg import FfmpegError
from app.groq_client import GroqAuthError, GroqClient, GroqQuotaError, GroqRequestError
from app.render import build_ass, render_args
from app.selector import SelectionError, select_clips
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
    code: str = ""
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
            "code": self.code,
            "shorts": [s.public() for s in self.shorts],
        }


class JobStore:
    def __init__(self, root: Path, ttl_seconds: int):
        self.root = root
        self.ttl = ttl_seconds
        self._jobs: dict[str, Job] = {}

    def create(self, captions: bool) -> Job:
        job_id = uuid.uuid4().hex
        job = Job(id=job_id, dir=self.root / job_id, captions=captions)
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


async def run_job(job: Job, groq: GroqClient) -> None:
    try:
        video = job.video_path
        check_duration(await ffmpeg.duration(video))
        job.status, job.progress = Status.transcribing, 10
        words, segments = await transcribe_video(video, job.dir, groq)
        if not segments:
            raise SelectionError("No speech found in this video", "no_speech")
        job.status, job.progress = Status.selecting, 40
        clips = await select_clips(segments, groq)
        job.status, job.progress = Status.rendering, 50
        for i, clip in enumerate(clips, start=1):
            out = job.dir / f"short-{i}.mp4"
            ass = None
            if job.captions:
                ass = job.dir / f"short-{i}.ass"
                ass.write_text(build_ass(words, clip.start, clip.end), encoding="utf-8")
            await ffmpeg.run(render_args(video, out, clip, ass))
            job.shorts.append(Short(i, clip.title, clip.start, clip.end, out))
            job.progress = 50 + int(50 * i / len(clips))
        job.status, job.progress = Status.done, 100
    except GroqAuthError as e:
        _fail(job, str(e), "auth")
    except GroqQuotaError as e:
        _fail(job, str(e), "quota")
    except (SelectionError, VideoTooLong) as e:
        _fail(job, str(e), e.code)
    except GroqRequestError as e:
        log.warning("groq %s for %s: %s", e.status, job.id, e)
        if e.status in (401, 403):
            _fail(job, "Groq rejected the API key. Set GROQ_API_KEY on the server.", "auth")
        elif e.status == 413:
            _fail(
                job, "This video's transcript is too long for the free tier.", "transcript_too_long"
            )
        else:
            _fail(job, f"The AI service answered {e.status}: {e}", "ai_error")
    except FfmpegError as e:
        log.warning("ffmpeg failed for %s: %s", job.id, e)
        _fail(job, "Could not read this video", "unreadable")
    except Exception:
        log.exception("job %s crashed", job.id)
        _fail(job, "Something went wrong, try again", "error")


def _fail(job: Job, message: str, code: str) -> None:
    """Mark the job failed with a human message and a stable code the UI can translate."""
    job.status, job.message, job.code = Status.failed, message, code
