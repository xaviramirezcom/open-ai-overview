"""End-to-end on the LOCAL stack: synthetic CDA data -> ingest -> query.

Exercises the whole write+read path with no cloud: the simulator writes true
CDA-layout Parquet + manifests, the ingestion orchestrator merges change rows
(honoring DELETE tombstones) and indexes them, and the LangGraph query graph
answers with access control enforced at retrieval.
"""

from __future__ import annotations

import pytest

pytest.importorskip("numpy", reason="local vector store needs numpy")
pytest.importorskip("pyarrow", reason="the CDA simulator writes Parquet")

from own_overview.config import (
    build_chunker,
    build_embedder,
    build_vector_store,
    get_settings,
)
from own_overview.ingestion.cda.simulator import generate
from own_overview.ingestion.cda.source import LocalCdaSource
from own_overview.ingestion.ingest import ingest_batch
from own_overview.pipeline.graph import build_query_graph
from own_overview.security.identity import dev_identity

QUESTION = "Why did the premium on POL-55012 go up?"


@pytest.fixture
def ingested():
    """Generate + ingest the whole synthetic corpus into a local store."""
    s = get_settings()
    batches = generate(s)
    assert batches, "simulator produced no batches"

    embedder = build_embedder(s)
    store = build_vector_store(s, embedder=embedder)
    chunker = build_chunker(s)
    source = LocalCdaSource(s.cda_local_root)

    totals = {"upserts": 0, "deletes": 0, "chunks": 0}
    for b in batches:
        res = ingest_batch(
            b.event, s, source=source, store=store, embedder=embedder, chunker=chunker
        )
        for k in totals:
            totals[k] += res.get(k, 0)
    return store, embedder, totals


def test_ingestion_indexes_and_evicts(ingested):
    _, _, totals = ingested
    assert totals["upserts"] > 0
    assert totals["chunks"] > 0
    # The corpus contains at least one DELETE row (a cancelled policy) — the
    # tombstone must be processed as a delete, not an upsert.
    assert totals["deletes"] >= 1


def _sources(state) -> set[str]:
    retrieved = state.get("reranked") or state.get("candidates") or []
    return {r.chunk.source_id for r in retrieved}


def _scopes(state):
    retrieved = state.get("reranked") or state.get("candidates") or []
    return {(r.chunk.scope.tenant_id, r.chunk.scope.env) for r in retrieved}


def test_query_adjuster_excludes_underwriting(ingested):
    store, embedder, _ = ingested
    graph = build_query_graph(store=store, embedder=embedder)

    state = graph.invoke(
        {"question": QUESTION, "identity": dev_identity("u", "acme", "prod", ["adjuster"])}
    )
    # A cited answer came back...
    assert state["answer"].text
    # ...but nothing from the underwriting-only doc reached the model.
    assert not any(sid.startswith("underwriting/") for sid in _sources(state))
    # ...and only the caller's tenant+env was in scope.
    assert _scopes(state) <= {("acme", "prod")}


def test_query_underwriter_includes_underwriting(ingested):
    store, embedder, _ = ingested
    graph = build_query_graph(store=store, embedder=embedder)

    state = graph.invoke(
        {"question": QUESTION, "identity": dev_identity("u", "acme", "prod", ["underwriter"])}
    )
    assert any(sid.startswith("underwriting/") for sid in _sources(state))
    assert _scopes(state) <= {("acme", "prod")}
    # The answer is grounded and cites its sources.
    ans = state["answer"]
    assert ans.citations
    assert ans.groundedness is not None
