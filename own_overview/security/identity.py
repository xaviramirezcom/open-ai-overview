"""Resolve a caller's Identity from a signed token.

In production this verifies a JWT (issuer, signature, expiry) and reads tenant,
environment and roles from trusted claims. Here we keep the shape and a dev
decoder so the CLI/notebooks can pass an identity without an auth server.

The rule that matters: Identity comes from the *token*, never from the prompt
or a request body the user controls.
"""

from __future__ import annotations

from ..contracts import Identity, TenantScope


def identity_from_claims(claims: dict) -> Identity:
    """Build an Identity from already-verified token claims."""
    return Identity(
        user_id=claims["sub"],
        scope=TenantScope(tenant_id=claims["tenant_id"], env=claims["env"]),
        roles=frozenset(claims.get("roles", [])),
    )


def dev_identity(user_id: str, tenant_id: str, env: str, roles: list[str]) -> Identity:
    """Convenience for local runs / tests. Do not use in production."""
    return Identity(
        user_id=user_id,
        scope=TenantScope(tenant_id=tenant_id, env=env),
        roles=frozenset(roles),
    )
