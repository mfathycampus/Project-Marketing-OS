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
    design_provider: str = "auto"  # auto (Chromium, falls back to Pillow) | html (same as auto) | pillow | canva
    encryption_key: str = ""  # Fernet key, required for Canva token storage
    canva_client_id: str = ""
    canva_client_secret: str = ""
    canva_redirect_uri: str = "http://127.0.0.1:8000/api/v1/canva/callback"
    canva_api_base: str = "https://api.canva.com/rest/v1"
    canva_template_map: dict = {}  # {"offer_square": {"brand_template_id": "...", "fields": {"headline": "HEADLINE"}}}
    canva_poll_timeout_s: int = 60
    worker_enabled: bool = True
    meta_app_id: str = ""
    meta_app_secret: str = ""
    meta_redirect_uri: str = "http://localhost:8000/api/v1/meta/callback"
    meta_graph_version: str = "v23.0"
    meta_scopes: str = "pages_show_list,pages_read_engagement,pages_manage_posts,instagram_basic,instagram_content_publish"
    linkedin_client_id: str = ""
    linkedin_client_secret: str = ""
    linkedin_redirect_uri: str = "http://localhost:8000/api/v1/linkedin/callback"
    linkedin_api_version: str = "202510"  # YYYYMM; LinkedIn retires old versions after ~a year
    linkedin_scopes: str = "r_organization_social w_organization_social rw_organization_admin"
    meta_config_id: str = ""  # only for 'Facebook Login for Business' apps (replaces scopes)
    azure_speech_key: str = ""  # Azure AI Speech (free tier F0: 0.5M characters/month) -> Arabic voiceover
    azure_speech_region: str = ""  # e.g. westeurope / eastus / uaenorth (from the resource's "Keys and Endpoint")
    tts_voice: str = "ar-SA-HamedNeural"  # ar-SA-ZariyahNeural, ar-AE-FatimaNeural, ar-AE-HamdanNeural, ar-KW-FahedNeural ...
    tts_rate: str = "+8%"  # speaking speed; slightly fast reads better in short social video
    voice_provider: str = "auto"  # auto (elevenlabs > azure > edge) | elevenlabs | azure | edge | off
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""  # from your ElevenLabs "My Voices" (python -m app.video.voice lists them)
    elevenlabs_model: str = "eleven_multilingual_v2"  # supports Arabic
    whisper_model: str = "small"  # speech-to-text for "your face" videos: tiny | base | small | medium (bigger = better, slower)
    pexels_api_key: str = ""  # free key from pexels.com/api -> real footage behind video scenes
    pixabay_api_key: str = ""  # alternative footage source (free key: pixabay.com/api/docs) when Pexels is unavailable
    public_base_url: str = ""  # optional; without it Instagram images are hosted via the linked Facebook Page
    publish_provider: str = "manual"  # manual | dryrun (real platform providers plug in here)
    publish_poll_interval_s: float = 5.0
    metrics_refresh_hours: float = 6.0  # how often results of published Meta posts are fetched (0 = never)
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

    @field_validator("encryption_key", "canva_client_id", "canva_client_secret", "anthropic_api_key", "meta_app_id", "meta_app_secret", "linkedin_client_id", "linkedin_client_secret", "azure_speech_key", "pexels_api_key", "pixabay_api_key", "elevenlabs_api_key", "elevenlabs_voice_id", mode="before")
    @classmethod
    def _clean_secret(cls, v):
        """Tolerate quotes, stray whitespace and inline '# comments' pasted into .env."""
        if not isinstance(v, str):
            return v
        v = v.split(" #")[0].strip().strip("\"'").strip()
        return v


settings = Settings()
