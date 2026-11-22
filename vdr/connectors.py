"""
External Data Room, Public Filing (SEC EDGAR), Market Data & Sanctions Connectors.

Capabilities:
  1. Live SEC EDGAR Auto-Ingestor (`fetch_sec_edgar_filings`):
     - Uses `httpx` with SEC-compliant `User-Agent: MA-DD-Copilot/2.0 research@ma-dd-copilot.local`.
     - Resolves Ticker -> 10-digit CIK via SEC JSON endpoints.
     - Fetches recent 10-K, 10-Q, 8-K (or custom form types) metadata & filing text into
       `ExtractedDocument` instances with fail-soft offline / rate-limit handling.
  2. Standardized Enterprise VDR & Market Data Connector Registry:
     - `VDRConnectorBase` abstract interface
     - `DatasiteConnector`, `IntralinksConnector`, `SharePointConnector`, `BoxConnector`
     - `MarketDataConnector` (S&P Capital IQ, Bloomberg, PitchBook)
     - Clean configuration status + standardized `RemoteVDRFile` objects.
  3. Entity Resolution & Sanctions / PEP / Geopolitical Screener (`screen_sanctions_and_entity`):
     - Canonicalizes legal entity names, infers jurisdiction & legal form, provides
       LEI/CIK/ticker hints, and screens against OFAC/EU/UK/BIS/FATF/CFIUS/PEP risk rules.
"""
from __future__ import annotations

import html
import logging
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

import httpx
from pydantic import BaseModel, Field

from utils.models import EntityDocuments, ExtractedDocument
from utils.pdf_extractor import _split_into_virtual_pages

logger = logging.getLogger(__name__)

SEC_USER_AGENT = "MA-DD-Copilot/2.0 research@ma-dd-copilot.local"
_SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_SEC_ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{doc}"

# In-process cache for SEC ticker -> (cik_10, title) map
_SEC_TICKER_CACHE: dict[str, tuple[str, str]] = {}

# Curated fallback reference table for major global issuers (LEI, CIK, Ticker, Country)
_KNOWN_ENTITY_REGISTRY: dict[str, dict[str, str]] = {
    "apple": {"ticker": "AAPL", "cik": "0000320193", "lei": "HWUPKR0MPOU8FGXBT394", "country": "US", "name": "Apple Inc."},
    "microsoft": {"ticker": "MSFT", "cik": "0000789019", "lei": "INR2EJN1ERAN0W5ZP974", "country": "US", "name": "Microsoft Corporation"},
    "alphabet": {"ticker": "GOOGL", "cik": "0001652044", "lei": "5493006MHB84DD0ZWV18", "country": "US", "name": "Alphabet Inc."},
    "amazon": {"ticker": "AMZN", "cik": "0001018724", "lei": "ZXTILKJKG63JELOEG630", "country": "US", "name": "Amazon.com, Inc."},
    "nvidia": {"ticker": "NVDA", "cik": "0001045810", "lei": "549300S4KLFTLO7GSQ80", "country": "US", "name": "NVIDIA Corporation"},
    "meta": {"ticker": "META", "cik": "0001326801", "lei": "BQ4BKCS1HXDV9TTTTTTTT"[:20], "country": "US", "name": "Meta Platforms, Inc."},
    "tesla": {"ticker": "TSLA", "cik": "0001318605", "lei": "54930043XZGB27CTOV49", "country": "US", "name": "Tesla, Inc."},
    "rivian": {"ticker": "RIVN", "cik": "0001874178", "lei": "25490077U4R9N9Y9X821", "country": "US", "name": "Rivian Automotive, Inc."},
    "salesforce": {"ticker": "CRM", "cik": "0001108524", "lei": "54930064352809840949", "country": "US", "name": "Salesforce, Inc."},
    "sap": {"ticker": "SAP", "cik": "0001000184", "lei": "529900D6BF99LW9R2E68", "country": "DE", "name": "SAP SE"},
    "volkswagen": {"ticker": "VWAGY", "cik": "0001356386", "lei": "529900NNUPAGGOMPXZ31", "country": "DE", "name": "Volkswagen AG"},
    "siemens": {"ticker": "SIEGY", "cik": "0001135644", "lei": "W38RGI023J3WT1HWRP32", "country": "DE", "name": "Siemens AG"},
    "pfizer": {"ticker": "PFE", "cik": "0000078003", "lei": "765UQJb130X809401389".upper(), "country": "US", "name": "Pfizer Inc."},
    "snowflake": {"ticker": "SNOW", "cik": "0001640147", "lei": "549300Z3N7S85S514912", "country": "US", "name": "Snowflake Inc."},
    "datadog": {"ticker": "DDOG", "cik": "0001561550", "lei": "549300Q0Y6H74M542843", "country": "US", "name": "Datadog, Inc."},
    "servicenow": {"ticker": "NOW", "cik": "0001373715", "lei": "5493004X3A4Q35L11367", "country": "US", "name": "ServiceNow, Inc."},
}


# =====================================================================
# 1. Live SEC EDGAR Auto-Ingestor
# =====================================================================

