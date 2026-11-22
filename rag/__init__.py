"""
Hybrid BM25 + TF-IDF Cosine RAG Engine & Cross-Document Contradiction Detector.
"""
from rag.engine import (
    ContradictionItem,
    DocumentChunk,
    HybridRAGIndex,
    SearchResult,
    detect_cross_document_contradictions,
)

__all__ = [
    "DocumentChunk",
    "SearchResult",
    "HybridRAGIndex",
    "ContradictionItem",
    "detect_cross_document_contradictions",
]
