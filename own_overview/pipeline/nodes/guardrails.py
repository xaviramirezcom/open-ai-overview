"""guardrails node — score, screen, redact, and abstain when the answer is thin.

The evals that gate a deploy also run inline as the last check before an answer
is returned:

1. **Injection screen** over the retrieved context. If a passage tries to hijack
   the model ("ignore previous instructions…"), we don't trust an answer built
   on it — abstain with a safe message.
2. **Groundedness score** of the answer against its context. Below
   ``settings.groundedness_threshold`` the answer is too unsupported to ship —
   abstain rather than risk a confident hallucination.
3. **PII redaction** on whatever text we actually emit, as a last line of
   defense against a direct identifier leaking into the output.

Abstaining is a feature, not a failure: in a regulated domain, "I don't have
enough grounded information to answer" beats a plausible wrong answer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...contracts import Answer
from ...evals.groundedness import score_groundedness
from ...evals.guardrails import redact_pii, screen_injection
from ..state import QueryState

if TYPE_CHECKING:
    from ...config import Settings

_ABSTAIN_MESSAGE = (
    "I don't have enough grounded information in the retrieved records to answer "
    "this reliably, so I'm not going to guess."
)


def run(state: QueryState, settings: Settings) -> dict:
    answer: Answer | None = state.get("answer")
    reranked = state.get("reranked", [])

    if answer is None:
        # Nothing to guard — produce an explicit abstention so downstream
        # (audit) still has an Answer to record.
        answer = Answer(text=_ABSTAIN_MESSAGE, citations=[], groundedness=0.0, abstained=True)
        trace = list(state.get("trace", []))
        trace.append({"node": "guardrails", "abstained": True, "reason": "no_answer"})
        return {"answer": answer, "trace": trace}

    # 1. Screen the *context* (not the user's question) for injection payloads.
    injection = any(screen_injection(r.chunk.text) for r in reranked)

    # 2. Score groundedness of the model's answer against its context.
    score = score_groundedness(answer.text, reranked)
    answer.groundedness = score

    reason = None
    if injection:
        answer.abstained = True
        reason = "injection_in_context"
    elif score < settings.groundedness_threshold:
        answer.abstained = True
        reason = "low_groundedness"

    if answer.abstained:
        # Drop the ungrounded / tainted text and its citations.
        answer.text = _ABSTAIN_MESSAGE
        answer.citations = []

    # 3. Redact PII from whatever we are about to emit (safe message included).
    answer.text = redact_pii(answer.text)

    trace = list(state.get("trace", []))
    trace.append(
        {
            "node": "guardrails",
            "groundedness": score,
            "threshold": settings.groundedness_threshold,
            "injection_in_context": injection,
            "abstained": answer.abstained,
            "reason": reason,
        }
    )
    return {"answer": answer, "trace": trace}
