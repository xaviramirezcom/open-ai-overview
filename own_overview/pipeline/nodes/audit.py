"""audit node — write the immutable "who asked what, and what we did" record.

The final node persists one :class:`AuditRecord` per query: the caller and their
tenant/env, the question, the chunk ids that were actually used, the
groundedness score and whether we abstained, and a UTC timestamp. That trail is
what makes the whole pipeline defensible in a regulated shop — every answer can
be reconstructed and reviewed after the fact.

We record chunk ids and the score, not the passage text or the answer body: the
log is a trail, not a second copy of sensitive data.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ...audit.log import AuditRecord, write_audit
from ..state import QueryState

if TYPE_CHECKING:
    from ...config import Settings


def run(state: QueryState, settings: Settings) -> dict:
    identity = state.get("identity")
    answer = state.get("answer")
    reranked = state.get("reranked", [])

    chunk_ids = [r.chunk.chunk_id for r in reranked]

    record = AuditRecord(
        timestamp=datetime.now(UTC).isoformat(),
        user_id=identity.user_id if identity else "unknown",
        tenant=identity.scope.tenant_id if identity else "unknown",
        env=identity.scope.env if identity else "unknown",
        question=state.get("question", ""),
        retrieved_chunk_ids=chunk_ids,
        groundedness=answer.groundedness if answer else None,
        abstained=answer.abstained if answer else False,
    )
    write_audit(record)

    trace = list(state.get("trace", []))
    trace.append(
        {
            "node": "audit",
            "timestamp": record.timestamp,
            "n_retrieved": len(chunk_ids),
            "abstained": record.abstained,
        }
    )
    return {"trace": trace}
