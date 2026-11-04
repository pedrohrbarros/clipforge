from app.youtube import build_options, format_for, is_youtube_url


def test_build_options_lists_known_heights_and_audio():
    info = {
        "formats": [
            {"height": 1080, "vcodec": "avc1"},
            {"height": 720, "vcodec": "avc1"},
            {"height": 360, "vcodec": "avc1"},
            {"height": 144, "vcodec": "avc1"},
            {"height": None, "vcodec": "none", "acodec": "mp4a"},
        ]
    }
    options = build_options(info)
    assert [o.id for o in options] == ["v1080", "v720", "v360", "audio"]
    assert options[0].label == "1080p video (mp4)"
    assert format_for("audio").startswith("bestaudio")
    assert "height<=720" in format_for("v720")


def test_is_youtube_url_accepts_watch_and_short_links_only():
    assert is_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert is_youtube_url("https://youtu.be/dQw4w9WgXcQ")
    assert is_youtube_url("https://youtube.com/shorts/dQw4w9WgXcQ")
    assert not is_youtube_url("https://vimeo.com/123")
    assert not is_youtube_url("not a url")
