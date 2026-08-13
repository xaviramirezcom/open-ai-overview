"""In-memory vector store — the zero-cloud `PROVIDER=local` retrieval backend.

Keeps chunks in a plain dict keyed by `(namespace, chunk_id)` and searches them
with a numpy cosine similarity. Crucially it enforces access control the *same
way* the OpenSearch store does — by calling `security.access.is_visible` (scope +
role) plus the optional doc-type narrowing **before scoring**, so restricted
chunks never reach the model. This is what makes the whole pipeline runnable
after `git clone` with no AWS and no OpenSearch.

`numpy` is an optional extra (`own-overview[local]`) and is imported lazily
inside the methods, so importing this module never forces it.
"""

from __future__ import annotations

import pickle
from collections.abc import Sequence
from pathlib import Path

from ..config import Settings
from ..contracts import Chunk, Embedder, RetrievalFilter, Retrieved, TenantScope
from ..security.access import is_visible


class LocalVectorStore:
    """VectorStore backed by an in-process dict. Implements `contracts.VectorStore`.

    An `Embedder` is injected so `upsert` can embed any chunk that arrives
    without a vector (the OpenSearch store, by contrast, expects embeddings to
    be set upstream).
    """

    def __init__(self, settings: Settings, *, embedder: Embedder) -> None:
        self.settings = settings
        self.embedder = embedder
        # (namespace, chunk_id) -> Chunk. Namespace = tenant__env keeps tenants
        # and envs partitioned exactly like the OpenSearch per-scope index.
        self._chunks: dict[tuple[str, str], Chunk] = {}
        # `seed` and `query` run as separate processes, so the store persists to
        # disk (pickle). A real deployment uses OpenSearch; this keeps the
        # zero-cloud demo working end-to-end across CLI invocations.
        self._path = Path(getattr(settings, "local_store_path", ".own_overview/local_store.pkl"))
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._chunks = pickle.loads(self._path.read_bytes())
            except Exception:
                self._chunks = {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_bytes(pickle.dumps(self._chunks))

    # -- VectorStore Protocol ---------------------------------------------

    def upsert(self, chunks: Sequence[Chunk]) -> None:
        to_embed = [c for c in chunks if c.embedding is None]
        if to_embed:
            vectors = self.embedder.embed_documents([c.text for c in to_embed])
            for chunk, vector in zip(to_embed, vectors, strict=True):
                chunk.embedding = vector
        for c in chunks:
            self._chunks[(c.scope.namespace(), c.chunk_id)] = c
        self._save()

    def search(self, query_vector: list[float], flt: RetrievalFilter, k: int) -> list[Retrieved]:
        import numpy as np

        q = np.asarray(query_vector, dtype=float)
        q_norm = float(np.linalg.norm(q))
        if q_norm == 0.0:
            return []

        results: list[Retrieved] = []
        for c in self._chunks.values():
            # Access control BEFORE scoring — same rule as the OpenSearch filter.
            if not is_visible(c.scope, c.acl_roles, flt):
                continue
            if flt.doc_types is not None and c.doc_type not in flt.doc_types:
                continue
            if c.embedding is None:
                continue
            v = np.asarray(c.embedding, dtype=float)
            v_norm = float(np.linalg.norm(v))
            if v_norm == 0.0:
                continue
            score = float(np.dot(q, v) / (q_norm * v_norm))
            results.append(Retrieved(chunk=c, score=score))

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:k]

    def delete_document(self, scope: TenantScope, doc_id: str) -> None:
        """Drop every chunk of a document in this scope — CDA DELETE tombstone."""
        ns = scope.namespace()
        stale = [
            key for key, chunk in self._chunks.items() if key[0] == ns and chunk.doc_id == doc_id
        ]
        for key in stale:
            del self._chunks[key]
        self._save()