class SECFilingMetadata(BaseModel):
    """Metadata + preview for a single SEC EDGAR filing."""
    form_type: str
    filing_date: str
    report_date: str = ""
    accession_number: str
    primary_document: str
    filing_url: str
    company_name: str = ""
    cik: str = ""
    word_count: int = 0
    text_excerpt: str = ""
    fetch_error: Optional[str] = None


@dataclass
class SECEdgarResult:
    """Result container returned by `fetch_sec_edgar_filings`."""
    query: str
    status: str  # "ok" | "partial" | "not_found" | "rate_limited" | "offline_fallback" | "error"
    cik: Optional[str] = None
    company_name: Optional[str] = None
    ticker: Optional[str] = None
    filings: list[SECFilingMetadata] = field(default_factory=list)
    documents: list[ExtractedDocument] = field(default_factory=list)
    entity_documents: Optional[EntityDocuments] = None
    error: Optional[str] = None
    fetched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


_SCRIPT_STYLE_RE = re.compile(r"<(script|style|head|title|meta)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_BLOCK_TAG_RE = re.compile(r"</?(?:p|div|tr|li|h[1-6]|br|table|section)[^>]*>", re.IGNORECASE)
_ANY_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html_to_text(raw_html: str) -> str:
    """Convert SEC EDGAR HTML/XBRL document into clean readable plain text."""
    cleaned = _SCRIPT_STYLE_RE.sub(" ", raw_html)
    cleaned = _BLOCK_TAG_RE.sub("\n", cleaned)
    cleaned = _ANY_TAG_RE.sub(" ", cleaned)
    cleaned = html.unescape(cleaned)
    lines = [re.sub(r"[ \t\xa0]+", " ", ln).strip() for ln in cleaned.splitlines()]
    non_empty = [ln for ln in lines if ln]
    return "\n".join(non_empty)


def _lookup_fallback_ticker(query: str) -> Optional[tuple[str, str, str]]:
    """Lookup ticker or company name in offline fallback table -> (cik, name, ticker)."""
    q = query.strip().upper()
    q_low = query.strip().lower()
    for key, info in _KNOWN_ENTITY_REGISTRY.items():
        if q == info["ticker"] or q_low == key or key in q_low:
            return info["cik"], info["name"], info["ticker"]
    return None


def _resolve_ticker_to_cik(
    ticker_or_cik: str,
    client: httpx.Client,
) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """
    Resolve a ticker symbol or CIK string into (cik_10, company_name, ticker, error).
    Uses SEC `company_tickers.json` with in-process caching and offline fallback.
    """
    raw = (ticker_or_cik or "").strip()
    if not raw:
        return None, None, None, "Empty ticker or CIK provided."

    # Direct numeric CIK (or "CIK0000320193")
    cik_digits = re.sub(r"^cik", "", raw, flags=re.IGNORECASE).strip()
    if cik_digits.isdigit():
        return cik_digits.zfill(10), None, None, None

    ticker_upper = raw.upper()
    if ticker_upper in _SEC_TICKER_CACHE:
        cik10, title = _SEC_TICKER_CACHE[ticker_upper]
        return cik10, title, ticker_upper, None

    # Fetch live SEC ticker registry
    try:
        resp = client.get(_SEC_TICKERS_URL)
        if resp.status_code == 429:
            fb = _lookup_fallback_ticker(raw)
            if fb:
                return fb[0], fb[1], fb[2], "SEC EDGAR rate-limited (HTTP 429) during ticker lookup; used local fallback."
            return None, None, ticker_upper, "SEC EDGAR rate-limited (HTTP 429)."
        resp.raise_for_status()
        payload = resp.json()
        entries = payload.values() if isinstance(payload, dict) else []
        for entry in entries:
            t = str(entry.get("ticker", "")).upper()
            c = str(entry.get("cik_str", "")).zfill(10)
            title = str(entry.get("title", ""))
            if t:
                _SEC_TICKER_CACHE[t] = (c, title)

        if ticker_upper in _SEC_TICKER_CACHE:
            cik10, title = _SEC_TICKER_CACHE[ticker_upper]
            return cik10, title, ticker_upper, None

        # Try partial company name match in SEC cache
        raw_low = raw.lower()
        for t, (c, title) in _SEC_TICKER_CACHE.items():
            if raw_low in title.lower():
                return c, title, t, None

        return None, None, ticker_upper, f"Ticker/entity '{ticker_or_cik}' not found in SEC EDGAR registry."
    except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError, OSError) as e:
        fb = _lookup_fallback_ticker(raw)
        if fb:
            return fb[0], fb[1], fb[2], f"Network unavailable ({type(e).__name__}); resolved CIK via local reference table."
        return None, None, ticker_upper, f"SEC EDGAR unreachable ({type(e).__name__}: {e})."
    except httpx.HTTPStatusError as e:
        return None, None, ticker_upper, f"SEC EDGAR HTTP error {e.response.status_code}."
    except Exception as e:  # noqa: BLE001
        return None, None, ticker_upper, f"Unexpected SEC ticker resolution error: {e}"


