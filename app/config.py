"""
Application settings. Values come from environment or .env file.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    database_url: str = "sqlite:///./data/geo.db"
    # When DATABASE_URL points at Turso (sqlite+libsql://...) supply the auth token here.
    turso_auth_token: str = ""
    cors_origins: str = "*"
    monthly_cost_cap_usd: float = 20.0

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
