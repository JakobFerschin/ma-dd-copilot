"""
Centralized configuration & secrets management.
Loads from (in priority order): Streamlit secrets > .env > environment variables.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = DATA_DIR / "outputs"

for _d in (UPLOAD_DIR, OUTPUT_DIR):
    _d.mkdir(parents=True, exist_ok=True)


@dataclass
class Settings:
    # ---- LLM ----
    # OpenAI-compatible: works with OpenAI, Azure, OpenRouter, local (Ollama/vLLM via base_url)
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "")  # "" = OpenAI default
    llm_model: str = os.getenv("LLM_MODEL", "gpt-4o")

    # ---- Supabase ----
    supabase_url: str = os.getenv("SUPABASE_URL", "")
    supabase_key: str = os.getenv("SUPABASE_KEY", "")

    # ---- App behavior ----
    max_pages_per_pdf: int = int(os.getenv("MAX_PAGES_PER_PDF", "200"))
    max_chars_to_llm: int = int(os.getenv("MAX_CHARS_TO_LLM", "120000"))  # ~30k tokens


settings = Settings()
