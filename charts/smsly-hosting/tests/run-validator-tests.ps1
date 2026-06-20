# Smoke tests for the smsly-hosting chart security validators.
# Runs without helm-unittest by exercising `helm template` directly.
#
# Usage:
#   pwsh charts/smsly-hosting/tests/run-validator-tests.ps1
#
# Exit code: 0 on success, 1 on any failed expectation.

param(
    [string]$ChartDir = "$PSScriptRoot\.."
)

$ChartDir = (Resolve-Path -LiteralPath $ChartDir).Path
$script:Pass = 0
$script:Fail = 0

$BaseSecure = @(
    "--set","global.environment=production",
    "--set","secrets.secretKey=strong-random-secret-key-1234567890",
    "--set","secrets.dbPassword=strong-db-password-1234567890",
    "--set","secrets.redisPassword=strong-redis-password-1234567890",
    "--set","secrets.fieldEncryptionKey=strong-fernet-key-1234567890",
    "--set","secrets.githubWebhookSecret=strong-webhook-secret-1234567890",
    "--set","backend.env.allowedHosts=smsly.cloud",
    "--set","backend.env.corsAllowedOrigins=https://smsly.cloud",
    "--set","redis.auth.enabled=true",
    "--set","redis.auth.password=strong-redis-password-1234567890"
)

function Invoke-Helm {
    param([string[]]$Extra)
    $args = @("template","smsly",$ChartDir) + $BaseSecure + $Extra
    $out = & helm @args 2>&1
    return ($out -join "`n")
}

function Test-HelmExpectFail {
    param([string]$Name, [string]$Needle, [string[]]$Extra)
    $out = Invoke-Helm -Extra $Extra
    if ($out -match [regex]::Escape($Needle)) {
        Write-Host "[PASS] $Name"
        $script:Pass++
    } else {
        Write-Host "[FAIL] $Name (expected substring: $Needle)"
        ($out -split "`n" | Select-Object -First 5) | ForEach-Object { Write-Host "    $_" }
        $script:Fail++
    }
}

function Test-HelmExpectPass {
    param([string]$Name, [string[]]$Extra)
    $out = Invoke-Helm -Extra $Extra
    if ($out -match "Error:" -or $out -match "smsly\.security") {
        Write-Host "[FAIL] $Name (unexpectedly rejected)"
        ($out -split "`n" | Select-Object -First 5) | ForEach-Object { Write-Host "    $_" }
        $script:Fail++
    } else {
        Write-Host "[PASS] $Name"
        $script:Pass++
    }
}

Write-Host "=== Negative: empty placeholders must be rejected ==="
Test-HelmExpectFail "empty secrets.secretKey"          "secrets.secretKey must be set"          @("--set","secrets.secretKey=")
Test-HelmExpectFail "empty secrets.dbPassword"         "secrets.dbPassword must be set"         @("--set","secrets.dbPassword=")
Test-HelmExpectFail "empty secrets.redisPassword"      "secrets.redisPassword must be set"      @("--set","secrets.redisPassword=")
Test-HelmExpectFail "empty secrets.fieldEncryptionKey" "secrets.fieldEncryptionKey must be set" @("--set","secrets.fieldEncryptionKey=")
Test-HelmExpectFail "empty secrets.githubWebhookSecret" "secrets.githubWebhookSecret must be set" @("--set","secrets.githubWebhookSecret=")

Write-Host "=== Negative: placeholder values must be rejected ==="
Test-HelmExpectFail "change-me secretKey"   "secrets.secretKey must be set"   @("--set","secrets.secretKey=change-me")
Test-HelmExpectFail "change-me dbPassword"  "secrets.dbPassword must be set"  @("--set","secrets.dbPassword=change-me")
Test-HelmExpectFail "change-me-in-prod redisPassword" "secrets.redisPassword must be set" @("--set","secrets.redisPassword=change-me-in-prod")

Write-Host "=== Negative: unpinned image tags must be rejected ==="
Test-HelmExpectFail "latest backend tag"   "backend.image.tag must be pinned"   @("--set","backend.image.tag=latest")
Test-HelmExpectFail "latest frontend tag"  "frontend.image.tag must be pinned"  @("--set","frontend.image.tag=latest")
Test-HelmExpectFail "latest celery tag"    "celery.image.tag must be pinned"    @("--set","celery.image.tag=latest")
Test-HelmExpectFail "latest postgres tag"  "postgresql.image.tag must be pinned" @("--set","postgresql.image.tag=latest")
Test-HelmExpectFail "redis:latest image"   "redis.image must be pinned"          @("--set","redis.image=redis:latest")

Write-Host "=== Negative: production-only host / origin settings ==="
Test-HelmExpectFail 'allowedHosts=wildcard'      'allowedHosts="*" is forbidden in production'   @("--set","backend.env.allowedHosts=*")
Test-HelmExpectFail "empty allowedHosts"        "backend.env.allowedHosts is empty in production" @("--set","backend.env.allowedHosts=")
Test-HelmExpectFail 'corsAllowedOrigins=wildcard' 'corsAllowedOrigins="*" is forbidden in production' @("--set","backend.env.corsAllowedOrigins=*")
Test-HelmExpectFail "redis no-pwd + auth-disabled" "secrets.redisPassword must be set" @("--set","secrets.redisPassword=","--set","redis.auth.enabled=false","--set","redis.auth.password=")

Write-Host "=== Negative: accidental re-introduction of secrets under backend.env ==="
Test-HelmExpectFail "backend.env.secretKey"             "backend.env.secretKey must not be empty"             @("--set","backend.env.secretKey=")
Test-HelmExpectFail "backend.env.dbPassword"            "backend.env.dbPassword must not be empty"            @("--set","backend.env.dbPassword=")
Test-HelmExpectFail "backend.env.fieldEncryptionKey"    "backend.env.fieldEncryptionKey must not be empty"    @("--set","backend.env.fieldEncryptionKey=")
Test-HelmExpectFail "backend.env.githubWebhookSecret"   "backend.env.githubWebhookSecret must not be empty"   @("--set","backend.env.githubWebhookSecret=")

Write-Host "=== Positive: non-production relaxes wildcard-host check ==="
Test-HelmExpectPass "staging with wildcard allowedHosts" @("--set","global.environment=staging","--set","backend.env.allowedHosts=*","--set","backend.env.corsAllowedOrigins=")

Write-Host "=== Negative: unconditional secrets checks still fire in staging ==="
Test-HelmExpectFail "staging: empty redisPassword (unconditional check fires)" "secrets.redisPassword must be set" @("--set","global.environment=staging","--set","secrets.redisPassword=","--set","redis.auth.enabled=false","--set","redis.auth.password=")
Test-HelmExpectFail "staging: empty fieldEncryptionKey" "secrets.fieldEncryptionKey must be set" @("--set","global.environment=staging","--set","secrets.fieldEncryptionKey=")
Test-HelmExpectFail "staging: latest backend tag"        "backend.image.tag must be pinned"       @("--set","global.environment=staging","--set","backend.image.tag=latest")

Write-Host "=== Positive: all secure production render succeeds ==="
Test-HelmExpectPass "production baseline"

Write-Host ""
Write-Host "Summary: PASS=$($script:Pass)  FAIL=$($script:Fail)"
if ($script:Fail -gt 0) { exit 1 } else { exit 0 }
