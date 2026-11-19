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


@respx.mock
async def test_upload_retries_a_timed_out_request(tmp_path, monkeypatch):
    async def no_wait(_seconds):
        return None

    monkeypatch.setattr("app.storage.asyncio.sleep", no_wait)
    route = respx.post("https://sb.test/storage/v1/object/shorts/u/j/short-1.mp4").mock(
        side_effect=[httpx.ReadTimeout("slow"), httpx.Response(200, json={"Key": "k"})]
    )
    short = tmp_path / "short-1.mp4"
    short.write_bytes(b"video")
    await Storage("https://sb.test", "key", "shorts").upload(short, "u/j/short-1.mp4", "video/mp4")
    assert route.call_count == 2
