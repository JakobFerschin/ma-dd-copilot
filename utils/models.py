"""
Dependency-free data structures for document extraction & intelligent context budgeting.

Kept separate from `pdf_extractor.py` (which imports pdfplumber/pypdf) so that the
dataclasses can be used in mock mode, tests, and schema code without pulling in
heavy PDF libraries.

Key upgrades over naive text slicing:
  1. Page-aligned tables (`page_tables`) are woven directly into each page's
     `[Source: <filename> | p.<n>]` block so financial tables are never dropped
     before reaching the LLM.
  2. Proportional multi-document budgeting in `EntityDocuments.provenance_text()`
     guarantees that uploading a long 10-K alongside an ESG report and an Earnings
     Call transcript never starves the shorter documents out of the LLM context.
  3. High-signal financial/M&A keyword scoring prioritizes dense analytical pages
     when a document exceeds its allocated character budget, while always keeping
     opening overview pages intact.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


# High-signal M&A / financial diligence keywords used when down-selecting pages
# of oversized filings (e.g., 300-page 10-Ks).
_SIGNAL_TERMS = (
    "revenue", "ebitda", "operating income", "margin", "cash flow", "debt", "liquidity",
    "goodwill", "synergy", "acquisition", "merger", "antitrust", "competition", "risk factor",
    "litigation", "regulatory", "esg", "scope 3", "csrd", "emissions", "supply chain",
    "supplier", "software", "cloud", "aws", "azure", "architecture", "retention", "attrition",
    "headcount", "segment", "geographic", "customer concentration", "backlog", "guidance",
)


def _page_signal_score(text: str) -> float:
    """Heuristic score measuring M&A due-diligence information density of a page."""
    if not text:
        return 0.0
    lower = text.lower()
    term_hits = sum(1 for term in _SIGNAL_TERMS if term in lower)
    # Financial figures (currency / percentages / tables) carry strong analytical signal
    num_hits = min(len(re.findall(r"(?:\$|€|£)\s*\d|\d+(?:\.\d+)?%", text)), 15)
    table_bonus = 4.0 if "[Table" in text or " | " in text else 0.0
    return term_hits * 2.0 + num_hits * 0.6 + table_bonus


@dataclass
class ExtractedDocument:
    """A single extracted document (PDF, DOCX, TXT, MD, CSV): per-page text + tables + metadata."""
    filename: str
    num_pages: int
    full_text: str
    pages: list[str] = field(default_factory=list)            # text per page, 0-indexed
    tables: list[str] = field(default_factory=list)           # flattened table text (legacy flat list)
    page_tables: dict[int, list[str]] = field(default_factory=dict)  # 0-indexed page -> markdown tables
    file_type: str = "pdf"
    word_count: int = 0
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.word_count == 0 and self.full_text:
            self.word_count = len(self.full_text.split())

    def page_with_tables(self, page_idx: int) -> str:
        """Return page text combined with any tables extracted on that page."""
        base = self.pages[page_idx] if 0 <= page_idx < len(self.pages) else ""
        tbls = self.page_tables.get(page_idx, [])
        if not tbls:
            return base
        formatted_tables = "\n\n".join(
            f"[Table {idx + 1}]\n{tbl}" for idx, tbl in enumerate(tbls)
        )
        if base.strip():
            return f"{base}\n\n{formatted_tables}"
        return formatted_tables

    def excerpt(self, max_chars: int) -> str:
        """Truncate to the first `max_chars` (LLM context budget)."""
        return self.full_text[:max_chars]


@dataclass
class EntityDocuments:
    """
    Aggregate of all documents uploaded for one entity (e.g. 10-K + ESG +
    Earnings Call). Central to multi-document DD.

    `provenance_text` interleaves every page (and its extracted tables) with a
    source tag so the LLM can cite WHICH document and page a claim came from.
    """
    entity_name: str
    documents: list[ExtractedDocument] = field(default_factory=list)

    @property
    def total_pages(self) -> int:
        return sum(d.num_pages for d in self.documents)

    @property
    def total_words(self) -> int:
        return sum(d.word_count for d in self.documents)

    @property
    def total_tables(self) -> int:
        return sum(len(d.tables) for d in self.documents)

    @property
    def document_names(self) -> list[str]:
        return [d.filename for d in self.documents]

    def _build_doc_chunks(self, doc: ExtractedDocument) -> list[tuple[int, str]]:
        """Return list of (0-indexed page_idx, tagged_chunk) for non-empty pages."""
        chunks: list[tuple[int, str]] = []
        for i in range(len(doc.pages)):
            body = doc.page_with_tables(i)
            if not body.strip():
                continue
            chunks.append((i, f"[Source: {doc.filename} | p.{i + 1}]\n{body}"))
        # Fallback if a document has tables without per-page mapping (legacy construction)
        if not chunks and doc.tables:
            tbl_blob = "\n\n".join(doc.tables)
            chunks.append((0, f"[Source: {doc.filename} | p.1]\n{tbl_blob}"))
        return chunks

    def _select_chunks_within_budget(
        self,
        doc: ExtractedDocument,
        chunks: list[tuple[int, str]],
        budget: int,
    ) -> str:
        """
        Fit a single document's tagged pages into `budget` chars.
        If it already fits, emit everything in page order.
        Otherwise, keep the opening 2 pages (executive overview) and fill remaining
        budget with the highest M&A-signal pages, emitted in original page order.
        """
        full = "\n\n".join(c for _, c in chunks)
        if len(full) <= budget:
            return full

        if not chunks or budget <= 0:
            return ""

        selected_indices: set[int] = set()
        used = 0

        # Always retain opening overview pages first (up to 2)
        for pos in range(min(2, len(chunks))):
            chunk_text = chunks[pos][1]
            cost = len(chunk_text) + 2
            if used + cost <= budget or not selected_indices:
                selected_indices.add(pos)
                used += cost

        # Rank remaining pages by M&A analytical signal density
        remaining = [
            (pos, _page_signal_score(chunks[pos][1]), chunks[pos][1])
            for pos in range(len(chunks))
            if pos not in selected_indices
        ]
        remaining.sort(key=lambda item: item[1], reverse=True)

        for pos, _score, chunk_text in remaining:
            cost = len(chunk_text) + 2
            if used + cost <= budget:
                selected_indices.add(pos)
                used += cost

        ordered = [chunks[pos][1] for pos in sorted(selected_indices)]
        omitted = len(chunks) - len(ordered)
        assembled = "\n\n".join(ordered)
        if omitted > 0:
            note = (
                f"\n\n[Note: {doc.filename} — prioritized {len(ordered)}/{len(chunks)} "
                f"high-signal pages within context budget; {omitted} lower-signal pages omitted]"
            )
            if len(assembled) + len(note) <= budget:
                assembled += note
        return assembled[:budget]

    def provenance_text(self, max_chars: int | None = None) -> str:
        """
        Build a combined provenance-tagged string across all uploaded documents:
            [Source: 10K_2024.pdf | p.3]
            <page text + extracted tables>

        When `max_chars` is provided and total content exceeds the budget, space is
        allocated fairly across every document (with unused budget redistributed to
        larger documents) so secondary filings (ESG, Earnings Calls) are never starved.
        """
        if not self.documents:
            return ""

        doc_chunks = [(doc, self._build_doc_chunks(doc)) for doc in self.documents]
        raw_docs = [(doc, chunks, "\n\n".join(c for _, c in chunks)) for doc, chunks in doc_chunks if chunks]

        if not raw_docs:
            return ""

        total_len = sum(len(text) for _, _, text in raw_docs) + 2 * max(len(raw_docs) - 1, 0)
        if max_chars is None or total_len <= max_chars:
            return "\n\n".join(text for _, _, text in raw_docs)

        # Fair water-filling budget allocation across documents
        separator_overhead = 2 * max(len(raw_docs) - 1, 0)
        available = max(max_chars - separator_overhead, 0)
        budgets: dict[int, int] = {}
        unassigned = set(range(len(raw_docs)))

        while unassigned:
            fair_share = available // len(unassigned)
            small_found = False
            for idx in list(unassigned):
                doc_len = len(raw_docs[idx][2])
                if doc_len <= fair_share:
                    budgets[idx] = doc_len
                    available -= doc_len
                    unassigned.remove(idx)
                    small_found = True
            if not small_found:
                # Split remaining budget evenly among remaining oversized documents
                rem_list = sorted(unassigned)
                base_share = available // len(rem_list)
                remainder = available % len(rem_list)
                for offset, idx in enumerate(rem_list):
                    budgets[idx] = base_share + (1 if offset < remainder else 0)
                break

        pieces: list[str] = []
        for idx, (doc, chunks, _full_text) in enumerate(raw_docs):
            pieces.append(self._select_chunks_within_budget(doc, chunks, budgets[idx]))

        return "\n\n".join(p for p in pieces if p)[:max_chars]

    def excerpt(self, max_chars: int) -> str:
        """Alias for provenance_text for symmetry with ExtractedDocument."""
        return self.provenance_text(max_chars)
