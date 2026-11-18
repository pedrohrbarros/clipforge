import json

from pydantic import BaseModel, Field, ValidationError, model_validator

from app.groq_client import GroqRequestError
from app.transcribe import Segment

MAX_CLIPS = 10
HARD_MIN_LEN = 5.0
HARD_MAX_LEN = 60.0
MAX_TRANSCRIPT_CHARS = 20_000


class Group(BaseModel):
    """Between `min_count` and `max_count` shorts, each `min_seconds` to `max_seconds` long."""

    min_count: int = Field(default=1, ge=1, le=MAX_CLIPS)
    max_count: int = Field(ge=1, le=MAX_CLIPS)
    min_seconds: float = Field(ge=HARD_MIN_LEN, le=HARD_MAX_LEN)
    max_seconds: float = Field(ge=HARD_MIN_LEN, le=HARD_MAX_LEN)

    @model_validator(mode="before")
    @classmethod
    def _legacy_count(cls, data):
        # Clients from before min/max send a single `count`, meaning exactly that many.
        if isinstance(data, dict) and "count" in data and "max_count" not in data:
            return {**data, "min_count": data["count"], "max_count": data["count"]}
        return data

    @model_validator(mode="after")
    def _ordered(self):
        if self.min_seconds >= self.max_seconds:
            raise ValueError("min_seconds must be below max_seconds")
        if self.min_count > self.max_count:
            raise ValueError("min_count must not exceed max_count")
        return self


class Plan(BaseModel):
    groups: list[Group] = Field(min_length=1)

    @model_validator(mode="after")
    def _total(self):
        if sum(g.max_count for g in self.groups) > MAX_CLIPS:
            raise ValueError(f"at most {MAX_CLIPS} shorts in total")
        return self

    @property
    def total(self) -> int:
        return sum(g.max_count for g in self.groups)

    @classmethod
    def default(cls) -> "Plan":
        return cls(groups=[Group(min_count=1, max_count=3, min_seconds=20, max_seconds=60)])

    @classmethod
    def parse(cls, raw: str) -> "Plan":
        """From the `plan` form field: a JSON list of groups (see Group)."""
        data = json.loads(raw)
        return cls(groups=data if isinstance(data, list) else data.get("groups", []))


LANGUAGES = {"en": "English", "pt": "Portuguese", "es": "Spanish"}

# Below this review score a short is only kept to reach a group's minimum.
QUALITY_BAR = 6
MAX_CANDIDATES = 20
JUDGE_EXCERPT_CHARS = 400

SCOUT_PROMPT = """You are a short-form video editor. The user message is a video transcript, one
numbered line per spoken segment: `<n>. [<start>-<end> s] <text>`.
List the strongest candidate moments to cut into standalone short vertical videos.
The user wants:
{groups}
Propose up to {limit} candidates, more than needed, so the best can be chosen afterwards.
A candidate is a contiguous run of lines, from line `first` to line `last`, both included.
A strong short:
- opens with a hook that grabs attention and makes sense with no earlier context;
- builds to a payoff: a punchline, an insight, an answer, a surprising fact or an emotional beat;
- ends on a complete thought, never in the middle of a sentence or a story.
Skip greetings, intros, sponsor reads, housekeeping and stretches that lean on what came before.
Match the requested lengths; the line timestamps tell you how long each run lasts.
Candidates must not overlap. Score each one from 1 to 10 as a standalone short, strictly.
Give each a punchy title under 60 characters, written in {language}.
Respond with JSON only:
{{"candidates": [{{"first": <int>, "last": <int>, "score": <int>, "title": "<string>"}}]}}"""

JUDGE_PROMPT = """You review candidate clips for short vertical videos. The user message lists them
as `<id>. (<seconds> s) <transcript>`. For each one ask: would someone scrolling past stop in the
first seconds, does it pay off, does it end cleanly, and does it make sense on its own?
Compare the candidates with one another and score each from 1 to 10; be critical, a 7 is good.
Respond with JSON only: {"scores": [{"id": <int>, "score": <int>}]}"""

TITLE_PROMPT = """Give each numbered transcript excerpt a punchy title under 60 characters,
written in {language}. Respond with JSON only:
{{"titles": ["<title for excerpt 1>", "<title for excerpt 2>", ...]}}"""


class Clip(BaseModel):
    start: float
    end: float
    title: str = ""
    group: int = 1


class Candidate(BaseModel):
    first: int
    last: int
    score: float = 0
    title: str = ""


class SelectionError(Exception):
    def __init__(self, message: str, code: str = "select_failed"):
        self.code = code
        super().__init__(message)


def scout_prompt(plan: Plan, lang: str = "en") -> str:
    lines = []
    for g in plan.groups:
        count = (
            f"exactly {g.max_count}"
            if g.min_count == g.max_count
            else f"{g.min_count} to {g.max_count}"
        )
        lines.append(f"- {count} clips of {int(g.min_seconds)} to {int(g.max_seconds)} seconds")
    return SCOUT_PROMPT.format(
        groups="\n".join(lines),
        limit=min(MAX_CANDIDATES, max(6, plan.total * 2)),
        language=LANGUAGES.get(lang, "English"),
    )


