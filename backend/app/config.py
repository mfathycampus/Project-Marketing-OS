from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_ROOT = Path(__file__).resolve().parents[2]  # repo root; .env may live here or in backend/


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(_ROOT / ".env", _ROOT / "backend" / ".env"), extra="ignore"
    )

    database_url: str = "postgresql+psycopg://marketing:marketing@localhost:5432/marketing_os"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5-5"
    ai_provider: str = "anthropic"  # anthropic | fake
    ai_max_output_tokens: int = 4096


    storage_dir: str = "./var/storage"
    design_provider: str = "html"  # html | canva
    encryption_key: str = ""  # Fernet key, required for Canva token storage
    canva_client_id: str = ""
    canva_client_secret: str = ""
    canva_redirect_uri: str = "http://127.0.0.1:8000/api/v1/canva/callback"
    canva_api_base: str = "https://api.canva.com/rest/v1"
    canva_template_map: dict = {}  # {"offer_square": {"brand_template_id": "...", "fields": {"headline": "HEADLINE"}}}
    canva_poll_timeout_s: int = 60


settings = Settings()
