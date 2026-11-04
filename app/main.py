import asyncio
import contextlib
import wave
import zipfile
from pathlib import Path
from typing import Annotated

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.auth import require_key
from app.config import settings
from app.groq_client import GroqClient
from app.jobs import Job, JobStore, Status, run_job

store = JobStore(Path(settings.work_dir), settings.job_ttl_seconds)
groq = GroqClient(
    settings.groq_api_key,
    settings.chat_model,
    settings.whisper_model,
    reasoning_effort=settings.chat_reasoning_effort,
    max_tokens=settings.chat_max_tokens,
)
CHUNK = 1024 * 1024


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    store.root.mkdir(parents=True, exist_ok=True)
    task = asyncio.create_task(store.sweeper())
    yield
    task.cancel()
    await groq.aclose()


app = FastAPI(title="ClipForge API", lifespan=lifespan)
app.middleware("http")(require_key)
# Added last so it wraps the auth check: preflight is answered here and 401s get CORS headers.
app.add_middleware(
    CORSMiddleware, allow_origins=settings.origins, allow_methods=["*"], allow_headers=["*"]
)


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


@app.post("/jobs", status_code=202)
async def create_job(
    background: BackgroundTasks,
    video: Annotated[UploadFile, File()],
    captions: Annotated[bool, Form()] = True,
) -> dict:
    """Start a shorts job from an uploaded video."""
    job = store.create(captions)
    limit = settings.max_upload_mb * 1024 * 1024
    written = 0
    with job.video_path.open("wb") as out:
        while chunk := await video.read(CHUNK):
            written += len(chunk)
            if written > limit:
                store.delete(job.id)
                raise HTTPException(
                    413, _err("too_big", f"Video larger than {settings.max_upload_mb} MB")
                )
            out.write(chunk)
    background.add_task(run_job, job, groq)
    return {"id": job.id}


def _err(code: str, message: str) -> dict:
    """Error detail with a stable code the UI translates, plus an English fallback."""
    return {"code": code, "message": message}


def _job(job_id: str) -> Job:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(404, _err("expired", "This result expired, generate again"))
    return job


@app.get("/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    return _job(job_id).public()


@app.get("/jobs/{job_id}/shorts/{index}")
async def get_short(job_id: str, index: int, download: bool = False) -> FileResponse:
    job = _job(job_id)
    short = next((s for s in job.shorts if s.index == index), None)
    if short is None:
        raise HTTPException(404, "No such short")
    slug = "".join(c if c.isalnum() else "-" for c in short.title.lower()).strip("-") or "short"
    return FileResponse(
        short.path,
        media_type="video/mp4",
        filename=f"{slug}-{index}.mp4",
        content_disposition_type="attachment" if download else "inline",
    )


@app.get("/jobs/{job_id}/zip")
async def get_zip(job_id: str) -> FileResponse:
    job = _job(job_id)
    if job.status != Status.done:
        raise HTTPException(409, "Job not finished")
    zip_path = job.dir / "shorts.zip"
    if not zip_path.exists():
        with zipfile.ZipFile(zip_path, "w") as zf:
            for s in job.shorts:
                zf.write(s.path, f"short-{s.index}.mp4")
    return FileResponse(zip_path, media_type="application/zip", filename="clipforge-shorts.zip")


def _silence(path: Path) -> Path:
    if not path.exists():
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\x00\x00" * 8000)
    return path


@app.get("/usage")
async def usage() -> dict:
    """Remaining Groq quota per model, from the last rate-limit headers seen.

    On first call, cheap probes (a 1-token chat, a half-second silent transcription)
    populate the headers so the UI is never empty.
    """
    if "chat" not in groq.usage:
        with contextlib.suppress(Exception):
            await groq.chat_ping()
    if "whisper" not in groq.usage:
        with contextlib.suppress(Exception):
            await groq.transcribe(_silence(store.root / "silence.wav"))
    return {"chat": groq.usage.get("chat"), "whisper": groq.usage.get("whisper")}
