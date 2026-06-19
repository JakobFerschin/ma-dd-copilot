# 🤝 M&A Due Diligence Copilot

A Streamlit web app that ingests public M&A documents (10-Ks, annual reports, transcripts)
for an Acquirer (Entity A) and Target (Entity B), runs an LLM-grounded MECE analysis, and
produces a structured 5-section synergy & risk memo. Reports are persisted to Supabase for
later retrieval.

## Architecture

| Layer | Tech |
|-------|------|
| Frontend | Streamlit |
| Backend | Python 3.10+ |
| PDF extraction | pdfplumber (+ pypdf metadata fallback) |
| AI | OpenAI-compatible LLM (OpenAI / Azure / OpenRouter / local) |
| Schema validation | Pydantic v2 |
| Database | Supabase (Postgres) |

```
ma-dd-copilot/
├── app.py                      # Streamlit entrypoint (UI + render logic)
├── requirements.txt
├── .env.example
├── config/
│   └── settings.py             # Centralized config (env + .env + Streamlit sidebar)
├── core/
│   └── analyzer.py             # MOCK + LIVE LLM analyzer entrypoint
├── db/
│   └── supabase_client.py      # Supabase persistence (fail-soft)
├── prompts/
│   └── system_prompt.py        # MBB-grade system prompt + schema injection
├── schemas/
│   └── report_schema.py        # Pydantic MECE 5-section report schema
├── utils/
│   └── pdf_extractor.py        # pdfplumber wrapper with per-page + table capture
├── data/
│   ├── uploads/                # gitignored
│   └── outputs/                # gitignored
└── tests/
    └── test_smoke.py
```

## Quickstart

```bash
# 1. Create + activate a virtualenv
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. (Optional) configure secrets
cp .env.example .env
# Edit .env — or just enter keys in the app sidebar at runtime.

# 4. Run
streamlit run app.py
```

The app runs in **MOCK mode** by default (no API key) so the full UI is demoable
immediately. Enter an OpenAI-compatible key in the sidebar to switch to LIVE analysis.

## Supabase schema

Persistence is optional (fail-soft). If you enable it, create this table:

```sql
CREATE TABLE dd_reports (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_a    TEXT,
    entity_b    TEXT,
    report_json JSONB NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT now()
);
```

## Testing

```bash
python -m pytest tests/ -v
```

The smoke test validates the Pydantic schema and the mock analyzer end-to-end
without network or API calls.
