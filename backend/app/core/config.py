from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
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
    #: additional host names the app answers to (comma separated), besides localhost / 127.0.0.1 / [::1].
    #: Needed when the app is opened under another name, e.g. `ALLOWED_HOSTS=nas.local,192.168.1.20`; `*` = any.
    allowed_hosts: str = ""

    @property
    def allowed_host_set(self) -> set[str]:
        return {"localhost", "127.0.0.1", "::1"} | {h.strip().lower().strip("[]") for h in self.allowed_hosts.split(",") if h.strip()}

    @field_validator("upload_dir", "frontend_dist", mode="after")
    @classmethod
    def _absolute(cls, value: Path) -> Path:
        """Relative paths (e.g. UPLOAD_DIR=./data/uploads in .env) are relative to the repo root,
        not to the directory uvicorn happens to be started from."""
        value = Path(value).expanduser()
        return value if value.is_absolute() else (REPO_ROOT / value).resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()
