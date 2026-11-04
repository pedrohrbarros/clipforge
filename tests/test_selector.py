import pytest

from app.groq_client import GroqRequestError
from app.selector import SelectionError, select_clips, validate_clips
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
    clips = validate_clips(raw, SEGS)
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


def test_validate_clips_honours_max_clips():
    raw = {
        "clips": [
            {"start": 0.0, "end": 30.0, "title": "a"},
            {"start": 40.0, "end": 70.0, "title": "b"},
        ]
    }
    assert [c.title for c in validate_clips(raw, SEGS, max_clips=1)] == ["a"]
