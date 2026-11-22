"""
Institutional Virtual Data Room (VDR) Ingestion, Parsing & Completeness Engine.

Capabilities:
  1. Recursive ZIP & directory VDR ingestion (`ingest_vdr_zip`, `ingest_vdr_paths`)
     preserving original VDR index folder paths and computing SHA-256 hashes for
     automatic deduplication.
  2. Native Excel workbook extractor (`extract_excel_workbook`) via `openpyxl`:
     - Extracts every worksheet into clean Markdown tables with sheet provenance.
     - Detects Formula/Hardcode Anomalies: hardcoded numeric overrides inside
       formula rows/columns, inline hardcoded plug constants in formulas, Excel
       formula errors (`#REF!`, `#DIV/0!`, `#VALUE!`, `#NAME?`, `#N/A`, `#NUM!`),
       and hidden / very-hidden worksheets.
  3. Native PowerPoint extractor (`extract_pptx_document`) via `python-pptx`:
     - Extracts slide titles, body text (including grouped shapes), speaker notes,
       and slide tables with exact slide-number provenance.
  4. 9-Category Institutional VDR Taxonomy Auto-Classifier (`classify_vdr_document`).
  5. Sector VDR Completeness Checker (`assess_vdr_completeness`) benchmarking
     uploaded VDR packages against institutional M&A Information Request Lists
     (SaaS, Automotive/Industrial, Healthcare/Biopharma, General).
  6. Document Version Diff Engine (`diff_document_versions`) returning added/removed
     lines, unified diff, similarity ratio, and quantified numeric figure changes
     between v1 and v2 filings, CIMs, or SPAs.
"""
from __future__ import annotations

import difflib
import hashlib
import io
import logging
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence, Union

from pydantic import BaseModel, Field

from utils.models import EntityDocuments, ExtractedDocument
from utils.pdf_extractor import (
    _format_markdown_table,
    extract_document as _extract_base_document,
    sanitize_filename,
)

logger = logging.getLogger(__name__)

# Supported extensions across the full VDR stack
VDR_SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".txt", ".md", ".csv",
    ".xlsx", ".xlsm", ".pptx",
}

# 9-category institutional M&A VDR taxonomy
VDR_CATEGORIES: tuple[str, ...] = (
    "Corporate",
    "Financials",
    "Tax",
    "Legal & Contracts",
    "IP & Tech",
    "HR & Compensation",
    "Commercial",
    "ESG & Regulatory",
    "General",
)

_EXCEL_ERROR_TOKENS = ("#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A", "#NUM!", "#NULL!")
_MAX_ZIP_FILES = 500
_MAX_ZIP_UNCOMPRESSED_BYTES = 250 * 1024 * 1024  # 250 MB zip-bomb safeguard


# =====================================================================
# Data Models
# =====================================================================

class ExcelAnomaly(BaseModel):
    """Detected audit anomaly inside an Excel financial model or databook."""
    filename: str
    sheet_name: str
    cell_ref: str = Field(..., description="Cell address (e.g. 'E14') or 'SHEET' for sheet-level flags.")
    anomaly_type: str = Field(
        ...,
        description="'hardcode_override' | 'formula_hardcode_plug' | 'formula_error' | 'hidden_sheet'",
    )
    severity: str = Field("High", description="'Critical' | 'High' | 'Medium' | 'Low'")
    formula_or_value: str = ""
    expected_pattern: str = ""
    description: str = ""


class ExcelSheetSummary(BaseModel):
    """Per-worksheet structural telemetry."""
    sheet_name: str
    sheet_state: str = "visible"
    rows: int = 0
    cols: int = 0
    formula_cells: int = 0
    numeric_cells: int = 0
    error_cells: int = 0
    hardcode_overrides: int = 0


@dataclass
class ExcelExtractionResult:
    """Result of `extract_excel_workbook`, combining ExtractedDocument + forensic audit anomalies."""
    document: ExtractedDocument
    anomalies: list[ExcelAnomaly] = field(default_factory=list)
    sheets: list[ExcelSheetSummary] = field(default_factory=list)
    hidden_sheets: list[str] = field(default_factory=list)
    total_formulas: int = 0
    total_errors: int = 0
    total_hardcode_overrides: int = 0

    # Convenience pass-through properties for interoperability with ExtractedDocument
    @property
    def filename(self) -> str:
        return self.document.filename

    @property
    def num_pages(self) -> int:
        return self.document.num_pages

    @property
    def full_text(self) -> str:
        return self.document.full_text

    @property
    def pages(self) -> list[str]:
        return self.document.pages

    @property
    def tables(self) -> list[str]:
        return self.document.tables

    @property
    def page_tables(self) -> dict[int, list[str]]:
        return self.document.page_tables


class VDRClassification(BaseModel):
    """Output of the 9-category VDR taxonomy auto-classifier."""
    category: str = "General"
    confidence: float = Field(0.5, ge=0.0, le=1.0)
    matched_signals: list[str] = Field(default_factory=list)
    subcategory: str = ""

    def __str__(self) -> str:
        return self.category


class DuplicateRecord(BaseModel):
    """Record of an automatically deduplicated VDR file matched by SHA-256."""
    vdr_path: str
    duplicate_of_path: str
    sha256: str
    file_size_bytes: int = 0


@dataclass
class VDRDocumentRecord:
    """Ingested VDR document enriched with original index path, hash, taxonomy, and anomalies."""
    vdr_path: str
    filename: str
    sha256: str
    file_size_bytes: int
    file_type: str
    category: str
    classification_confidence: float
    matched_signals: list[str] = field(default_factory=list)
    num_pages: int = 0
    word_count: int = 0
    table_count: int = 0
    anomalies: list[ExcelAnomaly] = field(default_factory=list)
    document: Optional[ExtractedDocument] = None


@dataclass
class VDRIngestionResult:
    """Complete result of recursive ZIP or directory VDR ingestion."""
    entity_name: str
    entity_documents: EntityDocuments
    records: list[VDRDocumentRecord] = field(default_factory=list)
    duplicates_skipped: list[DuplicateRecord] = field(default_factory=list)
    excel_anomalies: list[ExcelAnomaly] = field(default_factory=list)
    category_counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def total_files_ingested(self) -> int:
        return len(self.records)

    @property
    def total_duplicates_skipped(self) -> int:
        return len(self.duplicates_skipped)


class ChecklistItem(BaseModel):
    """Single item in a sector-specific institutional M&A VDR checklist."""
    item_id: str
    title: str
    category: str
    priority: str = Field("High", description="'Critical' | 'High' | 'Standard'")
    keywords: list[str] = Field(default_factory=list)
    rationale: str = ""


class ChecklistMatch(BaseModel):
    """Checklist item satisfied by one or more uploaded VDR documents."""
    item_id: str
    title: str
    category: str
    priority: str
    matched_documents: list[str] = Field(default_factory=list)


class MissingDocumentItem(BaseModel):
    """Missing Document Request List (IRL) entry generated when a VDR item is absent."""
    item_id: str
    title: str
    category: str
    priority: str
    rationale: str
    suggested_irl_request: str


class VDRCompletenessReport(BaseModel):
    """VDR completeness scorecard + Missing Document Request List."""
    sector: str
    total_checklist_items: int
    present_count: int
    missing_count: int
    coverage_pct: float
    critical_total: int
    critical_present: int
    critical_coverage_pct: float
    readiness_grade: str
    matched_items: list[ChecklistMatch] = Field(default_factory=list)
    missing_items: list[MissingDocumentItem] = Field(default_factory=list)
    category_coverage: dict[str, float] = Field(default_factory=dict)


class NumericFigureChange(BaseModel):
    """Quantified numeric delta detected between v1 and v2 of a filing/SPA/CIM."""
    context_label: str
    metric_unit: str = Field(..., description="'USD' | 'EUR' | 'GBP' | 'PCT' | 'MULTIPLE' | 'NUMBER'")
    old_display: str
    new_display: str
    old_value: float
    new_value: float
    delta: float
    delta_pct: Optional[float] = None
    materiality: str = Field("Medium", description="'High' | 'Medium' | 'Low'")
    old_line: str = ""
    new_line: str = ""


