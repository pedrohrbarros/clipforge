import httpx
import respx

from app.mailer import Mailer, render_short, render_started
from app.storage import Storage


def test_short_email_has_one_link_and_the_zip_on_the_last():
    short = {"title": "Um", "url": "https://x/1", "duration": 20.5}
    first = render_short("pt", "Pedro", short, 1, 2, 24, "http://app", "https://x/z")
    assert "Olá Pedro" in first and "short 1 de 2" in first
    assert '<a href="https://x/1">Um</a>' in first and "https://x/z" not in first
    last = render_short("pt", "Pedro", short, 2, 2, 24, "http://app", "https://x/z")
    assert "https://x/z" in last


def test_short_email_without_storage_link_points_at_the_app():
    html = render_short(
        "en", "P", {"title": "One", "url": None, "duration": 20}, 1, 1, 1, "http://app", None
    )
    assert "One (20s)" in html and "Open http://app to download them." in html


@respx.mock
async def test_storage_sign_returns_an_absolute_url():
    respx.post("https://sb.test/storage/v1/object/sign/shorts/u/j/short-1.mp4").mock(
        return_value=httpx.Response(200, json={"signedURL": "/object/sign/shorts/u/j/x?token=t"})
    )
    url = await Storage("https://sb.test", "key", "shorts").sign("u/j/short-1.mp4", 60)
    assert url == "https://sb.test/storage/v1/object/sign/shorts/u/j/x?token=t"


@respx.mock
async def test_ensure_bucket_skips_creation_when_it_exists():
    respx.get("https://sb.test/storage/v1/bucket/shorts").mock(
        return_value=httpx.Response(200, json={"id": "shorts"})
    )
    create = respx.post("https://sb.test/storage/v1/bucket")
    await Storage("https://sb.test", "key", "shorts").ensure_bucket()
    assert not create.called


def test_mailer_builds_a_message_from_the_gmail_account():
    mailer = Mailer("clipforge189@gmail.com", "abcd efgh ijkl mnop", "http://app", 24)
    assert mailer.enabled and mailer.password == "abcdefghijklmnop"
    msg = mailer.build("p@x.y", "Hi", "<p>body</p>")
    assert msg["From"] == "ClipForge <clipforge189@gmail.com>"
    assert msg["To"] == "p@x.y"
    assert msg.get_body(preferencelist=("html",)).get_content().strip() == "<p>body</p>"
    assert not Mailer("", "", "http://app", 24).enabled


def test_started_email_is_in_the_users_language():
    assert "começamos a criar os shorts" in render_started("pt", "Pedro")
    assert "Hi Pedro" in render_started("en", "Pedro")