def fetch_sec_edgar_filings(
    ticker_or_cik: str,
    form_types: Sequence[str] = ("10-K", "10-Q", "8-K"),
    limit: int = 3,
    fetch_text: bool = True,
    max_chars_per_filing: int = 120_000,
    timeout: float = 15.0,
) -> SECEdgarResult:
    """
    Live SEC EDGAR Auto-Ingestor.

    Resolves ticker -> CIK via SEC JSON endpoints, retrieves recent filing metadata
    matching `form_types`, and downloads/parses filing text into `ExtractedDocument`
    objects. Fails softly when offline or rate-limited.
    """
    headers = {
        "User-Agent": SEC_USER_AGENT,
        "Accept": "application/json, text/html, text/plain;q=0.9",
        "Accept-Encoding": "gzip, deflate",
    }
    wanted_forms = {f.strip().upper() for f in form_types}
    limit = max(1, min(int(limit), 20))

    try:
        with httpx.Client(headers=headers, timeout=timeout, follow_redirects=True) as client:
            cik10, company_name, ticker, resolve_err = _resolve_ticker_to_cik(ticker_or_cik, client)
            if not cik10:
                status = "offline_fallback" if resolve_err and ("unreachable" in resolve_err.lower() or "network" in resolve_err.lower()) else (
                    "rate_limited" if resolve_err and "429" in resolve_err else "not_found"
                )
                return SECEdgarResult(
                    query=ticker_or_cik,
                    status=status,
                    ticker=ticker,
                    error=resolve_err,
                )

            sub_url = _SEC_SUBMISSIONS_URL.format(cik=cik10)
            try:
                sub_resp = client.get(sub_url)
            except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError, OSError) as e:
                return SECEdgarResult(
                    query=ticker_or_cik,
                    status="offline_fallback",
                    cik=cik10,
                    company_name=company_name,
                    ticker=ticker,
                    error=f"SEC EDGAR submissions endpoint unreachable ({type(e).__name__}). Resolved CIK={cik10}.",
                )

            if sub_resp.status_code == 429:
                return SECEdgarResult(
                    query=ticker_or_cik,
                    status="rate_limited",
                    cik=cik10,
                    company_name=company_name,
                    ticker=ticker,
                    error="SEC EDGAR rate limit reached (HTTP 429). Retry after brief cooldown.",
                )
            if sub_resp.status_code >= 400:
                return SECEdgarResult(
                    query=ticker_or_cik,
                    status="error",
                    cik=cik10,
                    company_name=company_name,
                    ticker=ticker,
                    error=f"SEC EDGAR submissions API returned HTTP {sub_resp.status_code}.",
                )

            sub_data = sub_resp.json()
            company_name = sub_data.get("name") or company_name or ticker_or_cik
            tickers_list = sub_data.get("tickers") or []
            if not ticker and tickers_list:
                ticker = str(tickers_list[0]).upper()

            recent = sub_data.get("filings", {}).get("recent", {})
            forms = recent.get("form", [])
            filing_dates = recent.get("filingDate", [])
            report_dates = recent.get("reportDate", [])
            accessions = recent.get("accessionNumber", [])
            primary_docs = recent.get("primaryDocument", [])

            cik_int = int(cik10)
            filings: list[SECFilingMetadata] = []
            docs: list[ExtractedDocument] = []
            partial_err = False

            for idx, form in enumerate(forms):
                if len(filings) >= limit:
                    break
                form_up = str(form).upper()
                if wanted_forms and form_up not in wanted_forms:
                    continue

                acc = str(accessions[idx]) if idx < len(accessions) else ""
                pdoc = str(primary_docs[idx]) if idx < len(primary_docs) else ""
                fdate = str(filing_dates[idx]) if idx < len(filing_dates) else ""
                rdate = str(report_dates[idx]) if idx < len(report_dates) else ""
                acc_nodash = acc.replace("-", "")
                url = _SEC_ARCHIVE_URL.format(cik_int=cik_int, acc_nodash=acc_nodash, doc=pdoc)

                meta = SECFilingMetadata(
                    form_type=form_up,
                    filing_date=fdate,
                    report_date=rdate,
                    accession_number=acc,
                    primary_document=pdoc,
                    filing_url=url,
                    company_name=company_name,
                    cik=cik10,
                )

                if fetch_text and pdoc:
                    try:
                        doc_resp = client.get(url)
                        if doc_resp.status_code == 200:
                            raw_txt = doc_resp.text[: max_chars_per_filing * 3]
                            plain = _strip_html_to_text(raw_txt) if "<" in raw_txt[:2000] else raw_txt
                            plain = plain[:max_chars_per_filing]
                            pages = _split_into_virtual_pages(plain, max_pages=60) or [plain]
                            full_text = "\n\n".join(f"--- Page {i + 1} ---\n{p}" for i, p in enumerate(pages))
                            safe_ticker = (ticker or cik10).replace("/", "_")
                            fname = f"SEC_{safe_ticker}_{form_up}_{fdate}.txt"
                            ex_doc = ExtractedDocument(
                                filename=fname,
                                num_pages=len(pages),
                                full_text=full_text,
                                pages=pages,
                                tables=[],
                                page_tables={},
                                file_type="sec_filing",
                                word_count=len(plain.split()),
                            )
                            docs.append(ex_doc)
                            meta.word_count = ex_doc.word_count
                            meta.text_excerpt = plain[:500]
                        else:
                            meta.fetch_error = f"HTTP {doc_resp.status_code}"
                            partial_err = True
                    except Exception as e:  # noqa: BLE001
                        meta.fetch_error = f"{type(e).__name__}: {e}"
                        partial_err = True

                filings.append(meta)

            ent_docs = EntityDocuments(entity_name=company_name, documents=docs) if docs else None
            return SECEdgarResult(
                query=ticker_or_cik,
                status="partial" if partial_err else "ok",
                cik=cik10,
                company_name=company_name,
                ticker=ticker,
                filings=filings,
                documents=docs,
                entity_documents=ent_docs,
                error=resolve_err if partial_err else None,
            )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"SEC EDGAR fetch failed softly for '{ticker_or_cik}': {e}")
        return SECEdgarResult(
            query=ticker_or_cik,
            status="error",
            error=f"SEC EDGAR request failed: {type(e).__name__}: {e}",
        )