class DocumentDiffResult(BaseModel):
    """Structured diff between v1 and v2 documents including quantified numeric changes."""
    old_filename: str
    new_filename: str
    similarity_ratio: float
    added_count: int
    removed_count: int
    added_lines: list[str] = Field(default_factory=list)
    removed_lines: list[str] = Field(default_factory=list)
    numeric_changes: list[NumericFigureChange] = Field(default_factory=list)
    unified_diff: str = ""
    summary: str = ""


# =====================================================================
# 1. Native Excel Workbook Extractor + Forensic Formula/Hardcode Audit
# =====================================================================

def _is_numeric_literal(val: object) -> bool:
    """Return True for genuine numeric literals (excluding bools, which subclass int)."""
    return isinstance(val, (int, float)) and not isinstance(val, bool)


def _is_formula(val: object) -> bool:
    return isinstance(val, str) and val.startswith("=")


def _detect_error_token(val: object) -> Optional[str]:
    if not isinstance(val, str):
        return None
    upper = val.upper()
    for err in _EXCEL_ERROR_TOKENS:
        if err in upper:
            return err
    return None


def _format_cell_display(val: object) -> str:
    if val is None:
        return ""
    if isinstance(val, bool):
        return "TRUE" if val else "FALSE"
    if isinstance(val, float):
        if val.is_integer():
            return f"{int(val):,}"
        if abs(val) >= 1000:
            return f"{val:,.2f}"
        return f"{val:.4g}"
    if isinstance(val, int):
        return f"{val:,}"
    return re.sub(r"\s+", " ", str(val)).strip()


# Regex for suspicious hardcoded plug added/subtracted to a cell-reference formula,
# e.g., `=SUM(B2:B10)+250000` or `=C14-1500.5`
_FORMULA_PLUG_RE = re.compile(
    r"[A-Z]{1,3}\d+\)?\s*([+-])\s*(\d{2,}(?:\.\d+)?)\b",
    re.IGNORECASE,
)


