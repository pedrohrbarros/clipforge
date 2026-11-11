import json

from pydantic import BaseModel, Field, ValidationError, model_validator

from app.groq_client import GroqRequestError
from app.transcribe import Segment

MAX_CLIPS = 10
HARD_MIN_LEN = 5.0
HARD_MAX_LEN = 60.0
MAX_TRANSCRIPT_CHARS = 20_000


class Group(BaseModel):
    """`count` shorts between `min_seconds` and `max_seconds` long."""

    count: int = Field(ge=1, le=MAX_CLIPS)
    min_seconds: float = Field(ge=HARD_MIN_LEN, le=HARD_MAX_LEN)
    max_seconds: float = Field(ge=HARD_MIN_LEN, le=HARD_MAX_LEN)

    @model_validator(mode="after")
    def _ordered(self):
        if self.min_seconds >= self.max_seconds:
            raise ValueError("min_seconds must be below max_seconds")
        return self


class Plan(BaseModel):
    groups: list[Group] = Field(min_length=1)

    @model_validator(mode="after")
    def _total(self):
        if sum(g.count for g in self.groups) > MAX_CLIPS:
            raise ValueError(f"at most {MAX_CLIPS} shorts in total")
        return self

    @property
    def total(self) -> int:
        return sum(g.count for g in self.groups)

    @classmethod
    def default(cls) -> "Plan":
        return cls(groups=[Group(count=1, min_seconds=20, max_seconds=60)])

    @classmethod
    def parse(cls, raw: str) -> "Plan":
        """From the `plan` form field: a JSON list of {count, min_seconds, max_seconds}."""
        data = json.loads(raw)
        return cls(groups=data if isinstance(data, list) else data.get("groups", []))


LANGUAGES = {"en": "English", "pt": "Portuguese", "es": "Spanish"}

PROMPT = """You pick the best moments from a video transcript to post as short vertical clips.
The user wants these groups of clips, {total} at most in total:
{groups}
Rules:
- Fill every group as far as the material genuinely allows, never more clips than a group
  asks for, and tag each clip with its group number.
- Clips must not overlap and must start and end at natural sentence boundaries.
- Prefer moments that are self-contained, surprising, funny, emotional or useful.
- Give each clip a punchy title under 60 characters, written in {language}.
Respond with JSON only, matching exactly:
{{"clips": [{{"group": <1-based int>, "start": <seconds float>, "end": <seconds float>,
"title": "<string>"}}]}}"""

TITLE_PROMPT = """Give each numbered transcript excerpt a punchy title under 60 characters,
written in {language}. Respond with JSON only:
{{"titles": ["<title for excerpt 1>", "<title for excerpt 2>", ...]}}"""


class Clip(BaseModel):
    start: float
    end: float
    title: str = ""
    group: int = 1


class SelectionError(Exception):
    def __init__(self, message: str, code: str = "select_failed"):
        self.code = code
        super().__init__(message)


def build_prompt(plan: Plan, lang: str = "en") -> str:
    lines = [
        f"- group {i}: {g.count} clip{'s' if g.count > 1 else ''}, each "
        f"{int(g.min_seconds)} to {int(g.max_seconds)} seconds long"
        for i, g in enumerate(plan.groups, start=1)
    ]
    return PROMPT.format(
        total=plan.total, groups="\n".join(lines), language=LANGUAGES.get(lang, "English")
    )


def format_transcript(segments: list[Segment]) -> str:
    lines = [f"[{s.start:.1f}-{s.end:.1f}] {s.text}" for s in segments]
    return "\n".join(lines)[:MAX_TRANSCRIPT_CHARS]


def _snap(t: float, bounds: list[float]) -> float:
    return min(bounds, key=lambda b: abs(b - t)) if bounds else t


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


def _fits(group: Group, length: float) -> bool:
    return group.min_seconds <= length <= group.max_seconds


