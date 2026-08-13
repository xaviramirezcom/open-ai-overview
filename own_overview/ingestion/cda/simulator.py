"""Synthetic CDA data generator — writes true Guidewire CDA layout offline.

This is the file-based stand-in for a real CDA feed. It takes the deterministic
corpus in ``sample_data/seed/corpus.py`` and writes it exactly the way
Guidewire Cloud Data Access lands data, so the ingestion path can be exercised
end to end with no AWS:

    <cda_local_root>/<tenant>/<env>/<table>/<fingerprint>/<timestamp>/part-0.parquet
    <cda_local_root>/<tenant>/<env>/manifest.json

Each Parquet row carries the **CDA internal columns** alongside the business
columns:

    gwcbi___operation        'I' | 'U' | 'D'   (insert / update / delete)
    gwcbi___seqval_hex       monotonic ordering key (hex; sorts lexically)
    gwcdac__fingerprintfolder  schema fingerprint (also the folder name)
    gwcdac__timestampfolder    committed batch timestamp (also the folder name)

plus one convenience governance column the ACL travels on:

    gw_acl_roles             pipe-joined roles allowed to retrieve the record

The ``manifest.json`` records, per table, the ``lastCommittedTimestamp`` —
``LocalCdaSource``/``CdaSource`` trust only data at/under a committed timestamp,
mirroring how you must not read a batch before CDA commits it.

For each batch the generator also builds a matching **CloudEvents lifecycle
event** (``streamingBatchCompleted``) — the same shape real CDA emits onto
EventBridge. ``generate()`` returns them; ``emit_to_eventbridge()`` pushes them
to a (LocalStack) EventBridge bus when ``--emit-events`` is used.

Everything is deterministic: same corpus in, byte-for-byte same folders and the
same event ids out.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ...config import Settings
from .events import CdaEventType, CdaLifecycleEvent

# The custom EventBridge bus the lifecycle events are published to. The
# LocalStack bootstrap script creates a bus and rule with this name.
EVENT_BUS_NAME = "own-overview-cda-bus"

# CDA internal column names (kept in one place; merge.py owns OP/SEQ).
FP_COL = "gwcdac__fingerprintfolder"
TS_COL = "gwcdac__timestampfolder"
OP_COL = "gwcbi___operation"
SEQ_COL = "gwcbi___seqval_hex"
ACL_COL = "gw_acl_roles"


@dataclass(frozen=True)
class GeneratedBatch:
    """One committed table batch the generator wrote, plus its lifecycle event.

    ``s3_path`` is relative to ``cda_local_root`` — exactly what
    ``LocalCdaSource.read_batch`` expects, and what the event carries.
    """

    tenant_id: str
    env: str
    table: str
    fingerprint: str
    timestamp: str
    s3_path: str
    record_count: int
    event: CdaLifecycleEvent
    cloudevent: dict


# ---------------------------------------------------------------------------
# Deterministic folder-name helpers
# ---------------------------------------------------------------------------


def _fingerprint(table: str, columns: list[str]) -> str:
    """A stable schema fingerprint for a table (folder name in CDA).

    Derived only from the table name and its sorted business columns, so it is
    identical across runs and changes only when the schema does — which is
    exactly when real CDA rolls a new fingerprint folder.
    """
    basis = table + "|" + "|".join(sorted(columns))
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:12]


def _timestamp_folder(rows: list[dict]) -> str:
    """The committed batch timestamp (epoch millis, as a string).

    Uses the latest business ``UpdateTime`` in the batch, so it is meaningful
    ("data committed as of ...") and deterministic given the corpus.
    """
    latest = max(
        (r["UpdateTime"] for r in rows if isinstance(r.get("UpdateTime"), datetime)),
        default=datetime(2026, 1, 1, tzinfo=UTC),
    )
    return str(int(latest.timestamp() * 1000))


# ---------------------------------------------------------------------------
# Row assembly
# ---------------------------------------------------------------------------


def _build_rows(table_data: Any) -> tuple[list[dict], list[str]]:
    """Turn a corpus ``TableData`` into normalized Parquet rows.

    Adds the CDA operation/sequence columns and the ACL column, then pads every
    row to the union of business columns so the Parquet schema is uniform (a
    DELETE row only sets its key + status, the rest become null — which is how
    CDA tombstones look).
    """
    raw: list[dict] = []
    business_cols: list[str] = []
    for ch in table_data.changes:
        for col in ch.fields:
            if col not in business_cols:
                business_cols.append(col)
        raw.append(
            {
                OP_COL: ch.op,
                SEQ_COL: f"{ch.seq:016x}",
                ACL_COL: "|".join(table_data.acl),
                **ch.fields,
            }
        )

    # Pad to a uniform schema.
    rows: list[dict] = []
    for r in raw:
        padded = {col: r.get(col) for col in business_cols}
        padded[OP_COL] = r[OP_COL]
        padded[SEQ_COL] = r[SEQ_COL]
        padded[ACL_COL] = r[ACL_COL]
        rows.append(padded)
    return rows, business_cols


def _write_parquet(rows: list[dict], dest_dir: Path) -> None:
    """Write ``part-0.parquet`` under ``dest_dir`` (created if needed)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    dest_dir.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, dest_dir / "part-0.parquet")


# ---------------------------------------------------------------------------
# CloudEvents lifecycle event
# ---------------------------------------------------------------------------


