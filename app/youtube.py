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
    pass


def is_youtube_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and parsed.hostname in YOUTUBE_HOSTS


def _friendly(error: Exception) -> str:
    text = str(error)
    if "Sign in" in text or "bot" in text.lower():
        return "YouTube is blocking this server with a bot check. Try uploading the file instead."
    if "Private video" in text or "unavailable" in text.lower():
        return "This video is private or unavailable."
    return "Could not fetch this video from YouTube."


async def download_video(url: str, workdir: Path, on_progress) -> Path:
    """Download a YouTube video into workdir and return the file path."""

    def hook(d: dict) -> None:
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                on_progress(int(d.get("downloaded_bytes", 0) * 100 / total))

    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "format": FORMAT,
        "merge_output_format": "mp4",
        "outtmpl": str(workdir / "source.%(ext)s"),
        "progress_hooks": [hook],
    }
    try:
        await asyncio.to_thread(lambda: yt_dlp.YoutubeDL(opts).extract_info(url, download=True))
    except yt_dlp.utils.DownloadError as e:
        raise YoutubeError(_friendly(e)) from e
    files = [p for p in workdir.glob("source.*") if not p.name.endswith(".part")]
    if not files:
        raise YoutubeError("Download finished but no file was produced.")
    return max(files, key=lambda p: p.stat().st_mtime)
