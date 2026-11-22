# M&A Due Diligence Copilot — Enterprise Investment Committee Platform (v2.0)

An enterprise-grade **Pre-Merger Intelligence, Quantitative Valuation, Legal Diligence & Deal Execution Platform** built for Investment Banks, Private Equity Funds, and Corporate Development teams.

> Full 100-Point Enterprise Architecture & Security Traceability Matrix: **[docs/TRUST_CENTER.md](docs/TRUST_CENTER.md)**

---

## Enterprise Capabilities Across 10 Strategic Pillars

| Pillar | Implemented Modules |
|---|---|
| **1. Enterprise API & Infra** | [api/main.py](api/main.py) (FastAPI v2.0 REST + OpenAPI), [Dockerfile](Dockerfile), [docker-compose.yml](docker-compose.yml), [infra/k8s/deployment.yaml](infra/k8s/deployment.yaml), [infra/terraform/main.tf](infra/terraform/main.tf), [.github/workflows/ci.yml](.github/workflows/ci.yml) |
| **2. Bank-Grade Security & Audit** | [api/auth.py](api/auth.py) (RBAC, Chinese Walls, Clean Team gate, JWT/OIDC, PII/MNPI Redaction & Re-hydration, Prompt-Injection Firewall), [api/audit.py](api/audit.py) (Tamper-Evident SHA-256 WORM Hash Chain) |
| **3. Industrial VDR Ingestion** | [vdr/ingestion.py](vdr/ingestion.py) (Recursive ZIP/Folder ingest, SHA-256 deduplication, native Excel formula/hardcode anomaly detector, PPTX extractor, 9-category taxonomy, sector VDR completeness checker, v1→v2 diff) |
| **4. Hybrid RAG & Multi-Agent** | [rag/engine.py](rag/engine.py) (Okapi BM25 + TF-IDF Cosine Hybrid Search, cross-document contradiction detector), [agents/orchestrator.py](agents/orchestrator.py) (6 Specialist Agents, Adversarial Red-Team Critic, Deterministic Numerical Fact-Checker, Model Router, Golden Evaluation Harness) |
| **5. Quant Valuation Suite** | [core/financial_model.py](core/financial_model.py) (Synergy DCF + 2D Sensitivity), [quant/qoe_nwc.py](quant/qoe_nwc.py) (QoE Normalized EBITDA, NWC Peg, Customer Cohorts), [quant/merger_lbo.py](quant/merger_lbo.py) (3-Statement Accretion/Dilution Merger Model, PE LBO/IRR/MOIC, Section 338(h)(10) Tax Step-Up), [quant/comps_montecarlo.py](quant/comps_montecarlo.py) (Valuation Football Field, 10,000-Run Correlated Monte Carlo) |
| **6. Legal & Regulatory DD** | [legal/diligence.py](legal/diligence.py) (CoC & Anti-Assignment Scanner, ABA/CMS SPA Benchmark, HSR/EC/CMA/Bundeskartellamt/SAMR Antitrust & CFIUS/FDI Predictor, IP/OSS/Cyber/CSRD Disclosure Schedules) |
| **7. External Market Data** | [vdr/connectors.py](vdr/connectors.py) (Live SEC EDGAR 10-K/10-Q/8-K auto-ingestion by ticker/CIK, OFAC/Sanctions & Entity Resolution screener, Datasite/Intralinks/SharePoint/Box & CapIQ/Bloomberg/PitchBook adapter registry) |
| **8. Deal Execution & IC Portal** | [workflow/deal_lifecycle.py](workflow/deal_lifecycle.py) (8-Stage M&A Pipeline, VDR Q&A Tracker, Analyst Claim Sign-Off, Digital IC Voting Quorum, Isolated Clean Team Workbench, Post-Closing Value-Capture Tracker, White-Label Studio & Metering) |
| **9. Boardroom Publishing** | [utils/pptx_exporter.py](utils/pptx_exporter.py) (Native 12-Slide White-Label PPTX Pitchbook), [utils/export_engine.py](utils/export_engine.py) (IC Memo DOCX, Live-Formula Wall Street Excel `.xlsx`, Print-Ready HTML), [utils/audit_pack.py](utils/audit_pack.py) (60-Sec Red-Flag Flash, Jira PMI CSV, EN/DE/FR/ES Briefs, 8-File SHA-256 Audit Pack ZIP) |
| **10. Trust Center & Governance** | [docs/TRUST_CENTER.md](docs/TRUST_CENTER.md) (SOC 2 Type II / ISO 27001 / EU AI Act / ZDR Architecture + Complete 100-Point Implementation Traceability Matrix) |

---

## Quickstart

```bash
# 1. Setup virtual environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Launch Enterprise Investment Committee UI (Streamlit)
streamlit run app.py

# 3. Launch Enterprise REST API (FastAPI + Swagger /docs)
uvicorn api.main:app --host 0.0.0.0 --port 8000

# 4. Or launch the full containerized stack (API + UI + pgvector Postgres + Redis)
docker compose up --build
```

## Testing

```bash
python -m pytest tests/ -v
```
