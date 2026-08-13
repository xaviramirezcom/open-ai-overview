"""Guardrails — PII redaction and a prompt-injection screen.

Two cheap, transparent defenses used by the guardrails node:

* :func:`redact_pii` — regex-masks the obvious direct identifiers (email, phone,
  SSN-like) before an answer leaves the system. It is a last line of defense,
  not the whole data-privacy story (the real isolation happens at retrieval via
  the ACL filter), but masking any PII that slips into generated text is a
  requirement in a regulated (CCPA/GDPR) domain.
* :func:`screen_injection` — flags retrieved context that tries to hijack the
  model ("ignore previous instructions", "you are now…"). Because we retrieve
  from company data that may include user-authored fields, a malicious record
  could carry an injection payload; we screen the *context*, not the user's
  question.

Both are deliberately simple and readable. A production system would layer a
trained classifier or a managed service on top; these regex screens are the
honest, dependency-free baseline that already catches the common cases.
"""

from __future__ import annotations

import re

# --- PII patterns ----------------------------------------------------------
# Intentionally conservative: match clear identifiers, accept some misses over
# mangling ordinary text.
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# US-style SSN: 3-2-4 digits. Screened before phones so it is not partly eaten.
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
# Phone: optional +country, then 10 digits with common separators.
_PHONE = re.compile(r"\b(?:\+?\d{1,3}[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}\b")

# --- Injection patterns ----------------------------------------------------
# Obvious instruction-override phrasings. Matched case-insensitively against the
# retrieved text, not the user's own question.
_INJECTION = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"ignore (?:all |any |the )?(?:previous|prior|above|earlier) "
        r"(?:instructions|prompts|context)",
        r"disregard (?:all |any |the )?(?:previous|prior|above) (?:instructions|prompts)",
        r"forget (?:everything|all previous|the above)",
        r"you are now\b",
        r"new instructions?\s*:",
        r"system prompt\b",
        r"reveal (?:your|the) (?:system )?(?:prompt|instructions)",
        r"override (?:your|the) (?:previous |prior )?(?:instructions|rules)",
    )
]


def redact_pii(text: str) -> str:
    """Mask emails, SSN-like numbers and phone numbers in ``text``.

    Order matters: SSNs are masked before phone numbers so a 9-digit SSN is not
    partially consumed by the phone pattern.
    """
    text = _EMAIL.sub("[redacted-email]", text)
    text = _SSN.sub("[redacted-ssn]", text)
    text = _PHONE.sub("[redacted-phone]", text)
    return text


def screen_injection(text: str) -> bool:
    """Return ``True`` if ``text`` looks like a prompt-injection payload."""
    return any(pat.search(text) for pat in _INJECTION)
