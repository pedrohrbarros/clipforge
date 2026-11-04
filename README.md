# ClipForge API

Turns one uploaded video into up to five captioned, vertical (9:16) shorts ready for
TikTok, Reels or YouTube Shorts. No accounts, no database: jobs live in memory and their
files are deleted after one hour.

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
| `POST` | `/jobs` | multipart `video` (≤ 500 MB, ≤ 10 min) plus `captions` (bool, default `true`). The model decides how many shorts to return, from 1 to 5. Returns `202 {"id"}`. |
| `GET` | `/jobs/{id}` | `{id, status, progress, message, code, shorts[]}`. `code` is a stable failure code (`quota`, `auth`, `too_long`, `no_speech`, `select_failed`, `unreadable`, `transcript_too_long`, `ai_error`, `error`) the UI translates; `message` is the English fallback. 4xx errors use `detail: {code, message}` the same way. Status: `queued, transcribing, selecting, rendering, done, failed`. |
| `GET` | `/jobs/{id}/shorts/{n}` | Streams one short as `video/mp4`. |
| `GET` | `/jobs/{id}/zip` | Zip of all shorts (only when `done`). |
| `GET` | `/usage` | Remaining Groq quota per model (`chat`, `whisper`). |
| `GET` | `/health` | `{"ok": true}`. |

Interactive docs at `/docs` when running.

## Authentication

Every endpoint except `/health` requires the shared secret from `AUTHORIZATION_KEY`:

```
Authorization: Bearer <AUTHORIZATION_KEY>
```

Media URLs that a browser loads directly (`/jobs/{id}/shorts/{n}` and `/jobs/{id}/zip`) also
accept it as a query parameter, `?token=<AUTHORIZATION_KEY>`, because `<video>` tags and
download links cannot send headers. Missing or wrong keys get `401` with
`{"detail": {"code": "unauthorized"}}`; a server without the variable set answers `503`.
The UI asks for the key once and keeps it in the browser.

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
| `AUTHORIZATION_KEY` | | required; the shared secret every request must send (see Authentication) |
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
3. Set `GROQ_API_KEY`, `AUTHORIZATION_KEY` and `ALLOWED_ORIGINS` (your Vercel URL, e.g.
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
- Processing is CPU-only on Render's free plan: expect about real-time speed, so a
  ten-minute video takes a few minutes.
