"""retrieve node — permission-filtered vector search, the RBAC ballgame.

This is where "filter at retrieval, not after" happens. We build a
:class:`RetrievalFilter` from the caller's signed :class:`Identity` (tenant, env,
roles) — never from the question — and push it into every ``store.search`` call,
so restricted chunks are excluded before the model ever sees them.

We embed each fan-out sub-query, search with the filter, and merge the hits,
deduping by ``chunk_id`` and keeping each chunk's best score. The merged,
scope-and-role-scoped candidate set flows on to rerank.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...contracts import Embedder, Retrieved, VectorStore
from ...security.access import build_filter
from ..state import QueryState

if TYPE_CHECKING:
    from ...config import Settings


def run(state: QueryState, settings: Settings, *, embedder: Embedder, store: VectorStore) -> dict:
    identity = state["identity"]
    # The filter is derived only from the signed identity. Fail-closed logic
    # (no roles => no results) lives in build_filter / the store.
    flt = build_filter(identity)

    # Search on the fan-out sub-queries; fall back to the raw question if the
    # fan-out produced nothing.
    queries = state.get("sub_queries") or [state.get("question", "")]
    queries = [q for q in queries if q and q.strip()]

    best: dict[str, Retrieved] = {}
    for q in queries:
        vec = embedder.embed_query(q)
        for hit in store.search(vec, flt, k=settings.retrieve_k):
            cid = hit.chunk.chunk_id
            existing = best.get(cid)
            # Keep the strongest score seen for a chunk across sub-queries.
            if existing is None or hit.score > existing.score:
                best[cid] = hit

    candidates = sorted(best.values(), key=lambda r: r.score, reverse=True)

    trace = list(state.get("trace", []))
    trace.append(
        {
            "node": "retrieve",
            "scope": identity.scope.namespace(),
            "n_queries": len(queries),
            "n_candidates": len(candidates),
            "chunk_ids": [r.chunk.chunk_id for r in candidates],
        }
    )
    return {"candidates": candidates, "trace": trace}
