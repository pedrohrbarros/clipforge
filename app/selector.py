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
# Reviews this low mean the clip is broken (cut mid-thought, an ad, filler); it is dropped
# even when that leaves fewer shorts than asked, unless it is the only one.
MIN_SCORE = 4
JUDGE_CLIP_CHARS = 900

SCOUT_PROMPT = """You are an experienced short-form video editor. You cut one long video into
standalone vertical shorts for TikTok, Reels and YouTube Shorts.

The user message is the transcript, one numbered line per sentence or clause:
`<n>. [<start>-<end> s] <text>`. Timestamps are in seconds; use them to judge lengths.

The user wants:
{groups}

Find {at_least} to {limit} candidate moments, more than the user asked for, so the best can be
chosen afterwards. Look through the whole transcript, not only its start.

A good short:
- opens on a hook a stranger understands with zero context: a bold claim, a question, a
  surprising number, or the first line of a story;
- holds one complete idea, story or argument, from its setup to its payoff;
- ends right after the payoff, on a finished sentence.

Never include, at either end or in the middle:
- greetings, intros, "welcome back", channel housekeeping;
- sponsor reads, ads, discount codes;
- requests to like, subscribe, comment or follow, and sign-offs;
- filler, rambling, false starts and self-corrections ("anyway, where was I", "like, you know");
- transitions that point elsewhere ("which brings me to", "back to it", "as I said",
  "we will get to that later").
Do not open on words that refer to something outside the clip (this, that, it, which, so, and,
but) unless the clip itself explains them. When a strong idea sits next to any of the above,
cut tighter so the clip leaves it out.

For each candidate, copy from the transcript exactly as written the first 6 to 10 words of the
clip as `opening` and its last 6 to 10 words as `closing`. They are matched against the
transcript to place the cut, so copy them verbatim and in order. The closing words end the
payoff itself: never filler, a transition or the start of the next topic.
Score each candidate from 1 to 10 as a standalone short; be strict, a 7 is good.
Give each a title under 60 characters that says what the viewer gets, written in {language}.
Candidates must not overlap.
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
    for score, c in ranked:
        if score < MIN_SCORE and spans:
            break
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


async def select_clips(
    words: list[Word], groq, plan: Plan | None = None, lang: str = "en"
) -> list[Clip]:
    """Scout candidate moments, have them reviewed side by side, then fill the plan best first."""
    plan = plan or Plan.default()
    transcript = format_transcript(sentence_lines(words))
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
    clips = (
        place(candidates, await judge(candidates, words, groq), words, plan) if candidates else []
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
