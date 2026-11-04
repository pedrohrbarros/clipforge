from pathlib import Path

import pytest

from app import youtube
from app.youtube import configure_cookies, is_youtube_url


def test_is_youtube_url_accepts_watch_and_short_links_only():
    assert is_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert is_youtube_url("https://youtu.be/dQw4w9WgXcQ")
    assert is_youtube_url("https://youtube.com/shorts/dQw4w9WgXcQ")
    assert not is_youtube_url("https://vimeo.com/123")
    assert not is_youtube_url("not a url")


def test_configure_cookies_writes_file_and_rejects_garbage(tmp_path: Path):
    export = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tabc\n"
    path = configure_cookies(export, tmp_path)
    assert path is not None and path.read_text().startswith("# Netscape")
    assert youtube.COOKIES_FILE == path
    assert configure_cookies("", tmp_path) is None
    assert youtube.COOKIES_FILE is None
    with pytest.raises(ValueError):
        configure_cookies("not an export", tmp_path)
