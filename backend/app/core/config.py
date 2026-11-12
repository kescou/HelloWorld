from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(REPO_ROOT / ".env"), extra="ignore")

    database_url: str = "postgresql+psycopg://wealth:wealth@localhost:5432/wealth"
    upload_dir: Path = REPO_ROOT / "data" / "uploads"
    ollama_url: str = "http://localhost:11434"
    ollama_text_model: str = "qwen2.5:7b-instruct"
    ollama_vision_model: str = "qwen2.5vl:7b"
    base_currency: str = "EUR"
    enable_scheduler: bool = True
    quote_refresh_seconds: int = 120
    frontend_dist: Path = REPO_ROOT / "frontend" / "dist"


@lru_cache
def get_settings() -> Settings:
    return Settings()