def extract_excel_workbook(
    path: str | Path,
    max_sheets: int = 50,
    max_rows_per_sheet: int = 500,
    max_cols_per_sheet: int = 40,
) -> ExcelExtractionResult:
    """
    Extract an Excel workbook (.xlsx / .xlsm) via `openpyxl` into an `ExtractedDocument`
    and perform forensic financial-model anomaly detection:
      - Hidden / veryHidden worksheets
      - Formula error cells (#REF!, #DIV/0!, #VALUE!, #NAME?, #N/A, #NUM!, #NULL!)
      - Hardcoded numeric overrides sitting inside a formula row or column
      - Suspicious hardcoded numeric plugs appended to formulas (e.g. `=SUM(B2:B5)+50000`)
    """
    import openpyxl
    from openpyxl.utils import get_column_letter

    path = Path(path)
    anomalies: list[ExcelAnomaly] = []
    sheet_summaries: list[ExcelSheetSummary] = []
    hidden_sheets: list[str] = []
    pages_text: list[str] = []
    tables_text: list[str] = []
    page_tables: dict[int, list[str]] = {}
    warnings: list[str] = []

    wb_formulas = openpyxl.load_workbook(str(path), data_only=False, read_only=False)
    wb_values = openpyxl.load_workbook(str(path), data_only=True, read_only=False)

    total_formulas = 0
    total_errors = 0
    total_hardcodes = 0

    try:
        sheet_names = wb_formulas.sheetnames[:max_sheets]
        if len(wb_formulas.sheetnames) > max_sheets:
            warnings.append(f"Capped at {max_sheets}/{len(wb_formulas.sheetnames)} worksheets.")

        for s_idx, sheet_name in enumerate(sheet_names):
            ws_f = wb_formulas[sheet_name]
            ws_v = wb_values[sheet_name]
            sheet_state = getattr(ws_f, "sheet_state", "visible") or "visible"

            if sheet_state != "visible":
                hidden_sheets.append(sheet_name)
                anomalies.append(
                    ExcelAnomaly(
                        filename=path.name,
                        sheet_name=sheet_name,
                        cell_ref="SHEET",
                        anomaly_type="hidden_sheet",
                        severity="High" if sheet_state == "veryHidden" else "Medium",
                        formula_or_value=sheet_state,
                        expected_pattern="visible",
                        description=(
                            f"Worksheet '{sheet_name}' has state '{sheet_state}'. "
                            "Hidden tabs in M&A databooks frequently contain off-model adjustments or legacy schedules."
                        ),
                    )
                )

            max_r = min(ws_f.max_row or 0, max_rows_per_sheet)
            max_c = min(ws_f.max_column or 0, max_cols_per_sheet)

            # Build grids for formula inspection and value display
            formula_grid: list[list[object]] = []
            value_grid: list[list[object]] = []
            display_grid: list[list[str]] = []

            sheet_formulas = 0
            sheet_numerics = 0
            sheet_errors = 0
            sheet_hardcodes = 0
            flagged_cells: set[tuple[int, int]] = set()

            for r in range(1, max_r + 1):
                f_row: list[object] = []
                v_row: list[object] = []
                d_row: list[str] = []
                for c in range(1, max_c + 1):
                    fv = ws_f.cell(r, c).value
                    vv = ws_v.cell(r, c).value
                    f_row.append(fv)
                    v_row.append(vv)

                    # Display cached value when available, otherwise raw formula/literal
                    disp = _format_cell_display(vv if vv is not None else fv)
                    d_row.append(disp)

                    cell_ref = f"{get_column_letter(c)}{r}"

                    # 1. Check formula / cached value error tokens
                    err_tok = _detect_error_token(vv) or _detect_error_token(fv)
                    if err_tok:
                        sheet_errors += 1
                        anomalies.append(
                            ExcelAnomaly(
                                filename=path.name,
                                sheet_name=sheet_name,
                                cell_ref=cell_ref,
                                anomaly_type="formula_error",
                                severity="Critical" if err_tok in ("#REF!", "#DIV/0!", "#VALUE!") else "High",
                                formula_or_value=str(fv if fv is not None else vv),
                                expected_pattern="Valid numeric/text formula result",
                                description=f"Cell {sheet_name}!{cell_ref} evaluates to Excel error {err_tok}.",
                            )
                        )

                    if _is_formula(fv):
                        sheet_formulas += 1
                        # 2. Check inline hardcoded plug inside formula (exclude small constants like +1, -1, *100)
                        plug_match = _FORMULA_PLUG_RE.search(str(fv))
                        if plug_match:
                            plug_num = float(plug_match.group(2))
                            if plug_num >= 10.0 and plug_num not in (100.0, 1000.0, 12.0, 365.0, 360.0):
                                anomalies.append(
                                    ExcelAnomaly(
                                        filename=path.name,
                                        sheet_name=sheet_name,
                                        cell_ref=cell_ref,
                                        anomaly_type="formula_hardcode_plug",
                                        severity="Medium",
                                        formula_or_value=str(fv),
                                        expected_pattern="Pure cell-referenced formula without inline plug",
                                        description=(
                                            f"Cell {sheet_name}!{cell_ref} contains an inline hardcoded adjustment "
                                            f"({plug_match.group(1)}{plug_match.group(2)}) inside formula '{fv}'."
                                        ),
                                    )
                                )
                    elif _is_numeric_literal(fv):
                        sheet_numerics += 1

                formula_grid.append(f_row)
                value_grid.append(v_row)
                display_grid.append(d_row)

            # 3. Row-wise hardcode override detection (e.g., formula across FY21-FY25 with hardcoded override in FY23)
            for r_idx, f_row in enumerate(formula_grid):
                formula_cols = [c_idx for c_idx, val in enumerate(f_row) if _is_formula(val)]
                numeric_cols = [c_idx for c_idx, val in enumerate(f_row) if _is_numeric_literal(val)]
                if len(formula_cols) >= 2 and numeric_cols:
                    min_fc, max_fc = min(formula_cols), max(formula_cols)
                    ratio = len(formula_cols) / (len(formula_cols) + len(numeric_cols))
                    for c_idx in numeric_cols:
                        # Skip column A/B label/year columns (e.g. year header 2023 in row 1)
                        val = f_row[c_idx]
                        if r_idx == 0 and isinstance(val, int) and 1990 <= val <= 2100:
                            continue
                        is_sandwiched = min_fc < c_idx < max_fc
                        is_adjacent_in_formula_row = ratio >= 0.60 and (
                            (c_idx - 1 in formula_cols) or (c_idx + 1 in formula_cols)
                        )
                        if (is_sandwiched or is_adjacent_in_formula_row) and (r_idx, c_idx) not in flagged_cells:
                            flagged_cells.add((r_idx, c_idx))
                            sheet_hardcodes += 1
                            cell_ref = f"{get_column_letter(c_idx + 1)}{r_idx + 1}"
                            row_label = display_grid[r_idx][0] if display_grid[r_idx] else f"Row {r_idx + 1}"
                            neighbor_formula = str(f_row[formula_cols[0]])
                            anomalies.append(
                                ExcelAnomaly(
                                    filename=path.name,
                                    sheet_name=sheet_name,
                                    cell_ref=cell_ref,
                                    anomaly_type="hardcode_override",
                                    severity="High",
                                    formula_or_value=str(val),
                                    expected_pattern=f"Row formula pattern like '{neighbor_formula}'",
                                    description=(
                                        f"Hardcoded numeric value ({val}) in {sheet_name}!{cell_ref} "
                                        f"(row '{row_label}') overrides a formula series ({len(formula_cols)} formula cells in row)."
                                    ),
                                )
                            )

            # 4. Column-wise hardcode override detection (e.g., Total/Margin column with one hardcoded cell)
            for c_idx in range(max_c):
                col_vals = [formula_grid[r_idx][c_idx] for r_idx in range(len(formula_grid))]
                formula_rows = [r_idx for r_idx, val in enumerate(col_vals) if _is_formula(val)]
                numeric_rows = [r_idx for r_idx, val in enumerate(col_vals) if _is_numeric_literal(val)]
                if len(formula_rows) >= 3 and numeric_rows:
                    min_fr, max_fr = min(formula_rows), max(formula_rows)
                    ratio = len(formula_rows) / (len(formula_rows) + len(numeric_rows))
                    for r_idx in numeric_rows:
                        if (r_idx, c_idx) in flagged_cells:
                            continue
                        val = col_vals[r_idx]
                        if r_idx == 0 and isinstance(val, int) and 1990 <= val <= 2100:
                            continue
                        is_sandwiched = min_fr < r_idx < max_fr
                        if (is_sandwiched or ratio >= 0.70) and (
                            (r_idx - 1 in formula_rows) or (r_idx + 1 in formula_rows)
                        ):
                            flagged_cells.add((r_idx, c_idx))
                            sheet_hardcodes += 1
                            cell_ref = f"{get_column_letter(c_idx + 1)}{r_idx + 1}"
                            col_header = display_grid[0][c_idx] if display_grid and display_grid[0] else get_column_letter(c_idx + 1)
                            neighbor_formula = str(col_vals[formula_rows[0]])
                            anomalies.append(
                                ExcelAnomaly(
                                    filename=path.name,
                                    sheet_name=sheet_name,
                                    cell_ref=cell_ref,
                                    anomaly_type="hardcode_override",
                                    severity="High",
                                    formula_or_value=str(val),
                                    expected_pattern=f"Column formula pattern like '{neighbor_formula}'",
                                    description=(
                                        f"Hardcoded numeric value ({val}) in {sheet_name}!{cell_ref} "
                                        f"(column '{col_header}') breaks a vertical formula series ({len(formula_rows)} formula cells)."
                                    ),
                                )
                            )

            # Convert non-empty sheet grid into Markdown table
            non_empty_rows = [row for row in display_grid if any(cell.strip() for cell in row)]
            md_table = _format_markdown_table(non_empty_rows) if non_empty_rows else ""

            header_note = (
                f"[Sheet: {sheet_name} | State: {sheet_state} | "
                f"Rows: {len(non_empty_rows)} | Formulas: {sheet_formulas} | "
                f"Errors: {sheet_errors} | Hardcode Overrides: {sheet_hardcodes}]"
            )
            pages_text.append(header_note)
            if md_table:
                tables_text.append(f"### Sheet: {sheet_name}\n{md_table}")
                page_tables[s_idx] = [md_table]

            total_formulas += sheet_formulas
            total_errors += sheet_errors
            total_hardcodes += sheet_hardcodes

            sheet_summaries.append(
                ExcelSheetSummary(
                    sheet_name=sheet_name,
                    sheet_state=sheet_state,
                    rows=len(non_empty_rows),
                    cols=max_c,
                    formula_cells=sheet_formulas,
                    numeric_cells=sheet_numerics,
                    error_cells=sheet_errors,
                    hardcode_overrides=sheet_hardcodes,
                )
            )
    finally:
        wb_formulas.close()
        wb_values.close()

    if anomalies:
        warnings.append(
            f"Detected {len(anomalies)} Excel audit anomaly(ies): "
            f"{total_hardcodes} hardcode override(s), {total_errors} formula error(s), "
            f"{len(hidden_sheets)} hidden sheet(s)."
        )

    full_parts: list[str] = []
    for idx, p_txt in enumerate(pages_text):
        tbl_part = "\n".join(page_tables.get(idx, []))
        full_parts.append(f"--- Page {idx + 1} ---\n{p_txt}\n{tbl_part}".strip())
    full_text = "\n\n".join(full_parts)

    doc = ExtractedDocument(
        filename=path.name,
        num_pages=max(len(pages_text), 1),
        full_text=full_text,
        pages=pages_text,
        tables=tables_text,
        page_tables=page_tables,
        file_type=path.suffix.lower().lstrip(".") or "xlsx",
        word_count=len(full_text.split()),
        warnings=warnings,
    )

    return ExcelExtractionResult(
        document=doc,
        anomalies=anomalies,
        sheets=sheet_summaries,
        hidden_sheets=hidden_sheets,
        total_formulas=total_formulas,
        total_errors=total_errors,
        total_hardcode_overrides=total_hardcodes,
    )


# =====================================================================
# 2. Native PowerPoint Extractor (Slides + Speaker Notes + Tables)
# =====================================================================

def _extract_shape_content(shape, text_blocks: list[str], slide_tables: list[str]) -> None:
    """Recursively extract text frames and tables from PowerPoint shapes (including groups)."""
    # Grouped shapes
    if hasattr(shape, "shapes"):
        for sub in shape.shapes:
            _extract_shape_content(sub, text_blocks, slide_tables)
        return

    if getattr(shape, "has_table", False):
        tbl = shape.table
        grid = [
            [re.sub(r"\s+", " ", cell.text or "").strip() for cell in row.cells]
            for row in tbl.rows
        ]
        md = _format_markdown_table(grid)
        if md:
            slide_tables.append(md)
        return

    if getattr(shape, "has_text_frame", False) and shape.text_frame is not None:
        lines = [
            p.text.strip()
            for p in shape.text_frame.paragraphs
            if p.text and p.text.strip()
        ]
        if lines:
            text_blocks.append("\n".join(lines))


