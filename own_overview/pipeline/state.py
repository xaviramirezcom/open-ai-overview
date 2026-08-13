"""The state object that flows through the LangGraph query pipeline.

Each node reads and adds to this. Keeping it one typed dict makes the graph
inspectable — you can dump the state after any node to see exactly what the
model was given, which is the backbone of the grounding + audit story.
"""

from __future__ import annotations

from typing import TypedDict

from ..contracts import Answer, Identity, Retrieved


class QueryState(TypedDict, total=False):
    # inputs
    question: str
    identity: Identity

    # produced by nodes, in order
    sub_queries: list[str]  # fan-out
    candidates: list[Retrieved]  # retrieve (filtered)
    reranked: list[Retrieved]  # rerank
    answer: Answer  # ground + guardrails

    # observability
    trace: list[dict]  # per-node record for the audit log
