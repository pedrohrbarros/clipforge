import httpx
import respx

from app.storage import Storage


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
