"""
Smoke tests — no network, no LLM calls.

Validates:
  1. The Pydantic MECE schema accepts a well-formed report.
  2. The mock analyzer returns a valid report end-to-end with multi-doc inputs.
  3. JSON round-trip (model_dump -> model_validate) is lossless.
  4. EntityDocuments provenance tagging emits source tags per page.
  5. Supabase client fail-softs cleanly when unconfigured.
  6. DOCX export produces a valid Word file with cover/TOC/metric content.
  7. Live LLM response parser strips markdown fences and rejects bad JSON.
  8. Live LLM response parser injects authoritative deal params.

Run:  python -m pytest tests/ -v
"""
from __future__ import annotations

import io
import json

import pytest

from core.analyzer import AnalyzerError, DealParams, _parse_llm_response, analyze_documents
from schemas.report_schema import DueDiligenceReport
from utils.export_engine import report_to_docx_bytes
from utils.models import EntityDocuments, ExtractedDocument


def _stub_entity(name: str, filename: str) -> EntityDocuments:
    doc = ExtractedDocument(
        filename=filename,
        num_pages=1,
        full_text="--- Page 1 ---\nMock document content for " + name,
        pages=["Mock document content for " + name],
        tables=[],
    )
    return EntityDocuments(entity_name=name, documents=[doc])


# ----- Core schema & mock analyzer -----

def test_mock_report_is_schema_valid():
    entity_a = _stub_entity("Acquirer Co", "acquirer_10k.pdf")
    entity_b = _stub_entity("Target Inc", "target_10k.pdf")
    report = analyze_documents(entity_a, entity_b, use_mock=True)
    assert isinstance(report, DueDiligenceReport)
    assert report.deal.entity_a_name == "Acquirer Co"
    assert report.deal.entity_b_name == "Target Inc"
    assert report.deal.target_synergy_npv_usd_b is not None
    assert len(report.executive_summary.top_deal_breaker_risks) >= 1
    assert len(report.strategic_fit.market_position_overlap) >= 1


def test_deal_params_flow_into_report():
    entity_a = _stub_entity("A", "a.pdf")
    entity_b = _stub_entity("B", "b.pdf")
    params = DealParams(deal_value_usd_b=22.0, time_horizon_months=48)
    report = analyze_documents(entity_a, entity_b, params=params, use_mock=True)
    assert report.deal.deal_value_usd_b == 22.0
    assert report.deal.time_horizon_months == 48


def test_json_roundtrip_lossless():
    entity_a = _stub_entity("A", "a.pdf")
    entity_b = _stub_entity("B", "b.pdf")
    report = analyze_documents(entity_a, entity_b, use_mock=True)
    payload = json.dumps(report.model_dump(mode="json"))
    restored = DueDiligenceReport.model_validate_json(payload)
    assert restored == report


def test_provenance_tags_per_page():
    doc = ExtractedDocument(
        filename="10K.pdf", num_pages=2,
        full_text="p1\n\np2",
        pages=["page one text", "page two text"],
    )
    entity = EntityDocuments(entity_name="X", documents=[doc])
    text = entity.provenance_text()
    assert "[Source: 10K.pdf | p.1]" in text
    assert "[Source: 10K.pdf | p.2]" in text


def test_supabase_failsoft_when_unconfigured():
    from db.supabase_client import fetch_recent_deals, save_report
    assert fetch_recent_deals() == []
    entity_a = _stub_entity("A", "a.pdf")
    entity_b = _stub_entity("B", "b.pdf")
    report = analyze_documents(entity_a, entity_b, use_mock=True)
    assert save_report(report) is None


# ----- DOCX export engine -----

def test_docx_export_produces_valid_word_file():
    report = analyze_documents(_stub_entity("Acquirer Co", "a.pdf"),
                               _stub_entity("Target Inc", "b.pdf"), use_mock=True)
    data = report_to_docx_bytes(report)
    # A valid .docx is a ZIP; magic bytes are 'PK'.
    assert data[:2] == b"PK"
    assert len(data) > 5000  # a formatted memo is non-trivial in size


def test_docx_contains_key_content():
    from docx import Document
    report = analyze_documents(_stub_entity("Acquirer Co", "a.pdf"),
                               _stub_entity("Target Inc", "b.pdf"), use_mock=True)
    doc = Document(io.BytesIO(report_to_docx_bytes(report)))
    full_text = "\n".join(p.text for p in doc.paragraphs)
    assert "PRE-MERGER INTELLIGENCE MEMO" in full_text
    assert "Acquirer Co" in full_text
    assert "Target Inc" in full_text
    assert "Executive Summary" in full_text
    assert "Table of Contents" in full_text
    # Citations should appear as inline source lines
    assert "Source:" in full_text


# ----- Live mode response parser -----

def _minimal_valid_json() -> str:
    """A minimal but schema-valid LLM payload for parse tests."""
    return json.dumps({
        "deal": {
            "entity_a_name": "A", "entity_b_name": "B",
            "analysis_date": "2026-01-01",
        },
        "executive_summary": {
            "strategic_rationale": "rationale.",
            "integration_complexity": "High",
            "complexity_justification": "why",
            "top_deal_breaker_risks": [{"claim": "risk", "sources": []}],
            "recommendation": "Conditional Go",
            "recommendation_rationale": "because",
        },
        "strategic_fit": {
            "market_position_overlap": [{"claim": "x", "sources": []}],
            "cross_selling_potential": [{"claim": "x", "sources": []}],
            "revenue_synergies": [{"claim": "x", "sources": []}],
        },
        "operational_tech": {
            "tech_stack_clash": [{"claim": "x", "sources": []}],
            "supply_chain_redundancies": [{"claim": "x", "sources": []}],
            "cost_synergies": [{"claim": "x", "sources": []}],
        },
        "cultural_risk": {
            "leadership_style": [{"claim": "x", "sources": []}],
            "talent_retention_risk": [{"claim": "x", "sources": []}],
        },
        "regulatory_esg": {
            "antitrust_flags": [{"claim": "x", "sources": []}],
            "esg_csrd_gaps": [{"claim": "x", "sources": []}],
        },
        "data_gaps": [],
    })


def test_parse_strips_markdown_fences_and_injects_params():
    entity_a = _stub_entity("Real Acquirer", "a.pdf")
    entity_b = _stub_entity("Real Target", "b.pdf")
    params = DealParams(deal_value_usd_b=15.0, time_horizon_months=36)
    # Wrap the JSON in fences the way some models do even with json_object mode
    fenced = "```json\n" + _minimal_valid_json() + "\n```"
    report = _parse_llm_response(fenced, entity_a, entity_b, params)
    assert isinstance(report, DueDiligenceReport)
    # UI-entered params must override the model's placeholders
    assert report.deal.entity_a_name == "Real Acquirer"
    assert report.deal.entity_b_name == "Real Target"
    assert report.deal.deal_value_usd_b == 15.0


def test_parse_rejects_garbage():
    entity_a = _stub_entity("A", "a.pdf")
    entity_b = _stub_entity("B", "b.pdf")
    with pytest.raises(AnalyzerError):
        _parse_llm_response("this is not json at all", entity_a, entity_b, DealParams())


def test_parse_rejects_empty():
    entity_a = _stub_entity("A", "a.pdf")
    entity_b = _stub_entity("B", "b.pdf")
    with pytest.raises(AnalyzerError):
        _parse_llm_response("   ", entity_a, entity_b, DealParams())
