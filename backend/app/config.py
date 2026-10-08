from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ROOT = Path(__file__).resolve().parents[2]  # repo root; .env may live here or in backend/
_BACKEND = _ROOT / "backend"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(_ROOT / ".env", _ROOT / "backend" / ".env"), extra="ignore",
        env_file_encoding="utf-8-sig",  # Windows Notepad may add a BOM
    )

    database_url: str = "postgresql+psycopg://marketing:marketing@localhost:5432/marketing_os"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5-5"
    ai_provider: str = "anthropic"  # anthropic | fake
    ai_max_output_tokens: int = 16000  # includes thinking tokens
    ai_effort: str = "medium"  # low | medium | high | xhigh | max


    storage_dir: str = "./var/storage"
    design_provider: str = "html"  # html | canva
    encryption_key: str = ""  # Fernet key, required for Canva token storage
    canva_client_id: str = ""
    canva_client_secret: str = ""
    canva_redirect_uri: str = "http://127.0.0.1:8000/api/v1/canva/callback"
    canva_api_base: str = "https://api.canva.com/rest/v1"
    canva_template_map: dict = {}  # {"offer_square": {"brand_template_id": "...", "fields": {"headline": "HEADLINE"}}}
    canva_poll_timeout_s: int = 60
    worker_enabled: bool = True
    publish_provider: str = "manual"  # manual | dryrun (real platform providers plug in here)
    publish_poll_interval_s: float = 5.0
    publish_default_time: str = "19:00"  # local project time
    publish_max_attempts: int = 3
    auto_migrate: bool = True  # sqlite only: create/upgrade tables on startup
    worker_poll_interval_s: float = 1.0

    @field_validator("database_url", mode="after")
    @classmethod
    def _anchor_sqlite_path(cls, v: str) -> str:
        """A relative sqlite path must not depend on the directory uvicorn was started from."""
        prefix = "sqlite:///"
        if v.startswith(prefix):
            path = v[len(prefix):]
            if path and path != ":memory:" and not Path(path).is_absolute() and not path.startswith("/"):
                return prefix + (_BACKEND / path).as_posix()
        return v

    @field_validator("encryption_key", "canva_client_id", "canva_client_secret", "anthropic_api_key", mode="before")
    @classmethod
    def _clean_secret(cls, v):
        """Tolerate quotes, stray whitespace and inline '# comments' pasted into .env."""
        if not isinstance(v, str):
            return v
        v = v.split(" #")[0].strip().strip("\"'").strip()
        return v


settings = Settings()
