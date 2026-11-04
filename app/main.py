import asyncio
import contextlib
import datetime as dt
import logging
import wave
import zipfile
from pathlib import Path
from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app.auth import CurrentUser, bearer_scheme
from app.config import settings
from app.db import Db, today_start
from app.groq_client import GroqClient
from app.jobs import Job, JobStore, Services, Status, public_from_row, run_job
from app.mailer import Mailer
from app.storage import Storage

log = logging.getLogger("clipforge")

store = JobStore(Path(settings.work_dir), settings.job_ttl_seconds)
groq = GroqClient(
    settings.groq_api_key,
    settings.chat_model,
    settings.whisper_model,
    reasoning_effort=settings.chat_reasoning_effort,
    max_tokens=settings.chat_max_tokens,
)
db = Db(settings.database_connection_url)
storage = Storage(settings.supabase_url, settings.supabase_service_key, settings.supabase_bucket)
mailer = Mailer(
    settings.gmail_user,
    settings.gmail_app_password,
    settings.public_app_url,
    settings.retention_hours,
)
services = Services(db, storage, mailer)
CHUNK = 1024 * 1024
LANGS = {"en", "pt", "es"}


async def storage_sweeper(interval: int = 3600) -> None:
    """Delete shorts older than the retention window from the bucket."""
    while True:
        if storage.enabled:
            try:
                old = await storage.list_old(settings.retention_hours)
                await storage.delete(old)
                if old:
                    log.info("deleted %d expired objects", len(old))
            except Exception:
                log.exception("storage sweep failed")
        await asyncio.sleep(interval)


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    store.root.mkdir(parents=True, exist_ok=True)
    await db.create_tables()
    await storage.ensure_bucket()
    tasks = [asyncio.create_task(store.sweeper()), asyncio.create_task(storage_sweeper())]
    yield
    for task in tasks:
        task.cancel()
    for client in (groq, db, storage, mailer):
        await client.aclose()


app = FastAPI(
    title="ClipForge API",
    description="Every endpoint except /health needs `Authorization: Bearer <Google ID token>` "
    "issued for this app's Google client. Click Authorize and paste a token to try requests.",
    lifespan=lifespan,
    dependencies=[Depends(bearer_scheme)],
)
app.add_middleware(
    CORSMiddleware, allow_origins=settings.origins, allow_methods=["*"], allow_headers=["*"]
)


def _err(code: str, message: str) -> dict:
    """Error detail with a stable code the UI translates, plus an English fallback."""
    return {"code": code, "message": message}


@app.exception_handler(Exception)
async def unhandled(request, exc: Exception) -> JSONResponse:
    """Crashes still answer JSON with CORS headers; the outer error middleware would not."""
    headers = {}
    origin = request.headers.get("origin")
    if origin and (origin in settings.origins or "*" in settings.origins):
        headers["Access-Control-Allow-Origin"] = origin
        headers["Vary"] = "Origin"
    return JSONResponse(
        {"detail": _err("error", "Something went wrong, try again")},
        status_code=500,
        headers=headers,
    )


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


def _resets_at() -> str:
    return (today_start() + dt.timedelta(days=1)).isoformat()


@app.get("/me")
async def me(user: CurrentUser) -> dict:
    """Who is signed in and how many videos they have left today."""
    await db.upsert_user(user)
    used = await db.jobs_today(user.id)
    return {
        "email": user.email,
        "name": user.name,
        "used_today": used,
        "limit": settings.daily_limit,
        "resets_at": _resets_at(),
    }


@app.post("/jobs", status_code=202)
async def create_job(
    user: CurrentUser,
    background: BackgroundTasks,
    video: Annotated[UploadFile, File()],
    captions: Annotated[bool, Form()] = True,
    lang: Annotated[str, Form()] = "en",
) -> dict:
    """Start a shorts job from an uploaded video, within the user's daily limit."""
    await db.upsert_user(user)
    used = await db.jobs_today(user.id)
    if used >= settings.daily_limit:
        raise HTTPException(
            429,
            _err(
                "daily_limit",
                f"You have used your {settings.daily_limit} videos for today; "
                "resets at midnight UTC.",
            ),
        )
    job = store.create(captions, user, lang if lang in LANGS else "en")
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
    try:
        await db.insert_job(job.row())
    except Exception:
        log.exception("could not insert job %s", job.id)
    background.add_task(run_job, job, groq, services)
    return {"id": job.id}


def _own_job(job_id: str, user: CurrentUser) -> Job:
    job = store.get(job_id)
    if job is None or job.user.id != user.id:
        raise HTTPException(404, _err("expired", "This result expired, generate again"))
    return job


@app.get("/jobs/{job_id}")
async def get_job(job_id: str, user: CurrentUser) -> dict:
    job = store.get(job_id)
    if job is not None:
        if job.user.id != user.id:
            raise HTTPException(404, _err("expired", "This result expired, generate again"))
        return job.public()
    row = await db.get_job(job_id)
    if row is None or row.get("user_id") != user.id:
        raise HTTPException(404, _err("expired", "This result expired, generate again"))
    if row["status"] not in (Status.done, Status.failed):
        # The server restarted mid-job; the work is gone.
        return public_from_row(
            {**row, "status": "failed", "code": "error", "message": "The server restarted"}
        )
    return public_from_row(row)


@app.get("/jobs/{job_id}/shorts/{index}")
async def get_short(job_id: str, index: int, user: CurrentUser, download: bool = False):
    """Local fallback when no storage bucket is configured."""
    job = _own_job(job_id, user)
    short = next((s for s in job.shorts if s.index == index and s.path), None)
    if short is None or short.path is None or not short.path.exists():
        raise HTTPException(404, _err("expired", "No such short"))
    slug = "".join(c if c.isalnum() else "-" for c in short.title.lower()).strip("-") or "short"
    return FileResponse(
        short.path,
        media_type="video/mp4",
        filename=f"{slug}-{index}.mp4",
        content_disposition_type="attachment" if download else "inline",
    )


@app.get("/jobs/{job_id}/zip")
async def get_zip(job_id: str, user: CurrentUser) -> FileResponse:
    job = _own_job(job_id, user)
    if job.status != Status.done:
        raise HTTPException(409, _err("error", "Job not finished"))
    zip_path = job.dir / "shorts.zip"
    if not zip_path.exists():
        with zipfile.ZipFile(zip_path, "w") as zf:
            for s in job.shorts:
                if s.path and s.path.exists():
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
async def usage(_: CurrentUser) -> dict:
    """Remaining Groq quota per model, from the last rate-limit headers seen."""
    if "chat" not in groq.usage:
        with contextlib.suppress(Exception):
            await groq.chat_ping()
    if "whisper" not in groq.usage:
        with contextlib.suppress(Exception):
            await groq.transcribe(_silence(store.root / "silence.wav"))
    return {"chat": groq.usage.get("chat"), "whisper": groq.usage.get("whisper")}
