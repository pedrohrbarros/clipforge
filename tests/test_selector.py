import pytest

from app.groq_client import GroqRequestError
from app.selector import Group, Plan, SelectionError, select_clips, split_clips, validate_clips
from app.transcribe import Segment

SEGS = [Segment(f"s{i}", i * 10.0, i * 10.0 + 10.0) for i in range(12)]  # 0..120s


def test_validate_clips_snaps_filters_and_drops_overlaps():
    raw = {
        "clips": [
            {"start": 9.6, "end": 41.0, "title": "Good one"},  # snaps to 10-40
            {"start": 30.0, "end": 55.0, "title": "Overlaps"},  # overlaps -> dropped
            {"start": 50.0, "end": 55.0, "title": "Too short"},  # 5s -> dropped
            {"start": 60.0, "end": 100.0, "title": "Second"},
            {"": {"start": 100.0, "end": 120.0, "title": "Malformed, skipped"}},
        ]
    }
    plan = Plan(groups=[Group(count=2, min_seconds=20, max_seconds=60)])
    clips = validate_clips(raw, SEGS, plan)
    assert [(c.start, c.end, c.title) for c in clips] == [
        (10.0, 40.0, "Good one"),
        (60.0, 100.0, "Second"),
    ]


class FakeGroq:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = 0

    async def chat_json(self, system, user):
        self.calls += 1
        return self.answers.pop(0)


async def test_select_clips_retries_once_then_fails():
    groq = FakeGroq([{"clips": []}, {"nonsense": True}])
    with pytest.raises(SelectionError):
        await select_clips(SEGS, groq)
    assert groq.calls == 2


async def test_select_clips_retries_when_groq_rejects_malformed_json():
    class Flaky:
        calls = 0

        async def chat_json(self, system, user):
            self.calls += 1
            if self.calls == 1:
                raise GroqRequestError(400, "Failed to generate JSON", code="json_validate_failed")
            return {"clips": [{"start": 10.0, "end": 40.0, "title": "ok"}]}

    groq = Flaky()
    clips = await select_clips(SEGS, groq)
    assert groq.calls == 2
    assert clips[0].title == "ok"


def test_validate_clips_places_clips_into_plan_groups():
    plan = Plan(
        groups=[
            Group(count=2, min_seconds=10, max_seconds=30),
            Group(count=1, min_seconds=30, max_seconds=60),
        ]
    )
    raw = {
        "clips": [
            {"group": 1, "start": 0.0, "end": 20.0, "title": "a"},
            {"group": 1, "start": 20.0, "end": 40.0, "title": "b"},
            {"group": 1, "start": 40.0, "end": 50.0, "title": "c"},  # group 1 full -> dropped
            {"group": 2, "start": 60.0, "end": 100.0, "title": "d"},
            {"group": 9, "start": 100.0, "end": 120.0, "title": "e"},  # bad group, plan is full
        ]
    }
    clips = validate_clips(raw, SEGS, plan)
    assert [(c.title, c.group) for c in clips] == [("a", 1), ("b", 1), ("d", 2)]


async def test_select_clips_retries_for_the_plan_then_keeps_the_best():
    plan = Plan(groups=[Group(count=3, min_seconds=20, max_seconds=60)])

    class Groq:
        calls = 0

        async def chat_json(self, system, user):
            self.calls += 1
            assert "group 1: 3 clips" in system
            return {"clips": [{"group": 1, "start": 0.0, "end": 30.0, "title": "only one"}]}

    groq = Groq()
    clips = await select_clips(SEGS, groq, plan)
    assert groq.calls == 2
    assert [c.title for c in clips] == ["only one"]


def test_split_clips_cuts_consecutive_parts_at_sentence_bounds():
    plan = Plan(
        groups=[
            Group(count=2, min_seconds=15, max_seconds=30),
            Group(count=1, min_seconds=30, max_seconds=60),
        ]
    )
    parts = split_clips(SEGS, plan)  # 12 sentences of 10 s each, 0..120 s
    assert [(p.start, p.end) for p in parts] == [(0.0, 20.0), (20.0, 40.0), (40.0, 70.0)]


def test_plan_rejects_more_than_five_shorts_or_bad_ranges():
    with pytest.raises(ValueError):
        Plan.parse('[{"count": 11, "min_seconds": 10, "max_seconds": 30}]')
    with pytest.raises(ValueError):
        Plan.parse('[{"count": 1, "min_seconds": 10, "max_seconds": 90}]')
    with pytest.raises(ValueError):
        Plan.parse('[{"count": 1, "min_seconds": 30, "max_seconds": 20}]')
    assert Plan.parse('[{"count": 2, "min_seconds": 10, "max_seconds": 30}]').total == 2
