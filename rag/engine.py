"""
Hybrid BM25 + TF-IDF Cosine RAG Engine & Cross-Document Contradiction Detector.

Zero external vector database required — runs deterministically and fast in pure
Python + NumPy over `EntityDocuments` / `ExtractedDocument` objects:
  1. `DocumentChunk`: provenance-preserving chunk with exact page, section title,
     character span (`start_char`, `end_char`), category, and SHA-256 fingerprint.
  2. `HybridRAGIndex`:
     - Okapi BM25 (`k1=1.5`, `b=0.75`) lexical retrieval
     - Sublinear TF-IDF (unigrams + bigrams) L2-normalized NumPy cosine similarity
     - Hybrid score fusion with metadata filtering (`entity_name`, `filename`, `category`)
  3. `ContradictionItem` & `detect_cross_document_contradictions(entities, report=None)`:
     - Extracts normalized financial metrics (Revenue, EBITDA, ARR, Gross Margin,
       Operating Income, Net Income, Free Cash Flow, Total Debt, Cash, Headcount, NRR)
       with period hints across documents of an entity and flags diverging figures
       across CIM vs. 10-K vs. Transcript vs. Financial Model — plus report-vs-VDR
       and report-vs-DCF contradictions.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from typing import Optional, Sequence, Union

import numpy as np
from pydantic import BaseModel, Field

from core.financial_model import compute_synergy_dcf
from schemas.report_schema import DueDiligenceReport, Severity, SynergyType
from utils.models import EntityDocuments, ExtractedDocument
from vdr.ingestion import classify_vdr_document

# Compact English stop-words (intentionally preserves financial/M&A tokens like 'it', 'ai', 'ip', 'q1')
_STOP_WORDS = frozenset({
    "the", "and", "for", "that", "with", "this", "from", "are", "was", "were",
    "have", "has", "had", "not", "but", "you", "all", "can", "her", "his",
    "our", "out", "who", "will", "would", "could", "should", "into", "than",
    "then", "them", "these", "those", "their", "there", "about", "which",
    "when", "where", "while", "been", "being", "also", "such", "more", "most",
    "some", "any", "each", "other", "over", "under", "between", "through",
    "during", "before", "after", "above", "below", "only", "very", "just",
})

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[-'][a-z0-9]+)?")


# =====================================================================
# Data Models
# =====================================================================

class DocumentChunk(BaseModel):
    """Provenance-rich chunk stored inside `HybridRAGIndex`."""
    chunk_id: str
    entity_name: str
    filename: str
    page_num: int = Field(..., ge=1)
    section_title: str = "General"
    text: str
    start_char: int = Field(0, ge=0)
    end_char: int = Field(0, ge=0)
    sha256: str
    category: str = "General"
    word_count: int = 0


class SearchResult(BaseModel):
    """Ranked retrieval hit from `HybridRAGIndex.search`."""
    chunk: DocumentChunk
    score: float
    bm25_score: float
    cosine_score: float
    matched_terms: list[str] = Field(default_factory=list)
    citation: str = ""


class ContradictionItem(BaseModel):
    """Detected cross-document or report-vs-VDR numerical divergence."""
    contradiction_id: str
    entity_name: str
    metric_name: str
    period_hint: str = "Unspecified"
    severity: Severity = Severity.HIGH
    source_a_document: str
    source_a_location: str
    source_a_value: float
    source_a_display: str
    source_a_excerpt: str = ""
    source_b_document: str
    source_b_location: str
    source_b_value: float
    source_b_display: str
    source_b_excerpt: str = ""
    unit: str = "USD_M"
    variance_pct: float
    explanation: str


# =====================================================================
# Chunking & Section Detection Helpers
# =====================================================================

_HEADING_PATTERNS = (
    re.compile(r"^#{1,4}\s+(.+)$"),
    re.compile(r"^\[(?:Sheet|Slide)\s*[:\s]*([^\]]+)\]", re.IGNORECASE),
    re.compile(r"^(?:ITEM\s+\d+[A-Z]?\.?\s*[-—:]?\s*.+)$", re.IGNORECASE),
    re.compile(r"^(?:NOTE\s+\d+\s*[-—:]?\s*.+)$", re.IGNORECASE),
    re.compile(r"^(?:SECTION\s+\d+(?:\.\d+)?\s*[-—:]?\s*.+)$", re.IGNORECASE),
    re.compile(r"^(?:ARTICLE\s+[IVX\d]+\s*[-—:]?\s*.+)$", re.IGNORECASE),
)


def _detect_section_title(text: str, fallback: str = "Overview") -> str:
    """Infer a concise section title from the first heading-like line of a page/chunk."""
    for raw_line in text.splitlines()[:12]:
        line = raw_line.strip()
        if not line or line.startswith("|") or line.startswith("---"):
            continue
        for pat in _HEADING_PATTERNS:
            m = pat.match(line)
            if m:
                title = m.group(1) if m.lastindex else m.group(0)
                return title.strip()[:90]
        # Short ALL-CAPS heading line (e.g. "RISK FACTORS", "EXECUTIVE SUMMARY")
        if 4 <= len(line) <= 75 and line.isupper() and any(ch.isalpha() for ch in line):
            return line.title()
    return fallback


def _chunk_page_text(
    text: str,
    chunk_size: int = 1000,
    overlap: int = 160,
) -> list[tuple[str, int, int]]:
    """
    Split a page's text into overlapping (chunk_text, start_char, end_char) slices,
    preferring paragraph/line breaks over mid-word cuts.
    """
    cleaned = text.strip()
    if not cleaned:
        return []
    if len(cleaned) <= chunk_size:
        return [(cleaned, 0, len(cleaned))]

    chunks: list[tuple[str, int, int]] = []
    n = len(cleaned)
    start = 0
    while start < n:
        end = min(start + chunk_size, n)
        if end < n:
            # Prefer paragraph boundary, then newline, then sentence/space
            para_cut = cleaned.rfind("\n\n", start + chunk_size // 2, end)
            if para_cut != -1:
                end = para_cut
            else:
                nl_cut = cleaned.rfind("\n", start + chunk_size // 2, end)
                if nl_cut != -1:
                    end = nl_cut
                else:
                    sp_cut = cleaned.rfind(" ", start + chunk_size // 2, end)
                    if sp_cut != -1:
                        end = sp_cut

        piece = cleaned[start:end].strip()
        if piece:
            chunks.append((piece, start, end))
        if end >= n:
            break
        next_start = max(end - overlap, start + 1)
        start = next_start
    return chunks


def _tokenize(text: str, include_bigrams: bool = False) -> list[str]:
    """Tokenize text into normalized unigrams (and optional bigrams for phrase fidelity)."""
    raw = _TOKEN_RE.findall(text.lower())
    unigrams = [t for t in raw if len(t) >= 2 and t not in _STOP_WORDS]
    if not include_bigrams or len(unigrams) < 2:
        return unigrams
    bigrams = [f"{unigrams[i]}_{unigrams[i + 1]}" for i in range(len(unigrams) - 1)]
    return unigrams + bigrams


# =====================================================================
# 1. HybridRAGIndex (BM25 + TF-IDF Cosine in Pure Python + NumPy)
# =====================================================================

class HybridRAGIndex:
    """
    Fast, deterministic, zero-server Hybrid Search Engine combining Okapi BM25
    and L2-normalized TF-IDF Cosine similarity (unigrams + bigrams) in NumPy,
    with metadata filtering on `entity_name`, `filename`, and `category`.
    """

    def __init__(
        self,
        chunk_size: int = 1000,
        chunk_overlap: int = 160,
        bm25_k1: float = 1.5,
        bm25_b: float = 0.75,
        max_vocab_size: int = 25_000,
    ) -> None:
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.k1 = bm25_k1
        self.b = bm25_b
        self.max_vocab_size = max_vocab_size

        self.chunks: list[DocumentChunk] = []
        self._chunk_unigrams: list[list[str]] = []
        self._chunk_tf: list[Counter[str]] = []
        self._doc_lengths: np.ndarray = np.zeros(0, dtype=np.float32)
        self._avg_dl: float = 1.0
        self._bm25_idf: dict[str, float] = {}
        self._inverted_index: dict[str, list[tuple[int, int]]] = {}

        # TF-IDF Cosine structures
        self._vocab: dict[str, int] = {}
        self._tfidf_idf: np.ndarray = np.zeros(0, dtype=np.float32)
        self._tfidf_matrix: np.ndarray = np.zeros((0, 0), dtype=np.float32)
        self._is_built: bool = False

    @property
    def chunk_count(self) -> int:
        return len(self.chunks)

    @property
    def vocabulary_size(self) -> int:
        return len(self._vocab)

    @property
    def indexed_entities(self) -> list[str]:
        seen: list[str] = []
        for c in self.chunks:
            if c.entity_name not in seen:
                seen.append(c.entity_name)
        return seen

    @property
    def indexed_files(self) -> list[str]:
        seen: list[str] = []
        for c in self.chunks:
            if c.filename not in seen:
                seen.append(c.filename)
        return seen

    def clear(self) -> None:
        self.chunks.clear()
        self._chunk_unigrams.clear()
        self._chunk_tf.clear()
        self._doc_lengths = np.zeros(0, dtype=np.float32)
        self._avg_dl = 1.0
        self._bm25_idf.clear()
        self._inverted_index.clear()
        self._vocab.clear()
        self._tfidf_idf = np.zeros(0, dtype=np.float32)
        self._tfidf_matrix = np.zeros((0, 0), dtype=np.float32)
        self._is_built = False

    def add_document(
        self,
        entity_name: str,
        doc: ExtractedDocument,
        category: Optional[str] = None,
    ) -> int:
        """Chunk and append a single ExtractedDocument; returns number of chunks added."""
        resolved_cat = category or classify_vdr_document(doc).category
        ent_slug = re.sub(r"[^a-z0-9]+", "_", (entity_name or "entity").lower()).strip("_") or "entity"
        added = 0

        pages_count = len(doc.pages)
        if pages_count == 0 and doc.full_text.strip():
            pages_to_iter = [(0, doc.full_text)]
        else:
            pages_to_iter = [(idx, doc.page_with_tables(idx)) for idx in range(pages_count)]

        current_section = "Overview"
        for page_idx, body in pages_to_iter:
            if not body or not body.strip():
                continue
            current_section = _detect_section_title(body, fallback=current_section)
            slices = _chunk_page_text(body, chunk_size=self.chunk_size, overlap=self.chunk_overlap)
            for c_idx, (piece, s_char, e_char) in enumerate(slices):
                sec = _detect_section_title(piece, fallback=current_section)
                sha = hashlib.sha256(piece.encode("utf-8")).hexdigest()
                cid = f"{ent_slug}:{doc.filename}:p{page_idx + 1}:c{c_idx}"
                chunk = DocumentChunk(
                    chunk_id=cid,
                    entity_name=entity_name,
                    filename=doc.filename,
                    page_num=page_idx + 1,
                    section_title=sec,
                    text=piece,
                    start_char=s_char,
                    end_char=e_char,
                    sha256=sha,
                    category=resolved_cat,
                    word_count=len(piece.split()),
                )
                self.chunks.append(chunk)
                added += 1

        self._is_built = False
        return added

    def index_entities(
        self,
        entities: Union[EntityDocuments, Sequence[EntityDocuments]],
        category_map: Optional[dict[str, str]] = None,
        reset: bool = True,
    ) -> int:
        """Index one or more `EntityDocuments` and build the BM25 + TF-IDF matrices."""
        if reset:
            self.clear()

        ent_list = [entities] if isinstance(entities, EntityDocuments) else list(entities)
        cat_lookup = category_map or {}

        for ent in ent_list:
            for doc in ent.documents:
                cat = cat_lookup.get(doc.filename)
                self.add_document(ent.entity_name, doc, category=cat)

        self.build_index()
        return len(self.chunks)

    def build_index(self) -> None:
        """Compile BM25 inverted index and NumPy L2-normalized TF-IDF matrix."""
        n_docs = len(self.chunks)
        if n_docs == 0:
            self._is_built = True
            return

        # 1. BM25 unigram statistics
        self._chunk_unigrams = []
        self._chunk_tf = []
        self._inverted_index = {}
        df_uni: Counter[str] = Counter()
        lengths = np.zeros(n_docs, dtype=np.float32)

        # Also collect unigram+bigram counts for TF-IDF cosine
        chunk_hybrid_tf: list[Counter[str]] = []
        df_hybrid: Counter[str] = Counter()

        for idx, chunk in enumerate(self.chunks):
            # Include section title & filename cues lightly to aid retrieval
            enriched = f"{chunk.section_title} {chunk.text}"
            unis = _tokenize(enriched, include_bigrams=False)
            tf_uni = Counter(unis)
            self._chunk_unigrams.append(unis)
            self._chunk_tf.append(tf_uni)
            lengths[idx] = float(len(unis)) if unis else 1.0

            for term, freq in tf_uni.items():
                df_uni[term] += 1
                self._inverted_index.setdefault(term, []).append((idx, freq))

            hyb_tokens = _tokenize(enriched, include_bigrams=True)
            tf_hyb = Counter(hyb_tokens)
            chunk_hybrid_tf.append(tf_hyb)
            for term in tf_hyb:
                df_hybrid[term] += 1

        self._doc_lengths = lengths
        self._avg_dl = float(np.mean(lengths)) if n_docs > 0 else 1.0

        # Standard Okapi BM25 IDF (always non-negative via +1.0 smoothing)
        self._bm25_idf = {
            term: math.log(((n_docs - df + 0.5) / (df + 0.5)) + 1.0)
            for term, df in df_uni.items()
        }

        # 2. Build bounded vocabulary for dense NumPy TF-IDF matrix
        # Prioritize terms with higher document frequency / signal up to max_vocab_size
        ranked_terms = sorted(
            df_hybrid.items(),
            key=lambda kv: (-kv[1], kv[0]),
        )[: self.max_vocab_size]
        self._vocab = {term: col_idx for col_idx, (term, _df) in enumerate(ranked_terms)}
        vocab_size = len(self._vocab)

        if vocab_size == 0:
            self._tfidf_matrix = np.zeros((n_docs, 0), dtype=np.float32)
            self._tfidf_idf = np.zeros(0, dtype=np.float32)
            self._is_built = True
            return

        idf_vec = np.zeros(vocab_size, dtype=np.float32)
        for term, col_idx in self._vocab.items():
            df = df_hybrid[term]
            idf_vec[col_idx] = math.log((n_docs + 1.0) / (df + 1.0)) + 1.0
        self._tfidf_idf = idf_vec

        mat = np.zeros((n_docs, vocab_size), dtype=np.float32)
        for row_idx, tf_map in enumerate(chunk_hybrid_tf):
            for term, freq in tf_map.items():
                col_idx = self._vocab.get(term)
                if col_idx is not None:
                    # Sublinear TF scaling * IDF
                    mat[row_idx, col_idx] = (1.0 + math.log(freq)) * idf_vec[col_idx]

        # L2 normalize rows safely
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms == 0.0] = 1.0
        self._tfidf_matrix = mat / norms
        self._is_built = True

    def _matches_filter(
        self,
        chunk: DocumentChunk,
        entity_name: Optional[str],
        filename: Optional[str],
        category: Optional[str],
    ) -> bool:
        if entity_name and entity_name.strip().lower() not in chunk.entity_name.lower():
            return False
        if filename and filename.strip().lower() not in chunk.filename.lower():
            return False
        if category and category.strip().lower() != chunk.category.lower():
            return False
        return True

    def search(
        self,
        query: str,
        top_k: int = 8,
        entity_name: Optional[str] = None,
        filename: Optional[str] = None,
        category: Optional[str] = None,
        alpha: float = 0.55,
    ) -> list[SearchResult]:
        """
        Execute Hybrid BM25 + TF-IDF Cosine search with optional metadata filters.

        Args:
            query:       Natural language query or financial keyword expression.
            top_k:       Maximum results to return.
            entity_name: Optional entity name filter (case-insensitive substring).
            filename:    Optional filename filter (case-insensitive substring).
            category:    Optional VDR category filter (exact case-insensitive).
            alpha:       Blend weight for BM25 vs TF-IDF Cosine (default 0.55 BM25 / 0.45 Cosine).
        """
        if not self._is_built:
            self.build_index()
        if not self.chunks or not query or not query.strip():
            return []

        n_docs = len(self.chunks)
        candidate_indices = [
            i for i, ch in enumerate(self.chunks)
            if self._matches_filter(ch, entity_name, filename, category)
        ]
        if not candidate_indices:
            return []

        q_unigrams = _tokenize(query, include_bigrams=False)
        q_hybrid = _tokenize(query, include_bigrams=True)
        if not q_unigrams:
            return []

        candidate_set = set(candidate_indices)
        bm25_scores = np.zeros(n_docs, dtype=np.float32)
        matched_map: dict[int, set[str]] = {}

        # 1. BM25 scoring
        for term in set(q_unigrams):
            idf = self._bm25_idf.get(term)
            if idf is None:
                continue
            for doc_idx, freq in self._inverted_index.get(term, ()):
                if doc_idx not in candidate_set:
                    continue
                dl = self._doc_lengths[doc_idx]
                denom = freq + self.k1 * (1.0 - self.b + self.b * (dl / self._avg_dl))
                bm25_scores[doc_idx] += idf * ((freq * (self.k1 + 1.0)) / max(denom, 1e-6))
                matched_map.setdefault(doc_idx, set()).add(term)

        # 2. TF-IDF Cosine scoring
        cosine_scores = np.zeros(n_docs, dtype=np.float32)
        if self._tfidf_matrix.shape[1] > 0:
            q_tf = Counter(q_hybrid)
            q_vec = np.zeros(self._tfidf_matrix.shape[1], dtype=np.float32)
            for term, freq in q_tf.items():
                col_idx = self._vocab.get(term)
                if col_idx is not None:
                    q_vec[col_idx] = (1.0 + math.log(freq)) * self._tfidf_idf[col_idx]
            q_norm = float(np.linalg.norm(q_vec))
            if q_norm > 0.0:
                q_vec /= q_norm
                cand_arr = np.array(candidate_indices, dtype=np.int64)
                cos_vals = self._tfidf_matrix[cand_arr] @ q_vec
                cosine_scores[cand_arr] = np.clip(cos_vals, 0.0, 1.0)

        # 3. Min-max normalize over candidate pool and fuse
        cand_arr = np.array(candidate_indices, dtype=np.int64)
        bm25_max = float(np.max(bm25_scores[cand_arr])) if len(cand_arr) else 0.0
        cos_max = float(np.max(cosine_scores[cand_arr])) if len(cand_arr) else 0.0

        query_lower = query.strip().lower()
        scored: list[tuple[float, int, float, float]] = []
        for idx in candidate_indices:
            b_raw = float(bm25_scores[idx])
            c_raw = float(cosine_scores[idx])
            if b_raw <= 0.0 and c_raw <= 0.0:
                continue
            b_norm = (b_raw / bm25_max) if bm25_max > 0.0 else 0.0
            c_norm = (c_raw / cos_max) if cos_max > 0.0 else 0.0
            fused = alpha * b_norm + (1.0 - alpha) * c_norm
            # Exact phrase bonus
            if len(query_lower) >= 6 and query_lower in self.chunks[idx].text.lower():
                fused += 0.15
            scored.append((fused, idx, b_raw, c_raw))

        # Deterministic tie-breaking by chunk_id
        scored.sort(key=lambda item: (-item[0], self.chunks[item[1]].chunk_id))

        results: list[SearchResult] = []
        for fused, idx, b_raw, c_raw in scored[:top_k]:
            ch = self.chunks[idx]
            citation = f"{ch.filename} (p.{ch.page_num}, {ch.section_title})"
            results.append(
                SearchResult(
                    chunk=ch,
                    score=round(fused, 4),
                    bm25_score=round(b_raw, 4),
                    cosine_score=round(c_raw, 4),
                    matched_terms=sorted(matched_map.get(idx, set())),
                    citation=citation,
                )
            )
        return results

    @staticmethod
    def format_context(results: Sequence[SearchResult], max_chars: int = 12_000) -> str:
        """Assemble top RAG hits into a provenance-tagged LLM context string."""
        parts: list[str] = []
        used = 0
        for r in results:
            block = (
                f"[Source: {r.chunk.filename} | Entity: {r.chunk.entity_name} | "
                f"p.{r.chunk.page_num} | Section: {r.chunk.section_title} | Score: {r.score:.2f}]\n"
                f"{r.chunk.text}"
            )
            if used + len(block) + 2 > max_chars and parts:
                break
            parts.append(block)
            used += len(block) + 2
        return "\n\n".join(parts)[:max_chars]


# =====================================================================
# 2. Cross-Document Contradiction Detector
# =====================================================================

# Financial metric definitions: (metric_name, unit, regex_patterns)
_METRIC_SPECS: list[tuple[str, str, list[str]]] = [
    ("Revenue", "USD_M", [
        r"(?:total\s+|annual\s+|net\s+|fy\d{2,4}\s+)?(?:revenue|net\s+sales|top[- ]line\s+revenue)",
    ]),
    ("Adjusted EBITDA", "USD_M", [
        r"(?:adjusted\s+|adj\.?\s+)?ebitda(?:\s+of)?",
    ]),
    ("ARR", "USD_M", [
        r"(?:ending\s+|total\s+)?(?:annual\s+recurring\s+revenue(?:\s*\(arr\))?|\barr\b)",
    ]),
    ("Operating Income", "USD_M", [
        r"(?:operating\s+income|operating\s+profit|\bebit\b)",
    ]),
    ("Net Income", "USD_M", [
        r"(?:net\s+income|net\s+profit|net\s+earnings)",
    ]),
    ("Free Cash Flow", "USD_M", [
        r"(?:free\s+cash\s+flow|\bfcf\b)",
    ]),
    ("Total Debt", "USD_M", [
        r"(?:total\s+debt|gross\s+debt|long[- ]term\s+debt)",
    ]),
    ("Cash & Equivalents", "USD_M", [
        r"(?:cash\s+and\s+(?:cash\s+)?equivalents|unrestricted\s+cash|cash\s+balance)",
    ]),
    ("Gross Margin", "PCT", [
        r"(?:gross\s+margin|gross\s+profit\s+margin)",
    ]),
    ("EBITDA Margin", "PCT", [
        r"(?:(?:adjusted\s+|adj\.?\s+)?ebitda\s+margin)",
    ]),
    ("Net Revenue Retention", "PCT", [
        r"(?:net\s+(?:dollar|revenue)\s+retention|\bnrr\b|\bndr\b)",
    ]),
    ("Headcount", "COUNT", [
        r"(?:headcount|total\s+employees|full[- ]time\s+employees|\bftes?\b)",
    ]),
]

_MONEY_RE = re.compile(
    r"(?P<curr>\$|€|£|USD|EUR|GBP)\s*(?P<num>\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(?P<scale>billion|bn|million|mn|thousand|b|m|k)?\b",
    re.IGNORECASE,
)
_PCT_RE = re.compile(r"(?P<num>\d{1,3}(?:\.\d+)?)\s*%")
_COUNT_RE = re.compile(r"(?P<num>\d{1,3}(?:,\d{3})+|\d{2,6})\s*(?:employees|ftes?|staff|people|workers)?\b", re.IGNORECASE)
_PERIOD_RE = re.compile(
    r"\b(FY\s*20\d{2}|FY\s*\d{2}|Q[1-4]\s*20\d{2}|Q[1-4]\s*FY\d{2}|20[12]\d|LTM|TTM)\b",
    re.IGNORECASE,
)


class _MetricMention(BaseModel):
    entity_name: str
    metric_name: str
    unit: str
    period_hint: str
    value: float
    display: str
    source_document: str
    location: str
    excerpt: str


def _normalize_period(raw: str) -> str:
    if not raw:
        return "Unspecified"
    cleaned = re.sub(r"\s+", "", raw.upper())
    # Normalize "FY24" -> "FY2024", "2024" -> "FY2024"
    m_fy2 = re.fullmatch(r"FY(\d{2})", cleaned)
    if m_fy2:
        return f"FY20{m_fy2.group(1)}"
    m_yr = re.fullmatch(r"(20\d{2})", cleaned)
    if m_yr:
        return f"FY{m_yr.group(1)}"
    return cleaned


def _parse_money_to_millions(num_str: str, scale: str) -> Optional[float]:
    try:
        val = float(num_str.replace(",", ""))
    except ValueError:
        return None
    sc = (scale or "").lower()
    if sc in ("billion", "bn", "b"):
        return val * 1000.0
    if sc in ("million", "mn", "m"):
        return val
    if sc in ("thousand", "k"):
        return val / 1000.0
    # Unscaled raw dollars (e.g. $450,000,000) -> convert to millions
    if val >= 1_000_000:
        return val / 1_000_000.0
    if val >= 1_000:
        return val / 1_000.0
    return val


def _extract_mentions_from_text(
    text: str,
    entity_name: str,
    source_document: str,
    location: str,
) -> list[_MetricMention]:
    """Scan a page or claim string for labeled financial metrics."""
    mentions: list[_MetricMention] = []
    # Split into sentence/line windows so metric label and figure stay closely bound
    segments = re.split(r"(?:\n+|(?<=[.!?])\s+)", text)

    for seg in segments:
        seg_clean = seg.strip()
        if len(seg_clean) < 6:
            continue
        seg_low = seg_clean.lower()

        for metric_name, unit, patterns in _METRIC_SPECS:
            # Skip "EBITDA" match if this segment is specifically about "EBITDA Margin"
            if metric_name == "Adjusted EBITDA" and "ebitda margin" in seg_low:
                continue
            if metric_name == "Revenue" and any(k in seg_low for k in ("revenue synergy", "revenue synergies", "net revenue retention", "recurring revenue")):
                continue

            matched_pos = None
            for pat in patterns:
                m_lbl = re.search(pat, seg_clean, flags=re.IGNORECASE)
                if m_lbl:
                    matched_pos = m_lbl
                    break
            if matched_pos is None:
                continue

            # Look in a tight window around the label (preferring right after the label)
            after = seg_clean[matched_pos.end(): matched_pos.end() + 90]
            around = seg_clean[max(0, matched_pos.start() - 40): matched_pos.end() + 90]

            val: Optional[float] = None
            disp = ""

            if unit == "USD_M":
                m_val = _MONEY_RE.search(after) or _MONEY_RE.search(around)
                if m_val:
                    # Must have either scale or currency symbol with a meaningful number
                    val = _parse_money_to_millions(m_val.group("num"), m_val.group("scale") or "")
                    disp = m_val.group(0).strip()
            elif unit == "PCT":
                m_val = _PCT_RE.search(after) or _PCT_RE.search(around)
                if m_val:
                    v = float(m_val.group("num"))
                    if 0.0 < v <= 300.0:
                        val = v
                        disp = m_val.group(0).strip()
            elif unit == "COUNT":
                m_val = _COUNT_RE.search(after) or _COUNT_RE.search(around)
                if m_val:
                    v = float(m_val.group("num").replace(",", ""))
                    if 10 <= v <= 5_000_000 and not (1990 <= v <= 2035):
                        val = v
                        disp = m_val.group(0).strip()

            if val is None or val <= 0:
                continue

            p_match = _PERIOD_RE.search(around)
            period = _normalize_period(p_match.group(1)) if p_match else "Unspecified"

            mentions.append(
                _MetricMention(
                    entity_name=entity_name,
                    metric_name=metric_name,
                    unit=unit,
                    period_hint=period,
                    value=round(val, 4),
                    display=disp,
                    source_document=source_document,
                    location=location,
                    excerpt=seg_clean[:200],
                )
            )
    return mentions


def _periods_compatible(p1: str, p2: str) -> bool:
    if p1 == p2:
        return True
    if p1 == "Unspecified" or p2 == "Unspecified":
        return True
    return False


def _format_normalized(val: float, unit: str) -> str:
    if unit == "USD_M":
        if val >= 1000:
            return f"${val / 1000.0:.2f}B (${val:,.1f}M)"
        return f"${val:,.1f}M"
    if unit == "PCT":
        return f"{val:.1f}%"
    return f"{val:,.0f}"


def detect_cross_document_contradictions(
    entities: Union[EntityDocuments, Sequence[EntityDocuments]],
    report: Optional[DueDiligenceReport] = None,
    tolerance_pct: float = 5.0,
) -> list[ContradictionItem]:
    """
    Scan across all uploaded documents for each entity (and optionally against a
    generated `DueDiligenceReport`) to flag diverging financial figures (e.g.,
    different Revenue, EBITDA, ARR, or Gross Margin across CIM vs. 10-K vs. Transcript)
    with exact document & page citations.
    """
    ent_list = [entities] if isinstance(entities, EntityDocuments) else list(entities)
    contradictions: list[ContradictionItem] = []
    seen_keys: set[tuple[str, str, str, str]] = set()
    counter = 1

    # 1. Cross-document scan per entity
    for ent in ent_list:
        mentions: list[_MetricMention] = []
        for doc in ent.documents:
            if doc.pages:
                for p_idx in range(len(doc.pages)):
                    body = doc.page_with_tables(p_idx)
                    if not body.strip():
                        continue
                    mentions.extend(
                        _extract_mentions_from_text(
                            body,
                            entity_name=ent.entity_name,
                            source_document=doc.filename,
                            location=f"p.{p_idx + 1}",
                        )
                    )
            elif doc.full_text.strip():
                mentions.extend(
                    _extract_mentions_from_text(
                        doc.full_text,
                        entity_name=ent.entity_name,
                        source_document=doc.filename,
                        location="p.1",
                    )
                )

        # Group by metric_name and compare across DISTINCT documents
        by_metric: dict[str, list[_MetricMention]] = {}
        for m in mentions:
            by_metric.setdefault(m.metric_name, []).append(m)

        for metric_name, m_list in by_metric.items():
            # Pick representative mention per (source_document, period_hint) to avoid noisy duplicates
            doc_period_best: dict[tuple[str, str], _MetricMention] = {}
            for m in m_list:
                key = (m.source_document, m.period_hint)
                if key not in doc_period_best:
                    doc_period_best[key] = m

            reps = list(doc_period_best.values())
            for i in range(len(reps)):
                for j in range(i + 1, len(reps)):
                    a, b = reps[i], reps[j]
                    if a.source_document == b.source_document:
                        continue
                    if not _periods_compatible(a.period_hint, b.period_hint):
                        continue

                    hi = max(abs(a.value), abs(b.value), 1e-9)
                    lo = min(abs(a.value), abs(b.value))
                    var_pct = round(((hi - lo) / hi) * 100.0, 1)

                    # Percentage metrics use absolute point check or relative check
                    if a.unit == "PCT":
                        if abs(a.value - b.value) < max(2.0, tolerance_pct * 0.4):
                            continue
                    else:
                        if var_pct < tolerance_pct:
                            continue

                    # Guard against extreme 50x+ scale confusion unless clearly same metric (e.g., quarterly vs annual if unspecified)
                    pair_key = (
                        ent.entity_name,
                        metric_name,
                        min(a.source_document, b.source_document),
                        max(a.source_document, b.source_document),
                    )
                    if pair_key in seen_keys:
                        continue
                    seen_keys.add(pair_key)

                    if var_pct >= 25.0:
                        sev = Severity.CRITICAL
                    elif var_pct >= 12.0:
                        sev = Severity.HIGH
                    else:
                        sev = Severity.MEDIUM

                    period_label = a.period_hint if a.period_hint != "Unspecified" else b.period_hint
                    contradictions.append(
                        ContradictionItem(
                            contradiction_id=f"CONTRA-{counter:03d}",
                            entity_name=ent.entity_name,
                            metric_name=metric_name,
                            period_hint=period_label,
                            severity=sev,
                            source_a_document=a.source_document,
                            source_a_location=a.location,
                            source_a_value=a.value,
                            source_a_display=a.display,
                            source_a_excerpt=a.excerpt,
                            source_b_document=b.source_document,
                            source_b_location=b.location,
                            source_b_value=b.value,
                            source_b_display=b.display,
                            source_b_excerpt=b.excerpt,
                            unit=a.unit,
                            variance_pct=var_pct,
                            explanation=(
                                f"Diverging {metric_name} ({period_label}) for {ent.entity_name}: "
                                f"{a.source_document} ({a.location}) reports {_format_normalized(a.value, a.unit)} "
                                f"vs. {b.source_document} ({b.location}) reporting {_format_normalized(b.value, b.unit)} "
                                f"({var_pct:.1f}% variance). Verify definitions (GAAP vs. Adjusted / Non-GAAP) in VDR."
                            ),
                        )
                    )
                    counter += 1

    # 2. Report-vs-DCF & Report-vs-Workstreams consistency contradictions
    if report is not None:
        dcf = compute_synergy_dcf(report)
        deal_label = f"{report.deal.entity_a_name} × {report.deal.entity_b_name}"

        if report.deal.target_synergy_npv_usd_b is not None and dcf.total_synergy_npv_b != 0:
            memo_npv = report.deal.target_synergy_npv_usd_b
            dcf_npv = dcf.total_synergy_npv_b
            hi = max(abs(memo_npv), abs(dcf_npv), 1e-9)
            var_pct = round((abs(memo_npv - dcf_npv) / hi) * 100.0, 1)
            if var_pct >= tolerance_pct:
                contradictions.append(
                    ContradictionItem(
                        contradiction_id=f"CONTRA-{counter:03d}",
                        entity_name=deal_label,
                        metric_name="Target Synergy NPV",
                        period_hint=f"{report.deal.time_horizon_months or 36}M Horizon",
                        severity=Severity.CRITICAL if var_pct >= 20.0 else Severity.HIGH,
                        source_a_document="IC Memo (DealMeta)",
                        source_a_location="deal.target_synergy_npv_usd_b",
                        source_a_value=round(memo_npv * 1000.0, 1),
                        source_a_display=f"${memo_npv:.2f}B",
                        source_a_excerpt=f"Headline Synergy NPV stated as ${memo_npv:.2f}B.",
                        source_b_document="Deterministic DCF Engine",
                        source_b_location="core.financial_model.compute_synergy_dcf",
                        source_b_value=round(dcf_npv * 1000.0, 1),
                        source_b_display=f"${dcf_npv:.2f}B",
                        source_b_excerpt=f"Workstream-level DCF computes ${dcf_npv:.2f}B at {report.deal.wacc_pct:.1f}% WACC.",
                        unit="USD_M",
                        variance_pct=var_pct,
                        explanation=(
                            f"Headline Memo Synergy NPV (${memo_npv:.2f}B) diverges by {var_pct:.1f}% from "
                            f"deterministic workstream DCF (${dcf_npv:.2f}B)."
                        ),
                    )
                )
                counter += 1

        # Compare headline revenue/cost synergies against sum of itemized workstreams
        if report.synergy_workstreams:
            ws_rev_b = sum(
                w.annual_run_rate_usd_m for w in report.synergy_workstreams
                if w.synergy_type == SynergyType.REVENUE
            ) / 1000.0
            ws_cost_b = sum(
                w.annual_run_rate_usd_m for w in report.synergy_workstreams
                if w.synergy_type == SynergyType.COST
            ) / 1000.0

            for label, headline_val, ws_val, field_loc in (
                ("Run-Rate Revenue Synergy", report.deal.estimated_revenue_synergy_usd_b, ws_rev_b, "deal.estimated_revenue_synergy_usd_b"),
                ("Run-Rate Cost Synergy", report.deal.estimated_cost_synergy_usd_b, ws_cost_b, "deal.estimated_cost_synergy_usd_b"),
            ):
                if headline_val is not None and ws_val > 0:
                    hi = max(abs(headline_val), abs(ws_val), 1e-9)
                    var_pct = round((abs(headline_val - ws_val) / hi) * 100.0, 1)
                    if var_pct >= tolerance_pct:
                        contradictions.append(
                            ContradictionItem(
                                contradiction_id=f"CONTRA-{counter:03d}",
                                entity_name=deal_label,
                                metric_name=label,
                                period_hint="Steady-State Run-Rate",
                                severity=Severity.HIGH if var_pct >= 15.0 else Severity.MEDIUM,
                                source_a_document="IC Memo (DealMeta)",
                                source_a_location=field_loc,
                                source_a_value=round(headline_val * 1000.0, 1),
                                source_a_display=f"${headline_val:.2f}B",
                                source_a_excerpt=f"Headline {label}: ${headline_val:.2f}B/yr.",
                                source_b_document="Synergy Workstreams Schedule",
                                source_b_location="report.synergy_workstreams",
                                source_b_value=round(ws_val * 1000.0, 1),
                                source_b_display=f"${ws_val:.2f}B",
                                source_b_excerpt=f"Bottom-up sum of workstreams: ${ws_val:.2f}B/yr.",
                                unit="USD_M",
                                variance_pct=var_pct,
                                explanation=(
                                    f"Headline {label} (${headline_val:.2f}B) differs by {var_pct:.1f}% "
                                    f"from the sum of bottom-up workstreams (${ws_val:.2f}B)."
                                ),
                            )
                        )
                        counter += 1

    # Sort Critical -> High -> Medium -> Low, then highest variance first
    sev_order = {Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MEDIUM: 2, Severity.LOW: 3}
    contradictions.sort(key=lambda c: (sev_order.get(c.severity, 4), -c.variance_pct))
    return contradictions
