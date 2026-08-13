"""rerank node — re-score the candidates and keep only what fits the budget.

Vector search optimizes for "roughly similar"; a reranker (a cross-encoder or a
managed service like Bedrock Rerank) reads the query and each passage *together*
and scores true relevance, then we keep the top ``rerank_k``. That top set is
the context budget the grounding step gets — smaller, sharper, and cheaper for
the LLM to reason over. In local mode the reranker is a no-op passthrough, so
the graph still runs; the node's job is the same either way.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...contracts import Reranker
from ..state import QueryState

if TYPE_CHECKING:
    from ...config import Settings


def run(state: QueryState, settings: Settings, *, reranker: Reranker) -> dict:
    question = state.get("question", "")
    candidates = state.get("candidates", [])

    reranked = reranker.rerank(question, candidates, k=settings.rerank_k)

    trace = list(state.get("trace", []))
    trace.append(
        {
            "node": "rerank",
            "n_in": len(candidates),
            "n_out": len(reranked),
            "chunk_ids": [r.chunk.chunk_id for r in reranked],
        }
    )
    return {"reranked": reranked, "trace": trace}
