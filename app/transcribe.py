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
    probability: float = 1.0  # Whisper word-level confidence (0-1); high = clear emphasis


@dataclass
class Segment:
    text: str
    start: float
    end: float
    avg_logprob: float = 0.0          # Segment avg logprob; less negative = unusual/emphatic delivery
    no_speech_prob: float = 0.0       # Probability of non-speech; low = definitely speech
    compression_ratio: float = 1.0    # Repetitiveness; high = filler/rambling (e.g., "um, like, you know")


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
            words.append(Word(
                text=w["word"].strip(),
                start=offset + w["start"],
                end=offset + w["end"],
                probability=w.get("probability", 1.0),
            ))
        for s in data.get("segments") or []:
            text = s["text"].strip()
            if text:
                segments.append(Segment(
                    text=text,
                    start=offset + s["start"],
                    end=offset + s["end"],
                    avg_logprob=s.get("avg_logprob", 0.0),
                    no_speech_prob=s.get("no_speech_prob", 0.0),
                    compression_ratio=s.get("compression_ratio", 1.0),
                ))
    return words, segments


# A sentence longer than this is also split where the speaker pauses at a comma.
MAX_LINE_SECONDS = 8.0


def sentence_lines(words: list[Word]) -> list[Segment]:
    """Regroup words into sentences, and long sentences into clauses, so every line ends where
    the speech does. Whisper's own segments often stop in the middle of a sentence."""
    lines: list[Segment] = []
    current: list[Word] = []
    for w in words:
        current.append(w)
        text = w.text.rstrip("\"')»”")
        long_enough = w.end - current[0].start >= MAX_LINE_SECONDS
        if text.endswith((".", "?", "!", "…")) or (long_enough and text.endswith((",", ";", ":"))):
            lines.append(Segment(" ".join(x.text for x in current), current[0].start, w.end))
            current = []
    if current:
        lines.append(Segment(" ".join(x.text for x in current), current[0].start, current[-1].end))
    return lines


async def transcribe_video(
    video: Path, workdir: Path, groq: GroqClient, language: str | None = None
) -> tuple[list[Word], list[Segment]]:
    audio = workdir / "audio.mp3"
    await ffmpeg.run(extract_audio_args(video, audio))
    chunks = [audio]
    if audio.stat().st_size > MAX_CHUNK_BYTES:
        await ffmpeg.run(chunk_args(audio, workdir / "chunk-%03d.mp3"))
        chunks = sorted(workdir.glob("chunk-*.mp3"))
    parts = []
    for index, chunk in enumerate(chunks):
        parts.append((index * CHUNK_SECONDS, await groq.transcribe(chunk, language)))
    return merge_transcripts(parts)
