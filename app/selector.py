import json
import re

from pydantic import BaseModel, Field, ValidationError, model_validator

from app.groq_client import GroqRequestError
from app.transcribe import Segment, Word, sentence_lines

MAX_CLIPS = 10
HARD_MIN_LEN = 5.0
HARD_MAX_LEN = 60.0
MAX_TRANSCRIPT_CHARS = 20_000


class Group(BaseModel):
    """`count` shorts, each `min_seconds` to `max_seconds` long."""

    count: int = Field(ge=1, le=MAX_CLIPS)
    min_seconds: float = Field(ge=HARD_MIN_LEN, le=HARD_MAX_LEN)
    max_seconds: float = Field(ge=HARD_MIN_LEN, le=HARD_MAX_LEN)

    @model_validator(mode="before")
    @classmethod
    def _min_max_count(cls, data):
        # Pages loaded during the short-lived min/max version send `max_count` instead.
        if isinstance(data, dict) and "count" not in data and "max_count" in data:
            return {**data, "count": data["max_count"]}
        return data

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

    @property
    def min_seconds(self) -> float:
        """The shortest video that can hold every short in the plan."""
        return sum(g.count * g.min_seconds for g in self.groups)

    @classmethod
    def default(cls) -> "Plan":
        return cls(groups=[Group(count=1, min_seconds=20, max_seconds=60)])

    @classmethod
    def parse(cls, raw: str) -> "Plan":
        """From the `plan` form field: a JSON list of groups (see Group)."""
        data = json.loads(raw)
        return cls(groups=data if isinstance(data, list) else data.get("groups", []))


LANGUAGES = {"en": "English", "pt": "Portuguese", "es": "Spanish"}

MAX_CANDIDATES = 20
JUDGE_CLIP_CHARS = 900

SCOUT_PROMPT = """You are an expert TikTok editor specializing in comedy "best moments" compilations.
You cut one long video into standalone vertical shorts for TikTok.

The video is a comedy source (stream, podcast, video, show) — find the FUNNIEST or most IMPACTFUL moments.
Target audience: TikTok scrollers who want instant entertainment — hook in the first 0.5s, payoff by the end.
Style: Fast-paced, punchy, self-contained clips that work with zero context.

The user message is the transcript, one numbered line per sentence or clause:
`<n>. [<start>-<end> s] <text>`. Timestamps are in seconds; use them to judge lengths.

The user wants:
{groups}

Find {at_least} to {limit} candidate moments, more than the user asked for, so the best can be
chosen afterwards. Look through the WHOLE transcript, not only its start.

A GREAT TikTok comedy/impact clip:
- OPENS on an immediate hook: a ridiculous statement, a wild reaction, a shocking number, a perfect punchline setup, or "wait, what?" moment
- DELIVERS one complete beat: setup → escalation → payoff (or: premise → twist → reaction)
- ENDS on the laugh/impact — the exact line where the energy peaks, not after
- Is 15-60s of PURE signal — no fluff, no "so anyway," no rambling to the point

NEVER include (auto-reject if present):
- Greetings, intros, "what's up guys," "welcome back," channel housekeeping
- Sponsor reads, ads, codes, merch plugs
- "Like/subscribe/follow," sign-offs, "see you next time"
- Filler, false starts, "um/uh/like," self-corrections, rambling transitions
- Context-dependent opens: "this," "that," "it," "which," "so," "and," "but" — unless the clip itself explains them
- Transitions pointing elsewhere: "which brings me to," "back to it," "as I said," "we'll get to that"

For each candidate, copy from the transcript EXACTLY the first 6–10 words as `opening`
and the last 6–10 words as `closing` (matched verbatim to place cuts).
Score 1–10: 9–10 = viral-tier hook + clean payoff; 7–8 = solid; 5–6 = weak hook/payoff; 1–4 = broken.
Title: under 60 chars, TikTok-style (curiosity gap, specific, punchy), in {language}.
Candidates MUST NOT overlap.
Respond with JSON only:
{{"candidates": [{{"opening": "<verbatim words>", "closing": "<verbatim words>",
"score": <int>, "title": "<string>"}}]}}"""

