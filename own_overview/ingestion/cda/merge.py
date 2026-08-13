"""Turn CDA change rows into current-state Documents — honoring deletes.

CDA is change-data-capture, not a snapshot. In *raw* mode every row carries:
  - gwcbi___operation : 'I' | 'U' | 'D'  (insert / update / delete)
  - gwcbi___seqval_hex: monotonic ordering key
Deletes are first-class rows (tombstones), not physical removals.

For RAG we want latest-state, and — critically for a regulated shop — we must
propagate DELETEs so a redacted/removed claim leaves the vector index. This
module collapses raw rows per record and reports which ids were deleted so the
ingestion Lambda can call `VectorStore.delete_document`.

In *merged* mode CDA already collapses to latest state; we still surface the
operation column to catch deletes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from ...contracts import Document, TenantScope

OP_COL = "gwcbi___operation"
SEQ_COL = "gwcbi___seqval_hex"


@dataclass(slots=True)
class MergeResult:
    """Latest-state records to (re)index, plus ids to evict from the index."""

    upserts: list[Document]
    deletes: list[str]  # doc_ids


def _op(row: dict) -> str:
    return str(row.get(OP_COL, "I")).upper()[:1]


def merge_rows(
    rows: list[dict],
    *,
    scope: TenantScope,
    doc_type: str,
    source_system: str,
    id_field: str,
    text_builder: Callable[[dict], str],
    acl_for: Callable[[dict], frozenset[str]],
    fingerprint: str | None = None,
) -> MergeResult:
    """Collapse CDA rows to latest state.

    Parameters
    ----------
    rows        : parsed Parquet rows for one table/batch.
    id_field    : the business key column (e.g. "PolicyNumber", "ClaimNumber").
    text_builder: (row) -> str, renders a row into the passage text to embed.
    acl_for     : (row) -> frozenset[str], the roles allowed to retrieve it.
    """
    latest: dict[str, dict] = {}
    order: dict[str, str] = {}
    for row in rows:
        key = str(row[id_field])
        seq = str(row.get(SEQ_COL, ""))
        if key not in order or seq >= order[key]:
            order[key] = seq
            latest[key] = row

    upserts: list[Document] = []
    deletes: list[str] = []
    for key, row in latest.items():
        doc_id = f"{doc_type}/{key}"
        if _op(row) == "D":
            deletes.append(doc_id)
            continue
        upserts.append(
            Document(
                doc_id=doc_id,
                scope=scope,
                doc_type=doc_type,
                source_system=source_system,
                source_id=doc_id,
                text=text_builder(row),
                acl_roles=acl_for(row),
                updated_at=_updated_at(row),
                fingerprint=fingerprint,
                seqval_hex=str(row.get(SEQ_COL, "")) or None,
            )
        )
    return MergeResult(upserts=upserts, deletes=deletes)


def _updated_at(row: dict) -> datetime:
    for col in ("UpdateTime", "updated_at", "UpdatedDate"):
        v = row.get(col)
        if isinstance(v, datetime):
            return v
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v / 1000, tz=UTC)
    return datetime.now(tz=UTC)
