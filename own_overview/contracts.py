"""Core data types and component interfaces (Protocols) for own-overview.

Everything in the pipeline depends on this module and nothing else, so the
stages stay swappable: an embedder, a vector store, an LLM or a reranker is
anything that satisfies the Protocol here. Concrete adapters (Bedrock,
OpenSearch, local) live under their own packages and are wired up by
`config.build_*` factories.

Design note — multi-tenant, multi-env is first class. Every Document, Chunk
and query carries a `TenantScope` (tenant + environment). Retrieval filters on
it *in the query*, so one insurer never sees another's data and dev never
leaks into prod. This is the spine of the "RBAC at retrieval" post.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Identity / isolation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TenantScope:
    """The isolation key applied end to end.

    Guidewire CDA exposes data per **tenant** and per **environment**
    ("planets": dev / qa / prod) via separate event sources. We mirror that:
    tenant + env are partition keys for the index and mandatory retrieval
    filters. Never build a query without one.
    """

    tenant_id: str
    env: str  # e.g. "dev", "qa", "prod"

    def namespace(self) -> str:
        """Vector-store namespace / index suffix for this scope."""
        return f"{self.tenant_id}__{self.env}"


@dataclass(frozen=True, slots=True)
class Identity:
    """Resolved from the caller's signed token — never from the prompt.

    `roles` drives access control at retrieval: a chunk is only retrievable if
    its `acl_roles` intersect the caller's roles.
    """

    user_id: str
    scope: TenantScope
    roles: frozenset[str]


# ---------------------------------------------------------------------------
# Documents and chunks
# ---------------------------------------------------------------------------

# InsuranceSuite source systems, as they arrive over CDA.
DocType = str  # "policy" | "claim" | "billing" | "underwriting" | ...


@dataclass(slots=True)
class Document:
    """A logical record materialized from CDA change data (latest state).

    `source_id` traces back to the system of record (e.g. "claim/88431") so
    every answer can cite it. `acl_roles` and `scope` are copied onto each
    derived chunk and enforced at retrieval.
    """

    doc_id: str
    scope: TenantScope
    doc_type: DocType
    source_system: str  # "ClaimCenter" | "PolicyCenter" | "BillingCenter"
    source_id: str
    text: str
    acl_roles: frozenset[str]
    updated_at: datetime
    metadata: dict[str, str] = field(default_factory=dict)
    # CDA provenance — the batch this record's latest state came from.
    fingerprint: str | None = None
    seqval_hex: str | None = None


@dataclass(slots=True)
class Chunk:
    """A retrievable passage. Carries the isolation + ACL metadata so the
    filter can run in the query and citations can point back to the source."""

    chunk_id: str
    doc_id: str
    scope: TenantScope
    doc_type: DocType
    source_id: str
    text: str
    acl_roles: frozenset[str]
    updated_at: datetime
    embedding: list[float] | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class Retrieved:
    """A chunk returned by search, with its score."""

    chunk: Chunk
    score: float


@dataclass(slots=True)
class Citation:
    marker: str  # "1", "2", ...
    chunk_id: str
    source_id: str


@dataclass(slots=True)
class Answer:
    text: str
    citations: list[Citation]
    groundedness: float | None = None
    abstained: bool = False


# ---------------------------------------------------------------------------
# Retrieval filter (built from Identity, applied in the query)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RetrievalFilter:
    """The permission filter, derived from the caller's Identity and pushed
    down into the vector store query. This is the whole point of "filter at
    retrieval, not after": restricted data is excluded before the model sees
    it. See `security.access.build_filter`."""

    scope: TenantScope
    roles: frozenset[str]
    doc_types: frozenset[str] | None = None  # optional narrowing


# ---------------------------------------------------------------------------
# Component interfaces — implement these to swap a stage
# ---------------------------------------------------------------------------


@runtime_checkable
class Embedder(Protocol):
    """Turns text into vectors. Adapters: Bedrock Titan/Cohere, local BGE."""

    dim: int

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


@runtime_checkable
class VectorStore(Protocol):
    """Stores chunks and searches them with a mandatory RetrievalFilter.

    Adapters: OpenSearch (default), local in-memory. `delete_document` is not
    optional — CDA delivers DELETE tombstones and a redacted claim must leave
    the index (regulated-domain requirement)."""

    def upsert(self, chunks: Sequence[Chunk]) -> None: ...

    def search(
        self, query_vector: list[float], flt: RetrievalFilter, k: int
    ) -> list[Retrieved]: ...

    def delete_document(self, scope: TenantScope, doc_id: str) -> None: ...


@runtime_checkable
class Reranker(Protocol):
    """Re-scores candidates for relevance. Adapters: Bedrock/Cohere rerank,
    a no-op passthrough."""

    def rerank(self, query: str, candidates: Sequence[Retrieved], k: int) -> list[Retrieved]: ...


@runtime_checkable
class LLM(Protocol):
    """Generates a grounded answer. Adapters: Bedrock (Claude), others."""

    def complete(self, system: str, prompt: str) -> str: ...


@runtime_checkable
class Chunker(Protocol):
    """Splits a Document into retrievable Chunks. The naive splitter ships in
    the spine; structure-aware splitting is its own deep-dive post."""

    def split(self, doc: Document) -> list[Chunk]: ...
