"""
Supabase persistence layer (Deal History).

Stores completed DueDiligenceReports so users can retrieve / re-open past analyses
in the "Deal Room" UI tab. Designed to FAIL SOFT: if Supabase isn't configured
or the credentials are invalid, every function degrades gracefully (returns
None / empty list) so the UI never crashes during a demo.

Public API:
    save_report(report)            -> Optional[str]      (row id)
    fetch_recent_deals(limit=20)   -> list[dict]         (lightweight rows)
    fetch_report(row_id)           -> Optional[DueDiligenceReport]

Expected table schema (run once in Supabase SQL editor):
    CREATE TABLE dd_reports (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        entity_a    TEXT,
        entity_b    TEXT,
        report_json JSONB NOT NULL,
        created_at  TIMESTAMPTZ DEFAULT now()
    );
"""
from __future__ import annotations

import logging
from typing import Optional

from config.settings import settings
from schemas.report_schema import DueDiligenceReport

logger = logging.getLogger(__name__)

_CLIENT = None
_DISABLED = False  # set True after a hard init failure so we don't keep retrying


def _get_client():
    """
    Lazily initialize the Supabase client; return None if unconfigured.

    Once init fails hard (e.g. invalid credentials), we mark persistence
    DISABLED for the session to avoid noisy repeated errors on every call.
    """
    global _CLIENT, _DISABLED
    if _DISABLED:
        return None
    if _CLIENT is not None:
        return _CLIENT
    if not settings.supabase_url or not settings.supabase_key:
        logger.info("Supabase not configured — persistence disabled.")
        _DISABLED = True
        return None
    try:
        from supabase import create_client
        _CLIENT = create_client(settings.supabase_url, settings.supabase_key)
        return _CLIENT
    except Exception as e:  # noqa: BLE001 - any init failure disables persistence
        logger.error(f"Supabase client init failed — persistence disabled for session: {e}")
        _DISABLED = True
        return None


def save_report(report: DueDiligenceReport) -> Optional[str]:
    """
    Insert a report into the `dd_reports` table.
    Returns the row id, or None if persistence was skipped/failed (fail-soft).
    """
    client = _get_client()
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


def fetch_recent_deals(limit: int = 20) -> list[dict]:
    """
    Return the most recent reports (lightweight projection for the Deal Room list).
    Each row: {id, entity_a, entity_b, created_at}. Returns [] on failure.
    """
    client = _get_client()
    if client is None:
        return []
    try:
        return (
            client.table("dd_reports")
            .select("id, entity_a, entity_b, created_at")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
            .data
        )
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to list recent deals: {e}")
        return []


def fetch_report(row_id: str) -> Optional[DueDiligenceReport]:
    """
    Fetch a single report by id and rehydrate it into a DueDiligenceReport.
    Returns None if not found, unconfigured, or revalidation fails.
    """
    client = _get_client()
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
        logger.error(f"Failed to fetch report {row_id}: {e}")
        return None
