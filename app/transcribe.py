from dataclasses import dataclass
from pathlib import Path

from app import ffmpeg
from app.groq_client import GroqClient

MAX_CHUNK_BYTES = 24 * 1024 * 1024
CHUNK_SECONDS = 600


@dataclass
class Word:
    text: str
    start: float
    end: float


@dataclass
class Segment:
    text: str
    start: float
    end: float


def extract_audio_args(video: Path, audio: Path) -> list[str]:
    return [
        "ffmpeg", "-y", "-i", str(video),
        "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k",
        str(audio),
    ]  # fmt: skip


def chunk_args(audio: Path, pattern: Path) -> list[str]:
    return [
        "ffmpeg", "-y", "-i", str(audio),
        "-f", "segment", "-segment_time", str(CHUNK_SECONDS), "-c", "copy",
        str(pattern),
    ]  # fmt: skip


def merge_transcripts(parts: list[tuple[float, dict]]) -> tuple[list[Word], list[Segment]]:
    words: list[Word] = []
    segments: list[Segment] = []
    for offset, data in parts:
        for w in data.get("words") or []:
            words.append(Word(w["word"].strip(), offset + w["start"], offset + w["end"]))
        for s in data.get("segments") or []:
            text = s["text"].strip()
            if text:
                segments.append(Segment(text, offset + s["start"], offset + s["end"]))
    return words, segments


async def transcribe_video(
    video: Path, workdir: Path, groq: GroqClient
) -> tuple[list[Word], list[Segment]]:
    audio = workdir / "audio.mp3"
    await ffmpeg.run(extract_audio_args(video, audio))
    chunks = [audio]
    if audio.stat().st_size > MAX_CHUNK_BYTES:
        await ffmpeg.run(chunk_args(audio, workdir / "chunk-%03d.mp3"))
        chunks = sorted(workdir.glob("chunk-*.mp3"))
    parts = []
    for index, chunk in enumerate(chunks):
        parts.append((index * CHUNK_SECONDS, await groq.transcribe(chunk)))
    return merge_transcripts(parts)
