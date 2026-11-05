"""One email when a job finishes or fails, sent as the Gmail account over SMTP."""

import html
from email.message import EmailMessage

import aiosmtplib

SUBJECTS = {
    "started": {
        "en": "ClipForge is working on your video",
        "pt": "O ClipForge está processando seu vídeo",
        "es": "ClipForge está procesando tu vídeo",
    },
    "done": {
        "en": "ClipForge short {i} of {n}: {title}",
        "pt": "Short {i} de {n} do ClipForge: {title}",
        "es": "Short {i} de {n} de ClipForge: {title}",
    },
    "failed": {
        "en": "ClipForge could not make your shorts",
        "pt": "O ClipForge não conseguiu criar seus shorts",
        "es": "ClipForge no pudo crear tus shorts",
    },
}
TEXTS = {
    "en": {
        "started": "We received your video and started making shorts. This usually takes a few "
        "minutes; you will get another email with the links when it is done.",
        "hi": "Hi {name},",
        "ready": "Short {i} of {n} is ready. The link works for {hours} hours.",
        "zip": "Download all as a zip",
        "open": "Open {app} to download them.",
        "failed": "Something went wrong with your video: {reason}",
        "again": "You can try again at {app}.",
    },
    "pt": {
        "started": "Recebemos seu vídeo e começamos a criar os shorts. Costuma levar alguns "
        "minutos; você receberá outro e-mail com os links quando terminar.",
        "hi": "Olá {name},",
        "ready": "O short {i} de {n} está pronto. O link funciona por {hours} horas.",
        "zip": "Baixar todos em um zip",
        "open": "Abra {app} para baixá-los.",
        "failed": "Algo deu errado com o seu vídeo: {reason}",
        "again": "Você pode tentar de novo em {app}.",
    },
    "es": {
        "started": "Recibimos tu vídeo y empezamos a crear los shorts. Suele tardar unos "
        "minutos; recibirás otro correo con los enlaces cuando termine.",
        "hi": "Hola {name},",
        "ready": "El short {i} de {n} está listo. El enlace funciona durante {hours} horas.",
        "zip": "Descargar todo en un zip",
        "open": "Abre {app} para descargarlos.",
        "failed": "Algo salió mal con tu vídeo: {reason}",
        "again": "Puedes intentarlo de nuevo en {app}.",
    },
}


def render_short(
    lang: str, name: str, short: dict, i: int, n: int, hours: int, app_url: str, zip_url: str | None
) -> str:
    """One short per email: its title and link; the last one also carries the zip link."""
    t = TEXTS.get(lang, TEXTS["en"])
    title = html.escape(short["title"])
    if short.get("url"):
        link = f'<p><a href="{html.escape(short["url"])}">{title}</a> ({short["duration"]}s)</p>'
    else:
        open_app = t["open"].format(app=html.escape(app_url))
        link = f"<p>{title} ({short['duration']}s)</p><p>{open_app}</p>"
    zip_part = (
        f'<p><a href="{html.escape(zip_url)}">{t["zip"]}</a></p>' if zip_url and i == n else ""
    )
    return (
        f"<p>{t['hi'].format(name=html.escape(name or ''))}</p>"
        f"<p>{t['ready'].format(i=i, n=n, hours=hours)}</p>{link}{zip_part}"
    )


def render_started(lang: str, name: str) -> str:
    t = TEXTS.get(lang, TEXTS["en"])
    return f"<p>{t['hi'].format(name=html.escape(name or ''))}</p><p>{t['started']}</p>"


def render_failed(lang: str, name: str, reason: str, app_url: str) -> str:
    t = TEXTS.get(lang, TEXTS["en"])
    return (
        f"<p>{t['hi'].format(name=html.escape(name or ''))}</p>"
        f"<p>{t['failed'].format(reason=html.escape(reason))}</p>"
        f"<p>{t['again'].format(app=html.escape(app_url))}</p>"
    )


class Mailer:
    """Sends as the Gmail account itself over SMTP with an App Password (2-step verification).

    Disabled, and silently a no-op, when the credentials are not set.
    """

    def __init__(self, user: str, app_password: str, app_url: str, hours: int):
        self.user = user
        self.password = app_password.replace(" ", "")
        self.sender = f"ClipForge <{user}>"
        self.app_url = app_url
        self.hours = hours
        self.enabled = bool(user and app_password)

    async def aclose(self) -> None:
        return None

    def build(self, to: str, subject: str, body_html: str) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = self.sender
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content("Open this email in an HTML-capable client.")
        msg.add_alternative(body_html, subtype="html")
        return msg

    async def _send(self, to: str, subject: str, body_html: str) -> None:
        await aiosmtplib.send(
            self.build(to, subject, body_html),
            hostname="smtp.gmail.com",
            port=465,
            use_tls=True,  # implicit TLS; STARTTLS on 587 is cut by some networks
            username=self.user,
            password=self.password,
            timeout=30,
        )

    async def send_started(self, to: str, name: str, lang: str) -> None:
        if not self.enabled:
            return
        subject = SUBJECTS["started"].get(lang, SUBJECTS["started"]["en"])
        await self._send(to, subject, render_started(lang, name))

    async def send_done(
        self, to: str, name: str, lang: str, shorts: list[dict], zip_url: str | None
    ) -> None:
        """One email per short, in order; the last one also links the zip."""
        if not self.enabled:
            return
        n = len(shorts)
        for i, short in enumerate(shorts, start=1):
            subject = SUBJECTS["done"].get(lang, SUBJECTS["done"]["en"])
            subject = subject.format(i=i, n=n, title=short["title"])
            body = render_short(lang, name, short, i, n, self.hours, self.app_url, zip_url)
            await self._send(to, subject, body)

    async def send_failed(self, to: str, name: str, lang: str, reason: str) -> None:
        if not self.enabled:
            return
        subject = SUBJECTS["failed"].get(lang, SUBJECTS["failed"]["en"])
        await self._send(to, subject, render_failed(lang, name, reason, self.app_url))
