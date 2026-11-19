import pytest

from app.groq_client import GroqRequestError
from app.selector import (
    LANGUAGES,
    TITLE_PROMPT,
    Candidate,
    Clip,
    Group,
    Plan,
    fill,
    find_quote,
    place,
    scout_prompt,
    select_clips,
    split_clips,
)
from app.transcribe import Segment, Word

SEGS = [Segment(f"s{i}", i * 10.0, i * 10.0 + 10.0) for i in range(12)]  # 0..120s
WORDS = [Word(f"t{i}", float(i), i + 1.0) for i in range(120)]  # one word a second


def quote(first: int, last: int) -> str:
    return " ".join(f"t{i}" for i in range(first, last + 1))


class FakeGroq:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = 0

    async def chat_json(self, system, user):
        self.calls += 1
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


async def test_select_clips_cuts_on_quoted_words_and_keeps_the_best_reviewed():
    plan = Plan(groups=[Group(count=2, min_seconds=20, max_seconds=40)])
    scout = {
        "candidates": [
            {"opening": quote(0, 3), "closing": quote(26, 29), "score": 9, "title": "intro"},
            {"opening": "T40, t41 t42. T43", "closing": quote(66, 69), "score": 8, "title": "b"},
            {"opening": quote(75, 78), "closing": quote(96, 99), "score": 7, "title": "c"},
            {"opening": "words that were never said", "closing": quote(1, 4), "score": 10},
            {"opening": quote(100, 103), "closing": quote(116, 119), "score": 6, "title": "e"},
        ]
    }
    review = {
        "scores": [
            {"id": 0, "score": 3},
            {"id": 1, "score": 9},
            {"id": 2, "score": 8},
            {"id": 3, "score": 5},
        ]
    }
    groq = FakeGroq([scout, review])
    clips = await select_clips(WORDS, groq, plan)
    assert groq.calls == 2
    assert [(c.start, c.end, c.title) for c in clips] == [(40.0, 70.0, "b"), (75.0, 100.0, "c")]


def test_find_quote_tolerates_a_wrong_word_but_not_an_unrelated_quote():
    tokens = ["so", "the", "real", "reason", "was", "money"]
    assert find_quote(tokens, ["the", "real", "season", "was"]) == 1
    assert find_quote(tokens, ["nothing", "like", "this", "here"]) is None
    assert find_quote(tokens, ["real", "reason"]) is None  # too short to place reliably


def test_place_skips_wrong_lengths_and_overlaps():
    plan = Plan(groups=[Group(count=3, min_seconds=20, max_seconds=40)])

    def cand(first, last):
        return Candidate(opening="", closing="", first_word=first, last_word=last)

    candidates = [cand(0, 29), cand(10, 39), cand(40, 99), cand(50, 79)]
    clips = place(candidates, [9, 8, 7, 3], WORDS, plan)
    assert [(c.start, c.end) for c in clips] == [(0.0, 30.0), (50.0, 80.0)]


async def test_select_clips_retries_the_scout_once_then_cuts_and_titles_a_short_anyway():
    groq = FakeGroq([{"candidates": []}, {"nonsense": True}, {"titles": ["named"]}])
    clips = await select_clips(WORDS, groq)
    assert groq.calls == 3
    assert [(c.start, c.end, c.title) for c in clips] == [(0.0, 20.0, "named")]


def test_fill_tops_up_to_the_plan_count_and_recuts_when_the_gaps_are_too_small():
    three = Plan(groups=[Group(count=3, min_seconds=20, max_seconds=30)])
    picked = [Clip(start=40, end=70, title="ai pick")]
    out = fill(picked, SEGS, three, 120)
    assert [(c.start, c.end) for c in out] == [(0.0, 20.0), (20.0, 40.0), (40.0, 70.0)]
    two = Plan(groups=[Group(count=2, min_seconds=25, max_seconds=30)])
    out = fill([Clip(start=15, end=45)], SEGS[:6], two, 60)
    assert [(c.start, c.end) for c in out] == [(0.0, 30.0), (30.0, 60.0)]


async def test_select_clips_survives_malformed_json_and_a_failed_review():
    groq = FakeGroq(
        [
            GroqRequestError(400, "Failed to generate JSON", code="json_validate_failed"),
            {"candidates": [{"opening": quote(10, 13), "closing": quote(36, 39), "title": "ok"}]},
            RuntimeError("review unavailable"),
        ]
    )
    clips = await select_clips(WORDS, groq)
    assert groq.calls == 3
    assert [(c.start, c.end, c.title) for c in clips] == [(10.0, 40.0, "ok")]


def test_split_clips_cuts_consecutive_parts_at_sentence_bounds():
    plan = Plan(
        groups=[
            Group(count=2, min_seconds=15, max_seconds=30),
            Group(count=1, min_seconds=30, max_seconds=60),
        ]
    )
    parts = split_clips(SEGS, plan)  # 12 sentences of 10 s each, 0..120 s
    assert [(p.start, p.end) for p in parts] == [(0.0, 20.0), (20.0, 40.0), (40.0, 70.0)]


def test_plan_validates_counts_and_ranges():
    for bad in (
        '[{"count": 11, "min_seconds": 10, "max_seconds": 30}]',
        '[{"count": 1, "min_seconds": 10, "max_seconds": 90}]',
        '[{"count": 1, "min_seconds": 30, "max_seconds": 20}]',
    ):
        with pytest.raises(ValueError):
            Plan.parse(bad)
    assert Plan.parse('[{"count": 2, "min_seconds": 10, "max_seconds": 30}]').total == 2
    from_min_max = '[{"min_count": 1, "max_count": 4, "min_seconds": 10, "max_seconds": 30}]'
    assert Plan.parse(from_min_max).total == 4


def test_prompts_name_the_counts_and_the_title_language():
    prompt = scout_prompt(Plan.default(), "pt")
    assert "- 1 clip of 20 to 60 seconds" in prompt
    assert "written in Portuguese" in prompt
    assert "written in English" in scout_prompt(Plan.default(), "xx")
    assert "written in Spanish" in TITLE_PROMPT.format(language=LANGUAGES["es"])
