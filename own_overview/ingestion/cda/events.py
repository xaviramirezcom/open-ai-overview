"""Guidewire CDA Lifecycle Events (CloudEvents), the *recommended* trigger.

CDA emits CloudEvents-formatted lifecycle events onto an AWS EventBridge
partner event bus; you route them to Lambda. We do NOT trigger off raw S3
`ObjectCreated`, because those fire per-Parquet-object *before* the batch is
committed in `manifest.json` — reading then risks a half-written table. The
lifecycle event is the commit signal.

Event types we handle (names per Guidewire's developer blog):
  - batchModeTableWrittenOut : a table's initial bulk batch finished
  - batchModeCompleted       : full backfill done; streaming begins
  - streamingBatchCompleted  : a streaming micro-batch committed (the workhorse)
  - tableSchemaChanged       : schema evolved (new fingerprint)

The payload carries tenant id, environment, table, batch id and the S3 path of
the committed records — everything the ingestion Lambda needs.

Ref: https://www.guidewire.com/resources/blog/developers/streamline-data-consumption-with-cda-lifecycle-events-and-aws-eventbridge
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class CdaEventType(StrEnum):
    BATCH_TABLE_WRITTEN = "batchModeTableWrittenOut"
    BATCH_COMPLETED = "batchModeCompleted"
    STREAMING_BATCH_COMPLETED = "streamingBatchCompleted"
    TABLE_SCHEMA_CHANGED = "tableSchemaChanged"


# Event types that mean "committed data is ready to ingest for this table".
INGESTABLE = {CdaEventType.STREAMING_BATCH_COMPLETED, CdaEventType.BATCH_TABLE_WRITTEN}


@dataclass(frozen=True, slots=True)
class CdaLifecycleEvent:
    """A parsed CDA lifecycle event.

    `tenant_id` + `env` map straight onto our `TenantScope`; `table` maps to a
    doc_type; `s3_path` points at the committed Parquet for this batch.
    """

    type: CdaEventType
    tenant_id: str
    env: str
    table: str
    batch_id: str
    s3_path: str
    fingerprint: str | None = None
    record_count: int | None = None

    @classmethod
    def from_cloudevent(cls, ce: dict[str, Any]) -> CdaLifecycleEvent:
        """Parse a CloudEvents envelope. Real CDA nests domain fields under
        `data`; the local simulator emits the same shape."""
        data = ce.get("data", ce)
        # CloudEvents `type` may be reverse-DNS prefixed; take the last segment.
        raw_type = str(ce.get("type", data.get("type", "")))
        return cls(
            type=CdaEventType(raw_type.rsplit(".", 1)[-1]),
            tenant_id=data["tenantId"],
            env=data["environment"],
            table=data["table"],
            batch_id=str(data.get("batchId", "")),
            s3_path=data["s3Path"],
            fingerprint=data.get("fingerprint"),
            record_count=data.get("recordCount"),
        )

    def is_ingestable(self) -> bool:
        return self.type in INGESTABLE
