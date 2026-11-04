import httpx
import pytest
import respx

from app.groq_client import GroqClient, GroqQuotaError, quota_from_headers


def test_quota_from_headers_parses_ints_and_keeps_reset_strings():
    q = quota_from_headers(
        httpx.Headers(
            {
                "x-ratelimit-limit-requests": "1000",
                "x-ratelimit-remaining-requests": "998",
                "x-ratelimit-reset-requests": "2m3s",
            }
        )
    )
    assert q.limit_requests == 1000
    assert q.remaining_requests == 998
    assert q.reset_requests == "2m3s"
    assert q.limit_tokens is None


@respx.mock
async def test_client_records_usage_and_raises_on_429():
    route = respx.post("https://groq.test/chat/completions").mock(
        return_value=httpx.Response(
            429, headers={"x-ratelimit-reset-requests": "10s"}, json={"error": "rate"}
        )
    )
    client = GroqClient("key", "chat-model", "whisper-model", base_url="https://groq.test")
    with pytest.raises(GroqQuotaError) as exc:
        await client.chat_ping()
    assert route.called
    assert "10s" in str(exc.value)
    assert client.usage["chat"].reset_requests == "10s"
