import tempfile
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    groq_api_key: str = ""
    # Shared secret every request must present as "Authorization: Bearer <key>".
    authorization_key: str = ""
    allowed_origins: str = "http://localhost:3000"
    max_upload_mb: int = 500
    max_video_seconds: int = 600
    job_ttl_seconds: int = 3600
    # Contents of a Netscape cookies.txt exported from a signed-in YouTube session, so
    # datacenter IPs pass YouTube's bot check. Use a throwaway account; it expires in weeks.
    youtube_cookies: str = ""
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
