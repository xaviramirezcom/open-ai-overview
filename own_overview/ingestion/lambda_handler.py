"""AWS Lambda entrypoint for the event-driven ingestion path.

EventBridge routes CDA lifecycle events (see ``cda/events.py``) to this handler.
It stays deliberately thin: parse the CloudEvents envelope, drop anything that
isn't a committed, ingestable batch, then hand off to the shared
:func:`own_overview.ingestion.ingest.ingest_batch` orchestrator with components
built from config. The same handler runs against real AWS or LocalStack — only
the ``Settings`` (endpoint URLs, providers) differ.

EventBridge delivers the published event under ``event["detail"]``; direct
invocations may pass the CloudEvent at the top level. We accept both.
"""

from __future__ import annotations

from ..config import build_chunker, build_embedder, build_vector_store, get_settings
from .cda.events import CdaLifecycleEvent
from .cda.source import LocalCdaSource
from .ingest import ingest_batch


def handler(event: dict, context: object = None) -> dict:
    """Ingest one committed CDA batch. Returns a counts dict (also useful in
    logs). Non-ingestable event types are acknowledged and skipped."""
    settings = get_settings()

    # EventBridge wraps the published CloudEvent in `detail`; be tolerant of a
    # bare CloudEvent too (direct test invokes).
    cloudevent = event.get("detail", event)
    lifecycle = CdaLifecycleEvent.from_cloudevent(cloudevent)

    if not lifecycle.is_ingestable():
        return {
            "statusCode": 200,
            "skipped": True,
            "type": lifecycle.type.value,
            "table": lifecycle.table,
        }

    # Build components from the config factories (provider-swappable). For the
    # local/LocalStack file substrate, source rows come from the mounted CDA
    # root; a real deployment would inject an S3-backed CdaSource here.
    embedder = build_embedder(settings)
    store = build_vector_store(settings, embedder=embedder)
    chunker = build_chunker(settings)
    source = LocalCdaSource(settings.cda_local_root)

    result = ingest_batch(
        lifecycle,
        settings,
        source=source,
        store=store,
        embedder=embedder,
        chunker=chunker,
    )
    return {"statusCode": 200, **result}
