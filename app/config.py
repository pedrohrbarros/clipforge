import tempfile
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    groq_api_key: str = ""
    # Google OAuth web client id; every request must carry a Google ID token for it.
    google_client_id: str = ""
    # Postgres (Supabase) for users and jobs. On hosts without IPv6 such as Render use the
    # Supabase "session pooler" connection string.
    database_connection_url: str = ""
    # Supabase Storage for finished shorts: project URL, service_role key, private bucket.
    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_bucket: str = "shorts"
    # Resend for the "your shorts are ready" email.
    resend_api_key: str = ""
    email_from: str = "ClipForge <shorts@example.com>"
    daily_limit: int = 3
    # Public URL of the web app, used in emails. Defaults to the first allowed origin.
    app_url: str = ""
    retention_hours: int = 24
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

    @property
    def public_app_url(self) -> str:
        return self.app_url or (self.origins[0] if self.origins else "")


settings = Settings()
