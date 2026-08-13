"""End-to-end RBAC-at-retrieval test on the LOCAL stack (no AWS, no network).

This is the payoff of the whole design: the *same* corpus, queried by two
identities, yields different retrievable data — and cross-tenant / cross-env
data never leaks — because the permission filter is pushed into the vector
search, not applied after generation.

We build the real `LocalVectorStore` via `config.build_vector_store`, upsert a
few chunks that span two tenants / two envs / different roles, and assert:

  (a) an **adjuster** query does NOT surface an underwriter-only chunk,
  (b) an **underwriter** query DOES,
  (c) cross-tenant / cross-env chunks never appear for either.

It runs at two levels — directly against the store, and end-to-end through the
LangGraph query graph. Each level **skips gracefully** if the module it needs
(the local store, the graph nodes) hasn't landed yet: those are built in
parallel, and the assertions here are written against the documented contracts.

`numpy` is required for the in-memory store; we use a deterministic **stub
embedder** so the test never downloads a sentence-transformers model. If you'd
rather exercise the real local embedder, install the `local` extra — but the
access assertions hold regardless of embedding quality, since isolation is the
filter's job, not the vector's.
"""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy", reason="local vector store needs numpy")

from conftest import ACME_DEV, ACME_PROD, GLOBEX_PROD, make_chunk

from own_overview.config import build_vector_store, get_settings
from own_overview.security.access import build_filter
from own_overview.security.identity import dev_identity

QUESTION = "Why did the premium on POL-55012 go up?"

# The one chunk only an underwriter may retrieve.
UNDERWRITER_ONLY = "acme-prod-underwriter"
# Chunks that must never surface for an acme/prod caller.
OUT_OF_SCOPE = {"acme-dev-adjuster", "globex-prod-adjuster"}


# ---------------------------------------------------------------------------
# A deterministic, dependency-light embedder (stub) satisfying the Protocol.
# ---------------------------------------------------------------------------


