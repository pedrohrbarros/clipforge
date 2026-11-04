import asyncio
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp

YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com"}

# Up to 1080p H.264 mp4 with m4a audio, merged by ffmpeg; good enough for a 1080x1920 crop.
FORMAT = (
    "bestvideo[height<=1080][ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]"
    "/bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]"
    "/best[height<=1080][ext=mp4]/best[height<=1080]"
)


class YoutubeError(Exception):
    def __init__(self, message: str, code: str = "youtube_failed"):
        self.code = code
        super().__init__(message)


def is_youtube_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and parsed.hostname in YOUTUBE_HOSTS


# Path of the server-wide cookies file, set at startup by configure_cookies().
COOKIES_FILE: Path | None = None


def configure_cookies(contents: str, workdir: Path) -> Path | None:
    """Write the YOUTUBE_COOKIES export to disk and use it for every YouTube request."""
    global COOKIES_FILE
    contents = contents.strip()
    if not contents:
        COOKIES_FILE = None
        return None
    if "youtube.com" not in contents or "\t" not in contents:
        raise ValueError("YOUTUBE_COOKIES is not a Netscape cookies.txt export")
    workdir.mkdir(parents=True, exist_ok=True)
    path = workdir / "youtube-cookies.txt"
    path.write_text(contents + "\n", encoding="utf-8")
    COOKIES_FILE = path
    return path


def _friendly(error: Exception) -> YoutubeError:
    text = str(error)
    if "Sign in" in text or "bot" in text.lower():
        hint = "refresh YOUTUBE_COOKIES" if COOKIES_FILE else "set YOUTUBE_COOKIES"
        return YoutubeError(
            f"YouTube is blocking the server right now ({hint}). Try uploading the file instead.",
            "youtube_blocked",
        )
    if "Private video" in text or "unavailable" in text.lower():
        return YoutubeError("This video is private or unavailable.", "youtube_unavailable")
    return YoutubeError("Could not fetch this video from YouTube.", "youtube_failed")


def _base_opts() -> dict:
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True}
    if COOKIES_FILE is not None:
        opts["cookiefile"] = str(COOKIES_FILE)
    return opts


async def fetch_duration(url: str) -> float | None:
    """Length in seconds without downloading, or None when YouTube does not say."""
    opts = _base_opts()
    try:
        info = await asyncio.to_thread(
            lambda: yt_dlp.YoutubeDL(opts).extract_info(url, download=False)
        )
    except yt_dlp.utils.DownloadError as e:
        raise _friendly(e) from e
    return info.get("duration")


async def download_video(url: str, workdir: Path, on_progress) -> Path:
    """Download a YouTube video into workdir and return the file path."""

    def hook(d: dict) -> None:
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                on_progress(int(d.get("downloaded_bytes", 0) * 100 / total))

    opts = {
        **_base_opts(),
        "format": FORMAT,
        "merge_output_format": "mp4",
        "outtmpl": str(workdir / "source.%(ext)s"),
        "progress_hooks": [hook],
    }
    try:
        await asyncio.to_thread(lambda: yt_dlp.YoutubeDL(opts).extract_info(url, download=True))
    except yt_dlp.utils.DownloadError as e:
        raise _friendly(e) from e
    files = [p for p in workdir.glob("source.*") if not p.name.endswith(".part")]
    if not files:
        raise YoutubeError("Download finished but no file was produced.", "youtube_failed")
    return max(files, key=lambda p: p.stat().st_mtime)
