# LocalStack — exercise the real event-driven path offline

LocalStack emulates the AWS services own-overview's ingestion path depends on
(**S3 + EventBridge + Lambda + IAM + STS + Logs**), so you can run the full
`CDA → event → Lambda → vector store` flow with **no cloud account and no
network**. It pairs with the single-node **OpenSearch** container for the vector
index.

## How the pieces fit

| Piece                              | Role                                                                 |
| ---------------------------------- | ------------------------------------------------------------------- |
| `docker-compose.yml` (repo root)   | Brings up `localstack` (edge on `:4566`) and `opensearch` (`:9200`). |
| `scripts/bootstrap_localstack.sh`  | Provisions the stack **inside** LocalStack (bucket, bus, rule, Lambda). |
| `infra/terraform` (`use_localstack=true`) | Alternative to the script: the same HCL, applied against LocalStack. |
| `infra/localstack/volume/`         | LocalStack's persisted state (mounted by compose; git-ignored).      |

You provision **either** with the bootstrap script (fast, imperative) **or** with
Terraform's `use_localstack` toggle — not both. Both create the same four things.

## Quickstart

```bash
docker compose up -d                 # localstack + opensearch
./scripts/bootstrap_localstack.sh    # bucket + event bus + rule + deploy lambda
own-overview seed --emit-events      # write CDA-layout Parquet + fire lifecycle events
own-overview query "why did premium POL-55012 change?" --role adjuster
```

## What `bootstrap_localstack.sh` does

Using `awslocal` (the LocalStack wrapper around the AWS CLI that pins
`--endpoint-url http://localhost:4566` and dummy `test`/`test` credentials, so
you never touch a real account):

1. **S3** — creates the CDA landing bucket (`own-overview-cda`).
2. **EventBridge** — creates the custom event bus and the rule matching the
   ingestable CDA detail-types
   (`streamingBatchCompleted`, `batchModeTableWrittenOut`, `tableSchemaChanged`).
3. **Lambda** — packages `own_overview` and creates the ingestion function
   (`handler = own_overview.ingestion.lambda_handler.handler`, `python3.11`),
   then adds it as the rule's target with invoke permission.
4. **Env** — sets `OWN_OVERVIEW_*` on the function (notably
   `OWN_OVERVIEW_AWS_ENDPOINT_URL=http://localhost:4566` so the Lambda's own AWS
   calls also resolve back to LocalStack).

`own-overview seed --emit-events` then writes true CDA-layout data to the bucket
and `PutEvents` the lifecycle events onto the bus — LocalStack routes them
through the rule to the Lambda exactly as EventBridge would.

## awslocal

`awslocal` ships with the `localstack` Python package (`pip install
localstack`). It is just:

```bash
awslocal s3 ls
# ≡ aws --endpoint-url=http://localhost:4566 s3 ls   (with dummy creds)
```

Anywhere the docs say `awslocal`, plain `aws --endpoint-url=http://localhost:4566`
works too.

## Tips

- Reset everything: `docker compose down -v` (wipes the LocalStack volume) then
  bring it back up and re-bootstrap.
- The Lambda executor runs functions in Docker, so the compose file mounts the
  Docker socket — the container needs access to the host Docker daemon.
- Tests never touch this stack — the pytest suite runs the **local** (in-memory)
  providers only. LocalStack is for exercising the event path by hand.
