"""NaiveChunker — the fixed-size splitter that ships in the spine.

Chunking decides what a "passage" is, and that choice drives retrieval quality.
The spine uses the simplest thing that works: slide a fixed-size window over the
document text with a small overlap so a fact spanning a boundary still lands
whole in at least one chunk. Every chunk inherits the document's isolation and
ACL metadata (scope, doc_type, source_id, acl_roles, updated_at) so the
retrieval filter and citations keep working unchanged.

Structure-aware chunking (splitting on headings, tables, ACORD form sections,
sentence boundaries) is its own deep-dive post; this is the honest baseline it
is measured against.
"""

from __future__ import annotations

from ...contracts import Chunk, Document

_DEFAULT_SIZE = 800
_DEFAULT_OVERLAP = 100


class NaiveChunker:
    """Fixed-size character window with overlap. Implements the Chunker Protocol."""

    def __init__(self, chunk_size: int = _DEFAULT_SIZE, overlap: int = _DEFAULT_OVERLAP) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if not 0 <= overlap < chunk_size:
            raise ValueError("overlap must be in [0, chunk_size)")
        self.chunk_size = chunk_size
        self.overlap = overlap

    def split(self, doc: Document) -> list[Chunk]:
        text = doc.text or ""
        if not text.strip():
            return []

        step = self.chunk_size - self.overlap
        chunks: list[Chunk] = []
        start = 0
        idx = 0
        while start < len(text):
            piece = text[start : start + self.chunk_size]
            chunks.append(
                Chunk(
                    chunk_id=f"{doc.doc_id}#{idx}",
                    doc_id=doc.doc_id,
                    # Isolation + ACL metadata copied verbatim so the retrieval
                    # filter and citations keep working on the chunk.
                    scope=doc.scope,
                    doc_type=doc.doc_type,
                    source_id=doc.source_id,
                    text=piece,
                    acl_roles=doc.acl_roles,
                    updated_at=doc.updated_at,
                    metadata=dict(doc.metadata),
                )
            )
            idx += 1
            start += step
        return chunks
