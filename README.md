# ClipForge API

Turns one uploaded video into up to five captioned, vertical (9:16) shorts ready for
TikTok, Reels or YouTube Shorts. Users sign in with Google, get 3 videos per day, and can come back to the site for 180 days to check on a
job and download its shorts again.

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
| `POST` | `/auth/google` | `{credential}` (Google ID token) → `{token, expires_at, user}`; the session token for all other calls. |
| `GET` | `/me` | Signed-in user, `used_today`, `limit` and `resets_at`. |
| `GET` | `/jobs` | The signed-in user's jobs, newest first, with status (no links). |
| `POST` | `/jobs` | multipart `video` (≤ 500 MB, ≤ 10 min), `captions` (bool, default `true`). `429 daily_limit` past the daily quota. The model decides how many shorts to return, from 1 to 5. Returns `202 {"id"}`. |
| `GET` | `/jobs/{id}` | `{id, status, progress, message, code, shorts[], zip_url, stored, expired}`; when the job is done and stored, each short's `url` and `zip_url` are signed fresh for `LINK_HOURS`. Owner only; survives API restarts via the database. `code` is a stable failure code (`quota`, `auth`, `too_long`, `no_speech`, `select_failed`, `unreadable`, `transcript_too_long`, `ai_error`, `error`) the UI translates; `message` is the English fallback. 4xx errors use `detail: {code, message}` the same way. Status: `queued, transcribing, selecting, rendering, done, failed`. |
| `GET` | `/jobs/{id}/shorts/{n}` | Local fallback stream when no storage bucket is configured. |
| `GET` | `/jobs/{id}/zip` | Local fallback zip when no storage bucket is configured. |
| `GET` | `/usage` | Remaining Groq quota per model (`chat`, `whisper`). |
| `GET` | `/health` | `{"ok": true}`. |

Interactive docs at `/docs` when running; the page itself is public, but trying a request
there needs a session token: click **Authorize** and paste one (the web app keeps the
current one in `localStorage` under `clipforge-session`).

## Authentication

Sign-in is Google only. The web app obtains a Google ID token with Google Identity Services
and exchanges it once at `POST /auth/google`; the API verifies the token's signature,
audience (`GOOGLE_CLIENT_ID`) and expiry with `google-auth`, records the user, and returns a
session token signed with `SESSION_SECRET` that lasts `SESSION_HOURS` (24). Every other
request sends that session token:

```
Authorization: Bearer <session token>
```

An expired session answers `401 session_expired` and the UI shows the sign-in screen again. `/health`, `/docs`, `/redoc`
and `/openapi.json` are public. Missing or invalid tokens get `401 {"detail": {"code":
"unauthorized"}}`; a server without `GOOGLE_CLIENT_ID` answers `503 auth_unconfigured`.

Create the client at <https://console.cloud.google.com/apis/credentials>: OAuth client ID,
type **Web application**, authorized JavaScript origins `http://localhost:3000` and your Vercel
URL. No client secret is needed. The same id goes to the UI as `GOOGLE_CLIENT_ID`.

## Accounts, limits and storage

- **Database** (`DATABASE_CONNECTION_URL`): Postgres on Supabase through SQLAlchemy's async
  ORM (`app/models.py`). Tables `users` and `jobs` are created at startup. A user may start
  `DAILY_LIMIT` (3) jobs per UTC day; failed jobs do not count. Use Supabase's **Session
  pooler** connection string (Connect → Session pooler): the direct `db.<ref>.supabase.co`
  host is IPv6-only and unreachable from Render and most laptops.
- **Storage** (`SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_BUCKET`): finished shorts and
  a zip are uploaded to a private bucket (created on first run) under `<user>/<job>/`, and
  kept for `RETENTION_DAYS` (180). Opening a job signs fresh links valid `LINK_HOURS`; the
  keys are derived from the user and job ids, so nothing long-lived is stored. Local files
  are deleted right after the upload, and an hourly sweep removes objects older than the
  retention window. Note the free Supabase tier holds 1 GB, roughly 20 to 30 jobs; raise the
  plan or lower `RETENTION_DAYS` as usage grows. Without these variables the API serves files
  itself for one hour.

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
| `SESSION_SECRET` | | required; signs session tokens (Render generates one from `render.yaml`) |
| `SESSION_HOURS` | `24` | how long a login lasts |
| `DATABASE_CONNECTION_URL` | | Postgres session-pooler URL from Supabase |
| `SUPABASE_URL` | | `https://<ref>.supabase.co` |
| `SUPABASE_SERVICE_KEY` | | service_role key, Settings → API |
| `SUPABASE_BUCKET` | `shorts` | private bucket name |
| `DAILY_LIMIT` | `3` | jobs per user per UTC day |
| `RETENTION_DAYS` | `180` | how long finished shorts stay in storage and in the history |
| `LINK_HOURS` | `1` | validity of the download links signed when a job is opened |
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
3. Set `GROQ_API_KEY`, `GOOGLE_CLIENT_ID`, `SESSION_SECRET`, `DATABASE_CONNECTION_URL`, `SUPABASE_URL`,
   `SUPABASE_SERVICE_KEY` and `ALLOWED_ORIGINS` (your Vercel URL, e.g.
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
