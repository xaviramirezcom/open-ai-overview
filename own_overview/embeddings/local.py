"""Local, zero-cloud embedding adapter — the `PROVIDER=local` fallback.

Embeds text with a small open-source **sentence-transformers** model
(`settings.local_embedding_model`, default `BAAI/bge-small-en-v1.5`, 384-dim) so
the whole pipeline runs offline after a `git clone`, with no AWS credentials.

`sentence_transformers` is an *optional* extra (`pip install
own-overview[local]`) and is imported lazily on first use — importing this
module, or `config`, never pulls in torch on a Bedrock-only install. Embeddings
are L2-normalized, so a plain dot product equals cosine similarity downstream.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..config import Settings


class LocalEmbedder:
    """Embedder backed by an in-process sentence-transformers model.

    Implements the `contracts.Embedder` Protocol.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.dim: int = settings.local_embedding_dim
        self._model_name = settings.local_embedding_model
        # The model is loaded lazily (it downloads weights + imports torch), so
        # constructing the adapter — e.g. inside `config.build_embedder` — stays
        # cheap and side-effect free until an embedding is actually requested.
        self._model = None

    def _ensure_model(self) -> Any:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self._model_name)
        return self._model

    # -- Embedder Protocol -------------------------------------------------

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        model = self._ensure_model()
        vectors = model.encode(
            list(texts),
            normalize_embeddings=True,  # unit vectors => dot product == cosine
            convert_to_numpy=True,
        )
        return [[float(x) for x in row] for row in vectors]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]
