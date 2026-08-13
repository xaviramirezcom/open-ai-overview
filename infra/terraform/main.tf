# own-overview — ingestion infrastructure
#
# The event-driven CDA write path (see docs/architecture.md):
#
#   CDA (Parquet + manifest.json in S3)
#     -> lifecycle event -> custom EventBridge bus
#     -> rule (streamingBatchCompleted | batchModeTableWrittenOut | tableSchemaChanged)
#     -> ingestion Lambda -> merge -> chunk -> embed -> vector store
#
# This is deliberately substrate-agnostic: the same HCL provisions real AWS or,
# with `use_localstack = true`, points the AWS provider at LocalStack so the
# whole path can be exercised offline. It is written to be coherent and valid,
# not necessarily apply-perfect against an arbitrary account (OpenSearch, for
# instance, may be a managed domain or the docker-compose container — see the
# README — so it is referenced by endpoint rather than provisioned here).

terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# ---------------------------------------------------------------------------
# Provider — real AWS by default; LocalStack when use_localstack = true.
# ---------------------------------------------------------------------------
provider "aws" {
  region = var.aws_region

  # LocalStack accepts any credentials; real AWS uses the ambient chain.
  access_key = var.use_localstack ? "test" : null
  secret_key = var.use_localstack ? "test" : null

  # These skips + path-style S3 are what make the AWS provider talk to
  # LocalStack instead of the real endpoints.
  s3_use_path_style           = var.use_localstack
  skip_credentials_validation = var.use_localstack
  skip_metadata_api_check     = var.use_localstack
  skip_requesting_account_id  = var.use_localstack

  # Route every service this stack uses at the single LocalStack edge port.
  dynamic "endpoints" {
    for_each = var.use_localstack ? [1] : []
    content {
      s3       = var.localstack_endpoint
      lambda   = var.localstack_endpoint
      events   = var.localstack_endpoint
      iam      = var.localstack_endpoint
      sts      = var.localstack_endpoint
      logs     = var.localstack_endpoint
    }
  }
}

locals {
  # Env passed to the ingestion Lambda. Keys mirror config.Settings
  # (env_prefix = "OWN_OVERVIEW_") so the same code reads them everywhere.
  lambda_env = {
    OWN_OVERVIEW_LLM_PROVIDER       = var.llm_provider
    OWN_OVERVIEW_EMBEDDING_PROVIDER = var.embedding_provider
    OWN_OVERVIEW_VECTOR_STORE       = var.vector_store
    OWN_OVERVIEW_RERANKER           = var.reranker

    OWN_OVERVIEW_AWS_REGION   = var.aws_region
    # Empty on real AWS; the LocalStack edge when emulating.
    OWN_OVERVIEW_AWS_ENDPOINT_URL = var.use_localstack ? var.localstack_endpoint : ""

    OWN_OVERVIEW_BEDROCK_LLM_MODEL_ID       = var.bedrock_llm_model_id
    OWN_OVERVIEW_BEDROCK_EMBEDDING_MODEL_ID = var.bedrock_embedding_model_id
    OWN_OVERVIEW_BEDROCK_EMBEDDING_DIM      = tostring(var.bedrock_embedding_dim)
    OWN_OVERVIEW_BEDROCK_RERANK_MODEL_ID    = var.bedrock_rerank_model_id

    OWN_OVERVIEW_OPENSEARCH_HOST    = var.opensearch_endpoint
    OWN_OVERVIEW_OPENSEARCH_PORT    = tostring(var.opensearch_port)
    OWN_OVERVIEW_OPENSEARCH_USE_SSL = tostring(var.opensearch_use_ssl)

    OWN_OVERVIEW_CDA_BUCKET      = var.cda_bucket_name
    OWN_OVERVIEW_CDA_OUTPUT_MODE = var.cda_output_mode
  }

  tags = merge(
    {
      Project   = "own-overview"
      Component = "cda-ingestion"
      ManagedBy = "terraform"
    },
    var.tags,
  )
}

