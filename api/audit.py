"""
Cryptographically Hash-Chained WORM (Write-Once-Read-Many) Audit Trail.

Every security-relevant action (`UPLOAD`, `VIEW`, `ANALYZE`, `CLAIM_VERIFY`,
`IC_VOTE`, `EXPORT`, `CLEAN_TEAM_ACCESS`, `LOGIN`) is appended to a tamper-evident
SQLite ledger where each row commits to:

    entry_hash = SHA256(prev_hash + canonical_payload)

`verify_chain()` recomputes every hash from genesis (`"0" * 64`) to tip and detects
any out-of-band modification, deletion, reordering, or insertion.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from config.settings import OUTPUT_DIR

AUDIT_ACTIONS: tuple[str, ...] = (
    "UPLOAD",
    "VIEW",
    "ANALYZE",
    "CLAIM_VERIFY",
    "IC_VOTE",
    "EXPORT",
    "CLEAN_TEAM_ACCESS",
    "LOGIN",
)

GENESIS_HASH: str = "0" * 64
DEFAULT_AUDIT_DB = OUTPUT_DIR / "audit_trail.db"


def _canonical_payload(
    *,
    timestamp: str,
    tenant_id: str,
    actor: str,
    role: str,
    deal_id: str,
    action: str,
    metadata_json: str,
) -> str:
    payload = {
        "action": action,
        "actor": actor,
        "deal_id": deal_id,
        "metadata_json": metadata_json,
        "role": role,
        "tenant_id": tenant_id,
        "timestamp": timestamp,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _compute_entry_hash(prev_hash: str, canonical_payload: str) -> str:
    return hashlib.sha256((prev_hash + canonical_payload).encode("utf-8")).hexdigest()


class AuditLogger:
    """
    Append-only SQLite WORM audit logger with SHA-256 hash chaining.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else DEFAULT_AUDIT_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    @contextmanager
    def _connect(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=15.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield conn
            if conn.in_transaction:
                conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect(immediate=True) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    role TEXT NOT NULL,
                    deal_id TEXT NOT NULL DEFAULT '',
                    action TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    prev_hash TEXT NOT NULL,
                    entry_hash TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_tenant ON audit_log(tenant_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_deal ON audit_log(deal_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action)")

    def log_event(
        self,
        action: str,
        actor: str,
        role: str,
        *,
        tenant_id: str = "default",
        deal_id: str = "",
        metadata: Optional[dict[str, Any]] = None,
        timestamp: Optional[str] = None,
    ) -> dict[str, Any]:
        """Append a single tamper-evident event to the hash chain."""
        norm_action = str(action).strip().upper()
        norm_role = (role.value if hasattr(role, "value") else str(role)).strip().upper()
        ts = timestamp or datetime.now(timezone.utc).isoformat()
        meta_json = json.dumps(metadata or {}, sort_keys=True, separators=(",", ":"))

        with self._lock:
            with self._connect(immediate=True) as conn:
                last = conn.execute(
                    "SELECT entry_hash FROM audit_log ORDER BY id DESC LIMIT 1"
                ).fetchone()
                prev_hash = last["entry_hash"] if last else GENESIS_HASH
                canonical = _canonical_payload(
                    timestamp=ts,
                    tenant_id=tenant_id,
                    actor=actor,
                    role=norm_role,
                    deal_id=deal_id or "",
                    action=norm_action,
                    metadata_json=meta_json,
                )
                entry_hash = _compute_entry_hash(prev_hash, canonical)
                cur = conn.execute(
                    """
                    INSERT INTO audit_log (
                        timestamp, tenant_id, actor, role, deal_id,
                        action, metadata_json, prev_hash, entry_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (ts, tenant_id, actor, norm_role, deal_id or "", norm_action, meta_json, prev_hash, entry_hash),
                )
                row_id = cur.lastrowid

        return {
            "id": row_id,
            "timestamp": ts,
            "tenant_id": tenant_id,
            "actor": actor,
            "role": norm_role,
            "deal_id": deal_id or "",
            "action": norm_action,
            "metadata": json.loads(meta_json),
            "metadata_json": meta_json,
            "prev_hash": prev_hash,
            "entry_hash": entry_hash,
        }

    def query_events(
        self,
        *,
        tenant_id: Optional[str] = None,
        deal_id: Optional[str] = None,
        actor: Optional[str] = None,
        action: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if tenant_id:
            clauses.append("tenant_id = ?")
            params.append(tenant_id)
        if deal_id:
            clauses.append("deal_id = ?")
            params.append(deal_id)
        if actor:
            clauses.append("actor = ?")
            params.append(actor)
        if action:
            clauses.append("action = ?")
            params.append(action.strip().upper())

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, int(limit)))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM audit_log {where} ORDER BY id DESC LIMIT ?",
                params,
            ).fetchall()

        events: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            try:
                d["metadata"] = json.loads(d["metadata_json"] or "{}")
            except json.JSONDecodeError:
                d["metadata"] = {}
            events.append(d)
        return events

    def verify_chain(self) -> tuple[bool, int, str]:
        """
        Cryptographically verify the entire WORM hash chain from genesis to tip.

        Returns `(is_valid, entries_verified, message)`.
        """
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM audit_log ORDER BY id ASC").fetchall()

        expected_prev = GENESIS_HASH
        verified = 0
        for row in rows:
            if row["prev_hash"] != expected_prev:
                return (
                    False,
                    verified,
                    (
                        f"Hash-chain linkage broken at entry id={row['id']}: "
                        f"expected prev_hash={expected_prev[:12]}..., found={row['prev_hash'][:12]}..."
                    ),
                )
            canonical = _canonical_payload(
                timestamp=row["timestamp"],
                tenant_id=row["tenant_id"],
                actor=row["actor"],
                role=row["role"],
                deal_id=row["deal_id"],
                action=row["action"],
                metadata_json=row["metadata_json"],
            )
            recomputed = _compute_entry_hash(row["prev_hash"], canonical)
            if recomputed != row["entry_hash"]:
                return (
                    False,
                    verified,
                    (
                        f"Payload tampering detected at entry id={row['id']}: "
                        f"recomputed hash {recomputed[:12]}... != stored {row['entry_hash'][:12]}..."
                    ),
                )
            expected_prev = row["entry_hash"]
            verified += 1

        return (
            True,
            verified,
            f"Integrity verified: {verified} hash-chained audit entries intact.",
        )
