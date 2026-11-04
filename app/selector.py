from pydantic import BaseModel, ValidationError

from app.groq_client import GroqRequestError
from app.transcribe import Segment

MAX_CLIPS = 5
MIN_LEN = 20.0
MAX_LEN = 60.0
MAX_TRANSCRIPT_CHARS = 20_000

SYSTEM_PROMPT = f"""You pick the best moments from a video transcript to post as short
vertical clips.
Rules:
- Return between 1 and {MAX_CLIPS} clips, each {int(MIN_LEN)} to {int(MAX_LEN)} seconds long.
- Clips must not overlap and must start and end at natural sentence boundaries.
- Prefer moments that are self-contained, surprising, funny, emotional or useful.
- Give each clip a punchy title under 60 characters.
Respond with JSON only, matching exactly:
{{"clips": [{{"start": <seconds float>, "end": <seconds float>, "title": "<string>"}}]}}"""


class Clip(BaseModel):
    start: float
    end: float
    title: str = ""


class SelectionError(Exception):
    pass


def format_transcript(segments: list[Segment]) -> str:
    lines = [f"[{s.start:.1f}-{s.end:.1f}] {s.text}" for s in segments]
    return "\n".join(lines)[:MAX_TRANSCRIPT_CHARS]


def _snap(t: float, bounds: list[float]) -> float:
    return min(bounds, key=lambda b: abs(b - t)) if bounds else t


def _in_range(start: float, end: float) -> bool:
    return MIN_LEN <= end - start <= MAX_LEN


def _parse_items(raw: dict) -> list[Clip]:
    """Keep the well-formed clips and ignore any malformed entries the model slipped in."""
    items = raw.get("clips") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        raise ValueError("no clips list")
    parsed: list[Clip] = []
    for item in items:
        try:
            parsed.append(Clip.model_validate(item))
        except ValidationError:
            continue
    return parsed


def validate_clips(raw: dict, segments: list[Segment]) -> list[Clip]:
    bounds = sorted({s.start for s in segments} | {s.end for s in segments})
    out: list[Clip] = []
    for c in sorted(_parse_items(raw), key=lambda c: c.start):
        start, end = _snap(c.start, bounds), _snap(c.end, bounds)
        if not _in_range(start, end):
            start, end = c.start, c.end
            if not _in_range(start, end):
                continue
        if out and start < out[-1].end:
            continue
        title = c.title.strip()[:80] or f"Clip {len(out) + 1}"
        out.append(Clip(start=round(start, 3), end=round(end, 3), title=title))
        if len(out) == MAX_CLIPS:
            break
    return out


async def select_clips(segments: list[Segment], groq) -> list[Clip]:
    transcript = format_transcript(segments)
    for _ in range(2):
        try:
            clips = validate_clips(await groq.chat_json(SYSTEM_PROMPT, transcript), segments)
        except (ValidationError, ValueError, KeyError):
            clips = []
        except GroqRequestError as e:
            # Groq returns 400 json_validate_failed when the model's JSON is malformed.
            if e.code != "json_validate_failed":
                raise
            clips = []
        if clips:
            return clips
    raise SelectionError("Could not pick moments, try again")