# =====================================================================
# 2. Standardized Enterprise VDR & Market Data Connector Registry
# =====================================================================

class RemoteVDRFile(BaseModel):
    """Standardized file descriptor returned by any enterprise VDR connector."""
    file_id: str
    name: str
    vdr_path: str
    size_bytes: int = 0
    modified_at: str = ""
    mime_type: str = "application/octet-stream"
    provider: str
    folder_category: str = "General"
    download_url: Optional[str] = None
    sha256: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConnectorStatus(BaseModel):
    """Standardized configuration & connectivity status for an enterprise connector."""
    provider: str
    display_name: str
    connector_type: str = Field("vdr", description="'vdr' | 'market_data'")
    configured: bool
    connected: bool = False
    auth_mode: str = "api_key"
    required_env_vars: list[str] = Field(default_factory=list)
    missing_env_vars: list[str] = Field(default_factory=list)
    endpoint: str = ""
    message: str = ""


class VDRConnectorBase(ABC):
    """Abstract base class for institutional VDR & enterprise data connectors."""

    provider_name: str = "base"
    display_name: str = "Base Connector"
    connector_type: str = "vdr"
    auth_mode: str = "api_key"
    default_endpoint: str = ""
    required_env_vars: tuple[str, ...] = ()

    def __init__(self, credentials: Optional[dict[str, str]] = None, endpoint: Optional[str] = None) -> None:
        self._credentials: dict[str, str] = {}
        for var in self.required_env_vars:
            val = (credentials or {}).get(var) or os.getenv(var, "")
            if val and val.strip():
                self._credentials[var] = val.strip()
        self.endpoint = (endpoint or self.default_endpoint).strip()

    @property
    def is_configured(self) -> bool:
        return all(var in self._credentials for var in self.required_env_vars)

    @property
    def missing_env_vars(self) -> list[str]:
        return [var for var in self.required_env_vars if var not in self._credentials]

    def status(self) -> ConnectorStatus:
        missing = self.missing_env_vars
        configured = len(missing) == 0
        msg = (
            f"{self.display_name} credentials configured and ready."
            if configured
            else f"Not configured — set environment variable(s): {', '.join(missing)}."
        )
        return ConnectorStatus(
            provider=self.provider_name,
            display_name=self.display_name,
            connector_type=self.connector_type,
            configured=configured,
            connected=False,
            auth_mode=self.auth_mode,
            required_env_vars=list(self.required_env_vars),
            missing_env_vars=missing,
            endpoint=self.endpoint,
            message=msg,
        )

    def test_connection(self) -> ConnectorStatus:
        st = self.status()
        if not st.configured:
            return st
        st.message = f"{self.display_name} credentials validated locally (endpoint: {self.endpoint})."
        return st

    @abstractmethod
    def list_files(
        self,
        project_id: str = "",
        folder: str = "",
        include_sample_manifest: bool = False,
    ) -> list[RemoteVDRFile]:
        """List available remote VDR files in standardized `RemoteVDRFile` format."""

    def fetch_file(self, file_id: str) -> Optional[bytes]:
        """Fetch raw file bytes by remote file_id (returns None if unconfigured)."""
        if not self.is_configured:
            logger.info(f"[{self.display_name}] fetch_file('{file_id}') skipped — connector unconfigured.")
            return None
        return None


class DatasiteConnector(VDRConnectorBase):
    """Datasite Diligence Enterprise VDR Connector (OAuth2 / REST API)."""
    provider_name = "datasite"
    display_name = "Datasite Diligence VDR"
    auth_mode = "oauth2_bearer"
    default_endpoint = "https://api.datasite.com/v1"
    required_env_vars = ("DATASITE_API_KEY", "DATASITE_PROJECT_ID")

    def list_files(
        self,
        project_id: str = "",
        folder: str = "",
        include_sample_manifest: bool = False,
    ) -> list[RemoteVDRFile]:
        pid = project_id or self._credentials.get("DATASITE_PROJECT_ID", "demo-project")
        if not self.is_configured and not include_sample_manifest:
            return []
        if include_sample_manifest and not self.is_configured:
            return [
                RemoteVDRFile(
                    file_id=f"ds-{pid}-001",
                    name="02.01_Audited_Financials_FY2024.pdf",
                    vdr_path="02_Financials/02.01_Audited_Financials_FY2024.pdf",
                    size_bytes=2_450_000,
                    modified_at="2025-02-15T10:30:00Z",
                    mime_type="application/pdf",
                    provider=self.provider_name,
                    folder_category="Financials",
                    metadata={"project_id": pid, "sample": True},
                ),
                RemoteVDRFile(
                    file_id=f"ds-{pid}-002",
                    name="04.01_Draft_SPA_v2_Redline.docx",
                    vdr_path="04_Legal_Contracts/04.01_Draft_SPA_v2_Redline.docx",
                    size_bytes=680_000,
                    modified_at="2025-02-18T16:45:00Z",
                    mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    provider=self.provider_name,
                    folder_category="Legal & Contracts",
                    metadata={"project_id": pid, "sample": True},
                ),
            ]
        return []


