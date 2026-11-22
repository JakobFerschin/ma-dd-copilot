"""
Multi-format institutional document extraction utilities.

Supports the full Virtual Data Room (VDR) file stack:
  - PDF  (.pdf)  : pdfplumber primary (text + page-aligned Markdown tables) with pypdf fallback
  - Word (.docx) : python-docx paragraphs + tables
  - Text (.txt, .md, .csv) : chardet-backed encoding detection, chunked into virtual pages

Public API:
  - `sanitize_filename(name)` -> safe basename (prevents path traversal)
  - `extract_pdf(path, max_pages=200)` -> ExtractedDocument
  - `extract_document(path, max_pages=200)` -> ExtractedDocument (dispatches by extension)
  - `extract_entity(entity_name, paths, max_pages_per_doc=200)` -> EntityDocuments
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from utils.models import EntityDocuments, ExtractedDocument

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".csv"}
_VIRTUAL_PAGE_CHARS = 3500  # ~1 dense printed page for non-paginated text files


def sanitize_filename(name: str) -> str:
    """Strip directory components and unsafe characters from an uploaded filename."""
    base = Path(name).name
    cleaned = re.sub(r"[^A-Za-z0-9._\- ]+", "_", base).strip()
    return cleaned or "document.pdf"


def _format_markdown_table(tbl: list[list[str | None]]) -> str:
    """Convert a 2D cell grid into a clean pipe-delimited Markdown table."""
    cleaned_rows: list[list[str]] = []
    for row in tbl:
        if not row:
            continue
        cells = [re.sub(r"\s+", " ", (c or "")).strip() for c in row]
        if any(cells):
            cleaned_rows.append(cells)
    if not cleaned_rows:
        return ""
    col_count = max(len(r) for r in cleaned_rows)
    padded = [r + [""] * (col_count - len(r)) for r in cleaned_rows]
    header = "| " + " | ".join(padded[0]) + " |"
    sep = "| " + " | ".join("---" for _ in range(col_count)) + " |"
    body = ["| " + " | ".join(r) + " |" for r in padded[1:]]
    return "\n".join([header, sep, *body])


def _pypdf_fallback_page(path: Path, page_idx: int) -> str:
    """Fallback single-page text extraction using pypdf when pdfplumber returns empty."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        if 0 <= page_idx < len(reader.pages):
            return reader.pages[page_idx].extract_text() or ""
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[{path.name}] pypdf fallback failed on page {page_idx}: {e}")
    return ""


def extract_pdf(path: str | Path, max_pages: int = 200) -> ExtractedDocument:
    """
    Extract text + page-aligned tables from a single PDF.

    Uses pdfplumber for high-fidelity financial table extraction and falls back
    to pypdf on pages where pdfplumber fails or returns empty text.
    """
    import pdfplumber

    path = Path(path)
    pages_text: list[str] = []
    tables_text: list[str] = []
    page_tables: dict[int, list[str]] = {}
    warnings: list[str] = []

    with pdfplumber.open(path) as pdf:
        page_count = len(pdf.pages)
        if page_count > max_pages:
            warnings.append(f"Capped at {max_pages}/{page_count} pages.")

        for i, page in enumerate(pdf.pages[:max_pages]):
            txt = ""
            try:
                txt = page.extract_text() or ""
            except Exception as e:  # noqa: BLE001 - log and continue per-page
                logger.warning(f"[{path.name}] page {i + 1} text extraction failed: {e}")
                warnings.append(f"Page {i + 1} text fallback triggered.")

            if not txt.strip():
                fallback = _pypdf_fallback_page(path, i)
                if fallback.strip():
                    txt = fallback

            pages_text.append(txt)

            try:
                extracted_tbls = page.extract_tables() or []
                for tbl in extracted_tbls:
                    md_tbl = _format_markdown_table(tbl)
                    if md_tbl:
                        tables_text.append(md_tbl)
                        page_tables.setdefault(i, []).append(md_tbl)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[{path.name}] page {i + 1} table extraction failed: {e}")

    full_text = "\n\n".join(f"--- Page {i + 1} ---\n{t}" for i, t in enumerate(pages_text))
    word_count = len(full_text.split())
    if word_count == 0:
        warnings.append("No extractable text found (possibly a scanned image PDF).")

    logger.info(
        f"Extracted {page_count} pages, {len(tables_text)} tables, {word_count} words from {path.name}"
    )
    return ExtractedDocument(
        filename=path.name,
        num_pages=page_count,
        full_text=full_text,
        pages=pages_text,
        tables=tables_text,
        page_tables=page_tables,
        file_type="pdf",
        word_count=word_count,
        warnings=warnings,
    )