def extract_pptx_document(path: str | Path, max_slides: int = 200) -> ExtractedDocument:
    """
    Extract a PowerPoint presentation (.pptx) via `python-pptx`, capturing slide
    titles, body text, speaker notes, and tables with exact slide-number provenance.
    """
    from pptx import Presentation

    path = Path(path)
    prs = Presentation(str(path))
    pages_text: list[str] = []
    tables_text: list[str] = []
    page_tables: dict[int, list[str]] = {}
    warnings: list[str] = []

    slide_count = len(prs.slides)
    if slide_count > max_slides:
        warnings.append(f"Capped at {max_slides}/{slide_count} slides.")

    for idx, slide in enumerate(list(prs.slides)[:max_slides]):
        text_blocks: list[str] = []
        slide_tables: list[str] = []

        # Extract title explicitly first if available
        title_text = ""
        if slide.shapes.title is not None and slide.shapes.title.has_text_frame:
            title_text = slide.shapes.title.text.strip()

        for shape in slide.shapes:
            if slide.shapes.title is not None and shape == slide.shapes.title:
                continue
            _extract_shape_content(shape, text_blocks, slide_tables)

        # Extract speaker notes
        notes_text = ""
        try:
            if slide.has_notes_slide and slide.notes_slide and slide.notes_slide.notes_text_frame:
                notes_text = slide.notes_slide.notes_text_frame.text.strip()
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[{path.name}] slide {idx + 1} notes extraction failed: {e}")

        parts: list[str] = [f"[Slide {idx + 1}]" + (f" {title_text}" if title_text else "")]
        if text_blocks:
            parts.append("\n".join(text_blocks))
        if notes_text:
            parts.append(f"[Speaker Notes — Slide {idx + 1}]\n{notes_text}")

        pages_text.append("\n\n".join(parts))

        if slide_tables:
            page_tables[idx] = slide_tables
            for t in slide_tables:
                tables_text.append(f"[Slide {idx + 1} Table]\n{t}")

    full_parts: list[str] = []
    for idx, p_txt in enumerate(pages_text):
        tbls = "\n\n".join(page_tables.get(idx, []))
        block = f"--- Slide {idx + 1} ---\n{p_txt}"
        if tbls:
            block += f"\n\n{tbls}"
        full_parts.append(block)
    full_text = "\n\n".join(full_parts)

    return ExtractedDocument(
        filename=path.name,
        num_pages=max(slide_count, 1),
        full_text=full_text,
        pages=pages_text,
        tables=tables_text,
        page_tables=page_tables,
        file_type="pptx",
        word_count=len(full_text.split()),
        warnings=warnings,
    )


# =====================================================================
# 3. 9-Category VDR Taxonomy Auto-Classifier
# =====================================================================

_TAXONOMY_RULES: dict[str, dict[str, list[str]]] = {
    "Corporate": {
        "path": [
            "corporate", "governance", "charter", "bylaw", "cap table", "captable",
            "shareholder", "board", "minutes", "incorporation", "org chart", "subsidiar",
            "01_", "01.", "entity structure", "articles",
        ],
        "content": [
            "certificate of incorporation", "bylaws", "board of directors", "cap table",
            "capitalization table", "preferred stock", "common stock", "voting rights",
            "subsidiaries", "articles of association", "shareholders agreement",
            "corporate governance", "minutes of meeting",
        ],
    },
    "Financials": {
        "path": [
            "financial", "10-k", "10k", "10-q", "10q", "qoe", "quality of earnings",
            "audit", "p&l", "pnl", "income statement", "balance sheet", "cash flow",
            "budget", "forecast", "model", "databook", "trial balance", "gl_",
            "revenue", "ebitda", "working capital", "debt", "02_", "02.", "annual report",
        ],
        "content": [
            "consolidated statements of operations", "balance sheet", "adjusted ebitda",
            "quality of earnings", "net working capital", "accounts receivable",
            "gross profit", "operating income", "cash flows from operating activities",
            "deferred revenue", "gaap", "ifrs", "audited financial statements",
            "free cash flow", "capital expenditures", "debt schedule",
        ],
    },
    "Tax": {
        "path": [
            "tax", "1120", "irs", "vat", "transfer pricing", "nol", "deferred tax",
            "sales tax", "nexus", "03_", "03.", "tax return", "tax audit",
        ],
        "content": [
            "income tax return", "transfer pricing", "net operating loss", "deferred tax asset",
            "deferred tax liability", "internal revenue service", "tax audit", "value added tax",
            "withholding tax", "section 382", "effective tax rate", "uncertain tax positions",
        ],
    },
    "Legal & Contracts": {
        "path": [
            "legal", "contract", "spa", "apa", "msa", "agreement", "lease", "litigation",
            "dispute", "claim", "nda", "indemn", "amendment", "credit agreement",
            "loan", "04_", "04.", "material contract", "vendor agreement", "customer contract",
        ],
        "content": [
            "stock purchase agreement", "asset purchase agreement", "change of control",
            "indemnification", "representations and warranties", "material adverse effect",
            "governing law", "arbitration", "litigation", "pending claims", "master services agreement",
            "assignment clause", "termination for convenience", "escrow", "covenant",
        ],
    },
    "IP & Tech": {
        "path": [
            "ip", "patent", "trademark", "tech", "architecture", "software", "code",
            "open source", "oss", "sbom", "cyber", "soc2", "soc 2", "iso27001", "pentest",
            "infrastructure", "cloud", "roadmap", "05_", "05.", "security", "r&d",
        ],
        "content": [
            "intellectual property", "patent portfolio", "trademark registration",
            "open source software", "gpl", "apache license", "soc 2 type ii", "penetration test",
            "system architecture", "microservices", "aws", "azure", "kubernetes",
            "technical debt", "cybersecurity", "data privacy", "gdpr", "encryption",
        ],
    },
    "HR & Compensation": {
        "path": [
            "hr", "human resource", "employee", "headcount", "census", "compensation",
            "payroll", "bonus", "option", "equity plan", "esop", "benefit", "retention",
            "employment", "offer letter", "union", "works council", "06_", "06.", "people",
        ],
        "content": [
            "employee census", "key employee", "retention bonus", "severance",
            "equity incentive plan", "stock options", "attrition rate", "base salary",
            "collective bargaining", "works council", "401(k)", "pension liability",
            "non-compete", "employment agreement", "human capital",
        ],
    },
    "Commercial": {
        "path": [
            "commercial", "cim", "teaser", "management presentation", "mgmt pres",
            "sales", "pipeline", "customer", "cohort", "churn", "arr", "nrr", "pricing",
            "market", "competitor", "gtm", "marketing", "07_", "07.", "earnings call",
            "transcript", "investor",
        ],
        "content": [
            "confidential information memorandum", "annual recurring revenue", "net revenue retention",
            "customer concentration", "sales pipeline", "gross retention", "churn rate",
            "total addressable market", "go-to-market", "cohort analysis", "average contract value",
            "customer acquisition cost", "ltv/cac", "market share", "earnings call",
        ],
    },
    "ESG & Regulatory": {
        "path": [
            "esg", "sustainability", "csrd", "emissions", "scope 3", "carbon", "climate",
            "environment", "phase i", "regulatory", "antitrust", "hsr", "cfius", "fda",
            "ema", "compliance", "permit", "license", "08_", "08.", "gdpr", "sanction",
        ],
        "content": [
            "scope 1", "scope 2", "scope 3", "greenhouse gas", "csrd", "double materiality",
            "environmental phase i", "antitrust", "hart-scott-rodino", "merger control",
            "cfius", "foreign direct investment", "fda approval", "clinical trial",
            "regulatory compliance", "iso 14001", "sustainability report", "dei",
        ],
    },
}