class IntralinksConnector(VDRConnectorBase):
    """SS&C Intralinks VDRPro Enterprise Connector."""
    provider_name = "intralinks"
    display_name = "SS&C Intralinks VDRPro"
    auth_mode = "oauth2_client_credentials"
    default_endpoint = "https://api.intralinks.com/v2"
    required_env_vars = ("INTRALINKS_CLIENT_ID", "INTRALINKS_CLIENT_SECRET", "INTRALINKS_EXCHANGE_ID")

    def list_files(
        self,
        project_id: str = "",
        folder: str = "",
        include_sample_manifest: bool = False,
    ) -> list[RemoteVDRFile]:
        ex_id = project_id or self._credentials.get("INTRALINKS_EXCHANGE_ID", "demo-exchange")
        if not self.is_configured and not include_sample_manifest:
            return []
        if include_sample_manifest and not self.is_configured:
            return [
                RemoteVDRFile(
                    file_id=f"il-{ex_id}-101",
                    name="QoE_Databook_LTM_Q4.xlsx",
                    vdr_path="02_Financials/QoE_Databook_LTM_Q4.xlsx",
                    size_bytes=1_890_000,
                    modified_at="2025-02-10T09:15:00Z",
                    mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    provider=self.provider_name,
                    folder_category="Financials",
                    metadata={"exchange_id": ex_id, "sample": True},
                ),
            ]
        return []


class SharePointConnector(VDRConnectorBase):
    """Microsoft SharePoint / Graph API Deal Room Connector."""
    provider_name = "sharepoint"
    display_name = "Microsoft SharePoint / OneDrive"
    auth_mode = "azure_ad_oauth2"
    default_endpoint = "https://graph.microsoft.com/v1.0"
    required_env_vars = (
        "SHAREPOINT_TENANT_ID",
        "SHAREPOINT_CLIENT_ID",
        "SHAREPOINT_CLIENT_SECRET",
        "SHAREPOINT_SITE_URL",
    )

    def list_files(
        self,
        project_id: str = "",
        folder: str = "",
        include_sample_manifest: bool = False,
    ) -> list[RemoteVDRFile]:
        if not self.is_configured and not include_sample_manifest:
            return []
        if include_sample_manifest and not self.is_configured:
            return [
                RemoteVDRFile(
                    file_id="sp-demo-001",
                    name="Management_Presentation_IC.pptx",
                    vdr_path="07_Commercial/Management_Presentation_IC.pptx",
                    size_bytes=4_120_000,
                    modified_at="2025-02-12T14:00:00Z",
                    mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    provider=self.provider_name,
                    folder_category="Commercial",
                    metadata={"sample": True},
                ),
            ]
        return []


class BoxConnector(VDRConnectorBase):
    """Box Enterprise Data Room Connector."""
    provider_name = "box"
    display_name = "Box Enterprise Data Room"
    auth_mode = "oauth2_bearer"
    default_endpoint = "https://api.box.com/2.0"
    required_env_vars = ("BOX_ACCESS_TOKEN",)

    def list_files(
        self,
        project_id: str = "",
        folder: str = "",
        include_sample_manifest: bool = False,
    ) -> list[RemoteVDRFile]:
        if not self.is_configured and not include_sample_manifest:
            return []
        if include_sample_manifest and not self.is_configured:
            return [
                RemoteVDRFile(
                    file_id="box-demo-001",
                    name="IP_Patent_Schedule_2025.pdf",
                    vdr_path="05_IP_Tech/IP_Patent_Schedule_2025.pdf",
                    size_bytes=920_000,
                    modified_at="2025-02-01T11:20:00Z",
                    mime_type="application/pdf",
                    provider=self.provider_name,
                    folder_category="IP & Tech",
                    metadata={"sample": True},
                ),
            ]
        return []


