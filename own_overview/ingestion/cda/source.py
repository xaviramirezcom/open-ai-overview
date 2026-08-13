"""Read committed CDA Parquet — gated on the manifest, tenant/env aware.

CDA lands data as:
    <root>/<manifestKey>/manifest.json
    <root>/<table>/<fingerprint>/<timestamp>/*.parquet
`manifest.json` records, per table, the last *committed* timestamp folder.
Reading raw folders directly risks partial batches, so we resolve the S3 path
from the lifecycle event but only trust data at/under a committed timestamp.

`CdaSource` abstracts the substrate: `LocalCdaSource` reads a folder (used by
the simulator + LocalStack), an S3-backed source reads a real bucket. Both
return parsed rows for a batch so `merge.py` can collapse them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from ...contracts import TenantScope


class CdaSource(Protocol):
    def read_batch(self, s3_path: str) -> list[dict]: ...

    def manifest(self, scope: TenantScope) -> dict: ...


class LocalCdaSource:
    """File-system implementation. `root` mirrors the S3 layout; per-scope data
    lives under `<root>/<tenant>/<env>/`. Used by the simulator and, via
    LocalStack's mounted volume, by the local Lambda."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _scope_root(self, scope: TenantScope) -> Path:
        return self.root / scope.tenant_id / scope.env

    def manifest(self, scope: TenantScope) -> dict:
        p = self._scope_root(scope) / "manifest.json"
        if not p.exists():
            return {"tables": {}}
        return json.loads(p.read_text())

    def read_batch(self, s3_path: str) -> list[dict]:
        """`s3_path` here is a path relative to `root` (the simulator emits it
        that way); real S3 sources parse an s3:// URI instead."""
        import pyarrow.parquet as pq

        path = self.root / s3_path
        files = sorted(path.glob("*.parquet")) if path.is_dir() else [path]
        rows: list[dict] = []
        for f in files:
            table = pq.read_table(f)
            rows.extend(table.to_pylist())
        return rows

    def committed_timestamp(self, scope: TenantScope, table: str) -> str | None:
        return self.manifest(scope).get("tables", {}).get(table, {}).get("lastCommittedTimestamp")
