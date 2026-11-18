import pytest

from app.groq_client import GroqRequestError
from app.selector import (
    LANGUAGES,
    TITLE_PROMPT,
    Candidate,
    Group,
    Plan,
    SelectionError,
    fit_span,
    place,
    scout_prompt,
    select_clips,
    split_clips,
)
from app.transcribe import Segment

SEGS = [Segment(f"s{i}", i * 10.0, i * 10.0 + 10.0) for i in range(12)]  # 0..120s


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


async def test_select_clips_scouts_reviews_and_fills_the_plan_best_first():
    plan = Plan(groups=[Group(min_count=1, max_count=3, min_seconds=20, max_seconds=60)])
    scout = {
        "candidates": [
            {"first": 0, "last": 1, "score": 9, "title": "intro"},
            {"first": 3, "last": 5, "score": 8, "title": "b"},
            {"first": 7, "last": 8, "score": 7, "title": "c"},
            {"first": 9, "last": 10, "score": 6, "title": "d"},
            {"first": 4, "last": 99, "score": 10, "title": "points past the transcript"},
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
    clips = await select_clips(SEGS, groq, plan)
    assert groq.calls == 2
    assert [(c.start, c.end, c.title) for c in clips] == [(30.0, 60.0, "b"), (70.0, 90.0, "c")]


def test_place_keeps_weak_candidates_only_to_reach_the_minimum():
    plan = Plan(groups=[Group(min_count=2, max_count=3, min_seconds=20, max_seconds=60)])
    candidates = [
        Candidate(first=0, last=2),
        Candidate(first=4, last=6),
        Candidate(first=8, last=10),
    ]
    clips = place(candidates, [4, 3, 2], SEGS, plan)
    assert [(c.start, c.end) for c in clips] == [(0.0, 30.0), (40.0, 70.0)]


def test_fit_span_trims_by_whole_lines_and_never_pads_a_short_run():
    group = Group(max_count=1, min_seconds=20, max_seconds=30)
    assert fit_span(Candidate(first=0, last=8), group, SEGS) == (0.0, 30.0)
    assert fit_span(Candidate(first=0, last=0), group, SEGS) is None
    long_line = [Segment("one long sentence", 0.0, 50.0)]
    assert fit_span(Candidate(first=0, last=0), group, long_line) == (0.0, 30.0)


async def test_select_clips_retries_the_scout_once_then_fails():
    groq = FakeGroq([{"candidates": []}, {"nonsense": True}])
    with pytest.raises(SelectionError):
        await select_clips(SEGS, groq)
    assert groq.calls == 2


async def test_select_clips_survives_malformed_json_and_a_failed_review():
    groq = FakeGroq(
        [
            GroqRequestError(400, "Failed to generate JSON", code="json_validate_failed"),
            {"candidates": [{"first": 1, "last": 3, "score": 8, "title": "ok"}]},
            RuntimeError("review unavailable"),
        ]
    )
    clips = await select_clips(SEGS, groq)
    assert groq.calls == 3
    assert [(c.start, c.end, c.title) for c in clips] == [(10.0, 40.0, "ok")]


def test_split_clips_cuts_up_to_the_maximum_at_sentence_bounds():
    plan = Plan(
        groups=[
            Group(min_count=1, max_count=2, min_seconds=15, max_seconds=30),
            Group(min_count=1, max_count=1, min_seconds=30, max_seconds=60),
        ]
    )
    parts = split_clips(SEGS, plan)  # 12 sentences of 10 s each, 0..120 s
    assert [(p.start, p.end) for p in parts] == [(0.0, 20.0), (20.0, 40.0), (40.0, 70.0)]


def test_plan_validates_counts_and_ranges():
    for bad in (
        '[{"min_count": 1, "max_count": 11, "min_seconds": 10, "max_seconds": 30}]',
        '[{"min_count": 4, "max_count": 2, "min_seconds": 10, "max_seconds": 30}]',
        '[{"min_count": 1, "max_count": 1, "min_seconds": 10, "max_seconds": 90}]',
        '[{"min_count": 1, "max_count": 1, "min_seconds": 30, "max_seconds": 20}]',
    ):
        with pytest.raises(ValueError):
            Plan.parse(bad)
    legacy = Plan.parse('[{"count": 2, "min_seconds": 10, "max_seconds": 30}]')
    assert (legacy.groups[0].min_count, legacy.groups[0].max_count, legacy.total) == (2, 2, 2)


def test_prompts_name_the_counts_and_the_title_language():
    prompt = scout_prompt(Plan.default(), "pt")
    assert "1 to 3 clips of 20 to 60 seconds" in prompt
    assert "written in Portuguese" in prompt
    assert "written in English" in scout_prompt(Plan.default(), "xx")
    assert "written in Spanish" in TITLE_PROMPT.format(language=LANGUAGES["es"])
