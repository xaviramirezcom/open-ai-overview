---
description: own-overview conventions — ports & adapters (hexagonal) RAG pipeline
paths:
  - "own_overview/**"
---

# own-overview conventions (ports & adapters)

Full design: `docs/architecture.md`. These load whenever you touch
`own_overview/`.

## The dependency rule (enforced)

The **core** never depends on adapters, the composition root, or heavy
libraries. This is enforced by `import-linter` (`.importlinter`) via
`lint-imports` in `/verify`, CI, and pre-commit — a violation fails the build.

- **`contracts.py` is a pure leaf** — the domain types and the component
  Protocols (`Embedder`, `VectorStore`, `Reranker`, `LLM`, `Chunker`). It
  imports only the stdlib. Nothing framework-specific, no other `own_overview`
  module.
- **Domain logic is framework-free** — `security/` (the retrieval filter +
  identity), `grounding/` (prompt + citation parsing), `evals/` (groundedness,
  guardrails) import only `contracts` + stdlib. No langgraph, boto3, opensearch,
  no `config`.
- **Pipeline nodes depend on ports, not adapters.** `pipeline/nodes/*` receive
  components (embedder, store, reranker, llm) **injected** and take `Settings`
  as a parameter — they never import `config` or a concrete adapter
  (`embeddings/`, `llm/`, `vectorstore/`, `retrieval/`).
- **Adapters** (`embeddings/`, `llm/`, `vectorstore/`, `retrieval/`) implement a
  Protocol from `contracts.py`; heavy deps (boto3, opensearch-py, sentence-
  transformers, numpy) are imported **lazily** inside methods so importing one
  adapter never forces another's dependency.
- **`config.py` is the composition root** — the only module that imports
  concrete adapters, via the `build_*` factories. `pipeline/graph.py` wires the
  graph from those factories.

## Rules

- **Isolation is first class.** Every `Document`, `Chunk` and query carries a
  `TenantScope` (tenant + env). Retrieval always filters on it — never build a
  query without one. Access control (`security/access.py`) is compiled into the
  query, fail-closed (no roles → nothing). Do not filter after generation.
- **CDA is CDC.** Ingestion merges insert/update/delete rows to latest state and
  **evicts on DELETE tombstones** — a removed record must leave the index.
- **Type everything.** No untyped public functions; `mypy own_overview` must
  pass. Heavy libs without stubs are listed under `[[tool.mypy.overrides]]`.
- **Tests are a contract.** A failing test means the code is wrong. Never add a
  `skip`/`xfail`, loosen an assertion, add `# type: ignore` to silence an error,
  or lower the coverage floor to go green (blocked by the guard-tests hook).
- **No secrets in git.** Use `.env` (gitignored) + `.env.example` placeholders;
  never hardcode credentials or AWS keys.
