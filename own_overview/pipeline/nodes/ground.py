"""ground node — the LLM answers only from the reranked passages, with citations.

This is where retrieval becomes an answer. We assemble a grounded prompt from
the top passages (numbered ``[1]…[n]``), tell the model to answer only from them
and cite every claim, and parse the ``[n]`` markers in its reply back to the
chunk + source id they point at. The result is an :class:`Answer` you can trace
to a source. Groundedness scoring and the abstain decision happen next, in the
guardrails node — this node just produces the candidate answer and its
citations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...contracts import LLM, Answer
from ...grounding.prompt import build_prompt, parse_citations
from ..state import QueryState

if TYPE_CHECKING:
    from ...config import Settings


def run(state: QueryState, settings: Settings, *, llm: LLM) -> dict:
    question = state.get("question", "")
    reranked = state.get("reranked", [])

    system, prompt = build_prompt(question, reranked)
    text = llm.complete(system, prompt)
    citations = parse_citations(text, reranked)

    answer = Answer(text=text, citations=citations)

    trace = list(state.get("trace", []))
    trace.append(
        {
            "node": "ground",
            "n_context": len(reranked),
            "n_citations": len(citations),
            "cited_chunk_ids": [c.chunk_id for c in citations],
        }
    )
    return {"answer": answer, "trace": trace}
