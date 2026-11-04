from app.youtube import is_youtube_url


def test_is_youtube_url_accepts_watch_and_short_links_only():
    assert is_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert is_youtube_url("https://youtu.be/dQw4w9WgXcQ")
    assert is_youtube_url("https://youtube.com/shorts/dQw4w9WgXcQ")
    assert not is_youtube_url("https://vimeo.com/123")
    assert not is_youtube_url("not a url")
