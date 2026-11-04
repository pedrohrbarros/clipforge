# ClipForge API

Turns one uploaded video into up to five captioned, vertical (9:16) shorts ready for
TikTok, Reels or YouTube Shorts. Users sign in with Google, get 3 videos per day, receive an
email when their shorts are ready, and the files stay downloadable for 24 hours.

Frontend: [clipforge-ui](../clipforge-ui) (Next.js on Vercel).

## How it works

1. `ffmpeg` extracts mono 16 kHz audio from the upload (chunked under 24 MB if long).
2. Groq **Whisper large-v3-turbo** transcribes it with word timestamps.
3. Groq **GPT-OSS 120B** reads the timestamped transcript and picks up to five
   self-contained moments of 20 to 60 seconds, returned as strict JSON.
4. `ffmpeg` cuts each moment, center-crops to 9:16 at 1080x1920 and, when captions are
   on, burns an ASS subtitle track built from the word timestamps (3 to 4 words per line).

Everything runs on Groq's free tier. The API records Groq's rate-limit headers on every
call and exposes them on `/usage` so the UI can show how much quota is left.

## Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/me` | Signed-in user, `used_today`, `limit` and `resets_at`. |
| `POST` | `/jobs` | multipart `video` (≤ 500 MB, ≤ 10 min), `captions` (bool, default `true`), `lang` (`en`, `pt`, `es`, for the email). `429 daily_limit` past the daily quota. The model decides how many shorts to return, from 1 to 5. Returns `202 {"id"}`. |
| `GET` | `/jobs/{id}` | `{id, status, progress, message, code, shorts[], zip_url}`; each short carries a signed `url` valid 24 h. Owner only; survives API restarts via the database. `code` is a stable failure code (`quota`, `auth`, `too_long`, `no_speech`, `select_failed`, `unreadable`, `transcript_too_long`, `ai_error`, `error`) the UI translates; `message` is the English fallback. 4xx errors use `detail: {code, message}` the same way. Status: `queued, transcribing, selecting, rendering, done, failed`. |
| `GET` | `/jobs/{id}/shorts/{n}` | Local fallback stream when no storage bucket is configured. |
| `GET` | `/jobs/{id}/zip` | Local fallback zip when no storage bucket is configured. |
| `GET` | `/usage` | Remaining Groq quota per model (`chat`, `whisper`). |
| `GET` | `/health` | `{"ok": true}`. |

Interactive docs at `/docs` when running; the page itself is public, but trying a request
there needs a Google ID token: click **Authorize** and paste one (the web app's session
storage holds the current token under `clipforge-google`).

## Authentication

Sign-in is Google only. The web app obtains a Google ID token with Google Identity Services
and sends it on every request:

```
Authorization: Bearer <Google ID token>
```

The API verifies the token's signature, audience (`GOOGLE_CLIENT_ID`) and expiry with
`google-auth`, and takes the user's id, email and name from it. `/health`, `/docs`, `/redoc`
and `/openapi.json` are public. Missing or invalid tokens get `401 {"detail": {"code":
"unauthorized"}}`; a server without `GOOGLE_CLIENT_ID` answers `503 auth_unconfigured`.

Create the client at <https://console.cloud.google.com/apis/credentials>: OAuth client ID,
type **Web application**, authorized JavaScript origins `http://localhost:3000` and your Vercel
URL. No client secret is needed. The same id goes to the UI as `GOOGLE_CLIENT_ID`.

## Accounts, limits, storage and email

- **Database** (`DATABASE_CONNECTION_URL`): Postgres on Supabase through SQLAlchemy's async
  ORM (`app/models.py`). Tables `users` and `jobs` are created at startup. A user may start
  `DAILY_LIMIT` (3) jobs per UTC day; failed jobs do not count. Use Supabase's **Session
  pooler** connection string (Connect → Session pooler): the direct `db.<ref>.supabase.co`
  host is IPv6-only and unreachable from Render and most laptops.
- **Storage** (`SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_BUCKET`): finished shorts and
  a zip are uploaded to a private bucket (created on first run) under `<user>/<job>/`, and
  served through signed URLs valid `RETENTION_HOURS` (24). Local files are deleted after the
  upload. An hourly sweep removes objects older than the retention window. Without these
  variables the API serves files itself for one hour, as before.