def _cloudevent(batch: dict) -> dict:
    """Build a CloudEvents-shaped CDA lifecycle event for a committed batch.

    Matches ``CdaLifecycleEvent.from_cloudevent``: a top-level ``type`` plus the
    domain fields nested under ``data``.
    """
    et = CdaEventType.STREAMING_BATCH_COMPLETED.value
    return {
        "specversion": "1.0",
        "type": et,
        "source": f"guidewire.cda/{batch['tenantId']}/{batch['environment']}",
        "id": f"{batch['tenantId']}-{batch['environment']}-{batch['table']}-{batch['batchId']}",
        "time": datetime.now(tz=UTC).isoformat(),
        "datacontenttype": "application/json",
        "data": {
            "type": et,
            "tenantId": batch["tenantId"],
            "environment": batch["environment"],
            "table": batch["table"],
            "batchId": batch["batchId"],
            "s3Path": batch["s3Path"],
            "fingerprint": batch["fingerprint"],
            "recordCount": batch["recordCount"],
        },
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate(
    settings: Settings,
    *,
    tenants: list[str] | None = None,
    envs: list[str] | None = None,
    emit_events: bool = False,
) -> list[GeneratedBatch]:
    """Write the synthetic corpus in CDA layout and return the committed batches.

    Parameters
    ----------
    settings    : provides ``cda_local_root`` (where to write) and, for
                  ``emit_events``, the EventBridge endpoint/region.
    tenants     : restrict to these tenant ids (default: all in the corpus).
    envs        : restrict to these environments (default: all in the corpus).
    emit_events : also push the lifecycle events to EventBridge (LocalStack).
                  When False, callers typically loop the returned batches through
                  ``ingest_batch`` directly (the zero-cloud local path).

    Returns the list of :class:`GeneratedBatch`, in corpus order.
    """
    # Import here so the corpus lives with the sample data, not the package.
    from sample_data.seed import corpus as seed

    root = Path(settings.cda_local_root)
    keep_tenants = set(tenants) if tenants else None
    keep_envs = set(envs) if envs else None

    batches: list[GeneratedBatch] = []
    # Accumulate manifest table-entries per scope, written once per (tenant, env).
    manifests: dict[tuple[str, str], dict] = {}

    for td in seed.CORPUS:
        if keep_tenants and td.tenant not in keep_tenants:
            continue
        if keep_envs and td.env not in keep_envs:
            continue

        rows, business_cols = _build_rows(td)
        fingerprint = _fingerprint(td.table, business_cols)
        timestamp = _timestamp_folder(rows)

        # Stamp the folder-name columns onto every row (CDA carries them both as
        # path segments and as columns).
        for r in rows:
            r[FP_COL] = fingerprint
            r[TS_COL] = timestamp

        rel_path = f"{td.tenant}/{td.env}/{td.table}/{fingerprint}/{timestamp}"
        dest_dir = root / rel_path
        _write_parquet(rows, dest_dir)

        manifests.setdefault((td.tenant, td.env), {})[td.table] = {
            "lastCommittedTimestamp": timestamp,
            "fingerprint": fingerprint,
            "recordCount": len(rows),
            "s3Path": rel_path,
        }

        ce = _cloudevent(
            {
                "tenantId": td.tenant,
                "environment": td.env,
                "table": td.table,
                "batchId": timestamp,
                "s3Path": rel_path,
                "fingerprint": fingerprint,
                "recordCount": len(rows),
            }
        )
        event = CdaLifecycleEvent.from_cloudevent(ce)
        batches.append(
            GeneratedBatch(
                tenant_id=td.tenant,
                env=td.env,
                table=td.table,
                fingerprint=fingerprint,
                timestamp=timestamp,
                s3_path=rel_path,
                record_count=len(rows),
                event=event,
                cloudevent=ce,
            )
        )

    # Write one manifest.json per scope, covering all its tables.
    for (tenant, env), tables in manifests.items():
        scope_dir = root / tenant / env
        scope_dir.mkdir(parents=True, exist_ok=True)
        (scope_dir / "manifest.json").write_text(
            json.dumps({"tables": tables}, indent=2, sort_keys=True)
        )

    if emit_events:
        emit_to_eventbridge(batches, settings)

    return batches


def emit_to_eventbridge(batches: list[GeneratedBatch], settings: Settings) -> int:
    """Publish each batch's lifecycle event to the (LocalStack) EventBridge bus.

    Uses ``settings.aws_endpoint_url`` (LocalStack) and ``aws_region``. Returns
    the number of events accepted. The bus + rule are created by
    ``scripts/bootstrap_localstack.sh``.
    """
    import boto3

    client = boto3.client(
        "events",
        endpoint_url=settings.aws_endpoint_url or None,
        region_name=settings.aws_region,
    )
    entries = [
        {
            "Source": b.cloudevent["source"],
            "DetailType": b.event.type.value,
            "Detail": json.dumps(b.cloudevent),
            "EventBusName": EVENT_BUS_NAME,
        }
        for b in batches
    ]
    accepted = 0
    # put_events accepts up to 10 entries per call.
    for i in range(0, len(entries), 10):
        resp = client.put_events(Entries=entries[i : i + 10])
        accepted += len(entries[i : i + 10]) - resp.get("FailedEntryCount", 0)
    return accepted
