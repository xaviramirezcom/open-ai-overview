# Input variables for the own-overview ingestion stack.
# Defaults are tuned for a LocalStack run so `terraform apply` works out of the
# box once you flip use_localstack = true; override for real AWS.

# --- provider / target ------------------------------------------------------

variable "aws_region" {
  description = "AWS region (or the region LocalStack pretends to be)."
  type        = string
  default     = "us-east-1"
}

variable "use_localstack" {
  description = "Point the AWS provider at LocalStack instead of real AWS."
  type        = bool
  default     = false
}

variable "localstack_endpoint" {
  description = "LocalStack edge endpoint used for every service when use_localstack = true."
  type        = string
  default     = "http://localhost:4566"
}

variable "tags" {
  description = "Extra tags merged onto every resource."
  type        = map(string)
  default     = {}
}

# --- S3 landing zone --------------------------------------------------------

variable "cda_bucket_name" {
  description = "S3 bucket where CDA lands Parquet + manifest.json."
  type        = string
  default     = "own-overview-cda"
}

variable "cda_output_mode" {
  description = "CDA change-row mode passed to the Lambda: 'merged' (latest state) or 'raw' (I/U/D rows)."
  type        = string
  default     = "merged"

  validation {
    condition     = contains(["merged", "raw"], var.cda_output_mode)
    error_message = "cda_output_mode must be 'merged' or 'raw'."
  }
}

# --- EventBridge ------------------------------------------------------------

variable "event_bus_name" {
  description = "Name of the custom EventBridge bus that carries CDA lifecycle events."
  type        = string
  default     = "own-overview-cda"
}

# --- Lambda -----------------------------------------------------------------

variable "lambda_function_name" {
  description = "Name of the ingestion Lambda function."
  type        = string
  default     = "own-overview-ingestion"
}

variable "lambda_zip_path" {
  description = "Path to the built deployment package (.zip) for the ingestion Lambda."
  type        = string
  default     = "../../dist/ingestion_lambda.zip"
}

variable "lambda_timeout" {
  description = "Lambda timeout in seconds (ingestion reads Parquet + embeds, so give it room)."
  type        = number
  default     = 120
}

variable "lambda_memory_size" {
  description = "Lambda memory in MB."
  type        = number
  default     = 1024
}

# --- OpenSearch (vector store) ----------------------------------------------
# OpenSearch is referenced, not provisioned here: it may be a managed domain
# (real AWS) or the docker-compose container (local). See the README.

variable "opensearch_endpoint" {
  description = "OpenSearch host the Lambda writes the vector index to (managed domain endpoint or container host)."
  type        = string
  default     = "localhost"
}

variable "opensearch_port" {
  description = "OpenSearch port."
  type        = number
  default     = 9200
}

variable "opensearch_use_ssl" {
  description = "Whether to talk to OpenSearch over TLS (true for a managed domain)."
  type        = bool
  default     = false
}

# --- provider selection (mirrors config.Settings) ---------------------------

variable "llm_provider" {
  description = "LLM adapter: 'bedrock' or 'local'."
  type        = string
  default     = "bedrock"
}

variable "embedding_provider" {
  description = "Embedding adapter: 'bedrock' or 'local'."
  type        = string
  default     = "bedrock"
}

variable "vector_store" {
  description = "Vector store: 'opensearch' or 'local'."
  type        = string
  default     = "opensearch"
}

variable "reranker" {
  description = "Reranker: 'bedrock' or 'none'."
  type        = string
  default     = "bedrock"
}

# --- Bedrock model ids ------------------------------------------------------

variable "bedrock_llm_model_id" {
  description = "Bedrock model id for grounded generation."
  type        = string
  default     = "anthropic.claude-3-5-sonnet-20241022-v2:0"
}

variable "bedrock_embedding_model_id" {
  description = "Bedrock model id for embeddings."
  type        = string
  default     = "amazon.titan-embed-text-v2:0"
}

variable "bedrock_embedding_dim" {
  description = "Embedding dimensionality (must match the OpenSearch k-NN index)."
  type        = number
  default     = 1024
}

variable "bedrock_rerank_model_id" {
  description = "Bedrock model id for reranking."
  type        = string
  default     = "amazon.rerank-v1:0"
}