JUDGE_PROMPT = """You review candidate shorts cut from one longer video. The user message lists
them as `<id>. (<seconds> s) <the clip's full transcript>`.
Score each from 1 to 10 as a short that a stranger scrolls into with no other context:
- 9 or 10: a strong hook in the first sentence, one complete idea with a clear payoff, a clean
  ending;
- 6 to 8: good, but the hook or the payoff is weaker;
- 4 or 5: understandable but flat, or slightly rough at an edge;
- 1 to 3: broken. It starts mid-thought or leans on something not in the clip, ends in the middle
  of a sentence, or contains a sponsor read, a request to like or subscribe, a sign-off, or
  rambling filler.
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
    opening: str
    closing: str
    score: float = 0
    title: str = ""
    first_word: int = 0
    last_word: int = 0


class SelectionError(Exception):
    def __init__(self, message: str, code: str = "select_failed"):
        self.code = code
        super().__init__(message)


def scout_prompt(plan: Plan, lang: str = "en") -> str:
    lines = [
        f"- {g.count} clip{'s' if g.count > 1 else ''} of "
        f"{int(g.min_seconds)} to {int(g.max_seconds)} seconds"
        for g in plan.groups
    ]
    return SCOUT_PROMPT.format(
        groups="\n".join(lines),
        at_least=min(MAX_CANDIDATES, plan.total + 2),
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


def _tokens(text: str) -> list[str]:
    return [t for t in (re.sub(r"[^\w%]", "", w.lower()) for w in text.split()) if t]


def find_quote(tokens: list[str], quote: list[str], start: int = 0) -> int | None:
    """Where `quote` begins in `tokens` at or after `start`, tolerating a word or two off."""
    n = len(quote)
    best, best_hits = None, 0
    for i in range(start, len(tokens) - n + 1):
        hits = sum(a == b for a, b in zip(tokens[i : i + n], quote, strict=True))
        if hits > best_hits:
            best, best_hits = i, hits
            if hits == n:
                break
    return best if n >= 3 and best_hits >= n - max(1, n // 4) else None


def parse_candidates(raw: dict, words: list[Word]) -> list[Candidate]:
    """Place each candidate on the words its quotes match; drop the ones that match nothing."""
    items = raw.get("candidates") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        raise ValueError("no candidates list")
    tokens = [(_tokens(w.text) or [""])[0] for w in words]
    out: list[Candidate] = []
    seen: set[tuple[int, int]] = set()
    for item in items:
        try:
            c = Candidate.model_validate(item)
        except ValidationError:
            continue
        opening, closing = _tokens(c.opening), _tokens(c.closing)
        first = find_quote(tokens, opening)
        if first is None:
            continue
        close_at = find_quote(tokens, closing, first)
        if close_at is None:
            continue
        span = (first, close_at + len(closing) - 1)
        if span not in seen:
            seen.add(span)
            out.append(c.model_copy(update={"first_word": span[0], "last_word": span[1]}))
    return out


async def judge(candidates: list[Candidate], words: list[Word], groq) -> list[float]:
    """The model re-scores whole clips side by side; if that fails the scout scores stand."""
    lines = []
    for i, c in enumerate(candidates):
        text = " ".join(w.text for w in words[c.first_word : c.last_word + 1])
        if len(text) > JUDGE_CLIP_CHARS:
            half = JUDGE_CLIP_CHARS // 2
            text = f"{text[:half]} [...] {text[-half:]}"
        seconds = words[c.last_word].end - words[c.first_word].start
        lines.append(f"{i}. ({seconds:.0f} s) {text}")
    try:
        raw = await groq.chat_json(JUDGE_PROMPT, "\n".join(lines))
        reviewed = {int(s["id"]): float(s["score"]) for s in raw.get("scores", [])}
    except Exception:  # noqa: BLE001 - the review refines the scout's scores, never fails the job
        reviewed = {}
    return [reviewed.get(i, c.score) for i, c in enumerate(candidates)]


def place(
    candidates: list[Candidate], scores: list[float], words: list[Word], plan: Plan
) -> list[Clip]:
    """Best first into the first group whose length range fits and still has room.

    Clips are cut on the quoted words, never trimmed or padded to fit: a pick of the wrong
    length is skipped, since shortening it would drop its payoff and lengthening it would
    tack on unrelated speech.
    """
    ranked = sorted(zip(scores, candidates, strict=True), key=lambda p: -p[0])
    taken = [0] * len(plan.groups)
    spans: list[tuple[float, float, int, str]] = []
    for _, c in ranked:
        start, end = words[c.first_word].start, words[c.last_word].end
        for gi, group in enumerate(plan.groups):
            if (
                taken[gi] >= group.count
                or not group.min_seconds <= end - start <= group.max_seconds
            ):
                continue
            if any(start < e and s < end for s, e, _, _ in spans):
                break
            taken[gi] += 1
            spans.append((round(start, 3), round(end, 3), gi + 1, c.title.strip()[:80]))
            break
    return [
        Clip(start=s, end=e, group=g, title=title or f"Clip {n}")
        for n, (s, e, g, title) in enumerate(sorted(spans), start=1)
    ]


def _top_up(
    clips: list[Clip], lines: list[Segment], plan: Plan, duration: float
) -> list[Clip] | None:
    """Add the shorts `clips` is missing in the time they leave free, or None if none fits.

    Each one is the earliest run of whole sentences that fits its group's range, else a plain
    cut of the group's minimum length at the start of a free gap.
    """
    out = list(clips)
    for gi, g in enumerate(plan.groups, start=1):
        windows = []
        for i, first in enumerate(lines):
            last = next((x for x in lines[i:] if x.end - first.start >= g.min_seconds), None)
            if last and last.end - first.start <= g.max_seconds:
                windows.append((first.start, last.end))
        for _ in range(g.count - sum(c.group == gi for c in out)):
            spans = [(c.start, c.end) for c in out]
            cuts = [(e, e + g.min_seconds) for e in [0.0] + sorted(e for _, e in spans)]
            found = next(
                (
                    (s, e)
                    for s, e in windows + cuts
                    if e <= duration and not any(s < ce and cs < e for cs, ce in spans)
                ),
                None,
            )
            if not found:
                return None
            out.append(Clip(start=round(found[0], 3), end=round(found[1], 3), group=gi))
    return sorted(out, key=lambda c: c.start)


def fill(clips: list[Clip], lines: list[Segment], plan: Plan, duration: float) -> list[Clip]:
    """Exactly the plan's number of shorts, keeping `clips` when the free time allows.

    When the chosen clips leave the rest of the video too fragmented, every short is recut
    from scratch, and as a last resort back to back from the start. The caller makes sure the
    video is at least `plan.min_seconds` long.
    """
    for kept in (clips, []):
        out = _top_up(kept, lines, plan, duration)
        if out is not None:
            return out
    out, t = [], 0.0
    for gi, g in enumerate(plan.groups, start=1):
        for _ in range(g.count):
            out.append(Clip(start=round(t, 3), end=round(t + g.min_seconds, 3), group=gi))
            t += g.min_seconds
    return out


async def select_clips(
    words: list[Word],
    groq,
    plan: Plan | None = None,
    lang: str = "en",
    duration: float | None = None,
) -> list[Clip]:
    """Scout candidate moments, have them reviewed side by side, fill the plan best first,
    then top it up so the user always gets the number of shorts they asked for."""
    plan = plan or Plan.default()
    lines = sentence_lines(words)
    transcript = format_transcript(lines)
    prompt = scout_prompt(plan, lang)
    candidates: list[Candidate] = []
    for _ in range(2):
        try:
            candidates = parse_candidates(await groq.chat_json(prompt, transcript), words)
        except (ValidationError, ValueError, KeyError):
            candidates = []
        except GroqRequestError as e:
            # Groq returns 400 json_validate_failed when the model's JSON is malformed.
            if e.code != "json_validate_failed":
                raise
            candidates = []
        if candidates:
            break
    picked = (
        place(candidates, await judge(candidates, words, groq), words, plan) if candidates else []
    )
    clips = fill(picked, lines, plan, duration or (words[-1].end if words else 0))
    untitled = [c for c in clips if not c.title]
    if untitled:
        titled = iter(await title_clips(untitled, lines, groq, lang))
        clips = [c if c.title else next(titled) for c in clips]
    return clips


def split_clips(segments: list[Segment], plan: Plan | None = None) -> list[Clip]:
    """Cut the video into consecutive parts of the planned lengths, at sentence boundaries.

    Each part grows by whole sentences until it reaches its group's minimum, stops before it
    would pass the maximum, and falls back to a hard cut at the maximum when one sentence is
    longer than the whole range. Parts are produced in plan order until the video runs out.
    """
    plan = plan or Plan.default()
    wanted = [(gi, g) for gi, g in enumerate(plan.groups, start=1) for _ in range(g.count)]
    clips: list[Clip] = []
    i = 0
    for gi, group in wanted:
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
        clips.append(Clip(start=round(start, 3), end=round(end, 3), title="", group=gi))
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
