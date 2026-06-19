"""
PDF extraction utilities.

- `extract_pdf(path)`      -> ExtractedDocument      (single file)
- `extract_entity(files)`  -> EntityDocuments        (aggregated multi-file)

We use pdfplumber as the primary extractor because financial documents (10-Ks,
annual reports) are dense with tables, and pdfplumber preserves them far better
than pypdf. pypdf is kept as a metadata-only fallback.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pdfplumber

from utils.models import EntityDocuments, ExtractedDocument

logger = logging.getLogger(__name__)


def extract_pdf(path: str | Path, max_pages: int = 200) -> ExtractedDocument:
    """
    Extract text + tables from a single PDF.

    Args:
        path: PDF file path.
        max_pages: Safety cap to avoid runaway extraction on huge docs.

    Returns:
        ExtractedDocument with per-page text and flattened tables.
    """
    path = Path(path)
    pages_text: list[str] = []
    tables_text: list[str] = []

    with pdfplumber.open(path) as pdf:
        page_count = len(pdf.pages)
        for i, page in enumerate(pdf.pages[:max_pages]):
            try:
                txt = page.extract_text() or ""
            except Exception as e:  # noqa: BLE001 - log and continue per-page
                logger.warning(f"[{path.name}] page {i} text extraction failed: {e}")
                txt = ""
            pages_text.append(txt)

            try:
                for tbl in page.extract_tables() or []:
                    rows = [" | ".join((c or "") for c in row) for row in tbl]
                    tables_text.append("\n".join(rows))
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[{path.name}] page {i} table extraction failed: {e}")

    full_text = "\n\n".join(f"--- Page {i + 1} ---\n{t}" for i, t in enumerate(pages_text))
    logger.info(f"Extracted {page_count} pages, {len(tables_text)} tables from {path.name}")
    return ExtractedDocument(
        filename=path.name,
        num_pages=page_count,
        full_text=full_text,
        pages=pages_text,
        tables=tables_text,
    )


def extract_entity(entity_name: str, paths: list[str | Path], max_pages_per_doc: int = 200) -> EntityDocuments:
    """
    Extract and aggregate a list of PDFs for one entity into an EntityDocuments.

    A real DD uses multiple sources (10-K, ESG, Earnings Call). This aggregator
    preserves per-document provenance so the LLM can cite the right file+page.

    Args:
        entity_name: Human-readable entity name (e.g. "Volkswagen AG").
        paths: List of PDF file paths.
        max_pages_per_doc: Per-doc page cap.

    Returns:
        EntityDocuments with all extracted documents attached.
    """
    docs: list[ExtractedDocument] = []
    for p in paths:
        p = Path(p)
        try:
            docs.append(extract_pdf(p, max_pages=max_pages_per_doc))
        except Exception as e:  # noqa: BLE001 - one bad file shouldn't kill the run
            logger.error(f"Failed to extract {p.name}: {e}")
    logger.info(f"Entity '{entity_name}': aggregated {len(docs)}/{len(paths)} documents, "
                f"{sum(d.num_pages for d in docs)} total pages.")
    return EntityDocuments(entity_name=entity_name, documents=docs)
