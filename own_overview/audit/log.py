"""Audit log — an append-only record of every answered query.

In a regulated shop, "who asked what, over which tenant/env, what did we
retrieve, and did we answer or abstain" has to be reconstructable after the
fact. The audit node writes one :class:`AuditRecord` per query as a JSON line
to an append-only file, so the log is greppable, immutable-by-convention and
free of any dependency (no DB, no cloud) in local mode. A real deploy would
point the same record at CloudWatch / an OpenSearch audit index; the shape does
not change.

The log path comes from ``OWN_OVERVIEW_AUDIT_LOG`` (default ``audit.log``).
Note we log retrieved *chunk ids and the groundedness score*, never the answer
text or the passage contents — the log is a trail, not a second copy of the
(possibly sensitive) data.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field

_DEFAULT_PATH = "audit.log"
_ENV_VAR = "OWN_OVERVIEW_AUDIT_LOG"


@dataclass(slots=True)
class AuditRecord:
    """One immutable line in the audit trail for a single answered query."""

    timestamp: str  # ISO-8601 UTC, set by the audit node
    user_id: str
    tenant: str
    env: str
    question: str
    retrieved_chunk_ids: list[str] = field(default_factory=list)
    groundedness: float | None = None
    abstained: bool = False


def audit_log_path() -> str:
    """Resolve the audit-log path from the environment (default ``audit.log``)."""
    return os.environ.get(_ENV_VAR, _DEFAULT_PATH)


def write_audit(record: AuditRecord) -> None:
    """Append ``record`` to the audit log as a single JSON line.

    Opened in append mode so concurrent writers interleave whole lines rather
    than clobbering the file — the log only ever grows.
    """
    path = audit_log_path()
    line = json.dumps(asdict(record), ensure_ascii=False)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
