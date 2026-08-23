from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "agri-agent-service"
    app_env: str = "production"
    database_url: str = "sqlite:///./agri_agent.db"
    java_base_url: str = "http://127.0.0.1:8080"
    java_service_token: str = "change-me"
    java_timeout_seconds: float = 3.0
    allow_test_demo_fallback: bool = False
    max_agent_hops: int = 2
    human_confirmation_purchase_amount: float = 50_000.0

    model_config = SettingsConfigDict(env_file=".env", env_prefix="AGRI_", extra="ignore")

    @property
    def demo_fallback_enabled(self) -> bool:
        return self.app_env.lower() == "test" and self.allow_test_demo_fallback


@lru_cache
def get_settings() -> Settings:
    return Settings()