def extract_docx(path: str | Path, max_pages: int = 200) -> ExtractedDocument:
    """Extract paragraphs and tables from a Word (.docx) document."""
    from docx import Document

    path = Path(path)
    doc = Document(str(path))
    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text and p.text.strip()]
    tables_text: list[str] = []
    for tbl in doc.tables:
        grid = [[cell.text.strip() for cell in row.cells] for row in tbl.rows]
        md = _format_markdown_table(grid)
        if md:
            tables_text.append(md)

    raw_body = "\n\n".join(paragraphs)
    pages = _split_into_virtual_pages(raw_body, max_pages=max_pages)
    page_tables: dict[int, list[str]] = {}
    if tables_text:
        # Attach Word tables to the last virtual page (or page 0 if empty)
        target_page = max(len(pages) - 1, 0)
        if not pages:
            pages = [""]
        page_tables[target_page] = list(tables_text)

    full_text = "\n\n".join(f"--- Page {i + 1} ---\n{t}" for i, t in enumerate(pages))
    return ExtractedDocument(
        filename=path.name,
        num_pages=max(len(pages), 1),
        full_text=full_text,
        pages=pages,
        tables=tables_text,
        page_tables=page_tables,
        file_type="docx",
        word_count=len(full_text.split()),
    )


def _split_into_virtual_pages(text: str, max_pages: int = 200) -> list[str]:
    """Chunk plain-text / CSV / Markdown files into ~3,500-char virtual pages for citations."""
    if not text.strip():
        return []
    pages: list[str] = []
    current: list[str] = []
    current_len = 0
    for para in text.splitlines():
        if current_len + len(para) + 1 > _VIRTUAL_PAGE_CHARS and current:
            pages.append("\n".join(current))
            current = []
            current_len = 0
            if len(pages) >= max_pages:
                break
        current.append(para)
        current_len += len(para) + 1
    if current and len(pages) < max_pages:
        pages.append("\n".join(current))
    return pages


def extract_text_file(path: str | Path, max_pages: int = 200) -> ExtractedDocument:
    """Extract UTF-8 / auto-detected text from .txt, .md, or .csv files."""
    import chardet

    path = Path(path)
    raw_bytes = path.read_bytes()
    detected = chardet.detect(raw_bytes) or {}
    encoding = detected.get("encoding") or "utf-8"
    try:
        text = raw_bytes.decode(encoding, errors="replace")
    except LookupError:
        text = raw_bytes.decode("utf-8", errors="replace")

    pages = _split_into_virtual_pages(text, max_pages=max_pages)
    full_text = "\n\n".join(f"--- Page {i + 1} ---\n{t}" for i, t in enumerate(pages))
    ext = path.suffix.lower().lstrip(".") or "txt"
    return ExtractedDocument(
        filename=path.name,
        num_pages=max(len(pages), 1),
        full_text=full_text,
        pages=pages,
        tables=[],
        page_tables={},
        file_type=ext,
        word_count=len(text.split()),
    )


def extract_document(path: str | Path, max_pages: int = 200) -> ExtractedDocument:
    """Dispatch document extraction based on file extension."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".pdf":
        return extract_pdf(path, max_pages=max_pages)
    if ext == ".docx":
        return extract_docx(path, max_pages=max_pages)
    if ext in {".txt", ".md", ".csv"}:
        return extract_text_file(path, max_pages=max_pages)
    raise ValueError(f"Unsupported file type '{ext}' for {path.name}")


def extract_entity(
    entity_name: str,
    paths: list[str | Path],
    max_pages_per_doc: int = 200,
) -> EntityDocuments:
    """
    Extract and aggregate a list of VDR files for one entity into an EntityDocuments.

    Preserves per-document & per-page provenance (including Markdown tables) so
    the LLM can cite the exact file and page for every claim.
    """
    docs: list[ExtractedDocument] = []
    for p in paths:
        p = Path(p)
        try:
            docs.append(extract_document(p, max_pages=max_pages_per_doc))
        except Exception as e:  # noqa: BLE001 - one corrupt file shouldn't kill the run
            logger.error(f"Failed to extract {p.name}: {e}")
    logger.info(
        f"Entity '{entity_name}': aggregated {len(docs)}/{len(paths)} documents, "
        f"{sum(d.num_pages for d in docs)} total pages, {sum(d.word_count for d in docs)} words."
    )
    return EntityDocuments(entity_name=entity_name, documents=docs)