def classify_vdr_document(
    doc_or_filename: Union[ExtractedDocument, str, Path],
    text_sample: str = "",
    vdr_path: str = "",
) -> VDRClassification:
    """
    Auto-classify a VDR document into one of 9 institutional M&A categories:
    Corporate, Financials, Tax, Legal & Contracts, IP & Tech,
    HR & Compensation, Commercial, ESG & Regulatory, or General.
    """
    if isinstance(doc_or_filename, ExtractedDocument):
        filename = doc_or_filename.filename
        sample = text_sample or doc_or_filename.full_text[:12000]
    else:
        filename = str(doc_or_filename)
        sample = text_sample[:12000]

    path_blob = f"{vdr_path} {filename}".lower()
    content_blob = sample.lower()

    scores: dict[str, float] = {}
    signals_map: dict[str, list[str]] = {}

    for category, rule_set in _TAXONOMY_RULES.items():
        score = 0.0
        hits: list[str] = []

        for kw in rule_set["path"]:
            if kw in path_blob:
                score += 3.5
                hits.append(f"path:{kw}")

        for kw in rule_set["content"]:
            if kw in content_blob:
                count = min(content_blob.count(kw), 4)
                score += 1.5 + 0.35 * (count - 1)
                hits.append(f"text:{kw}")

        if score > 0:
            scores[category] = score
            signals_map[category] = hits

    if not scores:
        return VDRClassification(
            category="General",
            confidence=0.40,
            matched_signals=[],
            subcategory="Unclassified VDR File",
        )

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_cat, best_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0

    # Confidence blends absolute evidence strength and margin over runner-up
    margin = (best_score - second_score) / max(best_score, 1.0)
    strength = min(best_score / 10.0, 1.0)
    confidence = round(min(0.99, max(0.45, 0.45 + 0.35 * strength + 0.20 * margin)), 2)

    return VDRClassification(
        category=best_cat,
        confidence=confidence,
        matched_signals=signals_map[best_cat][:8],
        subcategory=best_cat,
    )


# =====================================================================
# 4. Recursive ZIP & Directory VDR Ingestor (SHA-256 Deduplication)
# =====================================================================

def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalize_vdr_path(raw_path: str) -> str:
    cleaned = raw_path.replace("\\", "/").lstrip("/")
    parts = [p for p in cleaned.split("/") if p and p not in (".", "..")]
    return "/".join(parts) or "document"


def _extract_single_vdr_file(
    disk_path: Path,
    vdr_path: str,
    raw_bytes: bytes,
    max_pages_per_doc: int = 200,
) -> tuple[VDRDocumentRecord, list[ExcelAnomaly]]:
    """Extract a single VDR file (any supported extension), classify it, and capture anomalies."""
    ext = disk_path.suffix.lower()
    sha = _sha256_bytes(raw_bytes)
    anomalies: list[ExcelAnomaly] = []

    if ext in {".xlsx", ".xlsm"}:
        xl_res = extract_excel_workbook(disk_path)
        doc = xl_res.document
        anomalies = xl_res.anomalies
    elif ext == ".pptx":
        doc = extract_pptx_document(disk_path, max_slides=max_pages_per_doc)
    else:
        doc = _extract_base_document(disk_path, max_pages=max_pages_per_doc)

    # Preserve VDR path basename cleanly while keeping original vdr_path metadata
    classification = classify_vdr_document(doc, vdr_path=vdr_path)
    record = VDRDocumentRecord(
        vdr_path=vdr_path,
        filename=doc.filename,
        sha256=sha,
        file_size_bytes=len(raw_bytes),
        file_type=doc.file_type,
        category=classification.category,
        classification_confidence=classification.confidence,
        matched_signals=classification.matched_signals,
        num_pages=doc.num_pages,
        word_count=doc.word_count,
        table_count=len(doc.tables),
        anomalies=anomalies,
        document=doc,
    )
    return record, anomalies


def _collect_from_zip_bytes(
    zip_bytes: bytes,
    prefix: str,
    collected: list[tuple[str, bytes]],
    warnings: list[str],
    depth: int = 0,
) -> None:
    """Safely unpack a ZIP archive (including nested ZIPs up to depth 3) in memory."""
    if depth > 3:
        warnings.append(f"Skipped deeply nested ZIP at '{prefix}' (depth > 3).")
        return

    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as e:
        warnings.append(f"Invalid ZIP archive '{prefix or 'root.zip'}': {e}")
        return

    total_uncompressed = 0
    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            member_name = info.filename
            # Skip macOS metadata / hidden junk
            base = Path(member_name).name
            if base.startswith(".") or "__MACOSX" in member_name:
                continue

            if len(collected) >= _MAX_ZIP_FILES:
                warnings.append(f"Reached VDR ZIP file limit ({_MAX_ZIP_FILES} files); remaining files skipped.")
                break

            total_uncompressed += info.file_size
            if total_uncompressed > _MAX_ZIP_UNCOMPRESSED_BYTES:
                warnings.append("ZIP uncompressed size cap (250 MB) exceeded; stopping extraction.")
                break

            vdr_rel = _normalize_vdr_path(f"{prefix}/{member_name}" if prefix else member_name)
            try:
                data = zf.read(info)
            except Exception as e:  # noqa: BLE001
                warnings.append(f"Could not read ZIP entry '{vdr_rel}': {e}")
                continue

            ext = Path(vdr_rel).suffix.lower()
            if ext == ".zip":
                nested_prefix = vdr_rel[:-4]
                _collect_from_zip_bytes(data, nested_prefix, collected, warnings, depth=depth + 1)
            elif ext in VDR_SUPPORTED_EXTENSIONS:
                collected.append((vdr_rel, data))
            else:
                warnings.append(f"Skipped unsupported file type in VDR: '{vdr_rel}'.")


def ingest_vdr_zip(
    zip_source: Union[str, Path, bytes, io.BytesIO],
    entity_name: str = "Target",
    max_pages_per_doc: int = 200,
) -> VDRIngestionResult:
    """
    Recursively ingest a Virtual Data Room ZIP archive (from disk path, raw bytes,
    or BytesIO stream), preserving VDR folder index paths, deduplicating identical
    files by SHA-256, auto-classifying into 9 M&A categories, and auditing Excel models.
    """
    if isinstance(zip_source, (str, Path)):
        zip_bytes = Path(zip_source).read_bytes()
    elif isinstance(zip_source, io.BytesIO):
        zip_bytes = zip_source.getvalue()
    elif isinstance(zip_source, (bytes, bytearray)):
        zip_bytes = bytes(zip_source)
    else:
        raise TypeError(f"Unsupported zip_source type: {type(zip_source).__name__}")

    collected: list[tuple[str, bytes]] = []
    warnings: list[str] = []
    errors: list[str] = []
    _collect_from_zip_bytes(zip_bytes, "", collected, warnings)

    return _process_collected_vdr_blobs(
        collected=collected,
        entity_name=entity_name,
        max_pages_per_doc=max_pages_per_doc,
        warnings=warnings,
        errors=errors,
    )


def ingest_vdr_paths(
    paths: Sequence[Union[str, Path]],
    entity_name: str = "Target",
    max_pages_per_doc: int = 200,
    base_dir: Optional[Union[str, Path]] = None,
) -> VDRIngestionResult:
    """
    Recursively ingest a list of VDR file paths, directories, and/or ZIP archives
    with preserved index paths, SHA-256 deduplication, auto-classification, and Excel audit.
    """
    collected: list[tuple[str, bytes]] = []
    warnings: list[str] = []
    errors: list[str] = []
    base_root = Path(base_dir).resolve() if base_dir else None

    for raw_p in paths:
        p = Path(raw_p)
        if not p.exists():
            errors.append(f"Path not found: {p}")
            continue

        if p.is_dir():
            root_for_rel = base_root or p.resolve()
            for dirpath, _dirnames, filenames in os.walk(p):
                for fname in sorted(filenames):
                    if fname.startswith("."):
                        continue
                    fp = Path(dirpath) / fname
                    ext = fp.suffix.lower()
                    try:
                        rel = str(fp.resolve().relative_to(root_for_rel))
                    except ValueError:
                        rel = fp.name
                    vdr_rel = _normalize_vdr_path(rel)
                    if ext == ".zip":
                        _collect_from_zip_bytes(fp.read_bytes(), vdr_rel[:-4], collected, warnings)
                    elif ext in VDR_SUPPORTED_EXTENSIONS:
                        collected.append((vdr_rel, fp.read_bytes()))
                    else:
                        warnings.append(f"Skipped unsupported file: '{vdr_rel}'.")
        else:
            ext = p.suffix.lower()
            if base_root:
                try:
                    vdr_rel = _normalize_vdr_path(str(p.resolve().relative_to(base_root)))
                except ValueError:
                    vdr_rel = _normalize_vdr_path(p.name)
            else:
                vdr_rel = _normalize_vdr_path(p.name)

            if ext == ".zip":
                _collect_from_zip_bytes(p.read_bytes(), vdr_rel[:-4], collected, warnings)
            elif ext in VDR_SUPPORTED_EXTENSIONS:
                collected.append((vdr_rel, p.read_bytes()))
            else:
                warnings.append(f"Skipped unsupported file: '{vdr_rel}'.")

    return _process_collected_vdr_blobs(
        collected=collected,
        entity_name=entity_name,
        max_pages_per_doc=max_pages_per_doc,
        warnings=warnings,
        errors=errors,
    )