def format_transcript(segments: list[Segment]) -> str:
    lines: list[str] = []
    size = 0
    for i, s in enumerate(segments):
        line = f"{i}. [{s.start:.1f}-{s.end:.1f} s] {s.text}"
        size += len(line) + 1
        if size > MAX_TRANSCRIPT_CHARS:
            break
        lines.append(line)
    return "\n".join(lines)


def parse_candidates(raw: dict, segments: list[Segment]) -> list[Candidate]:
    """Keep well-formed candidates that point at real lines; drop duplicates."""
    items = raw.get("candidates") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        raise ValueError("no candidates list")
    out: list[Candidate] = []
    seen: set[tuple[int, int]] = set()
    for item in items:
        try:
            c = Candidate.model_validate(item)
        except ValidationError:
            continue
        if 0 <= c.first <= c.last < len(segments) and (c.first, c.last) not in seen:
            seen.add((c.first, c.last))
            out.append(c)
    return out


def fit_span(c: Candidate, group: Group, segments: list[Segment]) -> tuple[float, float] | None:
    """Trim a long run by whole lines into the group's length, hard-cutting one overlong line.

    A run that is too short does not fit: padding it with the next lines would tack unrelated
    speech onto a finished thought.
    """
    first, last = c.first, c.last
    start = segments[first].start
    while last > first and segments[last].end - start > group.max_seconds:
        last -= 1
    end = min(segments[last].end, start + group.max_seconds)
    if end - start < group.min_seconds:
        return None
    return round(start, 3), round(end, 3)


async def judge(candidates: list[Candidate], segments: list[Segment], groq) -> list[float]:
    """The model re-scores the candidates side by side; if that fails the scout scores stand."""
    lines = []
    for i, c in enumerate(candidates):
        text = " ".join(s.text for s in segments[c.first : c.last + 1])[:JUDGE_EXCERPT_CHARS]
        seconds = segments[c.last].end - segments[c.first].start
        lines.append(f"{i}. ({seconds:.0f} s) {text}")
    try:
        raw = await groq.chat_json(JUDGE_PROMPT, "\n".join(lines))
        reviewed = {int(s["id"]): float(s["score"]) for s in raw.get("scores", [])}
    except Exception:  # noqa: BLE001 - the review refines the scout's scores, never fails the job
        reviewed = {}
    return [reviewed.get(i, c.score) for i, c in enumerate(candidates)]


def place(
    candidates: list[Candidate], scores: list[float], segments: list[Segment], plan: Plan
) -> list[Clip]:
    """Best first: fill each group's minimum, then add more only while they clear the bar."""
    taken = [0] * len(plan.groups)
    spans: list[tuple[float, float, int, str]] = []
    for score, c in sorted(zip(scores, candidates, strict=True), key=lambda p: -p[0]):
        order = sorted(range(len(plan.groups)), key=lambda i: taken[i] >= plan.groups[i].min_count)
        for gi in order:
            group = plan.groups[gi]
            if taken[gi] >= group.max_count:
                continue
            if taken[gi] >= group.min_count and score < QUALITY_BAR:
                continue
            span = fit_span(c, group, segments)
            if span is None or any(span[0] < e and s < span[1] for s, e, _, _ in spans):
                continue
            taken[gi] += 1
            spans.append((*span, gi + 1, c.title.strip()[:80]))
            break
    return [
        Clip(start=s, end=e, group=g, title=title or f"Clip {n}")
        for n, (s, e, g, title) in enumerate(sorted(spans), start=1)
    ]


async def select_clips(
    segments: list[Segment], groq, plan: Plan | None = None, lang: str = "en"
) -> list[Clip]:
    """Scout candidate moments, have them reviewed side by side, then fill the plan best first."""
    plan = plan or Plan.default()
    transcript = format_transcript(segments)
    prompt = scout_prompt(plan, lang)
    candidates: list[Candidate] = []
    for _ in range(2):
        try:
            candidates = parse_candidates(await groq.chat_json(prompt, transcript), segments)
        except (ValidationError, ValueError, KeyError):
            candidates = []
        except GroqRequestError as e:
            # Groq returns 400 json_validate_failed when the model's JSON is malformed.
            if e.code != "json_validate_failed":
                raise
            candidates = []
        if candidates:
            break
    clips = (
        place(candidates, await judge(candidates, segments, groq), segments, plan)
        if candidates
        else []
    )
    if not clips:
        raise SelectionError("Could not pick moments, try again")
    return clips


def split_clips(segments: list[Segment], plan: Plan | None = None) -> list[Clip]:
    """Cut the video into consecutive parts of the planned lengths, at sentence boundaries.

    Each part grows by whole sentences until it reaches its group's minimum, stops before it
    would pass the maximum, and falls back to a hard cut at the maximum when one sentence is
    longer than the whole range. Parts are produced in plan order until the video runs out.
    """
    plan = plan or Plan.default()
    wanted = [g for g in plan.groups for _ in range(g.max_count)]
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
