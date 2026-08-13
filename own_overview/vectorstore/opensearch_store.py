"""OpenSearch vector store — the default, Guidewire-shaped retrieval backend.

Stores chunks as k-NN vectors plus their isolation/ACL metadata, and searches
them with a **k-NN query wrapped in a boolean filter that enforces the
`RetrievalFilter` inside the query** — the same rule as
`security.access.is_visible`, compiled to OpenSearch DSL:

    scope match : term tenant_id == scope.tenant_id AND term env == scope.env
    role match  : terms acl_roles ∩ filter.roles ≠ ∅
    (optional)  : terms doc_type ∈ filter.doc_types

Restricted chunks are excluded *before* scoring, so the model never sees them —
"filter at retrieval, not after". Each tenant+env gets its own index
(`{prefix}-{tenant}__{env}`), so dev/qa/prod and separate insurers never share
one index. `delete_document` honors CDA DELETE tombstones (a regulated-domain
requirement).

`opensearch-py` is a base dependency but is imported lazily so importing this
module never forces the client on adapters that don't use it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from ..config import Settings
from ..contracts import Chunk, RetrievalFilter, Retrieved, TenantScope


class OpenSearchStore:
    """VectorStore backed by OpenSearch k-NN. Implements `contracts.VectorStore`."""

    def __init__(self, settings: Settings) -> None:
        from opensearchpy import OpenSearch

        self.settings = settings
        self.prefix = settings.opensearch_index_prefix
        self._client = OpenSearch(
            hosts=[{"host": settings.opensearch_host, "port": settings.opensearch_port}],
            use_ssl=settings.opensearch_use_ssl,
            verify_certs=settings.opensearch_use_ssl,
            ssl_show_warn=False,
        )

    # -- naming ------------------------------------------------------------

    def _index_for(self, scope: TenantScope) -> str:
        """Per-tenant, per-env index name — the isolation partition."""
        return f"{self.prefix}-{scope.namespace()}"

    def _ensure_index(self, index: str, dim: int) -> None:
        """Create the index with a knn_vector mapping if it does not exist."""
        if self._client.indices.exists(index=index):
            return
        body = {
            "settings": {"index": {"knn": True}},
            "mappings": {
                "properties": {
                    "embedding": {
                        "type": "knn_vector",
                        "dimension": dim,
                        # cosine space + lucene engine so the k-NN query can take
                        # an efficient boolean pre-filter (OpenSearch 2.4+).
                        "method": {
                            "name": "hnsw",
                            "space_type": "cosinesimil",
                            "engine": "lucene",
                        },
                    },
                    "chunk_id": {"type": "keyword"},
                    "doc_id": {"type": "keyword"},
                    "tenant_id": {"type": "keyword"},
                    "env": {"type": "keyword"},
                    "doc_type": {"type": "keyword"},
                    "acl_roles": {"type": "keyword"},
                    "source_id": {"type": "keyword"},
                    "updated_at": {"type": "date"},
                    "text": {"type": "text"},
                    "metadata": {"type": "object", "enabled": False},
                }
            },
        }
        self._client.indices.create(index=index, body=body)

    # -- VectorStore Protocol ---------------------------------------------

    def upsert(self, chunks: Sequence[Chunk]) -> None:
        """Bulk-index chunks (embeddings must already be set upstream)."""
        from opensearchpy import helpers

        if not chunks:
            return
        # Group by target index so each tenant+env is created/written once.
        by_index: dict[str, list[Chunk]] = {}
        for c in chunks:
            if c.embedding is None:
                raise ValueError(
                    f"chunk {c.chunk_id!r} has no embedding; embed before upserting "
                    "to OpenSearch (this store does not embed)."
                )
            by_index.setdefault(self._index_for(c.scope), []).append(c)

        actions: list[dict] = []
        for index, group in by_index.items():
            first_emb = group[0].embedding
            assert first_emb is not None  # validated above: this store never embeds
            self._ensure_index(index, dim=len(first_emb))
            for c in group:
                actions.append(
                    {
                        "_op_type": "index",
                        "_index": index,
                        "_id": c.chunk_id,
                        "_source": self._to_source(c),
                    }
                )
        helpers.bulk(self._client, actions, refresh=True)

    def search(self, query_vector: list[float], flt: RetrievalFilter, k: int) -> list[Retrieved]:
        index = self._index_for(flt.scope)
        if not self._client.indices.exists(index=index):
            return []
        query = {
            "size": k,
            "query": {
                "knn": {
                    "embedding": {
                        "vector": query_vector,
                        "k": k,
                        "filter": self._acl_filter(flt),
                    }
                }
            },
        }
        response = self._client.search(index=index, body=query)
        hits = response.get("hits", {}).get("hits", [])
        return [
            Retrieved(chunk=self._from_source(h["_source"]), score=float(h["_score"])) for h in hits
        ]

    def delete_document(self, scope: TenantScope, doc_id: str) -> None:
        """Remove every chunk of a document — honors CDA DELETE tombstones."""
        index = self._index_for(scope)
        if not self._client.indices.exists(index=index):
            return
        self._client.delete_by_query(
            index=index,
            body={"query": {"term": {"doc_id": doc_id}}},
            refresh=True,
            conflicts="proceed",
        )

    # -- filter (mirrors security.access.is_visible) -----------------------

    @staticmethod
    def _acl_filter(flt: RetrievalFilter) -> dict:
        """The boolean filter compiled from a RetrievalFilter.

        Same three conditions as `is_visible` (scope + role, plus the optional
        doc-type narrowing). With no roles, `terms acl_roles []` matches nothing,
        so an empty-role filter fails closed exactly like `build_filter`.
        """
        must: list[dict] = [
            {"term": {"tenant_id": flt.scope.tenant_id}},
            {"term": {"env": flt.scope.env}},
            {"terms": {"acl_roles": list(flt.roles)}},
        ]
        if flt.doc_types is not None:
            must.append({"terms": {"doc_type": list(flt.doc_types)}})
        return {"bool": {"filter": must}}

    # -- (de)serialization -------------------------------------------------

    @staticmethod
    def _to_source(c: Chunk) -> dict:
        return {
            "embedding": c.embedding,
            "chunk_id": c.chunk_id,
            "doc_id": c.doc_id,
            "tenant_id": c.scope.tenant_id,
            "env": c.scope.env,
            "doc_type": c.doc_type,
            "acl_roles": list(c.acl_roles),
            "source_id": c.source_id,
            "updated_at": c.updated_at.isoformat(),
            "text": c.text,
            "metadata": c.metadata,
        }

    @staticmethod
    def _from_source(src: dict) -> Chunk:
        return Chunk(
            chunk_id=src["chunk_id"],
            doc_id=src["doc_id"],
            scope=TenantScope(tenant_id=src["tenant_id"], env=src["env"]),
            doc_type=src["doc_type"],
            source_id=src["source_id"],
            text=src["text"],
            acl_roles=frozenset(src.get("acl_roles", [])),
            updated_at=datetime.fromisoformat(src["updated_at"]),
            embedding=src.get("embedding"),
            metadata=src.get("metadata") or {},
        )
