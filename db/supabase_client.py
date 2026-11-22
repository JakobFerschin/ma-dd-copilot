"""
Supabase persistence layer (Cloud Deal Room).

Fixes the legacy `_DISABLED` permanent-lock bug:
  - Client caching is now keyed by `(supabase_url, supabase_key)`, so entering
    new credentials in the Streamlit sidebar immediately re-enables Supabase
    without restarting the server.
  - Accepts an optional session-scoped `config: Settings` so multi-user sessions
    never collide.
  - Still FAILS SOFT: unconfigured or invalid credentials return None / [] cleanly.

Public API:
    reset_client()                                   -> None
    save_report(report, config=None)                 -> Optional[str]
    fetch_recent_deals(limit=20, config=None)        -> list[dict]
    fetch_report(row_id, config=None)                -> Optional[DueDiligenceReport]
    delete_report(row_id, config=None)               -> bool
"""
from __future__ import annotations

import logging
from typing import Optional

from config.settings import Settings, resolve_settings
from schemas.report_schema import DueDiligenceReport

logger = logging.getLogger(__name__)

_CLIENT = None
_CLIENT_CREDS: Optional[tuple[str, str]] = None
_FAILED_CREDS: set[tuple[str, str]] = set()


def reset_client() -> None:
    """Clear cached client and failure memory (called when sidebar settings change)."""
    global _CLIENT, _CLIENT_CREDS
    _CLIENT = None
    _CLIENT_CREDS = None
    _FAILED_CREDS.clear()


def _get_client(config: Optional[Settings] = None):
    """
    Lazily initialize the Supabase client for the given credentials.
    Automatically re-initializes if credentials change; remembers failed credential
    pairs to prevent repeated slow timeouts on every Streamlit rerun.
    """
    global _CLIENT, _CLIENT_CREDS
    cfg = resolve_settings(config)
    url = (cfg.supabase_url or "").strip()
    key = (cfg.supabase_key or "").strip()

    if not url or not key:
        return None

    creds = (url, key)
    if creds in _FAILED_CREDS:
        return None
    if _CLIENT is not None and _CLIENT_CREDS == creds:
        return _CLIENT

    try:
        from supabase import create_client
        _CLIENT = create_client(url, key)
        _CLIENT_CREDS = creds
        return _CLIENT
    except Exception as e:  # noqa: BLE001 - fail-soft on bad creds / network
        logger.error(f"Supabase client init failed for {url}: {e}")
        _FAILED_CREDS.add(creds)
        return None


def save_report(report: DueDiligenceReport, config: Optional[Settings] = None) -> Optional[str]:
    """
    Insert a report into the `dd_reports` table.
    Returns the row id, or None if persistence was skipped/failed (fail-soft).
    """
    client = _get_client(config)
    if client is None:
        return None
    try:
        row = {
            "entity_a": report.deal.entity_a_name,
            "entity_b": report.deal.entity_b_name,
            "report_json": report.model_dump(mode="json"),
        }
        result = client.table("dd_reports").insert(row).execute()
        return result.data[0]["id"] if result.data else None
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to save report to Supabase: {e}")
        return None


def fetch_recent_deals(limit: int = 20, config: Optional[Settings] = None) -> list[dict]:
    """
    Return the most recent reports (lightweight projection for the Deal Room list).
    Returns [] on failure or when unconfigured.
    """
    client = _get_client(config)
    if client is None:
        return []
    try:
        rows = (
            client.table("dd_reports")
            .select("id, entity_a, entity_b, created_at")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
            .data
        )
        return [dict(r, storage="supabase") for r in (rows or [])]
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to list recent deals from Supabase: {e}")
        return []


def fetch_report(row_id: str, config: Optional[Settings] = None) -> Optional[DueDiligenceReport]:
    """
    Fetch a single report by id and rehydrate it into a DueDiligenceReport.
    Returns None if not found, unconfigured, or revalidation fails.
    """
    client = _get_client(config)
    if client is None:
        return None
    try:
        rows = (
            client.table("dd_reports")
            .select("report_json")
            .eq("id", row_id)
            .limit(1)
            .execute()
            .data
        )
        if not rows:
            return None
        return DueDiligenceReport.model_validate(rows[0]["report_json"])
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to fetch report {row_id} from Supabase: {e}")
        return None


def delete_report(row_id: str, config: Optional[Settings] = None) -> bool:
    """Delete a report by id from Supabase. Returns True on success."""
    client = _get_client(config)
    if client is None:
        return False
    try:
        client.table("dd_reports").delete().eq("id", row_id).execute()
        return True
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to delete report {row_id} from Supabase: {e}")
        return False