def _process_collected_vdr_blobs(
    collected: list[tuple[str, bytes]],
    entity_name: str,
    max_pages_per_doc: int,
    warnings: list[str],
    errors: list[str],
) -> VDRIngestionResult:
    """Deduplicate by SHA-256, extract every unique document, classify, and assemble result."""
    seen_hashes: dict[str, str] = {}
    records: list[VDRDocumentRecord] = []
    duplicates: list[DuplicateRecord] = []
    all_anomalies: list[ExcelAnomaly] = []
    extracted_docs: list[ExtractedDocument] = []
    category_counts: dict[str, int] = {cat: 0 for cat in VDR_CATEGORIES}

    with tempfile.TemporaryDirectory(prefix="vdr_ingest_") as tmpdir:
        tmp_root = Path(tmpdir)
        for idx, (vdr_path, raw_bytes) in enumerate(collected):
            sha = _sha256_bytes(raw_bytes)
            if sha in seen_hashes:
                duplicates.append(
                    DuplicateRecord(
                        vdr_path=vdr_path,
                        duplicate_of_path=seen_hashes[sha],
                        sha256=sha,
                        file_size_bytes=len(raw_bytes),
                    )
                )
                continue
            seen_hashes[sha] = vdr_path

            safe_name = sanitize_filename(Path(vdr_path).name)
            # Keep unique temp subdir per file so identical basenames in different VDR folders don't collide
            slot_dir = tmp_root / f"f_{idx:04d}"
            slot_dir.mkdir(parents=True, exist_ok=True)
            disk_path = slot_dir / safe_name
            disk_path.write_bytes(raw_bytes)

            try:
                rec, anomalies = _extract_single_vdr_file(
                    disk_path=disk_path,
                    vdr_path=vdr_path,
                    raw_bytes=raw_bytes,
                    max_pages_per_doc=max_pages_per_doc,
                )
                records.append(rec)
                all_anomalies.extend(anomalies)
                category_counts[rec.category] = category_counts.get(rec.category, 0) + 1
                if rec.document is not None:
                    extracted_docs.append(rec.document)
            except Exception as e:  # noqa: BLE001
                logger.error(f"Failed to ingest VDR file '{vdr_path}': {e}")
                errors.append(f"{vdr_path}: {e}")

    # Prune zero-count categories? Keep all 9 for clean UI charts/tables
    entity_docs = EntityDocuments(entity_name=entity_name, documents=extracted_docs)
    return VDRIngestionResult(
        entity_name=entity_name,
        entity_documents=entity_docs,
        records=records,
        duplicates_skipped=duplicates,
        excel_anomalies=all_anomalies,
        category_counts=category_counts,
        warnings=warnings,
        errors=errors,
    )


# =====================================================================
# 5. Sector VDR Completeness Checker (Institutional M&A Checklists)
# =====================================================================

_CORE_GENERAL_CHECKLIST: list[ChecklistItem] = [
    ChecklistItem(
        item_id="GEN-01",
        title="Audited Financial Statements (3 Years) / 10-K",
        category="Financials",
        priority="Critical",
        keywords=["10-k", "10k", "audited", "annual report", "financial statement", "income statement", "balance sheet"],
        rationale="Baseline historical revenue, EBITDA, balance sheet, and audit opinion verification.",
    ),
    ChecklistItem(
        item_id="GEN-02",
        title="Quality of Earnings (QoE) & Monthly Financial Databook",
        category="Financials",
        priority="Critical",
        keywords=["qoe", "quality of earnings", "databook", "financial model", "ebitda bridge", "working capital", "trial balance"],
        rationale="Validates EBITDA adjustments, recurring cash conversion, and NWC peg.",
    ),
    ChecklistItem(
        item_id="GEN-03",
        title="Management Presentation / CIM & 3-Year Forecast",
        category="Commercial",
        priority="Critical",
        keywords=["cim", "management presentation", "mgmt", "forecast", "budget", "business plan", "investor deck"],
        rationale="Core investment thesis, standalone projections, and market positioning.",
    ),
    ChecklistItem(
        item_id="GEN-04",
        title="Corporate Charter, Cap Table & Entity Structure",
        category="Corporate",
        priority="High",
        keywords=["cap table", "captable", "charter", "incorporation", "org chart", "subsidiar", "shareholder"],
        rationale="Confirms ownership, fully diluted share count, and subsidiary ring-fencing.",
    ),
    ChecklistItem(
        item_id="GEN-05",
        title="Tax Returns, Tax Audits & NOL / Transfer Pricing Schedule",
        category="Tax",
        priority="High",
        keywords=["tax", "1120", "transfer pricing", "nol", "deferred tax", "vat"],
        rationale="Quantifies historical tax exposure, Section 382 limits, and structuring options.",
    ),
    ChecklistItem(
        item_id="GEN-06",
        title="Material Customer & Vendor Contracts (Change-of-Control Review)",
        category="Legal & Contracts",
        priority="Critical",
        keywords=["contract", "msa", "agreement", "spa", "apa", "change of control", "vendor", "lease"],
        rationale="Identifies termination-on-change-of-control, exclusivity, and MFN clauses.",
    ),
    ChecklistItem(
        item_id="GEN-07",
        title="Litigation, Disputes & Contingent Liabilities Register",
        category="Legal & Contracts",
        priority="High",
        keywords=["litigation", "dispute", "claim", "lawsuit", "contingent", "legal summary"],
        rationale="Sizes off-balance-sheet legal liabilities requiring SPA indemnity/escrow.",
    ),
    ChecklistItem(
        item_id="GEN-08",
        title="Employee Census, Key-Talent Compensation & Equity Plans",
        category="HR & Compensation",
        priority="High",
        keywords=["employee", "census", "headcount", "compensation", "retention", "esop", "option", "payroll", "hr"],
        rationale="Evaluates retention pool sizing, severance triggers, and union/works council risk.",
    ),
    ChecklistItem(
        item_id="GEN-09",
        title="IT Architecture, Cybersecurity & IP Ownership Schedule",
        category="IP & Tech",
        priority="High",
        keywords=["architecture", "cyber", "soc2", "soc 2", "patent", "trademark", "ip", "open source", "security"],
        rationale="Assesses tech debt, IP chain-of-title, and post-close IT integration cost.",
    ),
    ChecklistItem(
        item_id="GEN-10",
        title="ESG, Environmental & Regulatory / Antitrust Disclosures",
        category="ESG & Regulatory",
        priority="Standard",
        keywords=["esg", "csrd", "sustainability", "scope 3", "antitrust", "regulatory", "compliance", "environmental"],
        rationale="Evaluates merger control risk, CSRD compliance gap, and environmental remediation.",
    ),
]

