"""
Local Deal Vault & Unified Deal Room Store.

Provides zero-config local persistence (`data/outputs/deal_room/*.json`) so the
Deal Room, history, and side-by-side Deal Comparison work out of the box even
without a Supabase project. When Supabase IS configured, saves write to Supabase
and list/open transparently merge both backends (deduplicated by id).
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config.settings import DEAL_ROOM_DIR, Settings, resolve_settings
from db import supabase_client
from schemas.report_schema import DueDiligenceReport

logger = logging.getLogger(__name__)


def _vault_dir(base_dir: Optional[Path] = None) -> Path:
    d = base_dir or DEAL_ROOM_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _index_entry(deal_id: str, report: DueDiligenceReport, created_at: str, storage: str) -> dict:
    d = report.deal
    es = report.executive_summary
    return {
        "id": deal_id,
        "entity_a": d.entity_a_name,
        "entity_b": d.entity_b_name,
        "deal_type": d.deal_type,
        "deal_value_usd_b": d.deal_value_usd_b,
        "target_synergy_npv_usd_b": d.target_synergy_npv_usd_b,
        "recommendation": es.recommendation.value,
        "complexity": es.integration_complexity.value,
        "conviction_score": d.overall_conviction_score,
        "industry_sector": d.industry_sector or "",
        "created_at": created_at,
        "storage": storage,
    }


def save_local_report(report: DueDiligenceReport, base_dir: Optional[Path] = None) -> str:
    """Persist a report as a JSON envelope in the local Deal Vault."""
    vdir = _vault_dir(base_dir)
    deal_id = f"local_{uuid.uuid4().hex[:12]}"
    created_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "meta": _index_entry(deal_id, report, created_at, storage="local"),
        "report_json": report.model_dump(mode="json"),
    }
    target = vdir / f"{deal_id}.json"
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return deal_id


def list_local_deals(limit: int = 50, base_dir: Optional[Path] = None) -> list[dict]:
    """Return lightweight metadata rows for all deals in the local Deal Vault."""
    vdir = _vault_dir(base_dir)
    items: list[dict] = []
    for path in vdir.glob("*.json"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            meta = raw.get("meta")
            if meta and isinstance(meta, dict):
                meta.setdefault("storage", "local")
                items.append(meta)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Skipping corrupted vault file {path.name}: {e}")
    items.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return items[:limit]


def load_local_report(deal_id: str, base_dir: Optional[Path] = None) -> Optional[DueDiligenceReport]:
    """Rehydrate a DueDiligenceReport from the local Deal Vault."""
    vdir = _vault_dir(base_dir)
    # Prevent path traversal
    safe_id = Path(deal_id).name
    path = vdir / f"{safe_id}.json"
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return DueDiligenceReport.model_validate(raw["report_json"])
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to load local report {deal_id}: {e}")
        return None


def delete_local_report(deal_id: str, base_dir: Optional[Path] = None) -> bool:
    """Delete a local Deal Vault entry."""
    vdir = _vault_dir(base_dir)
    safe_id = Path(deal_id).name
    path = vdir / f"{safe_id}.json"
    if path.exists():
        path.unlink()
        return True
    return False


# =====================================================================
# Unified Deal Room Facade (Supabase primary + Local Vault automatic fallback)
# =====================================================================

def save_to_deal_room(
    report: DueDiligenceReport,
    config: Optional[Settings] = None,
    base_dir: Optional[Path] = None,
) -> tuple[str, str]:
    """
    Save report to Supabase if configured & reachable, otherwise Local Deal Vault.
    Returns `(deal_id, backend)` where backend is `"supabase"` or `"local"`.
    """
    cfg = resolve_settings(config)
    if cfg.is_supabase_configured:
        rid = supabase_client.save_report(report, config=cfg)
        if rid:
            return rid, "supabase"
    local_id = save_local_report(report, base_dir=base_dir)
    return local_id, "local"


def list_deal_room(
    limit: int = 30,
    config: Optional[Settings] = None,
    base_dir: Optional[Path] = None,
) -> list[dict]:
    """List saved deals from Supabase (if configured) merged with Local Vault."""
    cfg = resolve_settings(config)
    rows: list[dict] = []
    if cfg.is_supabase_configured:
        rows.extend(supabase_client.fetch_recent_deals(limit=limit, config=cfg))
    rows.extend(list_local_deals(limit=limit, base_dir=base_dir))
    rows.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return rows[:limit]


def load_from_deal_room(
    deal_id: str,
    storage: str = "auto",
    config: Optional[Settings] = None,
    base_dir: Optional[Path] = None,
) -> Optional[DueDiligenceReport]:
    """Lazily load a single Deal Room report by id and storage backend."""
    if storage == "local" or deal_id.startswith("local_"):
        return load_local_report(deal_id, base_dir=base_dir)
    if storage == "supabase":
        return supabase_client.fetch_report(deal_id, config=config)
    # Auto fallback
    rep = supabase_client.fetch_report(deal_id, config=config)
    if rep is not None:
        return rep
    return load_local_report(deal_id, base_dir=base_dir)


def delete_from_deal_room(
    deal_id: str,
    storage: str = "auto",
    config: Optional[Settings] = None,
    base_dir: Optional[Path] = None,
) -> bool:
    """Delete a saved deal from its respective backend."""
    if storage == "local" or deal_id.startswith("local_"):
        return delete_local_report(deal_id, base_dir=base_dir)
    return supabase_client.delete_report(deal_id, config=config)
