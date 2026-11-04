"""Transactional email through Resend: one message when a job finishes or fails."""

import html

import httpx

SUBJECTS = {
    "done": {
        "en": "Your {n} ClipForge shorts are ready",
        "pt": "Seus {n} shorts do ClipForge estão prontos",
        "es": "Tus {n} shorts de ClipForge están listos",
    },
    "failed": {
        "en": "ClipForge could not make your shorts",
        "pt": "O ClipForge não conseguiu criar seus shorts",
        "es": "ClipForge no pudo crear tus shorts",
    },
}
TEXTS = {
    "en": {
        "hi": "Hi {name},",
        "ready": "Your shorts are ready. The links work for {hours} hours.",
        "zip": "Download all as a zip",
        "open": "Open {app} to download them.",
        "failed": "Something went wrong with your video: {reason}",
        "again": "You can try again at {app}.",
    },
    "pt": {
        "hi": "Olá {name},",
        "ready": "Seus shorts estão prontos. Os links funcionam por {hours} horas.",
        "zip": "Baixar todos em um zip",
        "open": "Abra {app} para baixá-los.",
        "failed": "Algo deu errado com o seu vídeo: {reason}",
        "again": "Você pode tentar de novo em {app}.",
    },
    "es": {
        "hi": "Hola {name},",
        "ready": "Tus shorts están listos. Los enlaces funcionan durante {hours} horas.",
        "zip": "Descargar todo en un zip",
        "open": "Abre {app} para descargarlos.",
        "failed": "Algo salió mal con tu vídeo: {reason}",
        "again": "Puedes intentarlo de nuevo en {app}.",
    },
}


def _item(short: dict) -> str:
    title = html.escape(short["title"])
    label = f'<a href="{html.escape(short["url"])}">{title}</a>' if short.get("url") else title
    return f"<li>{label} ({short['duration']}s)</li>"


def render_done(
    lang: str, name: str, shorts: list[dict], zip_url: str | None, hours: int, app_url: str = ""
) -> str:
    """Links when the shorts were uploaded to storage; otherwise point at the app."""
    t = TEXTS.get(lang, TEXTS["en"])
    items = "".join(_item(s) for s in shorts)
    zip_part = f'<p><a href="{html.escape(zip_url)}">{t["zip"]}</a></p>' if zip_url else ""
    if not all(s.get("url") for s in shorts):
        zip_part += f"<p>{t['open'].format(app=html.escape(app_url))}</p>"
    return (
        f"<p>{t['hi'].format(name=html.escape(name or ''))}</p>"
        f"<p>{t['ready'].format(hours=hours)}</p><ul>{items}</ul>{zip_part}"
    )


def render_failed(lang: str, name: str, reason: str, app_url: str) -> str:
    t = TEXTS.get(lang, TEXTS["en"])
    return (
        f"<p>{t['hi'].format(name=html.escape(name or ''))}</p>"
        f"<p>{t['failed'].format(reason=html.escape(reason))}</p>"
        f"<p>{t['again'].format(app=html.escape(app_url))}</p>"
    )


class Mailer:
    def __init__(self, api_key: str, sender: str, app_url: str, hours: int):
        self.enabled = bool(api_key)
        self.sender = sender
        self.app_url = app_url
        self.hours = hours
        self._http = httpx.AsyncClient(
            base_url="https://api.resend.com",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _send(self, to: str, subject: str, body_html: str) -> None:
        resp = await self._http.post(
            "/emails", json={"from": self.sender, "to": [to], "subject": subject, "html": body_html}
        )
        resp.raise_for_status()

    async def send_done(
        self, to: str, name: str, lang: str, shorts: list[dict], zip_url: str | None
    ) -> None:
        if not self.enabled:
            return
        subject = SUBJECTS["done"].get(lang, SUBJECTS["done"]["en"]).format(n=len(shorts))
        body = render_done(lang, name, shorts, zip_url, self.hours, self.app_url)
        await self._send(to, subject, body)

    async def send_failed(self, to: str, name: str, lang: str, reason: str) -> None:
        if not self.enabled:
            return
        subject = SUBJECTS["failed"].get(lang, SUBJECTS["failed"]["en"])
        await self._send(to, subject, render_failed(lang, name, reason, self.app_url))
