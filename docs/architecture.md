# Architecture

`own-overview` builds **your company's own AI Overview**: the same
retrieve → ground → cite experience Google gives over the web, but over your
*private* InsuranceSuite data, in a way a regulated insurer can actually ship.

The data comes from **Guidewire Cloud Data Access (CDA)**. The pipeline is
**multi-tenant** and **multi-environment** end to end, orchestrated as an
explicit **LangGraph** graph, and defaults to the Guidewire-shaped stack
(**AWS Bedrock + OpenSearch**) with a zero-cloud local mode for `git clone`
and run.

---

## Two paths

### Write path — ingestion (event-driven, off CDA)

```
Guidewire CDA  (bulk backfill  →  streaming micro-batches)
   │  writes Apache Parquet + manifest.json to S3
   │      s3://<bucket>/<manifestKey>/manifest.json
   │      s3://<bucket>/<table>/<fingerprint>/<timestamp>/*.parquet
   │  emits CloudEvents Lifecycle Events
   ▼
AWS EventBridge  (partner event bus)
   │  rule filters: streamingBatchCompleted | batchModeTableWrittenOut | tableSchemaChanged
   ▼
Ingestion Lambda
   │  1. read the committed Parquet at the event's s3Path (gated on manifest)
   │  2. merge CDA change rows (I/U/D) to latest state  →  honor DELETE tombstones
   │  3. extract → chunk → embed
   │  4. upsert to the vector store  /  delete_document on tombstones
   ▼
OpenSearch  (namespace = tenant + env;  metadata = doc_type, acl_roles, dates)
```

**Why not raw S3 `ObjectCreated`?** Those events fire per Parquet object
*before* the batch is committed in `manifest.json`, so a naive trigger reads
half-written tables. CDA's **Lifecycle Events** are the commit signal — this is
Guidewire's documented pattern.
Ref: <https://www.guidewire.com/resources/blog/developers/streamline-data-consumption-with-cda-lifecycle-events-and-aws-eventbridge>

**Why deletes matter.** CDA is CDC: deletes arrive as rows with
`gwcbi___operation = 'D'`. We propagate them to `VectorStore.delete_document`
so a redacted/removed claim leaves retrieval — a hard requirement in a
regulated domain, and a nice governance story.

### Read path — query (a LangGraph graph)

```
caller's signed token → Identity (tenant, env, roles)   ← never from the prompt
   ▼
fan_out    one question → sub-queries
   ▼
retrieve   vector search WITH the permission filter pushed into the query:
             scope == identity.scope  (tenant AND env)
             AND acl_roles ∩ identity.roles ≠ ∅
   ▼
rerank     cross-encoder / Bedrock rerank → top-k that fit the budget
   ▼
ground     LLM answers only from retrieved passages; every claim cited
   ▼
guardrails groundedness score, PII redaction, injection screen → abstain if thin
   ▼
audit      who / what / retrieved chunk ids / scores → immutable log
```

Each **phase is a graph node** — which is exactly how the blog series is
organized: one post per node.

---

## Multi-tenant & multi-environment

CDA exposes data per **tenant** and per **environment** ("planets": dev/qa/prod)
via separate event sources; events carry a tenant id + environment. We carry
that `TenantScope` (tenant + env) on every `Document`, `Chunk` and query, and:

- the **vector index namespace** is `tenant__env`;
- retrieval **always** filters on scope (fail closed — no scope, no results);
- dev/qa/prod data never share an index.

## Access control at retrieval (RBAC)

The filter is built only from the signed `Identity` (`security/access.py`) and
compiled into the store query. Restricted chunks are excluded before the model
sees them — not filtered out of the answer afterward. `security/identity.py`
resolves the token; the prompt can never widen access.

---

## Component contracts

`contracts.py` defines the swappable interfaces — `Embedder`, `VectorStore`,
`Reranker`, `LLM`, `Chunker`. Adapters:

| Stage        | Default (Bedrock/OpenSearch)      | Local fallback            |
|--------------|-----------------------------------|---------------------------|
| Embeddings   | Bedrock Titan v2                  | BGE-small (sentence-transformers) |
| Vector store | OpenSearch (k-NN + filter)        | in-memory numpy           |
| Rerank       | Bedrock Rerank                    | no-op                     |
| LLM          | Bedrock Claude                    | echo (extractive)         |

`config.build_*` chooses the adapter from `Settings`.

---

## Run modes

- **Local (zero cloud):** `PROVIDER=local`, in-memory store, open-source
  embeddings, file-based CDA simulator. `git clone` → runs.
- **LocalStack:** real S3 + EventBridge + Lambda, emulated; the synthetic CDA
  generator writes true CDA-layout Parquet and fires lifecycle events. Exercises
  the whole event-driven path offline.
- **Real AWS:** the same code, deployed with Terraform (`infra/terraform`);
  point it at a real CDA feed.

---

## The blog series (one post per node)

`own-overview` is the reference implementation behind the **"Your own AI
Overview"** series. Each post deep-dives one stage and is tagged in git:

1. **The spine** — end-to-end minimal, clonable (this repo at `v1`).
2. **Chunking** — naive → structure-aware; measured.
3. **Retrieve & rerank** — dense k-NN + reranker + metadata filter (hybrid
   BM25 + vector is the documented next commit; OpenSearch already maps `text`
   as a full-text field, so BM25 is one query away).
4. **Access control at retrieval** — RBAC from the token, filter-in-query, audit.
5. **Grounding & citations** — grounded prompting, source attribution, faithfulness.
6. **Evals & guardrails** — groundedness/PII/injection evals that gate the deploy.
