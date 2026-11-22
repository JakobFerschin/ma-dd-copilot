"""
Comprehensive institutional test suite covering every bugfix and new feature:
  1. Page-aligned table provenance & proportional multi-document budgeting (no starvation).
  2. Multi-format VDR extraction (.txt, .csv, .docx) & filename sanitization.
  3. Deterministic Synergy DCF engine (NPV, WACC monotonicity, realization scaling, IRR, payback, fallback).
  4. 2D WACC × Realization Sensitivity Matrix invariants.
  5. All 3 Flagship Institutional Case Studies schema validity & audit grounding.
  6. Multi-format exports: DOCX, Live-Formula Excel (.xlsx), Print-Ready HTML.
  7. Local Deal Vault & Unified Deal Room CRUD + Supabase credential reset fix.
  8. IC Q&A Copilot offline synthesis across valuation, antitrust, tech, PMI, and VDR topics.
  9. Plotly chart builders produce valid non-empty Figures without exceptions.
  10. LLM JSON extraction with surrounding prose & deterministic NPV backfill.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from config.settings import Settings
from core.analyzer import DealParams, _parse_llm_response, analyze_documents
from core.demo_cases import DEMO_CASES
from core.financial_model import (
    ValuationAssumptions,
    compute_sensitivity_matrix,
    compute_synergy_dcf,
)
from core.qa_copilot import answer_ic_question
from db import local_vault, supabase_client
from schemas.report_schema import DueDiligenceReport
from ui.charts import (
    cashflow_phasing_chart,
    deal_comparison_chart,
    pillar_radar_chart,
    risk_matrix_chart,
    sensitivity_heatmap_chart,
    synergy_bridge_chart,
    workstream_breakdown_chart,
)
from utils.export_engine import (
    report_to_docx_bytes,
    report_to_excel_bytes,
    report_to_html_bytes,
)
from utils.models import EntityDocuments, ExtractedDocument
from utils.pdf_extractor import extract_document, sanitize_filename


# =====================================================================
# 1. Multi-Document Budgeting & Page-Aligned Tables (Bugfix Regressions)
# =====================================================================

def test_page_tables_included_in_provenance_text():
    doc = ExtractedDocument(
        filename="10K_2024.pdf",
        num_pages=2,
        full_text="Revenue overview",
        pages=["Page 1 revenue overview", "Page 2 footnote"],
        tables=["| Segment | FY24 |\n| --- | --- |\n| Cloud | $95B |"],
        page_tables={0: ["| Segment | FY24 |\n| --- | --- |\n| Cloud | $95B |"]},
    )
    ent = EntityDocuments(entity_name="Acquirer", documents=[doc])
    prov = ent.provenance_text()
    assert "[Source: 10K_2024.pdf | p.1]" in prov
    assert "[Table 1]" in prov
    assert "| Cloud | $95B |" in prov


def test_proportional_budgeting_never_starves_secondary_documents():
    # Doc 1 is huge (30,000 chars); Doc 2 is concise (400 chars ESG report).
    big_pages = [f"Revenue EBITDA margin page {i} " + ("x" * 1500) for i in range(20)]
    doc_10k = ExtractedDocument(
        filename="Huge_10K.pdf",
        num_pages=20,
        full_text="\n".join(big_pages),
        pages=big_pages,
    )
    doc_esg = ExtractedDocument(
        filename="Concise_ESG.pdf",
        num_pages=1,
        full_text="Scope 3 emissions CSRD audit gap 2025",
        pages=["Scope 3 emissions CSRD audit gap 2025"],
    )
    ent = EntityDocuments(entity_name="Entity A", documents=[doc_10k, doc_esg])
    # Budget far smaller than doc_10k alone
    prov = ent.provenance_text(max_chars=5000)
    assert len(prov) <= 5000
    assert "[Source: Huge_10K.pdf | p.1]" in prov
    # Secondary document MUST survive intact instead of being sliced off
    assert "[Source: Concise_ESG.pdf | p.1]" in prov
    assert "CSRD audit gap 2025" in prov


def test_sanitize_filename_prevents_path_traversal():
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("VW 10-K (2024).pdf") == "VW 10-K _2024_.pdf"
    assert sanitize_filename("") == "document.pdf"


def test_multi_format_vdr_extraction(tmp_path: Path):
    # Text / CSV
    txt_file = tmp_path / "earnings_call.txt"
    txt_file.write_text("Q4 Revenue grew 22% YoY with $1.4B EBITDA.", encoding="utf-8")
    ext_txt = extract_document(txt_file)
    assert ext_txt.file_type == "txt"
    assert "1.4B EBITDA" in ext_txt.full_text
    assert ext_txt.word_count > 5

    # DOCX round-trip extraction
    report = DEMO_CASES["vw_rivian"]["builder"]()
    docx_file = tmp_path / "memo.docx"
    docx_file.write_bytes(report_to_docx_bytes(report))
    ext_docx = extract_document(docx_file)
    assert ext_docx.file_type == "docx"
    assert "Volkswagen AG" in ext_docx.full_text
    assert len(ext_docx.tables) >= 2


# =====================================================================
# 2. Flagship Case Studies & Deterministic DCF Financial Engine
# =====================================================================

@pytest.mark.parametrize("case_id", list(DEMO_CASES.keys()))
def test_flagship_demo_cases_valid_and_grounded(case_id: str):
    report = DEMO_CASES[case_id]["builder"]()
    assert isinstance(report, DueDiligenceReport)
    assert report.grounding_ratio() == 1.0
    assert report.citation_count() >= 10
    assert len(report.synergy_workstreams) >= 4
    assert len(report.risk_register) >= 4
    assert len(report.integration_roadmap) >= 3
    assert len(report.pillar_scores) == 5
    assert len(report.due_diligence_questions) >= 5


def test_dcf_monotonicity_wacc_and_realization():
    report = DEMO_CASES["vw_rivian"]["builder"]()
    base = compute_synergy_dcf(report, ValuationAssumptions(wacc_pct=9.0, realization_factor_pct=100.0))
    higher_wacc = compute_synergy_dcf(report, ValuationAssumptions(wacc_pct=12.0, realization_factor_pct=100.0))
    downside = compute_synergy_dcf(report, ValuationAssumptions(wacc_pct=9.0, realization_factor_pct=70.0))
    overrun = compute_synergy_dcf(report, ValuationAssumptions(wacc_pct=9.0, integration_cost_overrun_pct=50.0))

    assert base.total_synergy_npv_b > higher_wacc.total_synergy_npv_b
    assert base.total_synergy_npv_b > downside.total_synergy_npv_b
    assert base.total_synergy_npv_b > overrun.total_synergy_npv_b
    assert base.payback_years is not None and base.payback_years > 0
    assert base.synergy_irr_pct is not None and base.synergy_irr_pct > 0
    assert base.value_creation_pct_of_deal is not None and base.value_creation_pct_of_deal > 0


def test_dcf_sensitivity_matrix_shape_and_ordering():
    report = DEMO_CASES["msft_databricks"]["builder"]()
    base_assump = ValuationAssumptions.from_report(report)
    waccs, reals, grid = compute_sensitivity_matrix(report, base_assump)
    assert len(waccs) == 5
    assert len(reals) == 5
    assert len(grid) == 5 and all(len(r) == 5 for r in grid)
    # Higher realization (right) yields strictly higher NPV
    for row in grid:
        assert row[0] < row[-1]
    # Higher WACC (bottom) yields strictly lower NPV
    for col_idx in range(5):
        assert grid[0][col_idx] > grid[-1][col_idx]


def test_dcf_works_on_legacy_report_without_workstreams():
    report = DEMO_CASES["lilly_vertex"]["builder"]()
    report.synergy_workstreams = []  # simulate legacy minimal schema
    dcf = compute_synergy_dcf(report)
    assert dcf.total_synergy_npv_b > 0
    assert dcf.steady_state_total_synergy_b > 0


# =====================================================================
# 3. Multi-Format Institutional Exports (DOCX, Live-Formula Excel, HTML)
# =====================================================================

def test_excel_export_contains_four_sheets_and_live_formulas():
    from openpyxl import load_workbook

    report = DEMO_CASES["vw_rivian"]["builder"]()
    dcf = compute_synergy_dcf(report)
    xlsx_bytes = report_to_excel_bytes(report, dcf)
    assert xlsx_bytes[:2] == b"PK"

    wb = load_workbook(io.BytesIO(xlsx_bytes), data_only=False)
    assert wb.sheetnames == [
        "Executive Summary",
        "Synergy DCF Model",
        "Synergy Workstreams",
        "Risk Register & PMI",
    ]
    ws_dcf = wb["Synergy DCF Model"]
    assert str(ws_dcf["B24"].value).startswith("=")
    assert str(ws_dcf["B13"].value).startswith("=")
    assert str(ws_dcf["B19"].value).startswith("=")
    ws_work = wb["Synergy Workstreams"]
    assert str(ws_work["E4"].value).startswith("=")


def test_html_boardroom_brief_export():
    report = DEMO_CASES["msft_databricks"]["builder"]()
    html_bytes = report_to_html_bytes(report)
    text = html_bytes.decode("utf-8")
    assert "<!DOCTYPE html>" in text
    assert "Microsoft Corporation" in text
    assert "Databricks, Inc." in text
    assert "Bottom-Up Synergy Workstreams" in text
    assert "Risk Register Matrix" in text


# =====================================================================
# 4. Local Deal Vault & Supabase Reset Regression
# =====================================================================

def test_local_deal_vault_crud(tmp_path: Path):
    report = DEMO_CASES["lilly_vertex"]["builder"]()
    cfg = Settings(supabase_url="", supabase_key="")

    deal_id, backend = local_vault.save_to_deal_room(report, config=cfg, base_dir=tmp_path)
    assert backend == "local"
    assert deal_id.startswith("local_")

    listed = local_vault.list_deal_room(config=cfg, base_dir=tmp_path)
    assert len(listed) == 1
    assert listed[0]["entity_a"] == "Eli Lilly and Company"

    rehydrated = local_vault.load_from_deal_room(deal_id, storage="local", config=cfg, base_dir=tmp_path)
    assert rehydrated == report

    assert local_vault.delete_from_deal_room(deal_id, storage="local", config=cfg, base_dir=tmp_path) is True
    assert local_vault.list_deal_room(config=cfg, base_dir=tmp_path) == []


def test_supabase_client_reset_allows_reconfiguration():
    # Calling with empty config must NOT permanently lock out future valid configs
    empty_cfg = Settings(supabase_url="", supabase_key="")
    assert supabase_client.fetch_recent_deals(config=empty_cfg) == []
    supabase_client.reset_client()
    assert supabase_client._CLIENT is None
    assert len(supabase_client._FAILED_CREDS) == 0


# =====================================================================
# 5. IC Q&A Copilot & Plotly Charts & Hardened JSON Extraction
# =====================================================================

@pytest.mark.parametrize(
    "question,expected_fragment",
    [
        ("Stress-test the DCF valuation and synergy NPV", "Synergy NPV"),
        ("What are the antitrust and regulatory hurdles?", "Regulatory, Antitrust"),
        ("How do we handle tech stack and talent retention?", "Technology Architecture"),
        ("Give me the priority VDR questions for management", "Priority VDR"),
        ("Walk through the 100-day PMI roadmap", "Post-Merger Integration"),
        ("Give me the executive thesis", "Investment Committee Synthesis"),
    ],
)
def test_qa_copilot_offline_answers(question: str, expected_fragment: str):
    report = DEMO_CASES["vw_rivian"]["builder"]()
    cfg = Settings(llm_api_key="")
    answer = answer_ic_question(question, report, config=cfg)
    assert expected_fragment in answer


def test_all_institutional_plotly_charts_render_cleanly():
    r1 = DEMO_CASES["vw_rivian"]["builder"]()
    r2 = DEMO_CASES["msft_databricks"]["builder"]()
    dcf = compute_synergy_dcf(r1)
    waccs, reals, grid = compute_sensitivity_matrix(r1, dcf.assumptions)

    figs = [
        synergy_bridge_chart(dcf),
        cashflow_phasing_chart(dcf),
        sensitivity_heatmap_chart(waccs, reals, grid),
        risk_matrix_chart(r1),
        pillar_radar_chart(r1),
        workstream_breakdown_chart(r1),
        deal_comparison_chart([r1, r2]),
    ]
    for fig in figs:
        assert len(fig.data) >= 1


def test_parse_extracts_json_wrapped_in_prose_and_backfills_npv():
    from tests.test_smoke import _minimal_valid_json, _stub_entity

    payload = json.loads(_minimal_valid_json())
    payload["synergy_workstreams"] = [
        {
            "name": "Procurement Consolidation",
            "synergy_type": "Cost",
            "annual_run_rate_usd_m": 400.0,
            "one_time_cost_usd_m": 100.0,
            "probability_pct": 80.0,
        }
    ]
    prose_wrapped = "Here is the requested JSON memo:\n" + json.dumps(payload) + "\nHope this helps!"
    report = _parse_llm_response(
        prose_wrapped,
        _stub_entity("Acq", "a.pdf"),
        _stub_entity("Tgt", "b.pdf"),
        DealParams(deal_value_usd_b=10.0),
    )
    assert report.deal.entity_a_name == "Acq"
    assert report.deal.target_synergy_npv_usd_b is not None
    assert report.deal.target_synergy_npv_usd_b > 0
