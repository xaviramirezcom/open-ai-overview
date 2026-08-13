"""The ingestion Lambda entrypoint, exercised locally.

We generate a real CDA batch on disk, wrap its lifecycle event the way
EventBridge delivers it, and call the handler — which builds its components from
config (local providers, per conftest) and ingests the batch. This covers the
EventBridge -> handler -> ingest wiring without AWS.
"""

from __future__ import annotations

import pytest

pytest.importorskip("numpy")
pytest.importorskip("pyarrow")

from own_overview.config import get_settings
from own_overview.ingestion.cda.simulator import generate
from own_overview.ingestion.lambda_handler import handler


def _eventbridge_envelope(ev) -> dict:
    """Mirror how CDA lifecycle events arrive on an EventBridge rule target."""
    return {
        "detail": {
            "type": str(ev.type),
            "data": {
                "tenantId": ev.tenant_id,
                "environment": ev.env,
                "table": ev.table,
                "s3Path": ev.s3_path,
                "batchId": ev.batch_id,
                "fingerprint": ev.fingerprint,
            },
        }
    }


def test_handler_ingests_a_generated_batch():
    s = get_settings()
    batches = generate(s)
    ingestable = [b for b in batches if b.event.is_ingestable()]
    assert ingestable, "expected at least one ingestable batch"

    result = handler(_eventbridge_envelope(ingestable[0].event))

    assert result["tenant"] == ingestable[0].tenant_id
    assert not result.get("skipped")
    assert result["upserts"] >= 1


def test_handler_skips_non_ingestable_event():
    result = handler(
        {
            "detail": {
                "type": "tableSchemaChanged",
                "data": {
                    "tenantId": "acme",
                    "environment": "prod",
                    "table": "claim",
                    "s3Path": "acme/prod/claim/fp/ts",
                    "batchId": "1",
                },
            }
        }
    )
    assert result.get("skipped") is True
