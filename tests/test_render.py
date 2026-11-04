from pathlib import Path

from app.render import build_ass, render_args
from app.selector import Clip
from app.transcribe import Word


def test_build_ass_groups_words_relative_to_clip_start():
    words = [Word(f"w{i}", 100.0 + i, 100.5 + i) for i in range(6)]
    ass = build_ass(words, clip_start=100.0, clip_end=106.0, words_per_cue=4)
    lines = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
    assert len(lines) == 2
    assert lines[0] == "Dialogue: 0,0:00:00.00,0:00:03.50,Default,,0,0,0,,w0 w1 w2 w3"
    assert lines[1].endswith(",w4 w5")


def test_render_args_crops_scales_and_burns_subtitles_when_given():
    clip = Clip(start=10.0, end=40.0, title="t")
    args = render_args(Path("/v.mp4"), Path("/out.mp4"), clip, Path("/c.ass"))
    vf = args[args.index("-vf") + 1]
    assert vf == "crop=ih*9/16:ih,scale=1080:1920,subtitles=/c.ass"
    assert args[args.index("-ss") + 1] == "10.000"
    plain = render_args(Path("/v.mp4"), Path("/out.mp4"), clip, None)
    assert "subtitles" not in plain[plain.index("-vf") + 1]
