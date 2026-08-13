# own-overview

**Build your company's own AI Overview.** The same retrieve → ground → cite
experience Google gives over the web — but over your *private* Guidewire
InsuranceSuite data (policies, claims, underwriting), built the way a regulated
insurer can actually ship: multi-tenant, access-controlled at retrieval,
grounded with citations, and auditable.

The data arrives over **Guidewire Cloud Data Access (CDA)**. The pipeline is an
explicit **LangGraph** graph and defaults to the Guidewire-shaped stack —
**AWS Bedrock + OpenSearch** — with a zero-cloud local mode so you can clone and
run it today.

> This is a teaching-grade reference implementation, not a Guidewire product.
> It reproduces the *ideas* and the integration shape; bring your own data and
> keys.

---

## What it does

```
CDA (Parquet + manifest in S3)
  → Lifecycle Event → EventBridge → ingestion Lambda
  → merge change rows to latest state (honoring DELETE tombstones)
  → chunk → embed → upsert to OpenSearch  (namespace = tenant + env)

query + signed token (tenant, env, roles)
  → fan-out → filtered retrieve → rerank → grounded answer w/ citations
  → groundedness guardrail (abstain if thin) → audit log
```

See [`docs/architecture.md`](docs/architecture.md) for the full design and why
we trigger on CDA Lifecycle Events rather than raw S3 events.

## Quickstart

### Local (zero cloud) — clone and run

```bash
uv sync --extra local
cp .env.example .env      # then set the four *_PROVIDER / VECTOR_STORE to "local" (see bottom of the file)
own-overview seed         # generate synthetic CDA insurance data + ingest it
own-overview query "Why did the premium on POL-55012 go up?" --role adjuster
own-overview query "Why did the premium on POL-55012 go up?" --role underwriter
```

Notice the answer changes with `--role`: an adjuster can't retrieve the
underwriting memo. That's access control at retrieval, not after.

### LocalStack — exercise the real event-driven path offline

```bash
docker compose up -d
uv sync
./scripts/bootstrap_localstack.sh     # bucket + event bus + deploy lambda
own-overview seed --emit-events       # write CDA-layout data + fire lifecycle events
own-overview query "..." --role adjuster
```

### Real AWS

```bash
cd infra/terraform && terraform init && terraform apply
# point OWN_OVERVIEW_* at your account + a real CDA feed
```

## Multi-tenant, multi-env

Every record and query carries a `TenantScope` (tenant + environment). The
index is namespaced `tenant__env`, retrieval always filters on it, and dev/qa/
prod never share data. See [`docs/architecture.md`](docs/architecture.md).

## The blog series

`own-overview` is the reference implementation behind the **"Your own AI
Overview"** series — one post per pipeline stage, each tagged in git
(`v1-spine`, `v2-chunking`, …). Check out any tag to see that stage's version.

## Layout

```
own_overview/
  contracts.py          core types + component Protocols
  config.py             Settings + build_* factories
  pipeline/graph.py     the LangGraph query graph (one node per phase)
  pipeline/nodes/       fan_out · retrieve · rerank · ground · guardrails · audit · chunk
  ingestion/cda/        events · merge (tombstones) · source (manifest-gated) · simulator
  ingestion/lambda_handler.py   EventBridge → ingest entrypoint
  embeddings/ llm/ vectorstore/ retrieval/   provider adapters (bedrock + local)
  security/             identity (token→scope/roles) · access (retrieval filter)
  evals/                groundedness · guardrails
  cli.py                seed / query / ingest
infra/terraform/        S3 + EventBridge + Lambda + OpenSearch
infra/localstack/       offline bootstrap
sample_data/            synthetic insurance corpus + generator
```

## License

MIT © 2026 Xavier Ramirez