def validate_clips(raw: dict, segments: list[Segment], plan: Plan | None = None) -> list[Clip]:
    """Snap to sentence bounds, enforce each group's length range and count, drop overlaps."""
    plan = plan or Plan.default()
    bounds = sorted({s.start for s in segments} | {s.end for s in segments})
    out: list[Clip] = []
    left = [g.count for g in plan.groups]
    for c in sorted(_parse_items(raw), key=lambda c: c.start):
        start, end = _snap(c.start, bounds), _snap(c.end, bounds)
        candidates = [(start, end), (c.start, c.end)]
        # The model's own group first, else any group whose range fits and still has room.
        order = [c.group - 1] + [i for i in range(len(plan.groups)) if i != c.group - 1]
        placed = None
        for gi in order:
            if not 0 <= gi < len(plan.groups) or left[gi] <= 0:
                continue
            for s0, e0 in candidates:
                if _fits(plan.groups[gi], e0 - s0):
                    placed = (gi, s0, e0)
                    break
            if placed:
                break
        if placed is None:
            continue
        gi, start, end = placed
        if out and start < out[-1].end:
            continue
        left[gi] -= 1
        title = c.title.strip()[:80] or f"Clip {len(out) + 1}"
        out.append(Clip(start=round(start, 3), end=round(end, 3), title=title, group=gi + 1))
        if len(out) == plan.total:
            break
    return out


async def select_clips(
    segments: list[Segment], groq, plan: Plan | None = None, lang: str = "en"
) -> list[Clip]:
    """Ask for the plan; after the retries, settle for the best attempt."""
    plan = plan or Plan.default()
    transcript = format_transcript(segments)
    prompt = build_prompt(plan, lang)
    best: list[Clip] = []
    for _ in range(2):
        try:
            clips = validate_clips(await groq.chat_json(prompt, transcript), segments, plan)
        except (ValidationError, ValueError, KeyError):
            clips = []
        except GroqRequestError as e:
            # Groq returns 400 json_validate_failed when the model's JSON is malformed.
            if e.code != "json_validate_failed":
                raise
            clips = []
        if len(clips) >= plan.total:
            return clips
        if len(clips) > len(best):
            best = clips
    if best:
        return best
    raise SelectionError("Could not pick moments, try again")


def split_clips(segments: list[Segment], plan: Plan | None = None) -> list[Clip]:
    """Cut the video into consecutive parts of the planned lengths, at sentence boundaries.

    Each part grows by whole sentences until it reaches its group's minimum, stops before it
    would pass the maximum, and falls back to a hard cut at the maximum when one sentence is
    longer than the whole range. Parts are produced in plan order until the video runs out.
    """
    plan = plan or Plan.default()
    wanted = [g for g in plan.groups for _ in range(g.count)]
    clips: list[Clip] = []
    i = 0
    for group in wanted:
        if i >= len(segments):
            break
        start = segments[i].start
        end = start
        while i < len(segments):
            seg_end = segments[i].end
            if seg_end - start > group.max_seconds:
                if end - start < group.min_seconds:
                    end = start + group.max_seconds  # hard cut inside a long sentence
                    # Skip the sentences swallowed by the hard cut.
                    while i < len(segments) and segments[i].end <= end:
                        i += 1
                break
            end = seg_end
            i += 1
            if end - start >= group.min_seconds:
                break
        if end - start < group.min_seconds and i >= len(segments):
            if end - start < HARD_MIN_LEN:
                break  # too little left for a usable part
        if end - start < HARD_MIN_LEN:
            continue
        clips.append(Clip(start=round(start, 3), end=round(end, 3), title="", group=1))
    return clips


async def title_clips(
    clips: list[Clip], segments: list[Segment], groq, lang: str = "en"
) -> list[Clip]:
    """Name split parts with the model; fall back to 'Part n' if it fails."""
    excerpts = []
    for n, c in enumerate(clips, start=1):
        text = " ".join(s.text for s in segments if s.start >= c.start and s.end <= c.end)
        excerpts.append(f"{n}. {text[:600]}")
    titles: list[str] = []
    try:
        prompt = TITLE_PROMPT.format(language=LANGUAGES.get(lang, "English"))
        raw = await groq.chat_json(prompt, "\n".join(excerpts))
        titles = [str(t) for t in raw.get("titles", [])]
    except Exception:  # noqa: BLE001 - titles are decoration; never fail the job for them
        titles = []
    return [
        c.model_copy(
            update={"title": (titles[n] if n < len(titles) else "")[:80] or f"Part {n + 1}"}
        )
        for n, c in enumerate(clips)
    ]
