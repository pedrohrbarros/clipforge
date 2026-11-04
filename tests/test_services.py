import httpx
import respx

from app.mailer import Mailer, render_done
from app.storage import Storage


def test_done_email_lists_links_in_the_users_language():
    html = render_done(
        "pt", "Pedro", [{"title": "Um", "url": "https://x/1", "duration": 20.5}], "https://x/z", 24
    )
    assert "Olá Pedro" in html
    assert '<a href="https://x/1">Um</a>' in html
    assert "24 horas" in html


def test_done_email_without_storage_links_points_at_the_app():
    html = render_done(
        "en", "P", [{"title": "One", "url": None, "duration": 20}], None, 1, "http://app"
    )
    assert "<li>One (20s)</li>" in html
    assert "Open http://app to download them." in html


@respx.mock
async def test_storage_sign_returns_an_absolute_url():
    respx.post("https://sb.test/storage/v1/object/sign/shorts/u/j/short-1.mp4").mock(
        return_value=httpx.Response(200, json={"signedURL": "/object/sign/shorts/u/j/x?token=t"})
    )
    url = await Storage("https://sb.test", "key", "shorts").sign("u/j/short-1.mp4", 60)
    assert url == "https://sb.test/storage/v1/object/sign/shorts/u/j/x?token=t"


@respx.mock
async def test_mailer_posts_to_resend():
    route = respx.post("https://api.resend.com/emails").mock(
        return_value=httpx.Response(200, json={"id": "1"})
    )
    await Mailer("rk", "ClipForge <a@b.c>", "http://app", 24).send_failed(
        "p@x.y", "Pedro", "en", "boom"
    )
    assert route.called
    sent = route.calls[0].request
    assert b'"to": ["p@x.y"]' in sent.content or b'"to":["p@x.y"]' in sent.content


@respx.mock
async def test_ensure_bucket_skips_creation_when_it_exists():
    respx.get("https://sb.test/storage/v1/bucket/shorts").mock(
        return_value=httpx.Response(200, json={"id": "shorts"})
    )
    create = respx.post("https://sb.test/storage/v1/bucket")
    await Storage("https://sb.test", "key", "shorts").ensure_bucket()
    assert not create.called
