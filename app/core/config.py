from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    app_name: str = "ProjectForge"
    app_env: str = "development"
    debug: bool = True

    database_url: str = "sqlite:///./project_forge.db"

    deepseek_api_key: str = Field(min_length=1)
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_default_model: str = "deepseek-v4-flash"
    deepseek_reasoning_model: str = "deepseek-v4-pro"

    daily_api_budget_usd: float = Field(default=0.50, ge=0)
    max_model_calls_per_run: int = Field(default=20, ge=1)
    max_repair_attempts: int = Field(default=3, ge=0)
    max_output_tokens: int = Field(default=4000, ge=256)

    generated_projects_dir: Path = Path("generated-projects")
    knowledge_vault_dir: Path = Path("knowledge-vault")

    auto_push: bool = False
    auto_merge: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    def create_required_directories(self) -> None:
        self.generated_projects_dir.mkdir(parents=True, exist_ok=True)
        self.knowledge_vault_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.create_required_directories()
    return settings