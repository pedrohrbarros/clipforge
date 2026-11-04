import json
import time
from pathlib import Path

import httpx
from pydantic import BaseModel

GROQ_BASE = "https://api.groq.com/openai/v1"

HEADER_FIELDS = {
    "limit_requests": "x-ratelimit-limit-requests",
    "remaining_requests": "x-ratelimit-remaining-requests",
    "limit_tokens": "x-ratelimit-limit-tokens",
    "remaining_tokens": "x-ratelimit-remaining-tokens",
    "limit_audio_seconds": "x-ratelimit-limit-audio-seconds",
    "remaining_audio_seconds": "x-ratelimit-remaining-audio-seconds",
    "reset_requests": "x-ratelimit-reset-requests",
    "reset_tokens": "x-ratelimit-reset-tokens",
    "reset_audio_seconds": "x-ratelimit-reset-audio-seconds",
}


class Quota(BaseModel):
    limit_requests: int | None = None
    remaining_requests: int | None = None
    limit_tokens: int | None = None
    remaining_tokens: int | None = None
    limit_audio_seconds: int | None = None
    remaining_audio_seconds: int | None = None
    reset_requests: str | None = None
    reset_tokens: str | None = None
    reset_audio_seconds: str | None = None
    observed_at: float | None = None


def quota_from_headers(headers: httpx.Headers) -> Quota:
    data: dict[str, int | str] = {}
    for field, header in HEADER_FIELDS.items():
        value = headers.get(header)
        if value is None:
            continue
        if field.startswith("reset"):
            data[field] = value
        else:
            try:
                data[field] = int(float(value))
            except ValueError:
                continue
    return Quota(**data, observed_at=time.time())


class GroqAuthError(Exception):
    pass


class GroqQuotaError(Exception):
    def __init__(self, reset: str | None):
        self.reset = reset
        super().__init__(f"Groq quota exhausted, resets in {reset or 'a while'}")


class GroqClient:
    def __init__(
        self, api_key: str, chat_model: str, whisper_model: str, base_url: str = GROQ_BASE
    ):
        self._api_key = api_key
        self.chat_model = chat_model
        self.whisper_model = whisper_model
        self.usage: dict[str, Quota] = {}
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(300, connect=30),
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _post(self, family: str, path: str, **kwargs) -> dict:
        if not self._api_key:
            raise GroqAuthError("GROQ_API_KEY is not set on the server")
        resp = await self._http.post(path, **kwargs)
        quota = quota_from_headers(resp.headers)
        self.usage[family] = quota
        if resp.status_code == 429:
            raise GroqQuotaError(resp.headers.get("retry-after") or quota.reset_requests)
        resp.raise_for_status()
        return resp.json()

    async def chat_json(self, system: str, user: str, max_tokens: int = 1024) -> dict:
        data = await self._post(
            "chat",
            "/chat/completions",
            json={
                "model": self.chat_model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.2,
                "max_tokens": max_tokens,
            },
        )
        return json.loads(data["choices"][0]["message"]["content"])

    async def chat_ping(self) -> None:
        await self._post(
            "chat",
            "/chat/completions",
            json={
                "model": self.chat_model,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 1,
            },
        )

    async def transcribe(self, audio: Path) -> dict:
        with audio.open("rb") as fh:
            return await self._post(
                "whisper",
                "/audio/transcriptions",
                files={"file": (audio.name, fh, "audio/mpeg")},
                data={
                    "model": self.whisper_model,
                    "response_format": "verbose_json",
                    "timestamp_granularities[]": ["word", "segment"],
                },
            )
