"""Groundedness scoring — how much of the answer is actually supported.

Groundedness (a.k.a. faithfulness) asks: is every claim in the answer backed by
the retrieved context, or did the model wander off? This module ships the cheap,
*transparent* default: a lexical-overlap heuristic. It splits the answer into
sentences and measures, per sentence, the best token overlap against any context
chunk; the score is the fraction of sentences that clear an overlap bar.

A production deploy would replace this with an LLM-as-judge or a library like
RAGAS (which prompts a model to check each claim against the sources). We keep a
lexical version as the default because it is fast, deterministic, dependency-free
and easy to reason about in an audit — and it is honest about being a proxy, not
a semantic judge. The guardrails node consumes this score to decide whether to
abstain.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from ..contracts import Retrieved

# A sentence counts as "supported" when at least this fraction of its content
# tokens also appear in some single context chunk. Tuned to be lenient enough
# for paraphrase but strict enough to catch invented claims.
_SUPPORT_OVERLAP = 0.5

_WORD = re.compile(r"[a-z0-9]+")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

# Extremely common words carry no grounding signal; ignoring them keeps a
# sentence full of stopwords from scoring "supported" for free.
_STOPWORDS = frozenset(
    """a an and are as at be by for from has have in is it its of on or that the
    this to was were will with we you your they their there here not no can""".split()
)


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS}


def _overlap(sentence_tokens: set[str], context_tokens: set[str]) -> float:
    """Fraction of the sentence's content tokens present in the context."""
    if not sentence_tokens:
        return 0.0
    return len(sentence_tokens & context_tokens) / len(sentence_tokens)


def score_groundedness(answer_text: str, reranked: Sequence[Retrieved]) -> float:
    """Return a groundedness score in ``[0, 1]``.

    The score is the fraction of the answer's (content-bearing) sentences whose
    best overlap with a single context chunk meets ``_SUPPORT_OVERLAP``. Empty
    answers and empty context both score ``0.0`` (nothing to stand on).
    """
    contexts = [_tokens(r.chunk.text) for r in reranked]
    contexts = [c for c in contexts if c]
    if not contexts:
        return 0.0

    sentences = [s for s in _SENTENCE_SPLIT.split(answer_text.strip()) if s.strip()]
    checked = 0
    supported = 0
    for sentence in sentences:
        stoks = _tokens(sentence)
        if not stoks:
            # Pure stopwords / punctuation — nothing to verify, don't count it.
            continue
        checked += 1
        best = max(_overlap(stoks, ctx) for ctx in contexts)
        if best >= _SUPPORT_OVERLAP:
            supported += 1

    if checked == 0:
        return 0.0
    return supported / checked
