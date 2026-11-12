"""Ingestion-specific tunables (env prefix ``INGEST_``; also read from the repo ``.env``).

The Ollama URL and model names live in ``app.core.config.Settings`` (shared contract);
everything here has sane defaults and is optional.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config import REPO_ROOT


class IngestSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(REPO_ROOT / ".env"), env_prefix="INGEST_", extra="ignore")

    # --- LLM (Ollama) ---
    llm_timeout_seconds: float = 180.0      # per request (first call includes model load)
    llm_status_timeout_seconds: float = 2.0
    llm_keep_alive: str = "30m"             # keep the model in RAM/VRAM between uploads
    llm_num_ctx: int = 8192                 # bounded context -> predictable RAM + speed
    llm_num_predict: int = 4096
    llm_concurrency: int = 2                # parallel chunk requests (see OLLAMA_NUM_PARALLEL)
    llm_chunk_chars: int = 6000             # ~2k tokens of German statement text per chunk
    llm_max_chunks: int = 40
    llm_retries: int = 1                    # extra attempts after malformed JSON
    # --- extraction ---
    pdf_min_chars_per_page: int = 40        # below -> treat as scanned, go to OCR/vision
    pdf_render_scale: float = 2.0           # 144 dpi
    pdf_max_pages: int = 60
    image_max_side: int = 1800              # px, downscale before sending to the vision model
    ocr_enabled: bool = True                # use Tesseract when installed
    ocr_lang: str = "deu+eng"
    # --- processing ---
    workers: int = 2
    max_upload_mb: int = 40
    duplicate_window_days: int = 4          # trade date vs. booking/valuta date drift


@lru_cache
def get_ingest_settings() -> IngestSettings:
    return IngestSettings()
