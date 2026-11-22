"""
Centralized configuration & secrets management.

Priority order for defaults:
  1. Session-scoped RuntimeConfig (Streamlit sidebar overrides — never mutates process globals)
  2. Streamlit secrets (when running inside Streamlit with secrets.toml configured)
  3. Environment variables / .env file
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = DATA_DIR / "outputs"
DEAL_ROOM_DIR = OUTPUT_DIR / "deal_room"

for _d in (UPLOAD_DIR, OUTPUT_DIR, DEAL_ROOM_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _get_secret_or_env(key: str, default: str = "") -> str:
    """Read from Streamlit secrets if available, falling back to os.environ."""
    val = os.getenv(key)
    if val is not None and val != "":
        return val
    try:
        import streamlit as st
        if hasattr(st, "secrets") and key in st.secrets:
            return str(st.secrets[key])
    except Exception:  # noqa: BLE001 - outside Streamlit runtime or no secrets.toml
        pass
    return default


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
    max_chars_to_llm: int = int(os.getenv("MAX_CHARS_TO_LLM", "140000"))  # ~35k tokens
    llm_timeout_seconds: int = int(os.getenv("LLM_TIMEOUT_SECONDS", "120"))
    llm_max_retries: int = int(os.getenv("LLM_MAX_RETRIES", "2"))

    @property
    def is_live_llm(self) -> bool:
        return bool(self.llm_api_key and self.llm_api_key.strip())

    @property
    def is_supabase_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_url.strip()
                    and self.supabase_key and self.supabase_key.strip())

    def clone(self) -> "Settings":
        """Create an isolated copy (used per Streamlit session to avoid cross-user mutation)."""
        return Settings(
            llm_api_key=self.llm_api_key,
            llm_base_url=self.llm_base_url,
            llm_model=self.llm_model,
            supabase_url=self.supabase_url,
            supabase_key=self.supabase_key,
            max_pages_per_pdf=self.max_pages_per_pdf,
            max_chars_to_llm=self.max_chars_to_llm,
            llm_timeout_seconds=self.llm_timeout_seconds,
            llm_max_retries=self.llm_max_retries,
        )


def load_runtime_settings() -> Settings:
    """Build a fresh Settings instance incorporating Streamlit secrets when present."""
    return Settings(
        llm_api_key=_get_secret_or_env("LLM_API_KEY", settings.llm_api_key),
        llm_base_url=_get_secret_or_env("LLM_BASE_URL", settings.llm_base_url),
        llm_model=_get_secret_or_env("LLM_MODEL", settings.llm_model or "gpt-4o"),
        supabase_url=_get_secret_or_env("SUPABASE_URL", settings.supabase_url),
        supabase_key=_get_secret_or_env("SUPABASE_KEY", settings.supabase_key),
        max_pages_per_pdf=int(_get_secret_or_env("MAX_PAGES_PER_PDF", str(settings.max_pages_per_pdf))),
        max_chars_to_llm=int(_get_secret_or_env("MAX_CHARS_TO_LLM", str(settings.max_chars_to_llm))),
        llm_timeout_seconds=int(_get_secret_or_env("LLM_TIMEOUT_SECONDS", str(settings.llm_timeout_seconds))),
        llm_max_retries=int(_get_secret_or_env("LLM_MAX_RETRIES", str(settings.llm_max_retries))),
    )


def resolve_settings(override: Optional[Settings] = None) -> Settings:
    """Return override if provided, otherwise fall back to the process default settings."""
    return override if override is not None else settings


settings = Settings()
