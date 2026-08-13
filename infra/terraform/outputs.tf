# Outputs — the handful of identifiers the CLI, bootstrap scripts and the CDA
# generator need to push events and data at the provisioned stack.

output "cda_bucket_name" {
  description = "S3 bucket that CDA lands Parquet + manifest.json into."
  value       = aws_s3_bucket.cda_landing.bucket
}

output "cda_bucket_arn" {
  description = "ARN of the CDA landing bucket."
  value       = aws_s3_bucket.cda_landing.arn
}

output "event_bus_name" {
  description = "Custom EventBridge bus that carries CDA lifecycle events (PutEvents target)."
  value       = aws_cloudwatch_event_bus.cda.name
}

output "event_rule_name" {
  description = "Rule matching ingestable CDA lifecycle detail-types."
  value       = aws_cloudwatch_event_rule.cda_lifecycle.name
}

output "ingestion_lambda_arn" {
  description = "ARN of the ingestion Lambda invoked by EventBridge."
  value       = aws_lambda_function.ingestion.arn
}

output "ingestion_lambda_name" {
  description = "Name of the ingestion Lambda."
  value       = aws_lambda_function.ingestion.function_name
}
