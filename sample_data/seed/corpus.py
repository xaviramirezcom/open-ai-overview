"""The canonical synthetic insurance corpus — deterministic, readable, small.

`simulator.py` imports this and writes it out in *true* Guidewire CDA layout so
the demo (and the blog series) always shows the same records and the same
numbers. Nothing here is random; edit the records and the whole pipeline moves
with them.

What the corpus is built to demonstrate
---------------------------------------
- **A premium change** on policy ``POL-55012`` (insert at $4,200 → update to
  $5,100). ``merge_rows`` collapses the two CDA rows to the latest state.
- **A claim** (``88431``, water damage) linked to that policy, also updated.
- **A restricted underwriting memo** (``UW-55012``) that *explains why* the
  premium went up — visible to underwriters only. This is the whole "access
  control at retrieval" story: an adjuster asking "why did the premium go up?"
  cannot retrieve this memo; an underwriter can.
- **A DELETE tombstone** (``POL-55090`` cancelled) so the ingestion path
  exercises ``VectorStore.delete_document``.
- **Two tenants** (``acme``, ``globex``) and **two environments**
  (``prod``, ``dev``) so tenant/env isolation is demonstrable — the same query
  under a different scope returns different data, and dev never leaks into prod.

Access-control vocabulary (roles)
---------------------------------
``adjuster``, ``underwriter``, ``agent``, ``admin``, ``billing``. Each record
carries the roles allowed to retrieve it (``acl`` below); the simulator writes
that onto every row so the ACL travels *with the data*, and retrieval filters on
it. Underwriting is the deliberately restricted set.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

# ---------------------------------------------------------------------------
# Role sets (ACLs) — who may retrieve a given table's records.
# ---------------------------------------------------------------------------

ACL_POLICY = ["adjuster", "underwriter", "agent", "admin"]
ACL_CLAIM = ["adjuster", "underwriter", "admin"]
ACL_UNDERWRITING = ["underwriter", "admin"]  # <-- deliberately restricted
ACL_BILLING = ["billing", "adjuster", "admin"]


def _ts(year: int, month: int, day: int) -> datetime:
    """A fixed, timezone-aware timestamp (UTC). Deterministic across runs."""
    return datetime(year, month, day, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Corpus data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Change:
    """One CDA change row for a record.

    ``op`` is the CDA operation (``I`` insert / ``U`` update / ``D`` delete);
    ``seq`` is a monotonic ordering key within the table batch (the simulator
    renders it to ``gwcbi___seqval_hex``). ``fields`` are the business columns.
    """

    op: str
    seq: int
    fields: dict


@dataclass(frozen=True)
class TableData:
    """All change rows for one ``(tenant, env, table)`` batch, plus the ACL that
    applies to the records in it."""

    tenant: str
    env: str
    table: str
    acl: list[str]
    changes: list[Change] = field(default_factory=list)


# ---------------------------------------------------------------------------
# The records. Grouped by tenant / env / table for readability.
# ---------------------------------------------------------------------------

# --- Acme Insurance · prod -------------------------------------------------

_ACME_PROD_POLICY = TableData(
    tenant="acme",
    env="prod",
    table="policy",
    acl=ACL_POLICY,
    changes=[
        # POL-55012: bound as new business, then endorsed (premium goes up).
        Change(
            op="I",
            seq=1001,
            fields={
                "PolicyNumber": "POL-55012",
                "AccountName": "Rivera Logistics LLC",
                "ProductType": "Commercial Auto",
                "State": "CA",
                "EffectiveDate": "2026-01-01",
                "PremiumAmount": 4200.0,
                "Status": "Bound",
                "ChangeReason": "New business",
                "UpdateTime": _ts(2026, 1, 5),
            },
        ),
        Change(
            op="U",
            seq=1005,
            fields={
                "PolicyNumber": "POL-55012",
                "AccountName": "Rivera Logistics LLC",
                "ProductType": "Commercial Auto",
                "State": "CA",
                "EffectiveDate": "2026-01-01",
                "PremiumAmount": 5100.0,
                "Status": "Bound",
                "ChangeReason": (
                    "Endorsement: added 3 vehicles and applied a prior at-fault loss surcharge"
                ),
                "UpdateTime": _ts(2026, 2, 10),
            },
        ),
        # POL-55090: written, then cancelled -> DELETE tombstone.
        Change(
            op="I",
            seq=1002,
            fields={
                "PolicyNumber": "POL-55090",
                "AccountName": "Cortez Bakery",
                "ProductType": "Businessowners (BOP)",
                "State": "TX",
                "EffectiveDate": "2026-01-01",
                "PremiumAmount": 1800.0,
                "Status": "Bound",
                "ChangeReason": "New business",
                "UpdateTime": _ts(2026, 1, 6),
            },
        ),
        Change(
            op="D",
            seq=1008,
            fields={
                "PolicyNumber": "POL-55090",
                "Status": "Cancelled",
                "ChangeReason": "Flat-cancelled at insured request",
                "UpdateTime": _ts(2026, 2, 1),
            },
        ),
    ],
)

_ACME_PROD_CLAIM = TableData(
    tenant="acme",
    env="prod",
    table="claim",
    acl=ACL_CLAIM,
    changes=[
        Change(
            op="I",
            seq=2001,
            fields={
                "ClaimNumber": "88431",
                "PolicyNumber": "POL-55012",
                "LossType": "Water damage",
                "LossDate": "2026-02-15",
                "Description": (
                    "Burst supply-line pipe flooded the ground-floor warehouse "
                    "overnight; damaged palletized inventory and drywall."
                ),
                "ReserveAmount": 38000.0,
                "Status": "Open",
                "UpdateTime": _ts(2026, 2, 16),
            },
        ),
        Change(
            op="U",
            seq=2004,
            fields={
                "ClaimNumber": "88431",
                "PolicyNumber": "POL-55012",
                "LossType": "Water damage",
                "LossDate": "2026-02-15",
                "Description": (
                    "Adjuster site visit confirmed inventory loss and added "
                    "forklift water damage; reserve increased."
                ),
                "ReserveAmount": 52000.0,
                "Status": "Open",
                "UpdateTime": _ts(2026, 2, 20),
            },
        ),
    ],
)

_ACME_PROD_UNDERWRITING = TableData(
    tenant="acme",
    env="prod",
    table="underwriting",
    acl=ACL_UNDERWRITING,  # restricted: underwriter + admin only
    changes=[
        Change(
            op="I",
            seq=3001,
            fields={
                "MemoNumber": "UW-55012",
                "PolicyNumber": "POL-55012",
                "AccountName": "Rivera Logistics LLC",
                "RiskScore": 78,
                "Recommendation": "Surcharge and monitor",
                "Notes": (
                    "Premium raised to $5,100 after a prior at-fault collision "
                    "loss on the newly added vehicles and a territory re-rate "
                    "into the high-theft LA basin. Recommend a telematics "
                    "condition at renewal to re-evaluate the surcharge."
                ),
                "Author": "M. Okafor, Senior Underwriter",
                "UpdateTime": _ts(2026, 2, 9),
            },
        ),
    ],
)

_ACME_PROD_BILLING = TableData(
    tenant="acme",
    env="prod",
    table="billing",
    acl=ACL_BILLING,
    changes=[
        Change(
            op="I",
            seq=4001,
            fields={
                "InvoiceNumber": "INV-55012-02",
                "PolicyNumber": "POL-55012",
                "AmountDue": 5100.0,
                "DueDate": "2026-03-01",
                "Status": "Sent",
                "UpdateTime": _ts(2026, 2, 11),
            },
        ),
    ],
)

# --- Acme Insurance · dev (env isolation: different data than prod) --------

_ACME_DEV_POLICY = TableData(
    tenant="acme",
    env="dev",
    table="policy",
    acl=ACL_POLICY,
    changes=[
        Change(
            op="I",
            seq=1001,
            fields={
                "PolicyNumber": "POL-DEV-001",
                "AccountName": "Sandbox Test Account",
                "ProductType": "Commercial Auto",
                "State": "NY",
                "EffectiveDate": "2026-01-01",
                "PremiumAmount": 999.0,
                "Status": "Draft",
                "ChangeReason": "Synthetic record for the dev planet",
                "UpdateTime": _ts(2026, 1, 3),
            },
        ),
    ],
)

# --- Globex Mutual · prod (second tenant: tenant isolation) ----------------

_GLOBEX_PROD_POLICY = TableData(
    tenant="globex",
    env="prod",
    table="policy",
    acl=ACL_POLICY,
    changes=[
        Change(
            op="I",
            seq=1001,
            fields={
                "PolicyNumber": "POL-70233",
                "AccountName": "Globex Manufacturing Inc.",
                "ProductType": "Workers' Compensation",
                "State": "IL",
                "EffectiveDate": "2026-01-15",
                "PremiumAmount": 12750.0,
                "Status": "Bound",
                "ChangeReason": "New business",
                "UpdateTime": _ts(2026, 1, 20),
            },
        ),
    ],
)

_GLOBEX_PROD_CLAIM = TableData(
    tenant="globex",
    env="prod",
    table="claim",
    acl=ACL_CLAIM,
    changes=[
        Change(
            op="I",
            seq=2001,
            fields={
                "ClaimNumber": "90114",
                "PolicyNumber": "POL-70233",
                "LossType": "Employee injury",
                "LossDate": "2026-02-02",
                "Description": (
                    "Line worker strained back lifting a die; lost-time claim, "
                    "light duty expected within two weeks."
                ),
                "ReserveAmount": 15500.0,
                "Status": "Open",
                "UpdateTime": _ts(2026, 2, 4),
            },
        ),
    ],
)


# ---------------------------------------------------------------------------
# The full corpus, in a stable order.
# ---------------------------------------------------------------------------

CORPUS: list[TableData] = [
    _ACME_PROD_POLICY,
    _ACME_PROD_CLAIM,
    _ACME_PROD_UNDERWRITING,
    _ACME_PROD_BILLING,
    _ACME_DEV_POLICY,
    _GLOBEX_PROD_POLICY,
    _GLOBEX_PROD_CLAIM,
]


def tenants() -> list[str]:
    """Distinct tenant ids in the corpus, in first-seen order."""
    seen: list[str] = []
    for td in CORPUS:
        if td.tenant not in seen:
            seen.append(td.tenant)
    return seen


def envs() -> list[str]:
    """Distinct environments in the corpus, in first-seen order."""
    seen: list[str] = []
    for td in CORPUS:
        if td.env not in seen:
            seen.append(td.env)
    return seen
