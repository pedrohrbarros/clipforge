import tempfile
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    groq_api_key: str = ""
    # Google OAuth web client id; sign-in exchanges a Google ID token for a session token.
    google_client_id: str = ""
    # Secret that signs session tokens (any long random string); sessions last SESSION_HOURS.
    session_secret: str = ""
    session_hours: int = 72
    # Postgres (Supabase) for users and jobs. On hosts without IPv6 such as Render use the
    # Supabase "session pooler" connection string.
    database_connection_url: str = ""
    # Supabase Storage for finished shorts: project URL, service_role key, private bucket.
    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_bucket: str = "shorts"
    daily_limit: int = 3
    # How long finished shorts stay in storage; links are signed fresh (1 h) whenever a job is
    # opened, so retention can be long.
    retention_days: int = 180
    link_hours: int = 1
    # Supabase's free tier rejects objects above 50 MB; a zip over that is skipped.
    max_object_mb: int = 50
    allowed_origins: str = "http://localhost:3000"
    max_upload_mb: int = 500
    max_video_seconds: int = 600
    job_ttl_seconds: int = 3600
    whisper_model: str = "whisper-large-v3-turbo"
    chat_model: str = "openai/gpt-oss-120b"
    # Reasoning models spend tokens thinking before the JSON; keep it short. Empty disables.
    chat_reasoning_effort: str = "low"
    chat_max_tokens: int = 4096
    work_dir: str = str(Path(tempfile.gettempdir()) / "clipforge")

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


settings = Settings()