_SECTOR_SPECIFIC_ADDONS: dict[str, list[ChecklistItem]] = {
    "SaaS": [
        ChecklistItem(
            item_id="SAAS-01",
            title="ARR / NRR Cohort & Customer Churn Databook",
            category="Commercial",
            priority="Critical",
            keywords=["arr", "nrr", "cohort", "churn", "retention", "saas metrics", "bookings", "customer concentration"],
            rationale="Verifies net dollar retention, gross logo churn, and cohort expansion durability.",
        ),
        ChecklistItem(
            item_id="SAAS-02",
            title="Open-Source License Scan (SBOM / Black Duck) & Code Audit",
            category="IP & Tech",
            priority="Critical",
            keywords=["open source", "oss", "sbom", "black duck", "fossa", "gpl", "code audit", "software"],
            rationale="Prevents copyleft IP contamination and validates proprietary source code ownership.",
        ),
        ChecklistItem(
            item_id="SAAS-03",
            title="SOC 2 Type II / ISO 27001 Report & Pentest Findings",
            category="IP & Tech",
            priority="High",
            keywords=["soc 2", "soc2", "iso 27001", "iso27001", "pentest", "penetration", "gdpr", "privacy"],
            rationale="Confirms enterprise security posture and data-protection compliance.",
        ),
        ChecklistItem(
            item_id="SAAS-04",
            title="Cloud Hosting Commitments & Unit Economics (AWS/Azure/GCP)",
            category="IP & Tech",
            priority="Standard",
            keywords=["aws", "azure", "gcp", "cloud", "hosting", "gross margin", "infrastructure"],
            rationale="Quantifies multi-year cloud commit liabilities and hosting cost synergy potential.",
        ),
    ],
    "Automotive/Industrial": [
        ChecklistItem(
            item_id="AUTO-01",
            title="BOM, Tier-1/Tier-2 Supplier Contracts & Sole-Source Map",
            category="Operational & Legal" if False else "Legal & Contracts",
            priority="Critical",
            keywords=["supplier", "supply chain", "bom", "bill of materials", "tier-1", "procurement", "oem"],
            rationale="Identifies single-source supply bottlenecks and raw-material pass-through clauses.",
        ),
        ChecklistItem(
            item_id="AUTO-02",
            title="Manufacturing Footprint, Capex Plan & Plant Utilization Schedule",
            category="Financials",
            priority="Critical",
            keywords=["plant", "factory", "manufacturing", "capex", "utilization", "capacity", "facility", "footprint"],
            rationale="Validates maintenance vs. growth capex and footprint consolidation synergies.",
        ),
        ChecklistItem(
            item_id="AUTO-03",
            title="Environmental Phase I/II & Scope 1–3 Emissions Report",
            category="ESG & Regulatory",
            priority="Critical",
            keywords=["phase i", "environmental", "emissions", "scope 3", "carbon", "esg", "remediation", "csrd"],
            rationale="Uncovers soil/groundwater remediation liabilities and EU CSRD/CBAM exposure.",
        ),
        ChecklistItem(
            item_id="AUTO-04",
            title="Warranty Reserves, Recall History & Collective Bargaining Agreements",
            category="HR & Compensation",
            priority="High",
            keywords=["warranty", "recall", "union", "collective bargaining", "works council", "pension"],
            rationale="Quantifies product-recall tail risk and labor restructuring constraints.",
        ),
    ],
    "Healthcare/Biopharma": [
        ChecklistItem(
            item_id="HC-01",
            title="FDA / EMA Regulatory Filings, Approvals & Inspection Letters (483)",
            category="ESG & Regulatory",
            priority="Critical",
            keywords=["fda", "ema", "510(k)", "nda", "bla", "483", "regulatory", "clinical", "gcp", "gmp"],
            rationale="Verifies marketing authorization integrity and manufacturing GMP compliance.",
        ),
        ChecklistItem(
            item_id="HC-02",
            title="Clinical Pipeline Data & Patent Cliff / Exclusivity Schedule",
            category="IP & Tech",
            priority="Critical",
            keywords=["patent", "exclusivity", "orange book", "clinical trial", "phase ii", "phase iii", "pipeline", "loe"],
            rationale="Assesses loss-of-exclusivity (LOE) cliffs and clinical read-out probability.",
        ),
        ChecklistItem(
            item_id="HC-03",
            title="Payor Mix, Reimbursement Contracts & Pricing / Rebate Schedule",
            category="Commercial",
            priority="High",
            keywords=["payor", "reimbursement", "medicare", "medicaid", "rebate", "gtn", "gross-to-net", "formulary"],
            rationale="Evaluates gross-to-net pricing erosion and government payor concentration.",
        ),
        ChecklistItem(
            item_id="HC-04",
            title="HIPAA / GDPR Patient Privacy & Pharmacovigilance Compliance",
            category="ESG & Regulatory",
            priority="High",
            keywords=["hipaa", "privacy", "pharmacovigilance", "compliance", "stark", "anti-kickback"],
            rationale="Mitigates statutory healthcare enforcement and False Claims Act exposure.",
        ),
    ],
}


def _normalize_sector(sector: str) -> str:
    s = (sector or "").strip().lower()
    if any(k in s for k in ("saas", "software", "tech", "cloud", "ai")):
        return "SaaS"
    if any(k in s for k in ("auto", "industr", "manufactur", "mobility", "aerospace", "ev")):
        return "Automotive/Industrial"
    if any(k in s for k in ("health", "bio", "pharma", "medtech", "life science")):
        return "Healthcare/Biopharma"
    return "General"


def assess_vdr_completeness(
    docs: Union[VDRIngestionResult, EntityDocuments, Sequence[Union[VDRDocumentRecord, ExtractedDocument, str]]],
    sector: str = "General",
) -> VDRCompletenessReport:
    """
    Compare uploaded VDR documents against institutional M&A checklists
    (SaaS, Automotive/Industrial, Healthcare/Biopharma, General) and return
    overall coverage %, critical-item coverage %, and a Missing Document Request List.
    """
    canonical_sector = _normalize_sector(sector)
    checklist: list[ChecklistItem] = list(_CORE_GENERAL_CHECKLIST)
    if canonical_sector in _SECTOR_SPECIFIC_ADDONS:
        checklist.extend(_SECTOR_SPECIFIC_ADDONS[canonical_sector])

    # Normalize incoming doc representations to (label, search_blob, category)
    doc_entries: list[tuple[str, str, str]] = []
    if isinstance(docs, VDRIngestionResult):
        for r in docs.records:
            txt = r.document.full_text[:6000] if r.document else ""
            doc_entries.append((r.vdr_path or r.filename, f"{r.vdr_path} {r.filename} {txt}".lower(), r.category))
    elif isinstance(docs, EntityDocuments):
        for d in docs.documents:
            cat = classify_vdr_document(d).category
            doc_entries.append((d.filename, f"{d.filename} {d.full_text[:6000]}".lower(), cat))
    else:
        for item in docs:
            if isinstance(item, VDRDocumentRecord):
                txt = item.document.full_text[:6000] if item.document else ""
                doc_entries.append((item.vdr_path or item.filename, f"{item.vdr_path} {item.filename} {txt}".lower(), item.category))
            elif isinstance(item, ExtractedDocument):
                cat = classify_vdr_document(item).category
                doc_entries.append((item.filename, f"{item.filename} {item.full_text[:6000]}".lower(), cat))
            elif isinstance(item, str):
                cat = classify_vdr_document(item).category
                doc_entries.append((item, item.lower(), cat))

    matched_items: list[ChecklistMatch] = []
    missing_items: list[MissingDocumentItem] = []
    cat_totals: dict[str, int] = {}
    cat_hits: dict[str, int] = {}

    for ci in checklist:
        cat_totals[ci.category] = cat_totals.get(ci.category, 0) + 1
        matched_doc_names: list[str] = []
        for label, blob, doc_cat in doc_entries:
            kw_hits = sum(1 for kw in ci.keywords if kw in blob)
            if kw_hits >= 1 and (doc_cat == ci.category or kw_hits >= 2 or any(kw in label.lower() for kw in ci.keywords)):
                matched_doc_names.append(label)

        if matched_doc_names:
            cat_hits[ci.category] = cat_hits.get(ci.category, 0) + 1
            matched_items.append(
                ChecklistMatch(
                    item_id=ci.item_id,
                    title=ci.title,
                    category=ci.category,
                    priority=ci.priority,
                    matched_documents=matched_doc_names[:5],
                )
            )
        else:
            missing_items.append(
                MissingDocumentItem(
                    item_id=ci.item_id,
                    title=ci.title,
                    category=ci.category,
                    priority=ci.priority,
                    rationale=ci.rationale,
                    suggested_irl_request=(
                        f"[{ci.item_id} | {ci.priority}] Please upload {ci.title} "
                        f"under VDR folder '{ci.category}' to enable verification of: {ci.rationale}"
                    ),
                )
            )

    total = len(checklist)
    present = len(matched_items)
    coverage_pct = round((present / total) * 100.0, 1) if total else 0.0

    crit_items = [ci for ci in checklist if ci.priority == "Critical"]
    crit_matched = [m for m in matched_items if m.priority == "Critical"]
    crit_pct = round((len(crit_matched) / len(crit_items)) * 100.0, 1) if crit_items else 100.0

    category_coverage = {
        cat: round((cat_hits.get(cat, 0) / cnt) * 100.0, 1)
        for cat, cnt in cat_totals.items()
    }

    if coverage_pct >= 80.0 and crit_pct >= 85.0:
        grade = "Deal-Ready"
    elif coverage_pct >= 60.0 and crit_pct >= 60.0:
        grade = "Substantial"
    elif coverage_pct >= 35.0:
        grade = "Partial"
    else:
        grade = "Insufficient"

    # Sort missing items: Critical first, then High, then Standard
    prio_rank = {"Critical": 0, "High": 1, "Standard": 2}
    missing_items.sort(key=lambda m: (prio_rank.get(m.priority, 3), m.item_id))

    return VDRCompletenessReport(
        sector=canonical_sector,
        total_checklist_items=total,
        present_count=present,
        missing_count=len(missing_items),
        coverage_pct=coverage_pct,
        critical_total=len(crit_items),
        critical_present=len(crit_matched),
        critical_coverage_pct=crit_pct,
        readiness_grade=grade,
        matched_items=matched_items,
        missing_items=missing_items,
        category_coverage=category_coverage,
    )