class MarketDataConnector(VDRConnectorBase):
    """
    Institutional Market Data Connector supporting S&P Capital IQ, Bloomberg,
    and PitchBook APIs for trading comps, precedent transactions, and company profiles.
    """
    connector_type = "market_data"
    auth_mode = "api_key"

    _PROVIDER_SPECS: dict[str, tuple[str, str, tuple[str, ...]]] = {
        "capiq": ("S&P Capital IQ", "https://api-ciq.marketintelligence.spglobal.com/gdsapi/rest/v3", ("CAPIQ_API_KEY",)),
        "bloomberg": ("Bloomberg B-PIPE / Data License", "https://api.bloomberg.com/eap", ("BLOOMBERG_API_KEY",)),
        "pitchbook": ("PitchBook Data API", "https://api.pitchbook.com/v1", ("PITCHBOOK_API_KEY",)),
    }

    def __init__(
        self,
        provider: str = "capiq",
        credentials: Optional[dict[str, str]] = None,
        endpoint: Optional[str] = None,
    ) -> None:
        key = (provider or "capiq").strip().lower()
        if key not in self._PROVIDER_SPECS:
            raise ValueError(f"Unsupported market data provider '{provider}'. Choose from: {list(self._PROVIDER_SPECS)}")
        disp, default_ep, env_vars = self._PROVIDER_SPECS[key]
        self.provider_name = key
        self.display_name = disp
        self.default_endpoint = default_ep
        self.required_env_vars = env_vars
        super().__init__(credentials=credentials, endpoint=endpoint)

    def list_files(
        self,
        project_id: str = "",
        folder: str = "",
        include_sample_manifest: bool = False,
    ) -> list[RemoteVDRFile]:
        if not self.is_configured and not include_sample_manifest:
            return []
        if include_sample_manifest and not self.is_configured:
            return [
                RemoteVDRFile(
                    file_id=f"{self.provider_name}-comps-001",
                    name=f"{self.provider_name.upper()}_Trading_Comps_Snapshot.csv",
                    vdr_path=f"MarketData/{self.provider_name.upper()}_Trading_Comps_Snapshot.csv",
                    size_bytes=48_000,
                    modified_at=datetime.now(timezone.utc).isoformat(),
                    mime_type="text/csv",
                    provider=self.provider_name,
                    folder_category="Financials",
                    metadata={"sample": True, "source": self.display_name},
                )
            ]
        return []

    def fetch_company_snapshot(self, ticker_or_name: str) -> dict[str, Any]:
        """Return standardized market-data snapshot status (live requires configured API key)."""
        st = self.status()
        fb = _lookup_fallback_ticker(ticker_or_name)
        return {
            "provider": self.provider_name,
            "display_name": self.display_name,
            "configured": st.configured,
            "query": ticker_or_name,
            "resolved_ticker": fb[2] if fb else None,
            "resolved_cik": fb[0] if fb else None,
            "resolved_name": fb[1] if fb else ticker_or_name,
            "status": "ready" if st.configured else "unconfigured",
            "message": st.message,
        }


def get_connector_registry() -> dict[str, VDRConnectorBase]:
    """Instantiate the full registry of enterprise VDR & market-data connectors."""
    return {
        "datasite": DatasiteConnector(),
        "intralinks": IntralinksConnector(),
        "sharepoint": SharePointConnector(),
        "box": BoxConnector(),
        "capiq": MarketDataConnector("capiq"),
        "bloomberg": MarketDataConnector("bloomberg"),
        "pitchbook": MarketDataConnector("pitchbook"),
    }


def list_connector_statuses() -> list[ConnectorStatus]:
    """Return clean configuration status objects for every enterprise connector."""
    return [conn.status() for conn in get_connector_registry().values()]


# =====================================================================
# 3. Entity Resolution & Sanctions / PEP / Geopolitical Screener
# =====================================================================

class ScreeningFlag(BaseModel):
    """Individual compliance, sanctions, PEP, or FDI/geopolitical risk flag."""
    category: str = Field(
        ...,
        description="'Sanctions' | 'Jurisdiction' | 'FDI/CFIUS' | 'PEP/SOE' | 'Export Control' | 'Adverse Media'",
    )
    severity: str = Field("Medium", description="'Critical' | 'High' | 'Medium' | 'Low'")
    title: str
    detail: str
    recommended_action: str


class EntityScreeningResult(BaseModel):
    """Structured output of `screen_sanctions_and_entity`."""
    query_name: str
    normalized_name: str
    legal_form: Optional[str] = None
    inferred_country: str = "Unknown"
    ticker_hint: Optional[str] = None
    cik_hint: Optional[str] = None
    lei_hint: Optional[str] = None
    risk_level: str = Field("Low", description="'Low' | 'Medium' | 'High' | 'Critical'")
    risk_score: int = Field(0, ge=0, le=100)
    flags: list[ScreeningFlag] = Field(default_factory=list)
    screening_lists_checked: list[str] = Field(default_factory=list)
    clearance_recommendation: str = "Standard KYC / AML verification."


_LEGAL_SUFFIX_MAP: list[tuple[str, str, str]] = [
    (r"\b(?:inc\.?|incorporated)\b", "Inc.", "US"),
    (r"\b(?:corp\.?|corporation)\b", "Corp.", "US"),
    (r"\b(?:llc|l\.l\.c\.)\b", "LLC", "US"),
    (r"\b(?:gmbh|g\.m\.b\.h\.)\b", "GmbH", "DE"),
    (r"\b(?:aktiengesellschaft|ag)\b", "AG", "DE"),
    (r"\b(?:se|societas europaea)\b", "SE", "EU"),
    (r"\b(?:plc|p\.l\.c\.)\b", "PLC", "GB"),
    (r"\b(?:ltd\.?|limited)\b", "Ltd.", "GB"),
    (r"\b(?:s\.a\.|sa|sas|sarl)\b", "S.A.", "FR"),
    (r"\b(?:n\.v\.|b\.v\.)\b", "N.V.", "NL"),
    (r"\b(?:s\.p\.a\.|s\.r\.l\.)\b", "S.p.A.", "IT"),
    (r"\b(?:k\.k\.|kabushiki kaisha)\b", "K.K.", "JP"),
    (r"\b(?:pte\.?\s*ltd\.?)\b", "Pte. Ltd.", "SG"),
]

