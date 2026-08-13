#!/usr/bin/env bash
#
# Bootstrap LocalStack for the own-overview event-driven ingestion path.
#
# Creates, all against the LocalStack endpoint:
#   1. the S3 bucket CDA lands Parquet + manifest.json into
#   2. a custom EventBridge event bus for CDA lifecycle events
#   3. a rule on that bus matching the ingestable lifecycle event types
#   4. (optional) packages + deploys the ingestion Lambda and wires it as the
#      rule target  --  commented, since it needs a built zip + an IAM role
#
# Everything here is idempotent-ish: re-running it is safe (creates are
# tolerated-if-exists where practical).
#
# Usage:
#   export OWN_OVERVIEW_AWS_ENDPOINT_URL=http://localhost:4566   # LocalStack
#   ./scripts/bootstrap_localstack.sh
#
# Prefers `awslocal` (from awscli-local) if present; otherwise falls back to
# `aws --endpoint-url=$OWN_OVERVIEW_AWS_ENDPOINT_URL`.

set -euo pipefail

# --- config (override via env) ---------------------------------------------
ENDPOINT="${OWN_OVERVIEW_AWS_ENDPOINT_URL:-http://localhost:4566}"
REGION="${OWN_OVERVIEW_AWS_REGION:-us-east-1}"
BUCKET="${OWN_OVERVIEW_CDA_BUCKET:-own-overview-cda}"
EVENT_BUS="own-overview-cda-bus"          # must match simulator.EVENT_BUS_NAME
RULE_NAME="own-overview-cda-ingestable"
LAMBDA_NAME="own-overview-ingest"
LAMBDA_ZIP="${LAMBDA_ZIP:-dist/lambda.zip}"
LAMBDA_ROLE_ARN="${LAMBDA_ROLE_ARN:-arn:aws:iam::000000000000:role/own-overview-lambda}"

# --- pick a CLI ------------------------------------------------------------
if command -v awslocal >/dev/null 2>&1; then
  AWS="awslocal"
else
  AWS="aws --endpoint-url=${ENDPOINT}"
fi
AWS="${AWS} --region ${REGION}"

echo "==> Using: ${AWS}"
echo "==> Endpoint: ${ENDPOINT}"

# --- 1. S3 bucket ----------------------------------------------------------
echo "==> Creating S3 bucket: ${BUCKET}"
${AWS} s3api create-bucket --bucket "${BUCKET}" >/dev/null 2>&1 \
  || echo "    (bucket already exists — ok)"

# --- 2. Custom EventBridge bus --------------------------------------------
echo "==> Creating EventBridge bus: ${EVENT_BUS}"
${AWS} events create-event-bus --name "${EVENT_BUS}" >/dev/null 2>&1 \
  || echo "    (bus already exists — ok)"

# --- 3. Rule: match the ingestable CDA lifecycle event types --------------
# We match on detail-type, which the simulator sets to the event type value.
echo "==> Creating rule: ${RULE_NAME}"
${AWS} events put-rule \
  --name "${RULE_NAME}" \
  --event-bus-name "${EVENT_BUS}" \
  --event-pattern '{"detail-type":["streamingBatchCompleted","batchModeTableWrittenOut"]}' \
  >/dev/null

echo "==> Rule created. It matches streamingBatchCompleted + batchModeTableWrittenOut."

# --- 4. (OPTIONAL) package + deploy the ingestion Lambda, wire it as target -
# This needs a built deployment zip and (on real AWS) an execution role. On
# LocalStack the role ARN can be a placeholder. Left commented so the script
# succeeds out of the box; uncomment once you have `dist/lambda.zip`.
#
# Build the zip (example — adapt to your packaging of choice, e.g. uv/pip):
#   mkdir -p dist build
#   pip install . -t build/            # vendor deps + package
#   (cd build && zip -qr ../${LAMBDA_ZIP} .)
#
# echo "==> Deploying Lambda: ${LAMBDA_NAME}"
# ${AWS} lambda create-function \
#   --function-name "${LAMBDA_NAME}" \
#   --runtime python3.11 \
#   --handler own_overview.ingestion.lambda_handler.handler \
#   --role "${LAMBDA_ROLE_ARN}" \
#   --timeout 60 --memory-size 512 \
#   --environment "Variables={OWN_OVERVIEW_AWS_ENDPOINT_URL=${ENDPOINT},OWN_OVERVIEW_LLM_PROVIDER=local,OWN_OVERVIEW_EMBEDDING_PROVIDER=local,OWN_OVERVIEW_VECTOR_STORE=local}" \
#   --zip-file "fileb://${LAMBDA_ZIP}" >/dev/null 2>&1 \
#   || ${AWS} lambda update-function-code \
#        --function-name "${LAMBDA_NAME}" --zip-file "fileb://${LAMBDA_ZIP}" >/dev/null
#
# LAMBDA_ARN=$(${AWS} lambda get-function --function-name "${LAMBDA_NAME}" \
#   --query 'Configuration.FunctionArn' --output text)
#
# echo "==> Wiring Lambda as the rule target"
# ${AWS} events put-targets \
#   --rule "${RULE_NAME}" \
#   --event-bus-name "${EVENT_BUS}" \
#   --targets "Id=1,Arn=${LAMBDA_ARN}"
#
# # Allow EventBridge to invoke the Lambda.
# ${AWS} lambda add-permission \
#   --function-name "${LAMBDA_NAME}" \
#   --statement-id "eventbridge-invoke" \
#   --action "lambda:InvokeFunction" \
#   --principal events.amazonaws.com >/dev/null 2>&1 || true

cat <<EOF

==> Bootstrap complete.
    Bucket : ${BUCKET}
    Bus    : ${EVENT_BUS}
    Rule   : ${RULE_NAME}

Next:
    own-overview seed --emit-events     # fire lifecycle events at the bus
    # (deploy the Lambda by uncommenting step 4 above once dist/lambda.zip exists)
EOF
