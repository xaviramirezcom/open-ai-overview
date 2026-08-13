"""Tests for access control at retrieval (own_overview.security.access).

The whole "RBAC at retrieval" story reduces to two functions:

- `build_filter(identity)` — turns a signed Identity into the permission filter
  pushed into the vector-store query. Crucially it **fails closed**: an identity
  with no roles yields a filter that matches nothing.
- `is_visible(chunk_scope, chunk_roles, flt)` — the reference predicate both
  stores share. A chunk is visible only when tenant AND env match **and** its
  acl_roles intersect the caller's roles.

A chunk in another tenant, another env, or without a matching role must NOT be
visible; a matching one must be.
"""

from __future__ import annotations

from own_overview.contracts import TenantScope
from own_overview.security.access import build_filter, is_visible
from own_overview.security.identity import dev_identity

ACME_PROD = TenantScope(tenant_id="acme", env="prod")
ACME_DEV = TenantScope(tenant_id="acme", env="dev")
GLOBEX_PROD = TenantScope(tenant_id="globex", env="prod")


def _adjuster(scope: TenantScope = ACME_PROD):
    return dev_identity("u1", scope.tenant_id, scope.env, ["adjuster"])


# ---------------------------------------------------------------------------
# The positive case
# ---------------------------------------------------------------------------


def test_matching_scope_and_role_is_visible():
    flt = build_filter(_adjuster())
    assert is_visible(ACME_PROD, frozenset({"adjuster"}), flt) is True
    # Role intersection is enough — one shared role suffices.
    assert is_visible(ACME_PROD, frozenset({"adjuster", "underwriter"}), flt) is True


# ---------------------------------------------------------------------------
# Isolation: tenant, env, role
# ---------------------------------------------------------------------------


def test_other_tenant_is_not_visible():
    flt = build_filter(_adjuster(ACME_PROD))
    assert is_visible(GLOBEX_PROD, frozenset({"adjuster"}), flt) is False


def test_other_env_is_not_visible():
    flt = build_filter(_adjuster(ACME_PROD))
    # Same tenant, wrong environment — dev must not leak into a prod caller.
    assert is_visible(ACME_DEV, frozenset({"adjuster"}), flt) is False


def test_non_matching_role_is_not_visible():
    flt = build_filter(_adjuster(ACME_PROD))
    # Right tenant + env, but an underwriter-only chunk.
    assert is_visible(ACME_PROD, frozenset({"underwriter"}), flt) is False


def test_underwriter_sees_underwriter_only_chunk():
    uw = dev_identity("u2", "acme", "prod", ["underwriter"])
    flt = build_filter(uw)
    assert is_visible(ACME_PROD, frozenset({"underwriter"}), flt) is True


# ---------------------------------------------------------------------------
# Fail-closed: no roles => nothing is visible
# ---------------------------------------------------------------------------


def test_identity_with_no_roles_fails_closed():
    no_roles = dev_identity("u3", "acme", "prod", [])
    flt = build_filter(no_roles)

    # The filter itself carries no roles (and no doc-types) — fail closed.
    assert flt.roles == frozenset()
    assert flt.doc_types == frozenset()

    # And it matches nothing, even a chunk in the caller's own tenant+env.
    assert is_visible(ACME_PROD, frozenset({"adjuster"}), flt) is False
    assert is_visible(ACME_PROD, frozenset({"underwriter"}), flt) is False


def test_chunk_with_no_roles_is_never_visible():
    # A chunk that forgot to declare acl_roles is unreachable, by construction.
    flt = build_filter(_adjuster(ACME_PROD))
    assert is_visible(ACME_PROD, frozenset(), flt) is False


# ---------------------------------------------------------------------------
# The filter preserves the caller's scope and optional doc-type narrowing
# ---------------------------------------------------------------------------


def test_filter_carries_scope_and_roles():
    flt = build_filter(_adjuster(ACME_PROD))
    assert flt.scope == ACME_PROD
    assert flt.roles == frozenset({"adjuster"})
    # No narrowing requested -> doc_types is None (match any type in scope).
    assert flt.doc_types is None


def test_doc_type_narrowing_is_recorded():
    flt = build_filter(_adjuster(ACME_PROD), doc_types={"claim"})
    assert flt.doc_types == frozenset({"claim"})