# =====================================================================
# 6. Document Version Diff Engine (v1 vs v2 Filings / SPAs / CIMs)
# =====================================================================

_NUM_TOKEN_RE = re.compile(
    r"(?P<curr>\$|€|£)?\s*(?P<num>\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(?P<scale>%|(?:billion|bn|million|mn|thousand|b|m|k|x)\b)?",
    re.IGNORECASE,
)

_UNIT_MAP = {"$": "USD", "€": "EUR", "£": "GBP"}
_SCALE_MULT = {
    "billion": 1e9, "bn": 1e9, "b": 1e9,
    "million": 1e6, "mn": 1e6, "m": 1e6,
    "thousand": 1e3, "k": 1e3,
}


def _parse_numeric_tokens(line: str) -> list[tuple[str, float, str]]:
    """Return list of (display_str, normalized_value, unit) found in a text line."""
    found: list[tuple[str, float, str]] = []
    for m in _NUM_TOKEN_RE.finditer(line):
        curr = m.group("curr") or ""
        num_str = m.group("num").replace(",", "")
        scale = (m.group("scale") or "").lower()
        # Require either currency, scale, or % / x to avoid flagging plain section numbers like "Section 4"
        if not curr and not scale:
            continue
        try:
            base = float(num_str)
        except ValueError:
            continue
        # Skip plain years accidentally matched
        if not curr and scale == "" and 1900 <= base <= 2100:
            continue
        if scale == "%":
            unit = "PCT"
            val = base
        elif scale == "x":
            unit = "MULTIPLE"
            val = base
        else:
            unit = _UNIT_MAP.get(curr, "NUMBER")
            val = base * _SCALE_MULT.get(scale, 1.0)
        found.append((m.group(0).strip(), val, unit))
    return found


def _extract_label_prefix(line: str) -> str:
    """Derive a concise context label from the non-numeric words of a line."""
    cleaned = re.sub(r"[\$€£]?\d[\d,\.]*\s*(?:billion|bn|million|mn|m|b|k|%|x)?", "", line, flags=re.IGNORECASE)
    cleaned = re.sub(r"[|\-:]+", " ", cleaned)
    words = [w for w in cleaned.split() if len(w) > 1][:8]
    return " ".join(words).strip() or line[:60].strip()


def diff_document_versions(
    doc_old: Union[ExtractedDocument, str],
    doc_new: Union[ExtractedDocument, str],
    old_name: str = "v1",
    new_name: str = "v2",
    max_diff_lines: int = 250,
) -> DocumentDiffResult:
    """
    Compare v1 and v2 of a filing, CIM, or SPA and return added/removed lines,
    unified diff, similarity score, and quantified numeric figure changes.
    """
    if isinstance(doc_old, ExtractedDocument):
        old_text = doc_old.full_text
        old_label = doc_old.filename
    else:
        old_text = str(doc_old)
        old_label = old_name

    if isinstance(doc_new, ExtractedDocument):
        new_text = doc_new.full_text
        new_label = doc_new.filename
    else:
        new_text = str(doc_new)
        new_label = new_name

    old_lines = [ln.strip() for ln in old_text.splitlines() if ln.strip()]
    new_lines = [ln.strip() for ln in new_text.splitlines() if ln.strip()]

    matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
    similarity = round(matcher.ratio(), 4)

    added_lines: list[str] = []
    removed_lines: list[str] = []
    numeric_changes: list[NumericFigureChange] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        old_chunk = old_lines[i1:i2]
        new_chunk = new_lines[j1:j2]

        if tag in ("delete", "replace"):
            removed_lines.extend(old_chunk)
        if tag in ("insert", "replace"):
            added_lines.extend(new_chunk)

        if tag == "replace":
            # Pair modified lines to detect changed numbers (e.g., purchase price cap, EBITDA, basket %)
            pair_count = min(len(old_chunk), len(new_chunk))
            for k in range(pair_count):
                o_ln = old_chunk[k]
                n_ln = new_chunk[k]
                o_nums = _parse_numeric_tokens(o_ln)
                n_nums = _parse_numeric_tokens(n_ln)
                for (o_disp, o_val, o_unit), (n_disp, n_val, n_unit) in zip(o_nums, n_nums):
                    if o_unit != n_unit:
                        continue
                    if abs(o_val - n_val) < 1e-9:
                        continue
                    delta = n_val - o_val
                    delta_pct = round((delta / abs(o_val)) * 100.0, 2) if abs(o_val) > 1e-9 else None
                    abs_pct = abs(delta_pct) if delta_pct is not None else 100.0
                    if abs_pct >= 15.0:
                        mat = "High"
                    elif abs_pct >= 5.0:
                        mat = "Medium"
                    else:
                        mat = "Low"
                    numeric_changes.append(
                        NumericFigureChange(
                            context_label=_extract_label_prefix(n_ln),
                            metric_unit=o_unit,
                            old_display=o_disp,
                            new_display=n_disp,
                            old_value=o_val,
                            new_value=n_val,
                            delta=round(delta, 4),
                            delta_pct=delta_pct,
                            materiality=mat,
                            old_line=o_ln[:200],
                            new_line=n_ln[:200],
                        )
                    )

    u_diff_lines = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=old_label,
            tofile=new_label,
            lineterm="",
            n=2,
        )
    )
    unified_diff = "\n".join(u_diff_lines[:max_diff_lines])
    if len(u_diff_lines) > max_diff_lines:
        unified_diff += f"\n... ({len(u_diff_lines) - max_diff_lines} additional diff lines omitted)"

    summary = (
        f"Compared '{old_label}' → '{new_label}': {similarity * 100:.1f}% line similarity, "
        f"+{len(added_lines)} added / -{len(removed_lines)} removed lines, "
        f"{len(numeric_changes)} quantified figure change(s) detected."
    )

    return DocumentDiffResult(
        old_filename=old_label,
        new_filename=new_label,
        similarity_ratio=similarity,
        added_count=len(added_lines),
        removed_count=len(removed_lines),
        added_lines=added_lines,
        removed_lines=removed_lines,
        numeric_changes=numeric_changes,
        unified_diff=unified_diff,
        summary=summary,
    )
