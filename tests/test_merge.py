"""Tests for CDA change-row merging (own_overview.ingestion.cda.merge).

Two behaviors matter for a regulated RAG pipeline:

1. **Latest-state collapse** — CDA is CDC, so a record can appear many times
   across a batch. We keep only the newest row, ordered by `gwcbi___seqval_hex`,
   regardless of the order the rows arrive in.
2. **Deletes are tombstones, not upserts** — a row with `gwcbi___operation='D'`
   must evict the doc (so a redacted claim leaves the index), never re-index it.
"""

from __future__ import annotations

from own_overview.contracts import TenantScope
from own_overview.ingestion.cda.merge import OP_COL, SEQ_COL, merge_rows

SCOPE = TenantScope(tenant_id="acme", env="prod")


def _row(claim: str, seq: str, op: str = "I", **extra) -> dict:
    """A minimal CDA change row keyed by ClaimNumber."""
    return {"ClaimNumber": claim, SEQ_COL: seq, OP_COL: op, **extra}


def _merge(rows: list[dict]):
    return merge_rows(
        rows,
        scope=SCOPE,
        doc_type="claim",
        source_system="ClaimCenter",
        id_field="ClaimNumber",
        text_builder=lambda r: f"claim {r['ClaimNumber']} status={r.get('Status', '')}",
        acl_for=lambda r: frozenset({"adjuster"}),
    )


# ---------------------------------------------------------------------------
# Latest-state collapse
# ---------------------------------------------------------------------------


def test_collapses_to_latest_state_by_seqval():
    # Three revisions of the same claim; the highest seqval is the truth.
    rows = [
        _row("CLM-1", "0000000000000001", op="I", Status="open"),
        _row("CLM-1", "0000000000000002", op="U", Status="in_review"),
        _row("CLM-1", "0000000000000003", op="U", Status="closed"),
    ]

    result = _merge(rows)

    assert result.deletes == []
    assert len(result.upserts) == 1
    doc = result.upserts[0]
    assert doc.doc_id == "claim/CLM-1"
    # Latest revision won -> its Status is the one rendered into the text.
    assert "status=closed" in doc.text
    # Provenance carries the winning seqval.
    assert doc.seqval_hex == "0000000000000003"


def test_latest_state_is_order_independent():
    # Same three revisions, shuffled so the newest arrives first.
    rows = [
        _row("CLM-1", "0000000000000003", op="U", Status="closed"),
        _row("CLM-1", "0000000000000001", op="I", Status="open"),
        _row("CLM-1", "0000000000000002", op="U", Status="in_review"),
    ]

    result = _merge(rows)

    assert len(result.upserts) == 1
    assert "status=closed" in result.upserts[0].text


def test_distinct_keys_each_produce_a_document():
    rows = [
        _row("CLM-1", "0000000000000001", Status="open"),
        _row("CLM-2", "0000000000000001", Status="open"),
    ]

    result = _merge(rows)

    assert {d.doc_id for d in result.upserts} == {"claim/CLM-1", "claim/CLM-2"}
    assert result.deletes == []


# ---------------------------------------------------------------------------
# Deletes are tombstones
# ---------------------------------------------------------------------------


def test_delete_operation_produces_a_tombstone_not_an_upsert():
    rows = [_row("CLM-9", "0000000000000001", op="D")]

    result = _merge(rows)

    assert result.upserts == []
    assert result.deletes == ["claim/CLM-9"]


def test_delete_after_insert_wins_and_evicts():
    # Inserted, then deleted (higher seqval): the record must leave the index.
    rows = [
        _row("CLM-9", "0000000000000001", op="I", Status="open"),
        _row("CLM-9", "0000000000000002", op="D"),
    ]

    result = _merge(rows)

    assert result.deletes == ["claim/CLM-9"]
    assert all(d.doc_id != "claim/CLM-9" for d in result.upserts)


def test_reinsert_after_delete_upserts():
    # Deleted, then re-inserted (higher seqval): it comes back as an upsert.
    rows = [
        _row("CLM-9", "0000000000000001", op="D"),
        _row("CLM-9", "0000000000000002", op="I", Status="reopened"),
    ]

    result = _merge(rows)

    assert result.deletes == []
    assert len(result.upserts) == 1
    assert "status=reopened" in result.upserts[0].text


def test_mixed_batch_splits_upserts_and_deletes():
    rows = [
        _row("CLM-1", "0000000000000001", op="I", Status="open"),
        _row("CLM-2", "0000000000000001", op="I", Status="open"),
        _row("CLM-2", "0000000000000002", op="D"),
    ]

    result = _merge(rows)

    assert [d.doc_id for d in result.upserts] == ["claim/CLM-1"]
    assert result.deletes == ["claim/CLM-2"]
