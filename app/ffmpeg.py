import asyncio


class FfmpegError(Exception):
    pass


async def run(args: list[str]) -> None:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise FfmpegError(err.decode(errors="replace")[-2000:])


async def duration(path) -> float:
    """Length of a media file in seconds, via ffprobe."""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "csv=p=0",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    try:
        return float(out.decode().strip())
    except ValueError as e:
        raise FfmpegError(err.decode(errors="replace")[-500:] or "no duration") from e