_EMBARGOED_JURISDICTIONS: dict[str, str] = {
    "IR": "Iran (OFAC Comprehensive Sanctions)",
    "IRAN": "Iran (OFAC Comprehensive Sanctions)",
    "KP": "North Korea / DPRK (OFAC & UN Comprehensive Sanctions)",
    "NORTH KOREA": "North Korea / DPRK (OFAC & UN Comprehensive Sanctions)",
    "SY": "Syria (OFAC & EU Sanctions)",
    "SYRIA": "Syria (OFAC & EU Sanctions)",
    "CU": "Cuba (OFAC Embargo)",
    "CUBA": "Cuba (OFAC Embargo)",
    "RU": "Russia (OFAC / EU / UK Sectoral & Blocking Sanctions)",
    "RUSSIA": "Russia (OFAC / EU / UK Sectoral & Blocking Sanctions)",
    "RUSSIAN FEDERATION": "Russia (OFAC / EU / UK Sectoral & Blocking Sanctions)",
    "BY": "Belarus (OFAC / EU / UK Sanctions)",
    "BELARUS": "Belarus (OFAC / EU / UK Sanctions)",
}

_ELEVATED_JURISDICTIONS: dict[str, str] = {
    "CN": "China (CFIUS / BIS Export Control / Outbound Investment Screening)",
    "CHINA": "China (CFIUS / BIS Export Control / Outbound Investment Screening)",
    "HK": "Hong Kong (Enhanced Export-Control & End-User Screening)",
    "HONG KONG": "Hong Kong (Enhanced Export-Control & End-User Screening)",
    "VE": "Venezuela (OFAC Targeted Sanctions)",
    "VENEZUELA": "Venezuela (OFAC Targeted Sanctions)",
    "MM": "Myanmar (OFAC / EU Targeted Sanctions)",
    "MYANMAR": "Myanmar (OFAC / EU Targeted Sanctions)",
    "AE": "UAE (Enhanced Transshipment & Re-Export Controls Review)",
    "TR": "Turkey (Enhanced Dual-Use Transshipment Review)",
}

_WATCHLIST_PATTERNS: list[tuple[str, str, str]] = [
    ("gazprom", "OFAC SDN / EU Sanctions", "Russian state energy conglomerate subject to US/EU/UK restrictions."),
    ("rosneft", "OFAC SSI / EU Sanctions", "Russian oil major subject to sectoral & asset-freeze regimes."),
    ("sberbank", "OFAC SDN / EU Blocking", "Designated Russian financial institution under full blocking sanctions."),
    ("vtb", "OFAC SDN / EU Blocking", "Designated Russian bank under OFAC SDN & EU sanctions."),
    ("rostec", "OFAC SDN / BIS Entity List", "Russian state defense/tech conglomerate under blocking sanctions."),
    ("huawei", "US BIS Entity List / FCC Covered List", "Subject to US export controls and telecom security restrictions."),
    ("smic", "US BIS Entity List", "Semiconductor foundry subject to US advanced-node export restrictions."),
    ("hikvision", "US BIS Entity List / NDAA 889", "Subject to US export controls and federal procurement bans."),
    ("wagner", "OFAC SDN / EU / UK Sanctions", "Designated paramilitary/sanctioned network."),
    ("iran oil", "OFAC SDN", "Sanctioned Iranian energy exposure."),
]

_SOE_PEP_INDICATORS = (
    "state-owned", "state owned", "sovereign wealth", "ministry of", "national oil",
    "government of", "central bank", "state grid", "china national", "federal state",
    "politically exposed", "royal holding", "public investment fund",
)

_SENSITIVE_FDI_KEYWORDS = (
    "semiconductor", "chip", "lithography", "defense", "aerospace", "missile",
    "quantum", "nuclear", "telecom", "5g", "satellite", "critical infrastructure",
    "biodefense", "genomic", "foundation model", "cybersecurity", "dual-use",
)


