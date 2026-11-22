"""
SQLite-backed Enterprise M&A Deal Execution Store (`EnterpriseWorkflowStore`).

Default persistence path: `data/outputs/enterprise_workflow.db` (configurable via
`db_path` argument for isolated testing and multi-tenant deployments).
Zero external database dependency — uses Python's built-in `sqlite3` in WAL mode.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from config.settings import OUTPUT_DIR

DEAL_STAGES: tuple[str, ...] = (
    "Origination",
    "NDA",
    "IOI / NBO",
    "Confirmatory DD",
    "IC Approval",
    "Signing",
    "Closing",
    "100-Day PMI",
)

QA_WORKSTREAMS: tuple[str, ...] = ("Finance", "Legal", "Tech", "Tax", "Commercial")
QA_PRIORITIES: tuple[str, ...] = ("Critical", "High", "Medium", "Low")
QA_STATUSES: tuple[str, ...] = ("Open", "Submitted to Seller", "Answered", "Verified", "Closed")

CLAIM_STATUSES: tuple[str, ...] = ("AI Draft", "Analyst Verified", "VP Approved", "Flagged")
VOTE_CHOICES: tuple[str, ...] = ("Approve", "Conditional Approve", "Reject", "Defer")
CLEAN_TEAM_ROLES: frozenset[str] = frozenset({"CLEAN_TEAM", "MD"})

DEFAULT_WORKFLOW_DB = OUTPUT_DIR / "enterprise_workflow.db"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_claim_hash(claim_text: str) -> str:
    """Stable 16-char SHA-256 fingerprint for a report Claim string."""
    normalized = " ".join((claim_text or "").strip().lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


class EnterpriseWorkflowStore:
    """
    Unified SQLite store for institutional M&A deal execution workflows.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else DEFAULT_WORKFLOW_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS deals_pipeline (
                    deal_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL DEFAULT 'default',
                    deal_name TEXT NOT NULL,
                    entity_a TEXT NOT NULL DEFAULT '',
                    entity_b TEXT NOT NULL DEFAULT '',
                    stage TEXT NOT NULL DEFAULT 'Origination',
                    deal_value_usd_b REAL,
                    target_signing_date TEXT,
                    diligence_coverage_pct REAL NOT NULL DEFAULT 0.0,
                    lead_banker TEXT NOT NULL DEFAULT '',
                    sector TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS vdr_qa (
                    qa_id TEXT PRIMARY KEY,
                    deal_id TEXT NOT NULL,
                    question TEXT NOT NULL,
                    workstream TEXT NOT NULL DEFAULT 'Finance',
                    priority TEXT NOT NULL DEFAULT 'Medium',
                    assignee TEXT NOT NULL DEFAULT '',
                    asked_by TEXT NOT NULL DEFAULT '',
                    seller_answer TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'Open',
                    verified_by TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_vdr_qa_deal ON vdr_qa(deal_id);

                CREATE TABLE IF NOT EXISTS claim_verifications (
                    deal_id TEXT NOT NULL,
                    claim_hash TEXT NOT NULL,
                    claim_text TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'AI Draft',
                    reviewer TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (deal_id, claim_hash)
                );

                CREATE TABLE IF NOT EXISTS ic_votes (
                    vote_id TEXT PRIMARY KEY,
                    deal_id TEXT NOT NULL,
                    voter_name TEXT NOT NULL,
                    voter_role TEXT NOT NULL,
                    vote TEXT NOT NULL,
                    conditions_precedent TEXT NOT NULL DEFAULT '',
                    rationale TEXT NOT NULL DEFAULT '',
                    cast_at TEXT NOT NULL,
                    UNIQUE (deal_id, voter_name)
                );
                CREATE INDEX IF NOT EXISTS idx_ic_votes_deal ON ic_votes(deal_id);

                CREATE TABLE IF NOT EXISTS clean_team_entries (
                    entry_id TEXT PRIMARY KEY,
                    deal_id TEXT NOT NULL,
                    author TEXT NOT NULL,
                    author_role TEXT NOT NULL,
                    category TEXT NOT NULL,
                    title TEXT NOT NULL,
                    sensitive_finding TEXT NOT NULL,
                    sanitized_summary TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_clean_team_deal ON clean_team_entries(deal_id);

                CREATE TABLE IF NOT EXISTS value_capture (
                    record_id TEXT PRIMARY KEY,
                    deal_id TEXT NOT NULL,
                    workstream TEXT NOT NULL,
                    quarter TEXT NOT NULL,
                    projected_usd_m REAL NOT NULL DEFAULT 0.0,
                    actual_usd_m REAL NOT NULL DEFAULT 0.0,
                    owner TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'On Track',
                    notes TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    UNIQUE (deal_id, workstream, quarter)
                );
                CREATE INDEX IF NOT EXISTS idx_value_capture_deal ON value_capture(deal_id);

                CREATE TABLE IF NOT EXISTS firm_branding (
                    tenant_id TEXT PRIMARY KEY,
                    firm_name TEXT NOT NULL,
                    primary_hex TEXT NOT NULL,
                    accent_hex TEXT NOT NULL,
                    legal_disclaimer TEXT NOT NULL DEFAULT '',
                    custom_checklist_json TEXT NOT NULL DEFAULT '[]',
                    system_prompt_addendum TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS usage_ledger (
                    ledger_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    deal_id TEXT NOT NULL DEFAULT '',
                    event_type TEXT NOT NULL,
                    pages_processed INTEGER NOT NULL DEFAULT 0,
                    estimated_tokens INTEGER NOT NULL DEFAULT 0,
                    actor TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    recorded_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_usage_tenant ON usage_ledger(tenant_id);
                """
            )

    # =================================================================
    # 1. M&A Deal Lifecycle Pipeline
    # =================================================================

    def upsert_deal(
        self,
        deal_id: str,
        deal_name: str,
        *,
        entity_a: str = "",
        entity_b: str = "",
        stage: str = "Origination",
        deal_value_usd_b: Optional[float] = None,
        target_signing_date: Optional[str] = None,
        diligence_coverage_pct: float = 0.0,
        lead_banker: str = "",
        sector: str = "",
        tenant_id: str = "default",
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        if stage not in DEAL_STAGES:
            raise ValueError(f"Invalid stage '{stage}'. Must be one of {DEAL_STAGES}.")
        cov = max(0.0, min(100.0, float(diligence_coverage_pct)))
        now = _utc_now()
        meta_json = json.dumps(metadata or {}, sort_keys=True)

        with self._connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM deals_pipeline WHERE deal_id = ?", (deal_id,)
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT INTO deals_pipeline (
                    deal_id, tenant_id, deal_name, entity_a, entity_b, stage,
                    deal_value_usd_b, target_signing_date, diligence_coverage_pct,
                    lead_banker, sector, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(deal_id) DO UPDATE SET
                    tenant_id=excluded.tenant_id,
                    deal_name=excluded.deal_name,
                    entity_a=excluded.entity_a,
                    entity_b=excluded.entity_b,
                    stage=excluded.stage,
                    deal_value_usd_b=excluded.deal_value_usd_b,
                    target_signing_date=excluded.target_signing_date,
                    diligence_coverage_pct=excluded.diligence_coverage_pct,
                    lead_banker=excluded.lead_banker,
                    sector=excluded.sector,
                    metadata_json=excluded.metadata_json,
                    updated_at=excluded.updated_at
                """,
                (
                    deal_id, tenant_id, deal_name, entity_a, entity_b, stage,
                    deal_value_usd_b, target_signing_date, cov,
                    lead_banker, sector, meta_json, created_at, now,
                ),
            )
        return self.get_deal(deal_id)  # type: ignore[return-value]

    def get_deal(self, deal_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM deals_pipeline WHERE deal_id = ?", (deal_id,)
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["metadata"] = json.loads(d.pop("metadata_json") or "{}")
        return d

    def list_deals(
        self,
        *,
        stage: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if stage:
            clauses.append("stage = ?")
            params.append(stage)
        if tenant_id:
            clauses.append("tenant_id = ?")
            params.append(tenant_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM deals_pipeline {where} ORDER BY updated_at DESC", params
            ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            d["metadata"] = json.loads(d.pop("metadata_json") or "{}")
            results.append(d)
        return results

    def update_deal_stage(
        self,
        deal_id: str,
        stage: str,
        diligence_coverage_pct: Optional[float] = None,
    ) -> Optional[dict[str, Any]]:
        if stage not in DEAL_STAGES:
            raise ValueError(f"Invalid stage '{stage}'. Must be one of {DEAL_STAGES}.")
        now = _utc_now()
        with self._connect() as conn:
            if diligence_coverage_pct is not None:
                cov = max(0.0, min(100.0, float(diligence_coverage_pct)))
                cur = conn.execute(
                    "UPDATE deals_pipeline SET stage = ?, diligence_coverage_pct = ?, updated_at = ? WHERE deal_id = ?",
                    (stage, cov, now, deal_id),
                )
            else:
                cur = conn.execute(
                    "UPDATE deals_pipeline SET stage = ?, updated_at = ? WHERE deal_id = ?",
                    (stage, now, deal_id),
                )
            if cur.rowcount == 0:
                return None
        return self.get_deal(deal_id)

    def delete_deal(self, deal_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM deals_pipeline WHERE deal_id = ?", (deal_id,))
            return cur.rowcount > 0

    # =================================================================
    # 2. Integrated VDR Q&A Tracker
    # =================================================================

    def create_qa_item(
        self,
        deal_id: str,
        question: str,
        *,
        workstream: str = "Finance",
        priority: str = "Medium",
        assignee: str = "",
        asked_by: str = "",
    ) -> dict[str, Any]:
        if workstream not in QA_WORKSTREAMS:
            raise ValueError(f"Invalid workstream '{workstream}'. Must be one of {QA_WORKSTREAMS}.")
        if priority not in QA_PRIORITIES:
            raise ValueError(f"Invalid priority '{priority}'. Must be one of {QA_PRIORITIES}.")
        qa_id = f"qa_{uuid.uuid4().hex[:10]}"
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO vdr_qa (
                    qa_id, deal_id, question, workstream, priority, assignee,
                    asked_by, seller_answer, status, verified_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, '', 'Open', '', ?, ?)
                """,
                (qa_id, deal_id, question.strip(), workstream, priority, assignee, asked_by, now, now),
            )
        return self.get_qa_item(qa_id)  # type: ignore[return-value]

    def get_qa_item(self, qa_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM vdr_qa WHERE qa_id = ?", (qa_id,)).fetchone()
        return dict(row) if row else None

    def list_qa_items(
        self,
        deal_id: str,
        *,
        workstream: Optional[str] = None,
        status: Optional[str] = None,
        priority: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        clauses = ["deal_id = ?"]
        params: list[Any] = [deal_id]
        if workstream:
            clauses.append("workstream = ?")
            params.append(workstream)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if priority:
            clauses.append("priority = ?")
            params.append(priority)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM vdr_qa WHERE {' AND '.join(clauses)} ORDER BY created_at DESC",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def update_qa_item(
        self,
        qa_id: str,
        *,
        assignee: Optional[str] = None,
        priority: Optional[str] = None,
        workstream: Optional[str] = None,
        seller_answer: Optional[str] = None,
        status: Optional[str] = None,
        verified_by: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        updates: list[str] = []
        params: list[Any] = []
        if assignee is not None:
            updates.append("assignee = ?")
            params.append(assignee)
        if priority is not None:
            if priority not in QA_PRIORITIES:
                raise ValueError(f"Invalid priority '{priority}'.")
            updates.append("priority = ?")
            params.append(priority)
        if workstream is not None:
            if workstream not in QA_WORKSTREAMS:
                raise ValueError(f"Invalid workstream '{workstream}'.")
            updates.append("workstream = ?")
            params.append(workstream)
        if seller_answer is not None:
            updates.append("seller_answer = ?")
            params.append(seller_answer)
            if status is None:
                status = "Answered"
        if status is not None:
            if status not in QA_STATUSES:
                raise ValueError(f"Invalid status '{status}'. Must be one of {QA_STATUSES}.")
            updates.append("status = ?")
            params.append(status)
        if verified_by is not None:
            updates.append("verified_by = ?")
            params.append(verified_by)

        if not updates:
            return self.get_qa_item(qa_id)

        updates.append("updated_at = ?")
        params.append(_utc_now())
        params.append(qa_id)

        with self._connect() as conn:
            cur = conn.execute(f"UPDATE vdr_qa SET {', '.join(updates)} WHERE qa_id = ?", params)
            if cur.rowcount == 0:
                return None
        return self.get_qa_item(qa_id)

    def record_seller_answer(self, qa_id: str, seller_answer: str) -> Optional[dict[str, Any]]:
        return self.update_qa_item(qa_id, seller_answer=seller_answer, status="Answered")

    def verify_qa_item(
        self,
        qa_id: str,
        verified_by: str,
        *,
        close: bool = True,
    ) -> Optional[dict[str, Any]]:
        return self.update_qa_item(
            qa_id,
            verified_by=verified_by,
            status="Closed" if close else "Verified",
        )

    def delete_qa_item(self, qa_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM vdr_qa WHERE qa_id = ?", (qa_id,))
            return cur.rowcount > 0

    # =================================================================
    # 3. Human-in-the-Loop Claim Verification
    # =================================================================

    def set_claim_review(
        self,
        deal_id: str,
        claim_hash: str,
        status: str,
        *,
        reviewer: str = "",
        note: str = "",
        claim_text: str = "",
    ) -> dict[str, Any]:
        if status not in CLAIM_STATUSES:
            raise ValueError(f"Invalid claim status '{status}'. Must be one of {CLAIM_STATUSES}.")
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO claim_verifications (
                    deal_id, claim_hash, claim_text, status, reviewer, note, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(deal_id, claim_hash) DO UPDATE SET
                    claim_text=CASE WHEN excluded.claim_text != '' THEN excluded.claim_text ELSE claim_verifications.claim_text END,
                    status=excluded.status,
                    reviewer=excluded.reviewer,
                    note=excluded.note,
                    updated_at=excluded.updated_at
                """,
                (deal_id, claim_hash, claim_text, status, reviewer, note, now),
            )
        return self.get_claim_review(deal_id, claim_hash)  # type: ignore[return-value]

    def get_claim_review(self, deal_id: str, claim_hash: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM claim_verifications WHERE deal_id = ? AND claim_hash = ?",
                (deal_id, claim_hash),
            ).fetchone()
        return dict(row) if row else None

    def list_claim_reviews(
        self,
        deal_id: str,
        *,
        status: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        clauses = ["deal_id = ?"]
        params: list[Any] = [deal_id]
        if status:
            clauses.append("status = ?")
            params.append(status)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM claim_verifications WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def claim_verification_summary(self, deal_id: str) -> dict[str, Any]:
        rows = self.list_claim_reviews(deal_id)
        counts = {s: 0 for s in CLAIM_STATUSES}
        for r in rows:
            if r["status"] in counts:
                counts[r["status"]] += 1
        total = len(rows)
        verified_or_approved = counts["Analyst Verified"] + counts["VP Approved"]
        pct = round((verified_or_approved / total) * 100.0, 1) if total > 0 else 0.0
        return {
            "deal_id": deal_id,
            "total_tracked": total,
            "counts": counts,
            "verification_rate_pct": pct,
        }

    # =================================================================
    # 4. Digital Investment Committee (IC) Voting Portal
    # =================================================================

    def cast_ic_vote(
        self,
        deal_id: str,
        voter_name: str,
        voter_role: str,
        vote: str,
        *,
        conditions_precedent: str = "",
        rationale: str = "",
    ) -> dict[str, Any]:
        if vote not in VOTE_CHOICES:
            raise ValueError(f"Invalid vote '{vote}'. Must be one of {VOTE_CHOICES}.")
        vote_id = f"vote_{uuid.uuid4().hex[:10]}"
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO ic_votes (
                    vote_id, deal_id, voter_name, voter_role, vote,
                    conditions_precedent, rationale, cast_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(deal_id, voter_name) DO UPDATE SET
                    voter_role=excluded.voter_role,
                    vote=excluded.vote,
                    conditions_precedent=excluded.conditions_precedent,
                    rationale=excluded.rationale,
                    cast_at=excluded.cast_at
                """,
                (vote_id, deal_id.strip(), voter_name.strip(), voter_role, vote, conditions_precedent, rationale, now),
            )
            row = conn.execute(
                "SELECT * FROM ic_votes WHERE deal_id = ? AND voter_name = ?",
                (deal_id.strip(), voter_name.strip()),
            ).fetchone()
        return dict(row)

    def list_ic_votes(self, deal_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ic_votes WHERE deal_id = ? ORDER BY cast_at DESC",
                (deal_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_ic_quorum_summary(self, deal_id: str, quorum_required: int = 3) -> dict[str, Any]:
        votes = self.list_ic_votes(deal_id)
        breakdown = {choice: 0 for choice in VOTE_CHOICES}
        cps: list[str] = []
        for v in votes:
            if v["vote"] in breakdown:
                breakdown[v["vote"]] += 1
            if v.get("conditions_precedent"):
                cps.append(f"[{v['voter_name']}] {v['conditions_precedent']}")

        total = len(votes)
        quorum_met = total >= quorum_required
        if not quorum_met:
            verdict = "Pending Quorum"
        elif breakdown["Reject"] > 0 and breakdown["Reject"] >= (breakdown["Approve"] + breakdown["Conditional Approve"]):
            verdict = "Rejected"
        elif breakdown["Defer"] > (breakdown["Approve"] + breakdown["Conditional Approve"]):
            verdict = "Deferred"
        elif breakdown["Reject"] > 0 or breakdown["Conditional Approve"] > 0:
            verdict = "Conditionally Approved" if (breakdown["Approve"] + breakdown["Conditional Approve"]) > breakdown["Reject"] else "Rejected"
        elif breakdown["Approve"] > 0:
            verdict = "Approved"
        else:
            verdict = "Deferred"

        return {
            "deal_id": deal_id,
            "total_votes": total,
            "quorum_required": quorum_required,
            "quorum_met": quorum_met,
            "breakdown": breakdown,
            "verdict": verdict,
            "conditions_precedent": cps,
            "votes": votes,
        }

    # =================================================================
    # 5. Clean Team Isolated Workbench (Role-Gated: CLEAN_TEAM or MD)
    # =================================================================

    @staticmethod
    def _assert_clean_team_role(role: str) -> str:
        norm = (role.value if hasattr(role, "value") else str(role)).strip().upper()
        if norm not in CLEAN_TEAM_ROLES:
            raise PermissionError(
                f"Clean Team access denied for role '{norm}'. Required role: {sorted(CLEAN_TEAM_ROLES)}."
            )
        return norm

    def add_clean_team_entry(
        self,
        deal_id: str,
        role: str,
        author: str,
        category: str,
        title: str,
        sensitive_finding: str,
        *,
        sanitized_summary: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        norm_role = self._assert_clean_team_role(role)
        entry_id = f"ct_{uuid.uuid4().hex[:10]}"
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO clean_team_entries (
                    entry_id, deal_id, author, author_role, category, title,
                    sensitive_finding, sanitized_summary, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_id, deal_id, author, norm_role, category, title,
                    sensitive_finding, sanitized_summary, json.dumps(metadata or {}, sort_keys=True), now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM clean_team_entries WHERE entry_id = ?", (entry_id,)
            ).fetchone()
        res = dict(row)
        res["metadata"] = json.loads(res.pop("metadata_json") or "{}")
        return res

    def list_clean_team_entries(self, deal_id: str, role: str) -> list[dict[str, Any]]:
        self._assert_clean_team_role(role)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM clean_team_entries WHERE deal_id = ? ORDER BY created_at DESC",
                (deal_id,),
            ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            d["metadata"] = json.loads(d.pop("metadata_json") or "{}")
            results.append(d)
        return results

    def delete_clean_team_entry(self, entry_id: str, role: str) -> bool:
        self._assert_clean_team_role(role)
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM clean_team_entries WHERE entry_id = ?", (entry_id,))
            return cur.rowcount > 0

    # =================================================================
    # 6. Post-Closing Value-Capture Tracker
    # =================================================================

    def record_value_capture(
        self,
        deal_id: str,
        workstream: str,
        quarter: str,
        projected_usd_m: float,
        actual_usd_m: float = 0.0,
        *,
        owner: str = "",
        status: str = "On Track",
        notes: str = "",
    ) -> dict[str, Any]:
        record_id = f"vc_{uuid.uuid4().hex[:10]}"
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO value_capture (
                    record_id, deal_id, workstream, quarter, projected_usd_m,
                    actual_usd_m, owner, status, notes, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(deal_id, workstream, quarter) DO UPDATE SET
                    projected_usd_m=excluded.projected_usd_m,
                    actual_usd_m=excluded.actual_usd_m,
                    owner=excluded.owner,
                    status=excluded.status,
                    notes=excluded.notes,
                    updated_at=excluded.updated_at
                """,
                (
                    record_id, deal_id, workstream, quarter,
                    float(projected_usd_m), float(actual_usd_m), owner, status, notes, now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM value_capture WHERE deal_id = ? AND workstream = ? AND quarter = ?",
                (deal_id, workstream, quarter),
            ).fetchone()
        return dict(row)

    def list_value_capture(
        self,
        deal_id: str,
        *,
        workstream: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        clauses = ["deal_id = ?"]
        params: list[Any] = [deal_id]
        if workstream:
            clauses.append("workstream = ?")
            params.append(workstream)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM value_capture WHERE {' AND '.join(clauses)} ORDER BY quarter ASC, workstream ASC",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def value_capture_summary(self, deal_id: str) -> dict[str, Any]:
        rows = self.list_value_capture(deal_id)
        total_proj = round(sum(r["projected_usd_m"] for r in rows), 2)
        total_act = round(sum(r["actual_usd_m"] for r in rows), 2)
        variance = round(total_act - total_proj, 2)
        realization_pct = round((total_act / total_proj) * 100.0, 1) if total_proj > 0 else 0.0

        by_workstream: dict[str, dict[str, float]] = {}
        by_quarter: dict[str, dict[str, float]] = {}
        for r in rows:
            ws = by_workstream.setdefault(r["workstream"], {"projected_usd_m": 0.0, "actual_usd_m": 0.0})
            ws["projected_usd_m"] = round(ws["projected_usd_m"] + r["projected_usd_m"], 2)
            ws["actual_usd_m"] = round(ws["actual_usd_m"] + r["actual_usd_m"], 2)

            q = by_quarter.setdefault(r["quarter"], {"projected_usd_m": 0.0, "actual_usd_m": 0.0})
            q["projected_usd_m"] = round(q["projected_usd_m"] + r["projected_usd_m"], 2)
            q["actual_usd_m"] = round(q["actual_usd_m"] + r["actual_usd_m"], 2)

        return {
            "deal_id": deal_id,
            "total_projected_usd_m": total_proj,
            "total_actual_usd_m": total_act,
            "variance_usd_m": variance,
            "realization_pct": realization_pct,
            "by_workstream": by_workstream,
            "by_quarter": by_quarter,
            "entries": rows,
        }

    # =================================================================
    # 7. White-Label Firm Branding & Playbook Studio
    # =================================================================

    def save_branding(
        self,
        *,
        tenant_id: str = "default",
        firm_name: str = "M&A Due Diligence Copilot Advisory",
        primary_hex: str = "#0B192C",
        accent_hex: str = "#B8935A",
        legal_disclaimer: str = "",
        custom_checklist: Optional[list[str]] = None,
        system_prompt_addendum: str = "",
    ) -> dict[str, Any]:
        now = _utc_now()
        checklist_json = json.dumps(custom_checklist or [])
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO firm_branding (
                    tenant_id, firm_name, primary_hex, accent_hex, legal_disclaimer,
                    custom_checklist_json, system_prompt_addendum, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id) DO UPDATE SET
                    firm_name=excluded.firm_name,
                    primary_hex=excluded.primary_hex,
                    accent_hex=excluded.accent_hex,
                    legal_disclaimer=excluded.legal_disclaimer,
                    custom_checklist_json=excluded.custom_checklist_json,
                    system_prompt_addendum=excluded.system_prompt_addendum,
                    updated_at=excluded.updated_at
                """,
                (
                    tenant_id, firm_name, primary_hex, accent_hex,
                    legal_disclaimer, checklist_json, system_prompt_addendum, now,
                ),
            )
        return self.get_branding(tenant_id)

    def get_branding(self, tenant_id: str = "default") -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM firm_branding WHERE tenant_id = ?", (tenant_id,)
            ).fetchone()
        if not row:
            return {
                "tenant_id": tenant_id,
                "firm_name": "M&A Due Diligence Copilot Advisory",
                "primary_hex": "#0B192C",
                "accent_hex": "#B8935A",
                "legal_disclaimer": (
                    "CONFIDENTIAL — Prepared exclusively for Investment Committee review. "
                    "Not legal or fairness-opinion advice."
                ),
                "custom_checklist": [
                    "Verify Change-of-Control waivers on top-10 commercial contracts",
                    "Confirm Quality-of-Earnings EBITDA bridge & normalized NWC peg",
                    "Validate HSR / EUMR / CFIUS filing triggers and Outside Date",
                ],
                "system_prompt_addendum": "",
                "updated_at": None,
            }
        d = dict(row)
        d["custom_checklist"] = json.loads(d.pop("custom_checklist_json") or "[]")
        return d

    # =================================================================
    # 8. Usage & Metering Ledger
    # =================================================================

    def log_usage(
        self,
        tenant_id: str,
        deal_id: str,
        event_type: str,
        *,
        pages_processed: int = 0,
        estimated_tokens: int = 0,
        actor: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        ledger_id = f"usg_{uuid.uuid4().hex[:12]}"
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO usage_ledger (
                    ledger_id, tenant_id, deal_id, event_type,
                    pages_processed, estimated_tokens, actor, metadata_json, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ledger_id, tenant_id, deal_id, event_type,
                    max(0, int(pages_processed)), max(0, int(estimated_tokens)),
                    actor, json.dumps(metadata or {}, sort_keys=True), now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM usage_ledger WHERE ledger_id = ?", (ledger_id,)
            ).fetchone()
        d = dict(row)
        d["metadata"] = json.loads(d.pop("metadata_json") or "{}")
        return d

    def list_usage(
        self,
        *,
        tenant_id: Optional[str] = None,
        deal_id: Optional[str] = None,
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
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, int(limit)))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM usage_ledger {where} ORDER BY recorded_at DESC LIMIT ?",
                params,
            ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            d["metadata"] = json.loads(d.pop("metadata_json") or "{}")
            results.append(d)
        return results

    def usage_summary(self, tenant_id: Optional[str] = None) -> dict[str, Any]:
        rows = self.list_usage(tenant_id=tenant_id, limit=10_000)
        by_tenant: dict[str, dict[str, int]] = {}
        by_event: dict[str, int] = {}
        total_pages = 0
        total_tokens = 0

        for r in rows:
            total_pages += r["pages_processed"]
            total_tokens += r["estimated_tokens"]
            t = by_tenant.setdefault(
                r["tenant_id"], {"events": 0, "pages_processed": 0, "estimated_tokens": 0}
            )
            t["events"] += 1
            t["pages_processed"] += r["pages_processed"]
            t["estimated_tokens"] += r["estimated_tokens"]
            by_event[r["event_type"]] = by_event.get(r["event_type"], 0) + 1

        return {
            "total_events": len(rows),
            "total_pages_processed": total_pages,
            "total_estimated_tokens": total_tokens,
            "by_tenant": by_tenant,
            "by_event_type": by_event,
        }
