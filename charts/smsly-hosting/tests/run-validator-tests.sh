#!/usr/bin/env bash
# Smoke tests for the smsly-hosting chart security validators.
# Runs without helm-unittest by exercising `helm template` directly.
#
# Exit codes: 0 on success, 1 on any failed expectation.

set -u

CHART_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PASS=0
FAIL=0

# All secure baseline values that pass every check.
BASE_SECURE=(
  --set global.environment=production
  --set secrets.secretKey=strong-random-secret-key-1234567890
  --set secrets.dbPassword=strong-db-password-1234567890
  --set secrets.redisPassword=strong-redis-password-1234567890
  --set secrets.fieldEncryptionKey=strong-fernet-key-1234567890
  --set secrets.githubWebhookSecret=strong-webhook-secret-1234567890
  --set backend.env.allowedHosts=smsly.cloud
  --set backend.env.corsAllowedOrigins=https://smsly.cloud
  --set redis.auth.enabled=true
  --set redis.auth.password=strong-redis-password-1234567890
)

# expect_fail <name> <expected substring> [<extra helm args>...]
expect_fail() {
  local name="$1"
  local needle="$2"
  shift 2
  local out
  out=$(helm template smsly "$CHART_DIR" "${BASE_SECURE[@]}" "$@" 2>&1)
  if echo "$out" | grep -qF "$needle"; then
    echo "[PASS] $name"
    PASS=$((PASS + 1))
  else
    echo "[FAIL] $name (expected substring: $needle)"
    echo "$out" | sed 's/^/    /' | head -5
    FAIL=$((FAIL + 1))
  fi
}

# expect_pass <name> [<extra helm args>...]
expect_pass() {
  local name="$1"
  shift
  local out
  out=$(helm template smsly "$CHART_DIR" "${BASE_SECURE[@]}" "$@" 2>&1)
  if echo "$out" | grep -qE "Error:|smsly\.security"; then
    echo "[FAIL] $name (unexpectedly rejected)"
    echo "$out" | sed 's/^/    /' | head -5
    FAIL=$((FAIL + 1))
  else
    echo "[PASS] $name"
    PASS=$((PASS + 1))
  fi
}

echo "=== Negative: empty placeholders must be rejected ==="
expect_fail "empty secrets.secretKey"          "secrets.secretKey must be set"          --set secrets.secretKey=
expect_fail "empty secrets.dbPassword"         "secrets.dbPassword must be set"         --set secrets.dbPassword=
expect_fail "empty secrets.redisPassword"      "secrets.redisPassword must be set"      --set secrets.redisPassword=
expect_fail "empty secrets.fieldEncryptionKey" "secrets.fieldEncryptionKey must be set" --set secrets.fieldEncryptionKey=
expect_fail "empty secrets.githubWebhookSecret" "secrets.githubWebhookSecret must be set" --set secrets.githubWebhookSecret=

echo "=== Negative: placeholder values must be rejected ==="
expect_fail "change-me secretKey"   "secrets.secretKey must be set"   --set secrets.secretKey=change-me
expect_fail "change-me dbPassword"  "secrets.dbPassword must be set"  --set secrets.dbPassword=change-me
expect_fail "change-me-in-prod redisPassword" "secrets.redisPassword must be set" --set secrets.redisPassword=change-me-in-prod

echo "=== Negative: unpinned image tags must be rejected ==="
expect_fail "latest backend tag"   "backend.image.tag must be pinned"  --set backend.image.tag=latest
expect_fail "latest frontend tag"  "frontend.image.tag must be pinned" --set frontend.image.tag=latest
expect_fail "latest celery tag"    "celery.image.tag must be pinned"   --set celery.image.tag=latest
expect_fail "latest postgres tag"  "postgresql.image.tag must be pinned" --set postgresql.image.tag=latest
expect_fail "redis:latest image"   "redis.image must be pinned"          --set redis.image=redis:latest

echo "=== Negative: production-only host / origin settings ==="
expect_fail 'allowedHosts="*"'             'allowedHosts="\*" is forbidden in production' --set backend.env.allowedHosts='*'
expect_fail "empty allowedHosts"           "backend.env.allowedHosts is empty in production" --set backend.env.allowedHosts=
expect_fail 'corsAllowedOrigins="*"'       'corsAllowedOrigins="\*" is forbidden in production' --set backend.env.corsAllowedOrigins='*'
expect_fail "redis no-pwd + auth-disabled" "secrets.redisPassword must be set" --set secrets.redisPassword= --set redis.auth.enabled=false --set redis.auth.password=

echo "=== Negative: accidental re-introduction of secrets under backend.env ==="
expect_fail "backend.env.secretKey"             "backend.env.secretKey must not be empty"             --set backend.env.secretKey=
expect_fail "backend.env.dbPassword"            "backend.env.dbPassword must not be empty"            --set backend.env.dbPassword=
expect_fail "backend.env.fieldEncryptionKey"    "backend.env.fieldEncryptionKey must not be empty"    --set backend.env.fieldEncryptionKey=
expect_fail "backend.env.githubWebhookSecret"   "backend.env.githubWebhookSecret must not be empty"   --set backend.env.githubWebhookSecret=

echo "=== Positive: non-production relaxes host / redis checks ==="
expect_pass "staging with wildcard allowedHosts"      --set global.environment=staging --set backend.env.allowedHosts='*' --set backend.env.corsAllowedOrigins=
expect_pass "staging with empty redisPassword"        --set global.environment=staging --set secrets.redisPassword= --set redis.auth.enabled=false --set redis.auth.password=

echo "=== Negative: non-production still requires pinned secrets ==="
expect_fail "staging: empty fieldEncryptionKey" "secrets.fieldEncryptionKey must be set" --set global.environment=staging --set secrets.fieldEncryptionKey=
expect_fail "staging: latest backend tag"        "backend.image.tag must be pinned"       --set global.environment=staging --set backend.image.tag=latest

echo "=== Positive: all secure production render succeeds ==="
expect_pass "production baseline"

echo
echo "Summary: PASS=$PASS  FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