class StubEmbedder:
    """Maps text -> a fixed unit vector deterministically. Good enough to
    exercise the store; semantics don't matter because access isolation is
    enforced by the filter, not by similarity."""

    def __init__(self, dim: int = 384):
        self.dim = dim

    def _vec(self, text: str) -> list[float]:
        rng = np.random.default_rng(abs(hash(text)) % (2**32))
        v = rng.standard_normal(self.dim)
        norm = float(np.linalg.norm(v))
        return (v / norm).tolist() if norm else v.tolist()

    def embed_documents(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


def _corpus():
    """The multi-tenant / multi-env / multi-role corpus (mirrors the fixture)."""
    return [
        make_chunk(
            "acme-prod-adjuster",
            ACME_PROD,
            {"adjuster", "underwriter"},
            "Claim CLM-100 on policy POL-55012: water damage, paid $4,200.",
            doc_type="claim",
        ),
        make_chunk(
            UNDERWRITER_ONLY,
            ACME_PROD,
            {"underwriter"},
            "Underwriting memo for POL-55012: premium raised 18% after prior-loss review.",
            doc_type="underwriting",
        ),
        make_chunk(
            "acme-dev-adjuster",
            ACME_DEV,
            {"adjuster", "underwriter"},
            "DEV fixture claim for POL-55012 — must never leak into prod answers.",
            doc_type="claim",
        ),
        make_chunk(
            "globex-prod-adjuster",
            GLOBEX_PROD,
            {"adjuster", "underwriter"},
            "Globex claim about POL-55012 — different tenant, must stay isolated.",
            doc_type="claim",
        ),
    ]


@pytest.fixture
def populated_store():
    """A LocalVectorStore built via config and loaded with the corpus.

    Skips if the local store adapter isn't importable yet (built in parallel).
    """
    s = get_settings()
    embedder = StubEmbedder(dim=s.local_embedding_dim)
    chunks = _corpus()
    # Pre-embed so the test is robust whether the store reads chunk.embedding or
    # re-embeds text with its injected embedder (either way, same vectors).
    for c in chunks:
        c.embedding = embedder.embed_query(c.text)

    try:
        store = build_vector_store(s, embedder=embedder)
    except ModuleNotFoundError as exc:  # local_store.py not landed yet
        pytest.skip(f"local vector store adapter not available yet: {exc}")

    store.upsert(chunks)
    return store, embedder


def _ids(retrieved) -> set[str]:
    return {r.chunk.chunk_id for r in retrieved}


def _scopes(retrieved):
    return {r.chunk.scope for r in retrieved}


# ---------------------------------------------------------------------------
# Level 1 — directly against the store (needs only local_store.py).
# ---------------------------------------------------------------------------


def test_store_rbac_adjuster_cannot_see_underwriter_chunk(populated_store):
    store, embedder = populated_store
    qv = embedder.embed_query(QUESTION)

    adjuster = dev_identity("u-adj", "acme", "prod", ["adjuster"])
    hits = store.search(qv, build_filter(adjuster), k=10)
    ids = _ids(hits)

    # (a) the underwriter-only chunk is excluded before the model ever sees it.
    assert UNDERWRITER_ONLY not in ids
    # (c) nothing from another tenant/env leaks.
    assert ids.isdisjoint(OUT_OF_SCOPE)
    assert _scopes(hits) <= {ACME_PROD}
    # the adjuster still gets their legitimately-visible claim.
    assert "acme-prod-adjuster" in ids


def test_store_rbac_underwriter_sees_underwriter_chunk(populated_store):
    store, embedder = populated_store
    qv = embedder.embed_query(QUESTION)

    underwriter = dev_identity("u-uw", "acme", "prod", ["underwriter"])
    hits = store.search(qv, build_filter(underwriter), k=10)
    ids = _ids(hits)

    # (b) the underwriter DOES surface the underwriting memo.
    assert UNDERWRITER_ONLY in ids
    # (c) still isolated to the caller's tenant+env.
    assert ids.isdisjoint(OUT_OF_SCOPE)
    assert _scopes(hits) <= {ACME_PROD}


def test_store_rbac_no_roles_retrieves_nothing(populated_store):
    store, embedder = populated_store
    qv = embedder.embed_query(QUESTION)

    nobody = dev_identity("u-none", "acme", "prod", [])
    hits = store.search(qv, build_filter(nobody), k=10)

    # Fail closed: an identity with no roles retrieves nothing.
    assert hits == []


# ---------------------------------------------------------------------------
# Level 2 — end-to-end through the LangGraph query graph.
# Needs the graph + every node module (fan_out/retrieve/rerank/ground/...),
# which are built in parallel — skip cleanly until they land.
# ---------------------------------------------------------------------------


def _build_graph(store, embedder):
    try:
        from own_overview.pipeline.graph import build_query_graph
    except ModuleNotFoundError as exc:
        pytest.skip(f"query graph / nodes not available yet: {exc}")
    return build_query_graph(store=store, embedder=embedder)


def _retrieved_from_state(state) -> list:
    """Pull the surviving retrieved chunks out of the final graph state,
    tolerant of which key the pipeline populates."""
    return state.get("reranked") or state.get("candidates") or []


def test_graph_adjuster_answer_excludes_underwriter_chunk(populated_store):
    store, embedder = populated_store
    graph = _build_graph(store, embedder)

    adjuster = dev_identity("u-adj", "acme", "prod", ["adjuster"])
    state = graph.invoke({"question": QUESTION, "identity": adjuster})

    ids = _ids(_retrieved_from_state(state))
    assert UNDERWRITER_ONLY not in ids
    assert ids.isdisjoint(OUT_OF_SCOPE)


def test_graph_underwriter_answer_includes_underwriter_chunk(populated_store):
    store, embedder = populated_store
    graph = _build_graph(store, embedder)

    underwriter = dev_identity("u-uw", "acme", "prod", ["underwriter"])
    state = graph.invoke({"question": QUESTION, "identity": underwriter})

    retrieved = _retrieved_from_state(state)
    ids = _ids(retrieved)
    assert UNDERWRITER_ONLY in ids
    assert ids.isdisjoint(OUT_OF_SCOPE)
    assert _scopes(retrieved) <= {ACME_PROD}
