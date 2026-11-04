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
| `POST` | `/jobs` | multipart `video` (≤ 500 MB) **or** `url` (a YouTube link), plus `captions` (bool, default `true`). Returns `202 {"id"}`. |
| `GET` | `/jobs/{id}` | `{id, status, progress, message, shorts[]}`. Status: `queued, downloading, transcribing, selecting, rendering, done, failed`. |
| `GET` | `/jobs/{id}/shorts/{n}` | Streams one short as `video/mp4`. |
| `GET` | `/jobs/{id}/zip` | Zip of all shorts (only when `done`). |
| `GET` | `/usage` | Remaining Groq quota per model (`chat`, `whisper`). |
| `GET` | `/health` | `{"ok": true}`. |

Interactive docs at `/docs` when running.

## YouTube links

When `/jobs` gets a `url`, the server fetches the video with
[yt-dlp](https://github.com/yt-dlp/yt-dlp) (up to 1080p H.264, audio merged by ffmpeg)
and then runs the same pipeline. Two caveats:

- YouTube frequently blocks requests from datacenter IPs such as Render's. It works from a
  laptop; on Render the job may fail with "YouTube is blocking this server with a bot check",
  in which case upload the file instead.
- YouTube changes often, and yt-dlp fixes follow within days. If links stop working,
  rebuild the image to pull the latest yt-dlp.

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
| `ALLOWED_ORIGINS` | `http://localhost:3000` | comma-separated CORS origins |
| `MAX_UPLOAD_MB` | `500` | upload limit |
| `JOB_TTL_SECONDS` | `3600` | how long results stay downloadable |
| `WHISPER_MODEL` | `whisper-large-v3-turbo` | |
| `CHAT_MODEL` | `openai/gpt-oss-120b` | |

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
3. Set `GROQ_API_KEY` and `ALLOWED_ORIGINS` (your Vercel URL, e.g.
   `https://clipforge-ui.vercel.app`) in the service's environment.
4. Put the Render URL into `NEXT_PUBLIC_API_URL` on the Vercel project.

## Free-tier limits to know

- Render free instances sleep after 15 minutes idle. The first request afterwards takes
  up to a minute, and any job in flight when the instance restarts is lost.
- Groq free tier caps audio per hour/day and tokens per minute. `/usage` shows the live
  numbers; a job that hits a limit fails with the reset time.
- Groq's free chat tier allows 8,000 tokens per minute, so the transcript sent to the model
  is capped at 20k characters (roughly 25 minutes of speech). Longer videos still work, but
  moments are chosen from the first part only.
- Processing is CPU-only on Render's free plan: expect about real-time speed, so a
  ten-minute video takes a few minutes.
