# Terraform — own-overview ingestion infrastructure

Provisions the event-driven CDA write path: an **S3 landing zone**, a custom
**EventBridge bus + rule**, and the **ingestion Lambda** (with a least-privilege
IAM role) wired to fire on CDA lifecycle events.

```
CDA → S3 (Parquet + manifest.json) → EventBridge rule → ingestion Lambda → vector store
```

See [`../../docs/architecture.md`](../../docs/architecture.md) for why we trigger
on CDA **lifecycle events** rather than raw S3 `ObjectCreated`.

## Usage

```bash
cd infra/terraform
terraform init
terraform apply
```

Key outputs: `cda_bucket_name`, `event_bus_name`, `ingestion_lambda_arn`.

### The `use_localstack` toggle

The same HCL targets real AWS or [LocalStack](../localstack/README.md). Flip one
variable and the AWS provider is pointed at the LocalStack edge endpoint
(`http://localhost:4566` by default) with dummy credentials and path-style S3:

```bash
# Emulated AWS, fully offline (pairs with docker-compose + bootstrap script):
terraform apply -var="use_localstack=true"

# Real AWS (default): uses your ambient credential chain.
terraform apply
```

Before either apply, build the Lambda deployment package and point
`lambda_zip_path` at it (default `../../dist/ingestion_lambda.zip`). `terraform
validate` succeeds without the zip present; `apply` needs it.

### OpenSearch: managed domain **or** the container

This stack **references** OpenSearch by endpoint rather than provisioning it, so
you can choose:

- **Real AWS** — an Amazon OpenSearch Service **managed domain**. Set
  `opensearch_endpoint` to the domain endpoint and `opensearch_use_ssl=true`.
- **Local / LocalStack** — the single-node **OpenSearch container** from the
  repo's `docker-compose.yml` (`localhost:9200`, the defaults).

The endpoint is passed to the Lambda as `OWN_OVERVIEW_OPENSEARCH_*` env vars, so
the same ingestion code writes to whichever you configured.

## Variables worth knowing

| Variable                    | Default                     | Purpose                                   |
| --------------------------- | --------------------------- | ----------------------------------------- |
| `use_localstack`            | `false`                     | Target LocalStack instead of real AWS.    |
| `localstack_endpoint`       | `http://localhost:4566`     | LocalStack edge endpoint.                 |
| `cda_bucket_name`           | `own-overview-cda`          | S3 CDA landing zone.                      |
| `event_bus_name`            | `own-overview-cda`          | Custom EventBridge bus.                   |
| `lambda_zip_path`           | `../../dist/ingestion_lambda.zip` | Ingestion Lambda deployment package. |
| `opensearch_endpoint`       | `localhost`                 | Managed domain endpoint or container host.|
| `llm_provider` / `embedding_provider` / `vector_store` / `reranker` | `bedrock` / `bedrock` / `opensearch` / `bedrock` | Adapter selection (mirrors `config.Settings`). |

See [`variables.tf`](variables.tf) for the full list and Bedrock model ids.

> Teaching-grade reference infrastructure: coherent and valid HCL that captures
> the real integration shape. It is not guaranteed to `apply` unmodified against
> an arbitrary account (naming/quotas/OpenSearch provisioning vary).
