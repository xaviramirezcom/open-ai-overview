"""Bedrock (Anthropic Claude) generation adapter — the default LLM.

Generates the grounded answer by calling **Amazon Bedrock**'s Anthropic Claude
model over `bedrock-runtime.invoke_model`. Bedrock speaks the Anthropic
**Messages API** shape (`anthropic_version`, `system`, `messages`, `max_tokens`);
that JSON is assembled below.

The client honors `settings.aws_endpoint_url` (LocalStack / real AWS), and
`boto3` is imported lazily inside `__init__` so importing this module never
forces the AWS SDK on a local-only install.
"""

from __future__ import annotations

import json

from ..config import Settings

# Bedrock pins the Anthropic wire format with this constant (not a model date).
_ANTHROPIC_VERSION = "bedrock-2023-05-31"
_DEFAULT_MAX_TOKENS = 1024


class BedrockLLM:
    """LLM backed by Anthropic Claude on Bedrock.

    Implements the `contracts.LLM` Protocol (`complete(system, prompt) -> str`).
    """

    def __init__(self, settings: Settings) -> None:
        import boto3

        self.settings = settings
        self.model_id = settings.bedrock_llm_model_id
        self.max_tokens = _DEFAULT_MAX_TOKENS

        client_kwargs: dict = {"region_name": settings.aws_region}
        if settings.aws_endpoint_url:
            client_kwargs["endpoint_url"] = settings.aws_endpoint_url
        self._client = boto3.client("bedrock-runtime", **client_kwargs)

    # -- LLM Protocol ------------------------------------------------------

    def complete(self, system: str, prompt: str) -> str:
        """Send a single grounded turn and return the model's text.

        `system` carries the grounding rules ("answer only from the passages,
        cite every claim"); `prompt` carries the question + retrieved context.
        """
        body = {
            "anthropic_version": _ANTHROPIC_VERSION,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
        }
        response = self._client.invoke_model(
            modelId=self.model_id,
            accept="application/json",
            contentType="application/json",
            body=json.dumps(body),
        )
        payload = json.loads(response["body"].read())
        # Messages API returns a list of content blocks; concatenate the text ones.
        parts = [
            block.get("text", "")
            for block in payload.get("content", [])
            if block.get("type") == "text"
        ]
        return "".join(parts).strip()
