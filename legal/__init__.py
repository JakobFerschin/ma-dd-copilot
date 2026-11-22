"""
Institutional Legal, Regulatory, Antitrust/FDI & Tech/ESG Due Diligence Suite.

Exports:
  - Change-of-Control & Material Contract Scanner: `CoCFinding`, `scan_change_of_control`
  - SPA / APA Market Benchmarking (ABA / CMS): `SPABenchmarkItem`, `SPABenchmarkResult`, `benchmark_spa_terms`
  - Antitrust Merger Control & FDI Screening: `AntitrustJurisdictionCheck`, `AntitrustAssessment`, `assess_antitrust_and_fdi`
  - IP / OSS, Cybersecurity & EU CSRD/CBAM/LkSG Diligence: `IPCyberESGDiligence`, `assess_ip_cyber_esg`
"""
from __future__ import annotations

from legal.diligence import (
    AntitrustAssessment,
    AntitrustJurisdictionCheck,
    CoCFinding,
    IPCyberESGDiligence,
    SPABenchmarkItem,
    SPABenchmarkResult,
    assess_antitrust_and_fdi,
    assess_ip_cyber_esg,
    benchmark_spa_terms,
    scan_change_of_control,
)

__all__ = [
    "CoCFinding",
    "scan_change_of_control",
    "SPABenchmarkItem",
    "SPABenchmarkResult",
    "benchmark_spa_terms",
    "AntitrustJurisdictionCheck",
    "AntitrustAssessment",
    "assess_antitrust_and_fdi",
    "IPCyberESGDiligence",
    "assess_ip_cyber_esg",
]
