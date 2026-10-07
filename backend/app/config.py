from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://marketing:marketing@localhost:5432/marketing_os"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5-5"
    ai_provider: str = "anthropic"  # anthropic | fake
    ai_max_output_tokens: int = 4096


settings = Settings()
