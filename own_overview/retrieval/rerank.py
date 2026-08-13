"""Rerankers — re-score retrieved candidates for relevance before grounding.

Dense retrieval is recall-oriented; a reranker sharpens *precision* by scoring
each candidate against the query with a cross-encoder, then keeping the top-k
that fit the context budget. Two adapters, both implementing `contracts.Reranker`:

- `BedrockReranker` — Amazon Bedrock's **Rerank API** (`bedrock-agent-runtime`)
  on `settings.bedrock_rerank_model_id`. It **falls back gracefully**: any client
  or API error degrades to the original order (`candidates[:k]`) instead of
  breaking the pipeline, since rerank is a quality boost, not a correctness gate.
- `NoopReranker` — the local / `reranker=none` passthrough: return `candidates[:k]`
  unchanged.

`boto3` is imported lazily so importing this module never forces the AWS SDK.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from ..config import Settings
from ..contracts import Retrieved

logger = logging.getLogger(__name__)


class NoopReranker:
    """Passthrough reranker — keeps retrieval order, trims to k."""

    def rerank(self, query: str, candidates: Sequence[Retrieved], k: int) -> list[Retrieved]:  # noqa: ARG002
        return list(candidates[:k])


class BedrockReranker:
    """Reranker backed by the Bedrock Rerank API, with a graceful fallback."""

    def __init__(self, settings: Settings) -> None:  # pragma: no cover - needs live Bedrock
        import boto3

        self.settings = settings
        self.model_id = settings.bedrock_rerank_model_id
        # The Rerank API wants a model ARN; accept a bare model id and expand it.
        self.model_arn = (
            self.model_id
            if self.model_id.startswith("arn:")
            else f"arn:aws:bedrock:{settings.aws_region}::foundation-model/{self.model_id}"
        )

        client_kwargs: dict = {"region_name": settings.aws_region}
        if settings.aws_endpoint_url:
            client_kwargs["endpoint_url"] = settings.aws_endpoint_url
        # Rerank lives on the agent-runtime client, not bedrock-runtime.
        self._client = boto3.client("bedrock-agent-runtime", **client_kwargs)

    def rerank(  # pragma: no cover - needs live Bedrock
        self, query: str, candidates: Sequence[Retrieved], k: int
    ) -> list[Retrieved]:
        candidates = list(candidates)
        if not candidates:
            return []
        try:
            return self._rerank_via_bedrock(query, candidates, k)
        except Exception as exc:  # noqa: BLE001 - rerank is best-effort, never fatal
            logger.warning("Bedrock rerank failed (%s); falling back to retrieval order.", exc)
            return candidates[:k]

    def _rerank_via_bedrock(  # pragma: no cover - needs live Bedrock
        self, query: str, candidates: list[Retrieved], k: int
    ) -> list[Retrieved]:
        sources = [
            {
                "type": "INLINE",
                "inlineDocumentSource": {
                    "type": "TEXT",
                    "textDocument": {"text": c.chunk.text},
                },
            }
            for c in candidates
        ]
        response = self._client.rerank(
            queries=[{"type": "TEXT", "textQuery": {"text": query}}],
            sources=sources,
            rerankingConfiguration={
                "type": "BEDROCK_RERANKING_MODEL",
                "bedrockRerankingConfiguration": {
                    "numberOfResults": min(k, len(candidates)),
                    "modelConfiguration": {"modelArn": self.model_arn},
                },
            },
        )
        # results: [{"index": <into sources>, "relevanceScore": <float>}, ...]
        reranked: list[Retrieved] = []
        for result in response.get("results", []):
            original = candidates[result["index"]]
            reranked.append(Retrieved(chunk=original.chunk, score=float(result["relevanceScore"])))
        return reranked[:k]