- **Email** (`GMAIL_USER`, `GMAIL_APP_PASSWORD`, `APP_URL`): the user gets an email when a job
  starts, and another when it finishes with the titles and links, in the language they used
  the app in; a failed job gets a short email with the reason. Mail is sent as the Gmail account over SMTP. In that Google
  account turn on 2-Step Verification, then Security → App passwords → create one and use
  the 16-character value. Gmail allows roughly 500 messages a day. `APP_URL` is the link back
  to the app used in the emails.

## Requirements

- Python 3.12+
- `ffmpeg` with libass on the PATH (`brew install ffmpeg` on macOS)
- A free Groq API key from <https://console.groq.com/keys>

## Local development

```bash
make install            # creates .venv and installs deps
cp .env.example .env    # then put your GROQ_API_KEY in .env
make dev                # http://localhost:8000
```

Settings are read from the environment (and `.env` via pydantic-settings):

| Variable | Default | Purpose |
|---|---|---|
| `GROQ_API_KEY` | | required |
| `GOOGLE_CLIENT_ID` | | required; see Authentication |
| `DATABASE_CONNECTION_URL` | | Postgres session-pooler URL from Supabase |
| `SUPABASE_URL` | | `https://<ref>.supabase.co` |
| `SUPABASE_SERVICE_KEY` | | service_role key, Settings → API |
| `SUPABASE_BUCKET` | `shorts` | private bucket name |
| `GMAIL_USER` | | Gmail address to send from |
| `GMAIL_APP_PASSWORD` | | App Password for that account |
| `APP_URL` | first allowed origin | web app URL used in emails |
| `DAILY_LIMIT` | `3` | jobs per user per UTC day |
| `RETENTION_HOURS` | `24` | how long signed links and stored files last |
| `ALLOWED_ORIGINS` | `http://localhost:3000` | comma-separated CORS origins |
| `MAX_UPLOAD_MB` | `500` | upload limit |
| `MAX_VIDEO_SECONDS` | `600` | longest video accepted, checked before any Groq call |
| `JOB_TTL_SECONDS` | `3600` | how long results stay downloadable |
| `WHISPER_MODEL` | `whisper-large-v3-turbo` | |
| `CHAT_MODEL` | `openai/gpt-oss-120b` | |
| `CHAT_REASONING_EFFORT` | `low` | reasoning budget for the chat model; set empty for models without it |
| `CHAT_MAX_TOKENS` | `4096` | completion budget; reasoning tokens count against it |

## Tests and formatting

```bash
make test      # pytest, Groq and ffmpeg are mocked
make format    # ruff format + ruff check --fix
```

## Docker

```bash
docker build -t clipforge .
docker run -p 8000:8000 -e GROQ_API_KEY=... -e ALLOWED_ORIGINS=http://localhost:3000 clipforge
```

## Deploy on Render

1. Push this repo to GitHub.
2. In Render choose **New → Blueprint** and select the repo; `render.yaml` defines a free
   Docker web service.
3. Set `GROQ_API_KEY`, `GOOGLE_CLIENT_ID`, `DATABASE_CONNECTION_URL`, `SUPABASE_URL`,
   `SUPABASE_SERVICE_KEY`, `GMAIL_USER`, `GMAIL_APP_PASSWORD` and `ALLOWED_ORIGINS` (your Vercel URL, e.g.
   `https://clipforge-ui.vercel.app`) in the service's environment.
4. Put the Render URL into `NEXT_PUBLIC_API_URL` on the Vercel project.

## Free-tier limits to know

- Render free instances sleep after 15 minutes idle. The first request afterwards takes
  up to a minute, and any job in flight when the instance restarts is lost.
- Groq free tier caps audio per hour/day and tokens per minute. `/usage` shows the live
  numbers; a job that hits a limit fails with the reset time.
- Videos longer than 10 minutes are rejected before transcription (`MAX_VIDEO_SECONDS`), so a
  long upload cannot burn through the daily quota. Groq's free chat tier allows 8,000 tokens
  per minute; a 10-minute transcript fits comfortably.
- Processing is CPU-only on Render's free plan with 512 MB of memory: expect about real-time
  speed, so a ten-minute video takes a few minutes. If the instance runs out of memory it
  restarts and in-flight jobs are lost; the Render logs show "Out of memory" when that
  happens, and the UI reports lost contact.
