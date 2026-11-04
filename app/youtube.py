import asyncio
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp

log = logging.getLogger("clipforge")

HEIGHTS = (2160, 1440, 1080, 720, 480, 360)
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com"}
OPTION_RE = re.compile(r"v(\d{3,4})|audio")


@dataclass
class Option:
    id: str
    label: str


def is_youtube_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and parsed.hostname in YOUTUBE_HOSTS


def build_options(info: dict) -> list[Option]:
    heights = {
        f.get("height")
        for f in info.get("formats") or []
        if f.get("vcodec") not in (None, "none") and f.get("height")
    }
    options = [Option(f"v{h}", f"{h}p video (mp4)") for h in HEIGHTS if h in heights]
    options.append(Option("audio", "Audio only (m4a)"))
    return options


def format_for(option_id: str) -> str:
    if option_id == "audio":
        return "bestaudio[ext=m4a]/bestaudio"
    height = int(option_id[1:])
    video = f"bestvideo[height<={height}][ext=mp4]"
    return (
        f"{video}[vcodec^=avc1]+bestaudio[ext=m4a]"
        f"/{video}+bestaudio[ext=m4a]"
        f"/best[height<={height}][ext=mp4]/best[height<={height}]"
    )


def is_valid_option(option_id: str) -> bool:
    return OPTION_RE.fullmatch(option_id) is not None


class YoutubeError(Exception):
    pass


def _friendly(error: Exception) -> str:
    text = str(error)
    if "Sign in" in text or "bot" in text.lower():
        return "YouTube is blocking this server with a bot check. Try again later."
    if "Private video" in text or "unavailable" in text.lower():
        return "This video is private or unavailable."
    return "Could not fetch this video from YouTube."


def _ydl(extra: dict | None = None) -> yt_dlp.YoutubeDL:
    return yt_dlp.YoutubeDL(
        {"quiet": True, "no_warnings": True, "noplaylist": True, **(extra or {})}
    )


async def fetch_info(url: str) -> dict:
    try:
        info = await asyncio.to_thread(lambda: _ydl().extract_info(url, download=False))
    except yt_dlp.utils.DownloadError as e:
        raise YoutubeError(_friendly(e)) from e
    return {
        "title": info.get("title") or "Untitled",
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "options": [o.__dict__ for o in build_options(info)],
    }


@dataclass
class DownloadJob:
    url: str
    option: str
    dir: Path
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: float = field(default_factory=time.time)
    status: str = "queued"
    progress: int = 0
    message: str = ""
    title: str = ""
    file: Path | None = None

    def public(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "title": self.title,
            "filename": self.file.name if self.file else None,
        }


async def run_download(job: DownloadJob) -> None:
    def hook(d: dict) -> None:
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                job.progress = min(99, int(d.get("downloaded_bytes", 0) * 100 / total))

    opts = {
        "format": format_for(job.option),
        "outtmpl": str(job.dir / "%(title).80s.%(ext)s"),
        "progress_hooks": [hook],
    }
    if job.option != "audio":
        opts["merge_output_format"] = "mp4"
    job.status = "downloading"
    try:
        info = await asyncio.to_thread(lambda: _ydl(opts).extract_info(job.url, download=True))
        job.title = info.get("title") or "video"
        files = [p for p in job.dir.iterdir() if p.is_file() and not p.name.endswith(".part")]
        if not files:
            raise YoutubeError("Download finished but no file was produced.")
        job.file = max(files, key=lambda p: p.stat().st_mtime)
        job.status, job.progress = "done", 100
    except yt_dlp.utils.DownloadError as e:
        job.status, job.message = "failed", _friendly(e)
    except YoutubeError as e:
        job.status, job.message = "failed", str(e)
    except Exception:
        log.exception("download %s crashed", job.id)
        job.status, job.message = "failed", "Something went wrong, try again"