# ---------------------------------------------------------------------------
# S3 — the CDA landing zone. CDA writes Parquet + manifest.json here; the
# lifecycle event carries the committed s3Path the Lambda then reads.
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "cda_landing" {
  bucket = var.cda_bucket_name
  tags   = local.tags
}

# Keep the change-data history immutable-ish; helpful for the audit story.
resource "aws_s3_bucket_versioning" "cda_landing" {
  bucket = aws_s3_bucket.cda_landing.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "cda_landing" {
  bucket                  = aws_s3_bucket.cda_landing.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ---------------------------------------------------------------------------
# EventBridge — a dedicated bus for CDA lifecycle events.
#
# In real deployments CDA delivers onto a *partner* event bus; here we own a
# custom bus so the synthetic generator (and LocalStack) can PutEvents onto the
# same surface the rule listens on.
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_event_bus" "cda" {
  name = var.event_bus_name
  tags = local.tags
}

# Route only the lifecycle events that mean "committed data is ready". We match
# on detail-type; the domain fields (tenant, env, table, s3Path) travel in the
# event detail and are parsed by CdaLifecycleEvent.from_cloudevent.
resource "aws_cloudwatch_event_rule" "cda_lifecycle" {
  name           = "${var.event_bus_name}-lifecycle"
  description    = "CDA lifecycle events that commit ingestable data"
  event_bus_name = aws_cloudwatch_event_bus.cda.name

  event_pattern = jsonencode({
    "detail-type" = [
      "streamingBatchCompleted",
      "batchModeTableWrittenOut",
      "tableSchemaChanged",
    ]
  })

  tags = local.tags
}

# ---------------------------------------------------------------------------
# IAM — least-privilege execution role for the ingestion Lambda.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "lambda_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ingestion_lambda" {
  name               = "${var.lambda_function_name}-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.tags
}

# Read the committed Parquet + manifest from the CDA landing bucket, and write
# CloudWatch logs. Nothing else.
data "aws_iam_policy_document" "ingestion_lambda" {
  statement {
    sid    = "ReadCdaLandingZone"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:ListBucket",
    ]
    resources = [
      aws_s3_bucket.cda_landing.arn,
      "${aws_s3_bucket.cda_landing.arn}/*",
    ]
  }

  statement {
    sid    = "WriteLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["arn:aws:logs:*:*:*"]
  }
}

resource "aws_iam_role_policy" "ingestion_lambda" {
  name   = "${var.lambda_function_name}-policy"
  role   = aws_iam_role.ingestion_lambda.id
  policy = data.aws_iam_policy_document.ingestion_lambda.json
}

# ---------------------------------------------------------------------------
# Lambda — the ingestion entrypoint. EventBridge invokes
# own_overview.ingestion.lambda_handler.handler with the lifecycle event.
# ---------------------------------------------------------------------------
resource "aws_lambda_function" "ingestion" {
  function_name = var.lambda_function_name
  role          = aws_iam_role.ingestion_lambda.arn
  runtime       = "python3.11"
  handler       = "own_overview.ingestion.lambda_handler.handler"

  filename = var.lambda_zip_path
  # try() keeps `terraform validate` happy before the deployment zip is built.
  source_code_hash = try(filebase64sha256(var.lambda_zip_path), null)

  timeout     = var.lambda_timeout
  memory_size = var.lambda_memory_size

  environment {
    variables = local.lambda_env
  }

  tags = local.tags
}

# ---------------------------------------------------------------------------
# Wiring — let EventBridge invoke the Lambda, and point the rule at it.
# ---------------------------------------------------------------------------
resource "aws_lambda_permission" "allow_eventbridge" {
  statement_id  = "AllowInvokeFromCdaLifecycleRule"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ingestion.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.cda_lifecycle.arn
}

resource "aws_cloudwatch_event_target" "ingestion" {
  rule           = aws_cloudwatch_event_rule.cda_lifecycle.name
  event_bus_name = aws_cloudwatch_event_bus.cda.name
  target_id      = "ingestion-lambda"
  arn            = aws_lambda_function.ingestion.arn
}
