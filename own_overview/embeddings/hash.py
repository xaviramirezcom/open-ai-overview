"""A deterministic, dependency-free embedder for instant clone-and-run.

`HashEmbedder` hashes word and character n-grams into a fixed-width vector and
L2-normalizes it — a classic "hashing vectorizer". It captures lexical overlap
(shared words/roots between a question and a passage), which is enough to make
the local demo retrieve the right documents with **no model download**.

It is NOT a semantic embedder — use `EMBEDDING_PROVIDER=local` (sentence-
transformers) or `bedrock` (Titan) for real semantic retrieval. This exists so
`own-overview seed && own-overview query ...` works seconds after `git clone`.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import Settings

_TOKEN = re.compile(r"[a-z0-9]+")


class HashEmbedder:
    """Implements `contracts.Embedder` with pure-Python hashing (no numpy/torch)."""

    def __init__(self, settings: Settings) -> None:
        self.dim = int(getattr(settings, "hash_embedding_dim", 256))

    # -- Embedder Protocol -------------------------------------------------

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    # -- internals ---------------------------------------------------------

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for tok in _TOKEN.findall(text.lower()):
            self._add(vec, tok, 1.0)
            # character trigrams give partial-match robustness (POL-55012 ~ 55012)
            padded = f"#{tok}#"
            for i in range(len(padded) - 2):
                self._add(vec, padded[i : i + 3], 0.5)
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            return vec
        return [v / norm for v in vec]

    def _add(self, vec: list[float], feature: str, weight: float) -> None:
        # Stable hash (Python's hash() is salted per-process) → use a simple FNV.
        h = 2166136261
        for ch in feature:
            h = (h ^ ord(ch)) * 16777619 & 0xFFFFFFFF
        idx = h % self.dim
        sign = 1.0 if (h >> 1) & 1 else -1.0
        vec[idx] += sign * weight