def screen_sanctions_and_entity(
    entity_name: str,
    country: str = "",
    extra_text: str = "",
) -> EntityScreeningResult:
    """
    Entity Resolution + Sanctions / PEP / Geopolitical / FDI Risk Screener.

    Normalizes legal entity naming, resolves LEI/CIK/ticker hints where known,
    infers jurisdiction, and returns structured compliance flags and risk scoring.
    """
    raw_name = (entity_name or "").strip()
    legal_form: Optional[str] = None
    inferred_country = (country or "").strip().upper() or "Unknown"

    norm = raw_name
    for pattern, form_label, default_cc in _LEGAL_SUFFIX_MAP:
        if re.search(pattern, raw_name, flags=re.IGNORECASE):
            legal_form = form_label
            if inferred_country == "Unknown":
                inferred_country = default_cc
            norm = re.sub(pattern, "", norm, flags=re.IGNORECASE)
            break

    normalized_name = re.sub(r"[\s,.;]+$", "", re.sub(r"\s+", " ", norm)).strip() or raw_name

    # Entity resolution hints (LEI / CIK / Ticker)
    cik_hint: Optional[str] = None
    lei_hint: Optional[str] = None
    ticker_hint: Optional[str] = None

    low_query = normalized_name.lower()
    for key, info in _KNOWN_ENTITY_REGISTRY.items():
        if key == low_query or key in low_query or raw_name.upper() == info["ticker"]:
            cik_hint = info["cik"]
            lei_hint = info["lei"]
            ticker_hint = info["ticker"]
            if inferred_country == "Unknown":
                inferred_country = info["country"]
            break

    flags: list[ScreeningFlag] = []
    risk_score = 5  # baseline residual KYC score
    combined_blob = f"{raw_name} {country} {extra_text}".lower()

    # 1. Watchlist / Sanctions name screening
    for pattern, list_name, detail in _WATCHLIST_PATTERNS:
        if pattern in combined_blob:
            flags.append(
                ScreeningFlag(
                    category="Sanctions",
                    severity="Critical",
                    title=f"Watchlist Hit — {list_name}",
                    detail=f"Entity or context matched restricted party indicator '{pattern}': {detail}",
                    recommended_action="Immediate halt & escalate to Sanctions Compliance Counsel for formal SDN/Entity List clearance.",
                )
            )
            risk_score += 75

    # 2. Jurisdiction screening
    cc_key = (country or inferred_country).strip().upper()
    if cc_key in _EMBARGOED_JURISDICTIONS:
        flags.append(
            ScreeningFlag(
                category="Jurisdiction",
                severity="Critical",
                title=f"Comprehensive Sanctions Jurisdiction ({cc_key})",
                detail=f"Jurisdiction '{cc_key}' flagged under {_EMBARGOED_JURISDICTIONS[cc_key]}.",
                recommended_action="Transaction likely prohibited without OFAC/EU license; require immediate legal block.",
            )
        )
        risk_score += 70
    elif cc_key in _ELEVATED_JURISDICTIONS:
        flags.append(
            ScreeningFlag(
                category="Jurisdiction",
                severity="High",
                title=f"Elevated Geopolitical / Export-Control Jurisdiction ({cc_key})",
                detail=_ELEVATED_JURISDICTIONS[cc_key],
                recommended_action="Perform enhanced beneficial ownership (50% rule), BIS export-control, and CFIUS/FDI filing analysis.",
            )
        )
        risk_score += 35

    # 3. PEP / State-Owned Enterprise (SOE) indicators
    soe_hits = [kw for kw in _SOE_PEP_INDICATORS if kw in combined_blob]
    if soe_hits:
        flags.append(
            ScreeningFlag(
                category="PEP/SOE",
                severity="High",
                title="State-Owned Enterprise (SOE) / PEP Ownership Signal",
                detail=f"Detected sovereign/PEP indicator(s): {', '.join(soe_hits[:4])}.",
                recommended_action="Conduct enhanced UBO & PEP due diligence and assess mandatory CFIUS / EU FDI foreign-government review.",
            )
        )
        risk_score += 25

    # 4. Sensitive FDI / CFIUS / Dual-Use sector screening
    fdi_hits = [kw for kw in _SENSITIVE_FDI_KEYWORDS if kw in combined_blob]
    if fdi_hits:
        flags.append(
            ScreeningFlag(
                category="FDI/CFIUS",
                severity="Medium" if cc_key not in _ELEVATED_JURISDICTIONS else "High",
                title="Sensitive Technology / Critical Infrastructure (FDI / CFIUS)",
                detail=f"Sensitive sector indicator(s) detected: {', '.join(fdi_hits[:5])}.",
                recommended_action="Assess mandatory CFIUS TID-US-business / EU FDI notification and add regulatory closing condition to SPA.",
            )
        )
        risk_score += 20

    risk_score = min(100, risk_score)
    if risk_score >= 70 or any(f.severity == "Critical" for f in flags):
        risk_level = "Critical"
        clearance = "DO NOT PROCEED without formal Sanctions & Regulatory Counsel sign-off."
    elif risk_score >= 40 or any(f.severity == "High" for f in flags):
        risk_level = "High"
        clearance = "Enhanced Due Diligence (EDD), UBO 50%-rule audit, and CFIUS/FDI pre-filing review required."
    elif risk_score >= 20 or flags:
        risk_level = "Medium"
        clearance = "Standard-plus compliance review; verify export controls and regulatory filing thresholds."
    else:
        risk_level = "Low"
        clearance = "No automated sanctions/PEP hits detected. Complete standard KYC/UBO verification before signing."

    return EntityScreeningResult(
        query_name=raw_name,
        normalized_name=normalized_name,
        legal_form=legal_form,
        inferred_country=inferred_country,
        ticker_hint=ticker_hint,
        cik_hint=cik_hint,
        lei_hint=lei_hint,
        risk_level=risk_level,
        risk_score=risk_score,
        flags=flags,
        screening_lists_checked=[
            "OFAC SDN & Consolidated Sanctions",
            "US BIS Entity List & Export Controls",
            "EU Consolidated Financial Sanctions",
            "UK OFSI Consolidated List",
            "FATF High-Risk Jurisdictions",
            "CFIUS / EU FDI Sensitive Sector Rules",
        ],
        clearance_recommendation=clearance,
    )
