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
    assert vf == (
        "crop=w='min(iw,ih*1080/1920)':h='min(ih,iw*1920/1080)',scale=1080:1920,subtitles=/c.ass"
    )
    assert args[args.index("-ss") + 1] == "10.000"
    plain = render_args(Path("/v.mp4"), Path("/out.mp4"), clip, None)
    assert "subtitles" not in plain[plain.index("-vf") + 1]


def test_aspect_changes_the_crop_and_the_caption_canvas():
    from app.render import video_filter

    assert video_filter("16:9").endswith("scale=1920:1080")
    assert video_filter("1:1").endswith("scale=1080:1080")
    assert video_filter("nonsense").endswith("scale=1080:1920")
    square = build_ass([], 0, 1, aspect="1:1")
    assert "PlayResX: 1080" in square and "PlayResY: 1080" in square


def test_render_args_caps_the_bitrate_so_a_60s_short_fits_the_object_limit():
    clip = Clip(start=0.0, end=60.0, title="t")
    args = render_args(Path("/v.mp4"), Path("/out.mp4"), clip, None)
    assert args[args.index("-maxrate") + 1] == "5M"
    assert args[args.index("-bufsize") + 1] == "10M"
