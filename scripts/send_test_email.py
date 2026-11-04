"""Send a test "shorts ready" email through the configured Gmail account.

Usage: .venv/bin/python scripts/send_test_email.py you@example.com
"""

import asyncio
import sys

from app.config import settings
from app.mailer import Mailer


async def main(to: str) -> None:
    mailer = Mailer(
        settings.gmail_user,
        settings.gmail_app_password,
        settings.public_app_url,
        settings.retention_hours,
    )
    if not mailer.enabled:
        sys.exit("GMAIL_USER / GMAIL_APP_PASSWORD are not set")
    shorts = [{"title": "Test short", "url": "https://example.com/1.mp4", "duration": 25.0}]
    await mailer.send_done(to, "Pedro", "en", shorts, None)
    print(f"sent a test email from {settings.gmail_user} to {to}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else settings.gmail_user))
