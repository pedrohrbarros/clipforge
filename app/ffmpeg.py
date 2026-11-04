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
