# M&A Due Diligence Copilot — Investment Committee Suite

An institutional-grade **Pre-Merger Intelligence, Synergy Valuation & Investment Committee Platform** built in Python & Streamlit. Ingests multi-format Virtual Data Room (VDR) filings for an **Acquirer (Entity A)** and **Target (Entity B)**, runs a citation-grounded MECE analysis, computes deterministic **Synergy DCF & 2D Sensitivity** models, enables **Interactive IC Q&A**, and exports boardroom-ready **DOCX Memos, Live-Formula Excel Workbooks (`.xlsx`), Print-Ready HTML Briefs, and JSON**.

---

## Key Capabilities

| Module | Institutional Features |
|---|---|
| **Multi-Format VDR Ingestion** | Ingests `.pdf` (`pdfplumber` + `pypdf` fallback), `.docx`, `.txt`, `.md`, and `.csv`. Preserves tables as page-aligned Markdown (`[Source: file | p.N]`) and applies **water-filling proportional budgeting** + M&A signal page prioritization so secondary filings are never starved. |
| **MECE Intelligence Engine** | OpenAI-compatible LLM integration (OpenAI, Azure, OpenRouter, local vLLM/Ollama) with exponential-backoff retries, hardened JSON extraction, strict Pydantic v2 validation, and **3 Flagship 1-Click Case Studies** (*VW × Rivian*, *Microsoft × Databricks*, *Eli Lilly × Vertex*). |
| **Deterministic Synergy DCF** | Zero LLM arithmetic. Computes 5-year synergy ramp-up, integration cost phasing, taxes, Gordon Growth terminal value, **Synergy NPV, Payback, IRR**, Value Creation % of EV, and a **2D WACC × Realization Sensitivity Heatmap** with live stress-testing sliders. |
| **Visual Boardroom Analytics** | Custom Plotly charts: Synergy Value Creation Bridge, Annual Ramp-Up & Cumulative PV, 2D Sensitivity Heatmap, Likelihood × Impact Risk Matrix, MECE 5-Pillar Conviction Radar, Workstream Comparison, and Side-by-Side Deal Benchmarking. |
| **IC Q&A Copilot** | Interactive chat to interrogate the active deal, DCF math, risks, PMI roadmap, and VDR filings — works in both LIVE LLM mode and grounded offline synthesis mode. |
| **4-Format Export Suite** | **DOCX IC Memo** (cover, running headers/page numbers, TOC, tables, citations), **Wall Street Excel Model (`.xlsx`)** with live formulas across 4 sheets, **Print-Ready HTML Brief**, and **JSON**. |
| **Hybrid Deal Room** | Fail-soft **Supabase Cloud** persistence + automatic **Local Deal Vault** (`data/outputs/deal_room/`), lazy on-demand loading (zero N+1 queries), search/filter, and side-by-side multi-deal comparison. |

---

## Project Structure

```text
ma-dd-copilot/
├── app.py                      # 5-view Institutional Investment Committee Streamlit UI
├── config/
│   └── settings.py             # Session-isolated Settings (.env / env / st.secrets / sidebar)
├── core/
│   ├── analyzer.py             # LIVE LLM (retry + JSON hardening) + document-aware MOCK
│   ├── demo_cases.py           # 3 flagship institutional M&A case studies
│   ├── financial_model.py      # Deterministic Synergy DCF, IRR, Payback, 2D Sensitivity
│   └── qa_copilot.py           # Interactive IC Q&A Copilot (LIVE + grounded offline)
├── db/
│   ├── supabase_client.py      # Credential-keyed fail-soft Supabase client with reset_client()
│   └── local_vault.py          # Local JSON Deal Vault + unified Deal Room facade
├── prompts/
│   └── system_prompt.py        # Institutional M&A/MBB system prompt + IC Q&A prompt
├── schemas/
│   └── report_schema.py        # Pydantic v2 MECE schema + workstreams, risks, PMI, audit helpers
├── ui/
│   ├── styles.py               # Private-Bank / Editorial CSS & HTML component helpers
│   └── charts.py               # Institutional Plotly chart suite
├── utils/
│   ├── models.py               # Dependency-free data models + proportional context budgeting
│   ├── pdf_extractor.py        # Multi-format VDR extractor (PDF/DOCX/TXT/MD/CSV)
│   └── export_engine.py        # DOCX Memo + Live-Formula Excel (.xlsx) + HTML Brief
└── tests/
    ├── test_smoke.py           # Core schema, mock analyzer, DOCX & parser smoke tests
    └── test_institutional.py   # Regression & feature tests (DCF, Excel, Vault, Q&A, Charts)
```

---

## Quickstart

```bash
# 1. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate       # macOS / Linux
# .venv\Scripts\activate        # Windows

# 2. Install dependencies
pip install -r requirements.txt

# 3. (Optional) Configure secrets
cp .env.example .env

# 4. Launch the Investment Committee Suite
streamlit run app.py
```

The platform launches ready for immediate demonstration with **3 Flagship Case Studies**, full **Synergy DCF stress-testing**, **IC Q&A**, **Deal Room**, and **DOCX/Excel/HTML exports** — no API key required. Enter any OpenAI-compatible API key in the sidebar to switch to **LIVE VDR analysis**.

## Testing

```bash
python -m pytest tests/ -v
```
