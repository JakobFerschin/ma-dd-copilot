"""
Dependency-free data structures for document extraction.

Kept separate from `pdf_extractor.py` (which imports pdfplumber) so that the
dataclasses can be used in mock mode, tests, and schema code without pulling in
the heavy PDF library.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ExtractedDocument:
    """A single extracted PDF: per-page text + flattened tables."""
    filename: str
    num_pages: int
    full_text: str
    pages: list[str] = field(default_factory=list)   # text per page, 0-indexed
    tables: list[str] = field(default_factory=list)  # flattened table text per occurrence

    def excerpt(self, max_chars: int) -> str:
        """Truncate to the first `max_chars` (LLM context budget)."""
        return self.full_text[:max_chars]


@dataclass
class EntityDocuments:
    """
    Aggregate of all documents uploaded for one entity (e.g. 10-K + ESG +
    Earnings Call). Central to multi-document DD.

    `provenance_text` interleaves every page with a source tag so the LLM can
    cite WHICH document a claim came from — without this, multi-doc upload
    breaks the data-grounding methodology.
    """
    entity_name: str
    documents: list[ExtractedDocument] = field(default_factory=list)

    @property
    def total_pages(self) -> int:
        return sum(d.num_pages for d in self.documents)

    @property
    def document_names(self) -> list[str]:
        return [d.filename for d in self.documents]

    def provenance_text(self, max_chars: int | None = None) -> str:
        """
        Build a single combined string with inline source tags:
            [Source: 10K_2024.pdf | p.3]
            <page text>

        Tags are page-granular, enabling precise citations like
        "10K_2024.pdf, p.47, Note 9".
        """
        chunks: list[str] = []
        for doc in self.documents:
            for i, page_text in enumerate(doc.pages):
                if not page_text.strip():
                    continue
                chunks.append(f"[Source: {doc.filename} | p.{i + 1}]\n{page_text}")
        combined = "\n\n".join(chunks)
        if max_chars is not None:
            combined = combined[:max_chars]
        return combined

    def excerpt(self, max_chars: int) -> str:
        """Alias for provenance_text for symmetry with ExtractedDocument."""
        return self.provenance_text(max_chars)
