"""fan_out node — one question becomes a small set of sub-queries.

Fanning out a query catches passages that phrase the same idea differently: the
user asks "why was this claim denied?", but the record says "coverage
determination". Retrieving on several phrasings and merging the hits lifts
recall before rerank trims back to the best few.

The spine uses a cheap, deterministic heuristic expansion — no LLM call — so the
graph runs offline and fast; an LLM-driven rewrite is a drop-in swap later. We
keep 2–4 sub-queries: always the original question, plus a keyword-only variant
and an intent-oriented rephrase when they add something.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..state import QueryState

if TYPE_CHECKING:
    from ...config import Settings

# Lightweight stopword list for the keyword-only variant.
_STOPWORDS = frozenset(
    """a an and are as at be by for from how what when where which who why is it
    its of on or that the this to was were will with do does did can could would
    should about into over under our your their my me i you we they them""".split()
)

_WORD = re.compile(r"[A-Za-z0-9]+")


def _keywords(question: str) -> str:
    """Drop question/stop words, leaving the content terms."""
    words = _WORD.findall(question)
    kept = [w for w in words if w.lower() not in _STOPWORDS]
    return " ".join(kept)


def _expand(question: str) -> list[str]:
    question = question.strip()
    if not question:
        return []

    variants = [question]

    keywords = _keywords(question)
    if keywords and keywords.lower() != question.lower():
        variants.append(keywords)

    # An intent-oriented rephrase nudges retrieval toward explanatory passages.
    if keywords:
        variants.append(f"details and explanation about {keywords}")

    # Dedup preserving order, cap at 4.
    seen: set[str] = set()
    out: list[str] = []
    for v in variants:
        key = v.lower()
        if key not in seen:
            seen.add(key)
            out.append(v)
    return out[:4]


def run(state: QueryState, settings: Settings) -> dict:
    question = (state.get("question") or "").strip()
    sub_queries = _expand(question)

    trace = list(state.get("trace", []))
    trace.append({"node": "fan_out", "question": question, "sub_queries": sub_queries})
    return {"sub_queries": sub_queries, "trace": trace}
