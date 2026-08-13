"""Ingestion orchestrator — the write path, shared by the CLI and the Lambda.

Given a committed CDA lifecycle event, materialize the batch into the vector
store:

    read committed rows  (LocalCdaSource.read_batch, manifest-gated)
      -> merge CDA change rows to latest state, honoring DELETE tombstones
      -> for deletes:  store.delete_document(scope, doc_id)
      -> for upserts:  chunker.split -> embedder.embed_documents -> store.upsert

Both entrypoints call :func:`ingest_batch`; components are injected (defaulting
to the ``config.build_*`` factories) so the same code runs on Bedrock+OpenSearch
or the local stack. The per-table knobs (`id_field`, `text_builder`, `acl_for`,
`source_system`) live in :data:`TABLE_CONFIG` — add a table by adding an entry.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..config import Settings, build_chunker, build_embedder, build_vector_store, get_settings
from ..contracts import Chunker, Embedder, TenantScope, VectorStore
from .cda.events import CdaLifecycleEvent
from .cda.merge import merge_rows
from .cda.source import LocalCdaSource

# The governance column the simulator writes the ACL onto (roles travel with the
# data). Real CDA feeds encode ACLs their own way; the mapping lives in one
# place — ``acl_for`` below — so swapping the encoding touches nothing else.
ACL_COL = "gw_acl_roles"

# Default roles per table when a row carries no explicit ACL column. Fail
# closed-ish: underwriting is restricted by default.
_DEFAULT_ACL: dict[str, frozenset[str]] = {
    "policy": frozenset({"adjuster", "underwriter", "agent", "admin"}),
    "claim": frozenset({"adjuster", "underwriter", "admin"}),
    "underwriting": frozenset({"underwriter", "admin"}),
    "billing": frozenset({"billing", "adjuster", "admin"}),
}


def _acl_for(table: str) -> Callable[[dict], frozenset[str]]:
    """Return an ``acl_for(row)`` that reads the row's ACL column, falling back
    to the table default. The ACL is data-driven so a record can be more or less
    restricted than its table without a code change."""

    default = _DEFAULT_ACL.get(table, frozenset({"admin"}))

    def acl_for(row: dict) -> frozenset[str]:
        raw = row.get(ACL_COL)
        if raw:
            return frozenset(r for r in str(raw).split("|") if r)
        return default

    return acl_for


# --- per-table text builders (row -> passage text to embed) ----------------
# Plain, self-contained sentences so a citation reads well on its own.


def _policy_text(r: dict) -> str:
    premium = _money(r.get("PremiumAmount"))
    parts = [
        f"Policy {r.get('PolicyNumber')} ({r.get('ProductType')}) for "
        f"{r.get('AccountName')} in {r.get('State')}.",
        f"Premium {premium}, status {r.get('Status')}, effective {r.get('EffectiveDate')}.",
    ]
    if r.get("ChangeReason"):
        parts.append(f"Most recent change: {r['ChangeReason']}.")
    return " ".join(p for p in parts if p)


def _claim_text(r: dict) -> str:
    reserve = _money(r.get("ReserveAmount"))
    return (
        f"Claim {r.get('ClaimNumber')} on policy {r.get('PolicyNumber')}: "
        f"{r.get('LossType')} reported {r.get('LossDate')}. "
        f"{r.get('Description')} Current reserve {reserve}, status {r.get('Status')}."
    )


def _underwriting_text(r: dict) -> str:
    return (
        f"Underwriting memo {r.get('MemoNumber')} for policy {r.get('PolicyNumber')} "
        f"({r.get('AccountName')}). Risk score {r.get('RiskScore')}. "
        f"Recommendation: {r.get('Recommendation')}. {r.get('Notes')} "
        f"— {r.get('Author')}."
    )


def _billing_text(r: dict) -> str:
    due = _money(r.get("AmountDue"))
    return (
        f"Billing invoice {r.get('InvoiceNumber')} for policy {r.get('PolicyNumber')}: "
        f"amount due {due}, due {r.get('DueDate')}, status {r.get('Status')}."
    )


def _money(v: Any) -> str:
    try:
        return f"${float(v):,.0f}"
    except (TypeError, ValueError):
        return "$0"


@dataclass(frozen=True)
class TableConfig:
    """How one CDA table maps into Documents."""

    id_field: str
    source_system: str
    text_builder: Callable[[dict], str]
    acl_for: Callable[[dict], frozenset[str]]


TABLE_CONFIG: dict[str, TableConfig] = {
    "policy": TableConfig("PolicyNumber", "PolicyCenter", _policy_text, _acl_for("policy")),
    "claim": TableConfig("ClaimNumber", "ClaimCenter", _claim_text, _acl_for("claim")),
    "underwriting": TableConfig(
        "MemoNumber", "PolicyCenter", _underwriting_text, _acl_for("underwriting")
    ),
    "billing": TableConfig("InvoiceNumber", "BillingCenter", _billing_text, _acl_for("billing")),
}


def ingest_batch(
    event: CdaLifecycleEvent,
    settings: Settings | None = None,
    *,
    source: LocalCdaSource | None = None,
    store: VectorStore | None = None,
    embedder: Embedder | None = None,
    chunker: Chunker | None = None,
) -> dict:
    """Materialize one committed CDA batch into the vector store.

    Idempotent-ish by design: re-running a batch upserts the same latest-state
    Documents and re-applies any tombstones. Components default from the
    ``config.build_*`` factories but can be injected (tests, CLI reuse, alternate
    providers).

    Returns a small counts dict for logging / the CLI table.
    """
    s = settings or get_settings()

    # Skip lifecycle events that don't signal committed, ingestable data
    # (e.g. batchModeCompleted / tableSchemaChanged).
    if not event.is_ingestable():
        return {
            "tenant": event.tenant_id,
            "env": event.env,
            "table": event.table,
            "skipped": True,
            "reason": f"non-ingestable event type: {event.type.value}",
            "upserts": 0,
            "deletes": 0,
            "chunks": 0,
        }

    cfg = TABLE_CONFIG.get(event.table)
    if cfg is None:
        return {
            "tenant": event.tenant_id,
            "env": event.env,
            "table": event.table,
            "skipped": True,
            "reason": f"no TableConfig for table: {event.table}",
            "upserts": 0,
            "deletes": 0,
            "chunks": 0,
        }

    source = source or LocalCdaSource(s.cda_local_root)
    embedder = embedder or build_embedder(s)
    store = store or build_vector_store(s, embedder=embedder)
    chunker = chunker or build_chunker(s)

    scope = TenantScope(tenant_id=event.tenant_id, env=event.env)
    rows = source.read_batch(event.s3_path)

    result = merge_rows(
        rows,
        scope=scope,
        doc_type=event.table,
        source_system=cfg.source_system,
        id_field=cfg.id_field,
        text_builder=cfg.text_builder,
        acl_for=cfg.acl_for,
        fingerprint=event.fingerprint,
    )

    # Deletes first: a redacted/removed record must leave the index even if the
    # same batch also re-upserts others (regulated-domain requirement).
    for doc_id in result.deletes:
        store.delete_document(scope, doc_id)

    # Upserts: chunk -> embed -> upsert.
    chunks = []
    for doc in result.upserts:
        chunks.extend(chunker.split(doc))

    if chunks:
        vectors = embedder.embed_documents([c.text for c in chunks])
        for chunk, vec in zip(chunks, vectors, strict=True):
            chunk.embedding = vec
        store.upsert(chunks)

    return {
        "tenant": event.tenant_id,
        "env": event.env,
        "table": event.table,
        "skipped": False,
        "upserts": len(result.upserts),
        "deletes": len(result.deletes),
        "chunks": len(chunks),
        "rows_read": len(rows),
    }
