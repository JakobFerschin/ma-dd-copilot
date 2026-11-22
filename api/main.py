"""
M&A Due Diligence Copilot — Enterprise FastAPI Service (`api/main.py`).

Provides institutional REST endpoints with:
  - HS256 JWT authentication & RBAC permission enforcement
  - Chinese Wall deal-level ACLs & Clean Team isolation
  - Automatic WORM hash-chained audit logging on protected endpoints
  - Deterministic Synergy DCF + Sensitivity Matrix execution
  - Legal DD (Change-of-Control, SPA Benchmarking, Antitrust/FDI, IP/Cyber/ESG)
  - VDR Q&A Tracker & Digital Investment Committee (IC) Voting Portal
  - Multi-format boardroom exports (`docx`, `xlsx`, `html`, `json`)
  - PII/MNPI redaction & prompt-injection defense test endpoint
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from api.audit import AuditLogger
from api.auth import (
    Role,
    TenantPolicy,
    TokenError,
    UserPrincipal,
    can_access_clean_team,
    can_access_deal,
    create_access_token,
    has_permission,
    redact_pii_mnpi,
    sanitize_prompt_injection,
    verify_access_token,
)
from core.demo_cases import DEMO_CASES
from core.financial_model import (
    ValuationAssumptions,
    compute_sensitivity_matrix,
    compute_synergy_dcf,
)
from db.local_vault import list_local_deals, load_local_report, save_local_report
from legal.diligence import (
    assess_antitrust_and_fdi,
    assess_ip_cyber_esg,
    benchmark_spa_terms,
    scan_change_of_control,
)
from schemas.report_schema import DueDiligenceReport
from utils.export_engine import (
    report_to_docx_bytes,
    report_to_excel_bytes,
    report_to_html_bytes,
)
from workflow.deal_lifecycle import EnterpriseWorkflowStore

app = FastAPI(
    title="M&A Due Diligence Copilot — Enterprise API",
    version="2.0.0",
    description=(
        "Institutional M&A Due Diligence, Valuation, Legal/Antitrust Screening, "
        "IC Governance & WORM Audit API."
    ),
)

_bearer_scheme = HTTPBearer(auto_error=False)


# =====================================================================
# Configurable Store Accessors (supports clean test isolation)
# =====================================================================

def configure_stores(
    *,
    workflow_db: str | Path | None = None,
    audit_db: str | Path | None = None,
    vault_dir: str | Path | None = None,
) -> None:
    """Re-bind the FastAPI app's SQLite stores and Local Deal Vault directory."""
    app.state.workflow_store = EnterpriseWorkflowStore(db_path=workflow_db)
    app.state.audit_logger = AuditLogger(db_path=audit_db)
    app.state.vault_dir = Path(vault_dir) if vault_dir is not None else None


def get_workflow_store() -> EnterpriseWorkflowStore:
    store = getattr(app.state, "workflow_store", None)
    if store is None:
        store = EnterpriseWorkflowStore()
        app.state.workflow_store = store
    return store


def get_audit_logger() -> AuditLogger:
    audit = getattr(app.state, "audit_logger", None)
    if audit is None:
        audit = AuditLogger()
        app.state.audit_logger = audit
    return audit


def get_vault_dir() -> Optional[Path]:
    return getattr(app.state, "vault_dir", None)


# =====================================================================
# Authentication, RBAC & Chinese Wall Dependencies
# =====================================================================

def get_current_principal(
    request: Request,
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> UserPrincipal:
    if creds is None or not creds.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Bearer access token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        principal = verify_access_token(creds.credentials)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    client_ip = request.client.host if request.client else ""
    if principal.policy.ip_allowlist and not principal.policy.is_ip_allowed(client_ip):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Client IP '{client_ip}' is not permitted by tenant IP allowlist.",
        )
    return principal


def _require_permission(principal: UserPrincipal, action: str) -> None:
    if not has_permission(principal, action):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Role '{principal.role.value}' lacks permission '{action}'.",
        )


def _require_deal_access(principal: UserPrincipal, deal_id: str) -> None:
    if not can_access_deal(principal, deal_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Chinese Wall policy restricts access to deal '{deal_id}'.",
        )


