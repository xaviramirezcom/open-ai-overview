"""The query pipeline as an explicit LangGraph graph.

Each pipeline *phase* is a node — which is exactly how the blog series is
organized (one post per node). The graph makes the flow inspectable and lets
a later post swap a node (e.g. add reranking, add a guardrail branch) without
touching the others.

    fan_out -> retrieve -> rerank -> ground -> guardrails -> audit

`build_query_graph()` wires the nodes with their injected components so the
same graph runs against Bedrock+OpenSearch or the local stack.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langgraph.graph import END, START, StateGraph

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

from ..config import (
    Settings,
    build_embedder,
    build_llm,
    build_reranker,
    build_vector_store,
    get_settings,
)
from ..contracts import LLM, Embedder, Reranker, VectorStore
from .nodes import audit, fan_out, ground, guardrails, rerank, retrieve
from .state import QueryState


def build_query_graph(
    *,
    settings: Settings | None = None,
    embedder: Embedder | None = None,
    store: VectorStore | None = None,
    reranker: Reranker | None = None,
    llm: LLM | None = None,
) -> CompiledStateGraph:
    """Compile the query graph. Components default from config but can be
    injected (tests, notebooks, alternate providers)."""
    s = settings or get_settings()
    embedder = embedder or build_embedder(s)
    store = store or build_vector_store(s, embedder=embedder)
    reranker = reranker or build_reranker(s)
    llm = llm or build_llm(s)

    g = StateGraph(QueryState)

    g.add_node("fan_out", lambda st: fan_out.run(st, s))
    g.add_node("retrieve", lambda st: retrieve.run(st, s, embedder=embedder, store=store))
    g.add_node("rerank", lambda st: rerank.run(st, s, reranker=reranker))
    g.add_node("ground", lambda st: ground.run(st, s, llm=llm))
    g.add_node("guardrails", lambda st: guardrails.run(st, s))
    g.add_node("audit", lambda st: audit.run(st, s))

    g.add_edge(START, "fan_out")
    g.add_edge("fan_out", "retrieve")
    g.add_edge("retrieve", "rerank")
    g.add_edge("rerank", "ground")
    g.add_edge("ground", "guardrails")
    g.add_edge("guardrails", "audit")
    g.add_edge("audit", END)

    return g.compile()
