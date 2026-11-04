"""Supabase Storage: private bucket for finished shorts, served through signed URLs."""

import datetime as dt
from pathlib import Path

import httpx


class Storage:
    def __init__(self, url: str, service_key: str, bucket: str):
        self.enabled = bool(url and service_key)
        self.bucket = bucket
        self._http = httpx.AsyncClient(
            base_url=f"{url.rstrip('/')}/storage/v1",
            headers={"apikey": service_key, "Authorization": f"Bearer {service_key}"},
            timeout=httpx.Timeout(120, connect=30),
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def ensure_bucket(self) -> None:
        """Create the private bucket on first run; 409 means it already exists."""
        if not self.enabled:
            return
        resp = await self._http.post(
            "/bucket", json={"id": self.bucket, "name": self.bucket, "public": False}
        )
        if resp.status_code not in (200, 201, 409):
            resp.raise_for_status()

    async def upload(self, path: Path, key: str, content_type: str) -> None:
        with path.open("rb") as fh:
            resp = await self._http.post(
                f"/object/{self.bucket}/{key}",
                content=fh.read(),
                headers={"Content-Type": content_type, "x-upsert": "true"},
            )
        resp.raise_for_status()

    async def sign(self, key: str, expires_seconds: int) -> str:
        resp = await self._http.post(
            f"/object/sign/{self.bucket}/{key}", json={"expiresIn": expires_seconds}
        )
        resp.raise_for_status()
        signed = resp.json()["signedURL"]
        base = str(self._http.base_url).rstrip("/")
        return f"{base}{signed}" if signed.startswith("/") else f"{base}/{signed}"

    async def delete(self, keys: list[str]) -> None:
        if keys:
            resp = await self._http.request(
                "DELETE", f"/object/{self.bucket}", json={"prefixes": keys}
            )
            resp.raise_for_status()

    async def list_old(self, hours: int) -> list[str]:
        """Object keys older than `hours`, walking the user/job folder layout."""
        cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(hours=hours)
        old: list[str] = []
        for user_dir in await self._list(""):
            for job_dir in await self._list(user_dir):
                for obj in await self._list_objects(job_dir):
                    created = obj.get("created_at")
                    if (
                        created
                        and dt.datetime.fromisoformat(created.replace("Z", "+00:00")) < cutoff
                    ):
                        old.append(f"{job_dir}/{obj['name']}")
        return old

    async def _list_objects(self, prefix: str) -> list[dict]:
        resp = await self._http.post(
            f"/object/list/{self.bucket}", json={"prefix": prefix, "limit": 1000}
        )
        resp.raise_for_status()
        return resp.json()

    async def _list(self, prefix: str) -> list[str]:
        names = [o["name"] for o in await self._list_objects(prefix) if o.get("id") is None]
        return [f"{prefix}/{n}" if prefix else n for n in names]