def _load_deal_or_404(deal_id: str) -> DueDiligenceReport:
    report = load_local_report(deal_id, base_dir=get_vault_dir())
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Deal '{deal_id}' not found in Deal Vault.",
        )
    return report


# =====================================================================
# Request / Response Pydantic Schemas
# =====================================================================

class TokenRequest(BaseModel):
    username: str = Field(..., min_length=1, description="User identifier / email.")
    name: Optional[str] = None
    role: Role = Role.MD
    tenant_id: str = "default"
    allowed_deals: Optional[list[str]] = None
    blocked_deals: list[str] = Field(default_factory=list)
    clean_team_deals: list[str] = Field(default_factory=list)
    ip_allowlist: list[str] = Field(default_factory=list)
    zdr_enforced: bool = True
    expires_minutes: int = Field(120, ge=5, le=1440)


class CreateDealRequest(BaseModel):
    demo_case_id: Optional[str] = Field(
        None,
        description="Load a flagship demo case ('vw_rivian', 'msft_databricks', 'lilly_vertex').",
    )
    report: Optional[DueDiligenceReport] = Field(
        None,
        description="Optional full validated DueDiligenceReport payload to store directly.",
    )
    stage: str = "Confirmatory DD"
    lead_banker: str = ""
    target_signing_date: Optional[str] = None


class DCFRequest(BaseModel):
    wacc_pct: Optional[float] = Field(None, ge=1.0, le=30.0)
    tax_rate_pct: Optional[float] = Field(None, ge=0.0, le=60.0)
    terminal_growth_pct: Optional[float] = Field(None, ge=0.0, le=8.0)
    realization_factor_pct: float = Field(100.0, ge=10.0, le=200.0)
    integration_cost_overrun_pct: float = Field(0.0, ge=-50.0, le=200.0)
    projection_years: Optional[int] = Field(None, ge=3, le=10)
    use_probability_weighting: bool = True
    terminal_method: Literal["multiple", "perpetuity"] = "multiple"
    terminal_multiple: float = Field(4.5, ge=0.0, le=20.0)


class LegalDiligenceRequest(BaseModel):
    contract_texts: list[str] = Field(default_factory=list)
    liability_cap_pct: float = 10.0
    basket_pct: float = 0.75
    escrow_pct: float = 8.0
    survival_months: int = 18
    has_rw_insurance: bool = True
    has_mac_carveouts: bool = True
    acquirer_rev_b: float = 25.0
    target_rev_b: float = 4.5
    combined_market_share_pct: float = 22.0
    target_country: str = "US"
    acquirer_country: str = "DE"


class CreateQARequest(BaseModel):
    question: str = Field(..., min_length=3)
    workstream: str = "Finance"
    priority: str = "Medium"
    assignee: str = ""


class UpdateQARequest(BaseModel):
    assignee: Optional[str] = None
    priority: Optional[str] = None
    workstream: Optional[str] = None
    seller_answer: Optional[str] = None
    status: Optional[str] = None


class CastVoteRequest(BaseModel):
    vote: str
    conditions_precedent: str = ""
    rationale: str = ""
    quorum_required: int = Field(3, ge=1, le=25)


class CleanTeamEntryRequest(BaseModel):
    category: str = "Pricing / Customer Sensitivity"
    title: str = Field(..., min_length=2)
    sensitive_finding: str = Field(..., min_length=3)
    sanitized_summary: str = ""


class RedactRequest(BaseModel):
    text: str
    code_words: list[str] = Field(default_factory=list)
    check_prompt_injection: bool = True


# =====================================================================
# Health & Readiness Probes
# =====================================================================

@app.get("/health", tags=["Operations"])
def health_probe() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "ma-dd-copilot-api",
        "version": app.version,
    }


@app.get("/ready", tags=["Operations"])
def readiness_probe() -> dict[str, Any]:
    store = get_workflow_store()
    audit = get_audit_logger()
    chain_ok, count, msg = audit.verify_chain()
    _ = store.list_deals()
    if not chain_ok:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Audit trail integrity check failed: {msg}",
        )
    return {
        "status": "ready",
        "workflow_db": str(store.db_path),
        "audit_chain_valid": chain_ok,
        "audit_entries": count,
    }


