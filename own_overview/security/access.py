"""Build the retrieval filter from the caller's Identity — the whole ballgame.

"Filter at retrieval, not after." The permission check is compiled into the
vector-store query so restricted data is excluded *before* the model sees it.
The filter is derived only from the signed Identity (tenant, env, roles),
never from the question text or anything the model produced.

Three conditions, all mandatory:
  1. scope match     : chunk.scope == identity.scope   (tenant AND env isolation)
  2. role match      : chunk.acl_roles ∩ identity.roles ≠ ∅
  3. optional doc-type narrowing
"""

from __future__ import annotations

from ..contracts import Identity, RetrievalFilter, TenantScope


def build_filter(identity: Identity, *, doc_types: set[str] | None = None) -> RetrievalFilter:
    if not identity.roles:
        # No roles => can retrieve nothing. Fail closed, never open.
        return RetrievalFilter(scope=identity.scope, roles=frozenset(), doc_types=frozenset())
    return RetrievalFilter(
        scope=identity.scope,
        roles=frozenset(identity.roles),
        doc_types=frozenset(doc_types) if doc_types else None,
    )


def is_visible(chunk_scope: TenantScope, chunk_roles: frozenset[str], flt: RetrievalFilter) -> bool:
    """Reference predicate used by the local vector store (OpenSearch encodes
    the same logic as a boolean filter query). Kept here so both stores share
    one definition of 'visible'."""
    if chunk_scope != flt.scope:
        return False
    if not (chunk_roles & flt.roles):
        return False
    return True
