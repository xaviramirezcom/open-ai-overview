"""Shared pytest fixtures — pin the suite to the LOCAL (zero-cloud) stack.

Every test here runs against the in-memory / open-source adapters: no AWS, no
network, no OpenSearch container. The `local_env` fixture forces the four
provider knobs to their local values and clears the `get_settings` LRU cache so
`config.build_*` returns the local adapters.

`sample_chunks` builds a small corpus that spans **two tenants**, **two
environments** and **different acl_roles**, which is exactly what the access and
pipeline tests need to prove isolation.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from own_overview.contracts import Chunk, TenantScope

# ---------------------------------------------------------------------------
# Environment — force the local providers for the whole suite.
# ---------------------------------------------------------------------------

_LOCAL_ENV = {
    "OWN_OVERVIEW_LLM_PROVIDER": "local",
    # `hash` is the zero-dependency embedder: deterministic and no torch
    # download, so the suite stays fast and hermetic.
    "OWN_OVERVIEW_EMBEDDING_PROVIDER": "hash",
    "OWN_OVERVIEW_VECTOR_STORE": "local",
    "OWN_OVERVIEW_RERANKER": "none",
    # Keep the settings self-contained: don't read a developer's real .env.
    "OWN_OVERVIEW_AWS_ENDPOINT_URL": "",
}


@pytest.fixture(autouse=True)
def local_env(monkeypatch, tmp_path):
    """Autouse: pin providers to local and reset the cached Settings.

    Runs for every test so nothing can accidentally reach for Bedrock/OpenSearch.
    The local vector store is pointed at a per-test temp file so tests are
    isolated from each other and never touch a developer's dev pickle. The cache
    is cleared before (so these env vars take effect) and after (so a later test
    starts clean).
    """
    from own_overview.config import get_settings

    for key, value in _LOCAL_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("OWN_OVERVIEW_LOCAL_STORE_PATH", str(tmp_path / "local_store.pkl"))
    monkeypatch.setenv("OWN_OVERVIEW_CDA_LOCAL_ROOT", str(tmp_path / "cda"))

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Scopes, roles and a small multi-tenant / multi-env corpus.
# ---------------------------------------------------------------------------

# Two tenants, two environments — the isolation matrix.
ACME_PROD = TenantScope(tenant_id="acme", env="prod")
ACME_DEV = TenantScope(tenant_id="acme", env="dev")
GLOBEX_PROD = TenantScope(tenant_id="globex", env="prod")


def make_chunk(
    chunk_id: str,
    scope: TenantScope,
    roles: set[str],
    text: str,
    *,
    doc_type: str = "claim",
    source_id: str | None = None,
) -> Chunk:
    """Build a Chunk with the isolation + ACL metadata the filter runs on."""
    return Chunk(
        chunk_id=chunk_id,
        doc_id=source_id or f"{doc_type}/{chunk_id}",
        scope=scope,
        doc_type=doc_type,
        source_id=source_id or f"{doc_type}/{chunk_id}",
        text=text,
        acl_roles=frozenset(roles),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


@pytest.fixture
def scopes() -> dict[str, TenantScope]:
    return {"acme_prod": ACME_PROD, "acme_dev": ACME_DEV, "globex_prod": GLOBEX_PROD}


@pytest.fixture
def sample_chunks() -> list[Chunk]:
    """A handful of chunks spanning tenants, envs and roles.

    The interesting pair lives in acme/prod: one chunk any adjuster can read and
    one **underwriter-only** chunk. The other chunks are decoys in a different
    env or a different tenant that must never surface for an acme/prod caller.
    """
    return [
        # --- acme / prod : the tenant+env under test -----------------------
        make_chunk(
            "acme-prod-adjuster",
            ACME_PROD,
            {"adjuster", "underwriter"},
            "Claim CLM-100 on policy POL-55012: water damage, paid $4,200.",
            doc_type="claim",
        ),
        make_chunk(
            "acme-prod-underwriter",
            ACME_PROD,
            {"underwriter"},
            "Underwriting memo for POL-55012: premium raised 18% after prior-loss review.",
            doc_type="underwriting",
        ),
        # --- acme / dev : same tenant, wrong environment -------------------
        make_chunk(
            "acme-dev-adjuster",
            ACME_DEV,
            {"adjuster", "underwriter"},
            "DEV fixture claim for POL-55012 — must never leak into prod answers.",
            doc_type="claim",
        ),
        # --- globex / prod : different tenant ------------------------------
        make_chunk(
            "globex-prod-adjuster",
            GLOBEX_PROD,
            {"adjuster", "underwriter"},
            "Globex claim about POL-55012 — different tenant, must stay isolated.",
            doc_type="claim",
        ),
    ]