# =====================================================================
# Auth Token Issuer (Dev / Demo HS256 JWT)
# =====================================================================

@app.post("/api/v1/auth/token", tags=["Authentication"])
def issue_token(payload: TokenRequest) -> dict[str, Any]:
    policy = TenantPolicy(
        tenant_id=payload.tenant_id,
        ip_allowlist=payload.ip_allowlist,
        zdr_enforced=payload.zdr_enforced,
    )
    principal = UserPrincipal(
        sub=payload.username,
        name=payload.name or payload.username,
        role=payload.role,
        tenant_id=payload.tenant_id,
        allowed_deals=payload.allowed_deals,
        blocked_deals=payload.blocked_deals,
        clean_team_deals=payload.clean_team_deals,
        policy=policy,
    )
    token = create_access_token(principal, expires_minutes=payload.expires_minutes)
    get_audit_logger().log_event(
        "LOGIN",
        actor=principal.sub,
        role=principal.role.value,
        tenant_id=principal.tenant_id,
        metadata={"expires_minutes": payload.expires_minutes, "zdr_enforced": policy.zdr_enforced},
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "principal": principal.to_dict(),
    }


# =====================================================================
# Deals CRUD & Demo Case Ingestion
# =====================================================================

@app.get("/api/v1/deals", tags=["Deals"])
def list_deals(principal: UserPrincipal = Depends(get_current_principal)) -> dict[str, Any]:
    _require_permission(principal, "deal:read")
    vault_deals = list_local_deals(limit=100, base_dir=get_vault_dir())
    pipeline_map = {
        d["deal_id"]: d for d in get_workflow_store().list_deals(tenant_id=principal.tenant_id)
    }

    accessible: list[dict[str, Any]] = []
    for row in vault_deals:
        did = row["id"]
        if not can_access_deal(principal, did):
            continue
        merged = dict(row)
        if did in pipeline_map:
            merged["workflow"] = pipeline_map[did]
        accessible.append(merged)

    get_audit_logger().log_event(
        "VIEW",
        actor=principal.sub,
        role=principal.role.value,
        tenant_id=principal.tenant_id,
        metadata={"endpoint": "GET /api/v1/deals", "returned_count": len(accessible)},
    )
    return {"deals": accessible, "count": len(accessible), "available_demo_cases": list(DEMO_CASES.keys())}


@app.post("/api/v1/deals", status_code=status.HTTP_201_CREATED, tags=["Deals"])
def create_or_load_deal(
    payload: CreateDealRequest,
    principal: UserPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    _require_permission(principal, "deal:analyze")

    if payload.report is not None:
        report = payload.report
        source_mode = "custom_report"
    else:
        case_key = payload.demo_case_id or "vw_rivian"
        if case_key not in DEMO_CASES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown demo_case_id '{case_key}'. Choose from {list(DEMO_CASES.keys())}.",
            )
        report = DEMO_CASES[case_key]["builder"]()
        source_mode = f"demo:{case_key}"

    deal_id = save_local_report(report, base_dir=get_vault_dir())
    store = get_workflow_store()
    pipeline = store.upsert_deal(
        deal_id=deal_id,
        deal_name=f"{report.deal.entity_a_name} × {report.deal.entity_b_name}",
        entity_a=report.deal.entity_a_name,
        entity_b=report.deal.entity_b_name,
        stage=payload.stage,
        deal_value_usd_b=report.deal.deal_value_usd_b,
        target_signing_date=payload.target_signing_date,
        diligence_coverage_pct=round(report.grounding_ratio() * 100.0, 1),
        lead_banker=payload.lead_banker or principal.name,
        sector=report.deal.industry_sector or "",
        tenant_id=principal.tenant_id,
        metadata={"source_mode": source_mode},
    )
    store.log_usage(
        tenant_id=principal.tenant_id,
        deal_id=deal_id,
        event_type="DEAL_ANALYZE",
        pages_processed=len(report.deal.documents_analyzed) * 20,
        estimated_tokens=len(report.model_dump_json()) // 4,
        actor=principal.sub,
        metadata={"source_mode": source_mode},
    )
    get_audit_logger().log_event(
        "ANALYZE",
        actor=principal.sub,
        role=principal.role.value,
        tenant_id=principal.tenant_id,
        deal_id=deal_id,
        metadata={"source_mode": source_mode, "deal_name": pipeline["deal_name"]},
    )
    return {
        "deal_id": deal_id,
        "workflow": pipeline,
        "deal": report.deal.model_dump(mode="json"),
        "recommendation": report.executive_summary.recommendation.value,
    }


