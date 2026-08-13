"""Runtime configuration and component factories.

`Settings` is read from environment / `.env` (see `.env.example`). The
`build_*` factories return the concrete adapter for the configured provider,
so the rest of the code depends only on the Protocols in `contracts.py`.

Default stack (Guidewire-shaped): AWS Bedrock (Claude + Titan embeddings) and
OpenSearch. Every provider also has a zero-setup local fallback so the repo
runs offline with `PROVIDER=local`.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

from .contracts import LLM, Chunker, Embedder, Reranker, VectorStore

Provider = Literal["bedrock", "local", "hash"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OWN_OVERVIEW_", env_file=".env", extra="ignore")

    # --- provider selection ------------------------------------------------
    llm_provider: Provider = "bedrock"
    embedding_provider: Provider = "bedrock"
    vector_store: Literal["opensearch", "local"] = "opensearch"
    reranker: Literal["bedrock", "none"] = "bedrock"

    # --- AWS / Bedrock -----------------------------------------------------
    aws_region: str = "us-east-1"
    # LocalStack / OpenSearch-container endpoints; empty => real AWS.
    aws_endpoint_url: str | None = None
    bedrock_llm_model_id: str = "anthropic.claude-3-5-sonnet-20241022-v2:0"
    bedrock_embedding_model_id: str = "amazon.titan-embed-text-v2:0"
    bedrock_embedding_dim: int = 1024
    bedrock_rerank_model_id: str = "amazon.rerank-v1:0"

    # --- local fallbacks ---------------------------------------------------
    local_embedding_model: str = "BAAI/bge-small-en-v1.5"
    local_embedding_dim: int = 384
    # Zero-dependency hashing embedder (instant clone-and-run demo).
    hash_embedding_dim: int = 256
    # Where the local vector store persists between `seed` and `query` processes.
    local_store_path: str = ".own_overview/local_store.pkl"

    # --- OpenSearch --------------------------------------------------------
    opensearch_host: str = "localhost"
    opensearch_port: int = 9200
    opensearch_use_ssl: bool = False
    opensearch_index_prefix: str = "own_overview"

    # --- CDA ingestion -----------------------------------------------------
    # S3 bucket where CDA lands Parquet + manifest.json (or a local path when
    # using the file-based simulator).
    cda_bucket: str = "own-overview-cda"
    cda_local_root: str = "sample_data/cda"
    cda_output_mode: Literal["merged", "raw"] = "merged"

    # --- retrieval / generation knobs -------------------------------------
    retrieve_k: int = 12
    rerank_k: int = 5
    groundedness_threshold: float = 0.6


@lru_cache
def get_settings() -> Settings:
    return Settings()


# --- factories -------------------------------------------------------------
# Imported lazily so that, e.g., running the local stack does not require boto3
# to import cleanly, and vice-versa.


def build_embedder(s: Settings | None = None) -> Embedder:
    s = s or get_settings()
    if s.embedding_provider == "bedrock":
        from .embeddings.bedrock import BedrockEmbedder

        return BedrockEmbedder(s)
    if s.embedding_provider == "hash":
        from .embeddings.hash import HashEmbedder

        return HashEmbedder(s)
    from .embeddings.local import LocalEmbedder

    return LocalEmbedder(s)


def build_llm(s: Settings | None = None) -> LLM:
    s = s or get_settings()
    if s.llm_provider == "bedrock":
        from .llm.bedrock import BedrockLLM

        return BedrockLLM(s)
    from .llm.local import EchoLLM

    return EchoLLM(s)


def build_vector_store(
    s: Settings | None = None, *, embedder: Embedder | None = None
) -> VectorStore:
    s = s or get_settings()
    if s.vector_store == "opensearch":
        from .vectorstore.opensearch_store import OpenSearchStore

        return OpenSearchStore(s)
    from .vectorstore.local_store import LocalVectorStore

    return LocalVectorStore(s, embedder=embedder or build_embedder(s))


def build_reranker(s: Settings | None = None) -> Reranker:
    s = s or get_settings()
    if s.reranker == "bedrock":
        from .retrieval.rerank import BedrockReranker

        return BedrockReranker(s)
    from .retrieval.rerank import NoopReranker

    return NoopReranker()


def build_chunker(s: Settings | None = None) -> Chunker:
    s = s or get_settings()
    from .pipeline.nodes.chunk import NaiveChunker

    return NaiveChunker()
