"""Bedrock (Amazon Titan) embedding adapter — the default, Guidewire-shaped path.

Turns text into dense vectors by calling **Amazon Bedrock**'s Titan Text
Embeddings v2 model over `bedrock-runtime.invoke_model`. Titan embeds one text
per call (no server-side batching), so `embed_documents` loops; the request /
response shapes below are Titan v2's.

The client honors `settings.aws_endpoint_url` so the exact same code runs
against **LocalStack** (`endpoint_url=http://localhost:4566`) or real AWS. `boto3`
is imported lazily inside `__init__` so that merely importing this module — or
`config` — never forces the AWS SDK on a pure-local install.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from ..config import Settings


class BedrockEmbedder:
    """Embedder backed by Bedrock Titan Text Embeddings v2.

    Implements the `contracts.Embedder` Protocol (`dim`, `embed_documents`,
    `embed_query`).
    """

    def __init__(self, settings: Settings) -> None:
        # Lazy import: keeps `import config` cheap and avoids requiring boto3 in
        # environments that only ever run the local provider.
        import boto3

        self.settings = settings
        self.model_id = settings.bedrock_embedding_model_id
        # Advertised vector width; must match the vector store's index mapping.
        self.dim: int = settings.bedrock_embedding_dim

        client_kwargs: dict = {"region_name": settings.aws_region}
        if settings.aws_endpoint_url:
            client_kwargs["endpoint_url"] = settings.aws_endpoint_url
        self._client = boto3.client("bedrock-runtime", **client_kwargs)

    # -- internal ----------------------------------------------------------

    def _embed_one(self, text: str) -> list[float]:
        """Invoke Titan for a single string and return its embedding."""
        body: dict = {"inputText": text}
        # `dimensions` + `normalize` are Titan **v2** knobs; only send them for
        # v2 so a v1 model id (which rejects them) still works.
        if "v2" in self.model_id:
            body["dimensions"] = self.dim
            body["normalize"] = True

        response = self._client.invoke_model(
            modelId=self.model_id,
            accept="application/json",
            contentType="application/json",
            body=json.dumps(body),
        )
        payload = json.loads(response["body"].read())
        return [float(x) for x in payload["embedding"]]

    # -- Embedder Protocol -------------------------------------------------

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed_one(text)