@app.get("/api/v1/deals/{deal_id}", tags=["Deals"])
def get_deal(
    deal_id: str,
    principal: UserPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    _require_permission(principal, "deal:read")
    _require_deal_access(principal, deal_id)
    report = _load_deal_or_404(deal_id)
    store = get_workflow_store()

    get_audit_logger().log_event(
        "VIEW",
        actor=principal.sub,
        role=principal.role.value,
        tenant_id=principal.tenant_id,
        deal_id=deal_id,
        metadata={"endpoint": "GET /api/v1/deals/{deal_id}"},
    )
    return {
        "deal_id": deal_id,
        "report": report.model_dump(mode="json"),
        "workflow": store.get_deal(deal_id),
        "claim_verification": store.claim_verification_summary(deal_id),
    }


# =====================================================================
# Live Synergy DCF & Sensitivity Matrix
# =====================================================================

@app.post("/api/v1/deals/{deal_id}/dcf", tags=["Valuation"])
def run_deal_dcf(
    deal_id: str,
    payload: DCFRequest,
    principal: UserPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    _require_permission(principal, "dcf:run")
    _require_deal_access(principal, deal_id)
    report = _load_deal_or_404(deal_id)

    base = ValuationAssumptions.from_report(report)
    assumptions = ValuationAssumptions(
        wacc_pct=payload.wacc_pct if payload.wacc_pct is not None else base.wacc_pct,
        tax_rate_pct=payload.tax_rate_pct if payload.tax_rate_pct is not None else base.tax_rate_pct,
        terminal_growth_pct=(
            payload.terminal_growth_pct if payload.terminal_growth_pct is not None else base.terminal_growth_pct
        ),
        realization_factor_pct=payload.realization_factor_pct,
        integration_cost_overrun_pct=payload.integration_cost_overrun_pct,
        projection_years=payload.projection_years if payload.projection_years is not None else base.projection_years,
        use_probability_weighting=payload.use_probability_weighting,
        terminal_method=payload.terminal_method,
        terminal_multiple=payload.terminal_multiple,
    )
    dcf = compute_synergy_dcf(report, assumptions)
    wacc_vals, real_vals, grid = compute_sensitivity_matrix(report, assumptions)

    get_audit_logger().log_event(
        "ANALYZE",
        actor=principal.sub,
        role=principal.role.value,
        tenant_id=principal.tenant_id,
        deal_id=deal_id,
        metadata={
            "module": "synergy_dcf",
            "wacc_pct": assumptions.wacc_pct,
            "realization_factor_pct": assumptions.realization_factor_pct,
            "total_synergy_npv_b": dcf.total_synergy_npv_b,
        },
    )
    return {
        "deal_id": deal_id,
        "dcf": asdict(dcf),
        "sensitivity": {
            "wacc_values_pct": wacc_vals,
            "realization_levels_pct": real_vals,
            "npv_grid_usd_b": grid,
        },
    }


# =====================================================================
# Legal DD: CoC + SPA Benchmark + Antitrust/FDI + IP/Cyber/ESG
# =====================================================================

@app.post("/api/v1/deals/{deal_id}/legal", tags=["Legal & Regulatory"])
def run_legal_diligence(
    deal_id: str,
    payload: LegalDiligenceRequest,
    principal: UserPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    _require_permission(principal, "legal:run")
    _require_deal_access(principal, deal_id)
    report = _load_deal_or_404(deal_id)

    deal_val_m = (report.deal.deal_value_usd_b or 1.5) * 1000.0
    sector = report.deal.industry_sector or "Technology"

    coc_findings = scan_change_of_control(payload.contract_texts, report=report)
    spa_result = benchmark_spa_terms(
        deal_value_usd_m=deal_val_m,
        liability_cap_pct=payload.liability_cap_pct,
        basket_pct=payload.basket_pct,
        escrow_pct=payload.escrow_pct,
        survival_months=payload.survival_months,
        has_rw_insurance=payload.has_rw_insurance,
        has_mac_carveouts=payload.has_mac_carveouts,
    )
    antitrust_result = assess_antitrust_and_fdi(
        acquirer_rev_b=payload.acquirer_rev_b,
        target_rev_b=payload.target_rev_b,
        combined_market_share_pct=payload.combined_market_share_pct,
        sector=sector,
        target_country=payload.target_country,
        acquirer_country=payload.acquirer_country,
    )
    ip_cyber_esg = assess_ip_cyber_esg(report, entities=payload.contract_texts)

    get_audit_logger().log_event(
        "ANALYZE",
        actor=principal.sub,
        role=principal.role.value,
        tenant_id=principal.tenant_id,
        deal_id=deal_id,
        metadata={
            "module": "legal_dd",
            "coc_findings_count": len(coc_findings),
            "spa_score": spa_result.buyer_protection_score,
            "antitrust_risk": antitrust_result.overall_regulatory_risk,
            "ip_cyber_esg_risk": ip_cyber_esg.overall_risk_score,
        },
    )
    return {
        "deal_id": deal_id,
        "change_of_control": {
            "findings": [f.to_dict() for f in coc_findings],
            "total_revenue_at_risk_usd_m": round(sum(f.revenue_at_risk_usd_m for f in coc_findings), 1),
        },
        "spa_benchmark": spa_result.to_dict(),
        "antitrust_fdi": antitrust_result.to_dict(),
        "ip_cyber_esg": ip_cyber_esg.to_dict(),
    }


# =====================================================================
# VDR Q&A Tracker
# =====================================================================

@app.get("/api/v1/deals/{deal_id}/qa", tags=["Workflow"])
def list_deal_qa(
    deal_id: str,
    workstream: Optional[str] = Query(None),
    qa_status: Optional[str] = Query(None, alias="status"),
    priority: Optional[str] = Query(None),
    principal: UserPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    _require_permission(principal, "qa:read")
    _require_deal_access(principal, deal_id)
    items = get_workflow_store().list_qa_items(
        deal_id, workstream=workstream, status=qa_status, priority=priority
    )
    get_audit_logger().log_event(
        "VIEW",
        actor=principal.sub,
        role=principal.role.value,
        tenant_id=principal.tenant_id,
        deal_id=deal_id,
        metadata={"endpoint": "GET /qa", "count": len(items)},
    )
    return {"deal_id": deal_id, "items": items, "count": len(items)}


@app.post("/api/v1/deals/{deal_id}/qa", status_code=status.HTTP_201_CREATED, tags=["Workflow"])
def create_deal_qa(
    deal_id: str,
    payload: CreateQARequest,
    principal: UserPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    _require_permission(principal, "qa:write")
    _require_deal_access(principal, deal_id)
    try:
        item = get_workflow_store().create_qa_item(
            deal_id=deal_id,
            question=payload.question,
            workstream=payload.workstream,
            priority=payload.priority,
            assignee=payload.assignee,
            asked_by=principal.name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    get_audit_logger().log_event(
        "ANALYZE",
        actor=principal.sub,
        role=principal.role.value,
        tenant_id=principal.tenant_id,
        deal_id=deal_id,
        metadata={"action_detail": "create_qa", "qa_id": item["qa_id"], "workstream": item["workstream"]},
    )
    return item


@app.patch("/api/v1/deals/{deal_id}/qa/{qa_id}", tags=["Workflow"])
def update_deal_qa(
    deal_id: str,
    qa_id: str,
    payload: UpdateQARequest,
    principal: UserPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    action_needed = "qa:verify" if payload.status in {"Verified", "Closed"} else "qa:write"
    _require_permission(principal, action_needed)
    _require_deal_access(principal, deal_id)
    try:
        updated = get_workflow_store().update_qa_item(
            qa_id,
            assignee=payload.assignee,
            priority=payload.priority,
            workstream=payload.workstream,
            seller_answer=payload.seller_answer,
            status=payload.status,
            verified_by=principal.name if payload.status in {"Verified", "Closed"} else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Q&A item '{qa_id}' not found.")

    get_audit_logger().log_event(
        "CLAIM_VERIFY" if payload.status in {"Verified", "Closed"} else "ANALYZE",
        actor=principal.sub,
        role=principal.role.value,
        tenant_id=principal.tenant_id,
        deal_id=deal_id,
        metadata={"qa_id": qa_id, "status": updated["status"]},
    )
    return updated


# =====================================================================
# Digital Investment Committee (IC) Voting Portal
# =====================================================================

@app.get("/api/v1/deals/{deal_id}/votes", tags=["Investment Committee"])
def get_deal_votes(
    deal_id: str,
    quorum_required: int = Query(3, ge=1, le=25),
    principal: UserPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    _require_permission(principal, "ic:read")
    _require_deal_access(principal, deal_id)
    summary = get_workflow_store().get_ic_quorum_summary(deal_id, quorum_required=quorum_required)
    get_audit_logger().log_event(
        "VIEW",
        actor=principal.sub,
        role=principal.role.value,
        tenant_id=principal.tenant_id,
        deal_id=deal_id,
        metadata={"endpoint": "GET /votes", "verdict": summary["verdict"]},
    )
    return summary


@app.post("/api/v1/deals/{deal_id}/votes", status_code=status.HTTP_201_CREATED, tags=["Investment Committee"])
def cast_deal_vote(
    deal_id: str,
    payload: CastVoteRequest,
    principal: UserPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    _require_permission(principal, "ic:vote")
    _require_deal_access(principal, deal_id)
    try:
        vote_row = get_workflow_store().cast_ic_vote(
            deal_id=deal_id,
            voter_name=principal.name,
            voter_role=principal.role.value,
            vote=payload.vote,
            conditions_precedent=payload.conditions_precedent,
            rationale=payload.rationale,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    summary = get_workflow_store().get_ic_quorum_summary(deal_id, quorum_required=payload.quorum_required)
    get_audit_logger().log_event(
        "IC_VOTE",
        actor=principal.sub,
        role=principal.role.value,
        tenant_id=principal.tenant_id,
        deal_id=deal_id,
        metadata={"vote": payload.vote, "verdict": summary["verdict"]},
    )
    return {"vote": vote_row, "quorum_summary": summary}


# =====================================================================
# Clean Team Isolated Workbench (Role-Gated)
# =====================================================================

@app.get("/api/v1/deals/{deal_id}/clean-team", tags=["Clean Team"])
def list_clean_team(
    deal_id: str,
    principal: UserPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    if not can_access_clean_team(principal, deal_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Clean Team access requires CLEAN_TEAM or MD clearance and deal authorization.",
        )
    entries = get_workflow_store().list_clean_team_entries(deal_id, principal.role.value)
    get_audit_logger().log_event(
        "CLEAN_TEAM_ACCESS",
        actor=principal.sub,
        role=principal.role.value,
        tenant_id=principal.tenant_id,
        deal_id=deal_id,
        metadata={"operation": "read", "count": len(entries)},
    )
    return {"deal_id": deal_id, "entries": entries, "count": len(entries)}


@app.post("/api/v1/deals/{deal_id}/clean-team", status_code=status.HTTP_201_CREATED, tags=["Clean Team"])
def add_clean_team(
    deal_id: str,
    payload: CleanTeamEntryRequest,
    principal: UserPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    if not can_access_clean_team(principal, deal_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Clean Team access requires CLEAN_TEAM or MD clearance and deal authorization.",
        )
    entry = get_workflow_store().add_clean_team_entry(
        deal_id=deal_id,
        role=principal.role.value,
        author=principal.name,
        category=payload.category,
        title=payload.title,
        sensitive_finding=payload.sensitive_finding,
        sanitized_summary=payload.sanitized_summary,
    )
    get_audit_logger().log_event(
        "CLEAN_TEAM_ACCESS",
        actor=principal.sub,
        role=principal.role.value,
        tenant_id=principal.tenant_id,
        deal_id=deal_id,
        metadata={"operation": "write", "entry_id": entry["entry_id"]},
    )
    return entry


# =====================================================================
# Multi-Format Boardroom Export (`docx`, `xlsx`, `html`, `json`)
# =====================================================================

@app.get("/api/v1/deals/{deal_id}/export/{fmt}", tags=["Exports"])
def export_deal(
    deal_id: str,
    fmt: Literal["docx", "xlsx", "html", "json"],
    principal: UserPrincipal = Depends(get_current_principal),
) -> Response:
    _require_permission(principal, "deal:export")
    _require_deal_access(principal, deal_id)
    report = _load_deal_or_404(deal_id)
    dcf = compute_synergy_dcf(report)

    slug = f"{deal_id}_ic_memo"
    if fmt == "docx":
        data = report_to_docx_bytes(report, dcf_result=dcf)
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        filename = f"{slug}.docx"
    elif fmt == "xlsx":
        data = report_to_excel_bytes(report, dcf_result=dcf)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = f"{slug}.xlsx"
    elif fmt == "html":
        data = report_to_html_bytes(report, dcf_result=dcf)
        media_type = "text/html; charset=utf-8"
        filename = f"{slug}.html"
    else:
        data = report.model_dump_json(indent=2).encode("utf-8")
        media_type = "application/json"
        filename = f"{slug}.json"

    get_audit_logger().log_event(
        "EXPORT",
        actor=principal.sub,
        role=principal.role.value,
        tenant_id=principal.tenant_id,
        deal_id=deal_id,
        metadata={"format": fmt, "bytes": len(data)},
    )
    return Response(
        content=data,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# =====================================================================
# RBAC-Protected WORM Audit Log & Cryptographic Verification
# =====================================================================

@app.get("/api/v1/audit", tags=["Security & Audit"])
def read_audit_log(
    deal_id: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    principal: UserPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    _require_permission(principal, "audit:read")
    events = get_audit_logger().query_events(
        tenant_id=None if principal.role == Role.ADMIN else principal.tenant_id,
        deal_id=deal_id,
        action=action,
        limit=limit,
    )
    return {"events": events, "count": len(events)}


@app.get("/api/v1/audit/verify", tags=["Security & Audit"])
def verify_audit_chain(
    principal: UserPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    _require_permission(principal, "audit:verify")
    valid, verified_count, message = get_audit_logger().verify_chain()
    return {
        "valid": valid,
        "entries_verified": verified_count,
        "message": message,
    }


# =====================================================================
# PII / MNPI Redaction & Prompt-Injection Defense Endpoint
# =====================================================================

@app.post("/api/v1/security/redact", tags=["Security & Audit"])
def redact_text_endpoint(
    payload: RedactRequest,
    principal: UserPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    _require_permission(principal, "security:redact")
    working = payload.text
    detected_injections: list[str] = []
    if payload.check_prompt_injection:
        working, detected_injections = sanitize_prompt_injection(working)
    redacted, vault = redact_pii_mnpi(working, code_words=payload.code_words)

    get_audit_logger().log_event(
        "ANALYZE",
        actor=principal.sub,
        role=principal.role.value,
        tenant_id=principal.tenant_id,
        metadata={
            "module": "security_redact",
            "redactions_count": len(vault),
            "injections_detected": detected_injections,
            "zdr_enforced": principal.policy.zdr_enforced,
        },
    )
    return {
        "redacted_text": redacted,
        "redactions_count": len(vault),
        "vault_tokens": list(vault.keys()),
        "prompt_injections_detected": detected_injections,
        "zdr_enforced": principal.policy.zdr_enforced,
    }
