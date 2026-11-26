#!/usr/bin/env bash
# rotate_secrets.sh — generate strong replacements for all .env secrets
#
# Threat model
# ------------
# Treat the working-copy .env as COMPROMISED. Anything that ever touched it
# (laptops, CI runners, backup snapshots, forks of the repo) must be assumed
# to have read the values. The only safe response is to invalidate every
# secret and re-issue it with the upstream provider.
#
# This script does NOT modify the existing .env. It writes a new
# .env.rotated.<timestamp> file that the operator reviews with `diff` and
# then `mv`s into place. The script also tags which values need an
# upstream action (e.g. GitHub webhook secret must be updated on github.com)
# and which do not.
#
# Usage:
#   ./scripts/rotate_secrets.sh [--rotate-encryption-keys] [path-to-env-file]
#   # default: .env in the current directory
#
# Output:
#   .env.rotated.<UTC-timestamp>      (mode 0640, root:1000)

set -euo pipefail

ROTATE_ENCRYPTION_KEYS=false
ENV_FILE=""
for arg in "$@"; do
    case "$arg" in
        --rotate-encryption-keys|--force-rekey-warning-acknowledged)
            ROTATE_ENCRYPTION_KEYS=true
            ;;
        -h|--help)
            echo "Usage: $0 [--rotate-encryption-keys] [path-to-env-file]"
            exit 0
            ;;
        -*)
            echo "ERROR: Unknown option: $arg" >&2
            exit 1
            ;;
        *)
            ENV_FILE="$arg"
            ;;
    esac
done
ENV_FILE="${ENV_FILE:-.env}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${ENV_FILE}.rotated.${TS}"

# Generate cryptographically strong secrets
gen_hex() { openssl rand -hex 32; }
gen_base64() { openssl rand -base64 32 | tr -d '\n'; }
gen_token() { timeout 15 python3 -c "import secrets; print(secrets.token_urlsafe(32))"; }
gen_fernet() { timeout 15 python3 -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"; }

# Read the existing .env (if any) to inspect existing keys
declare -A EXISTING
if [ -f "$ENV_FILE" ]; then
    # NOTE: `${line#*=}`, not `IFS='=' read` — bash drops a trailing
    # delimiter, corrupting values ending in '=' (Fernet keys, 2026-09-12).
    while IFS= read -r line || [ -n "$line" ]; do
        line="${line%$'\r'}"
        [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
        [[ "$line" == *"="* ]] || continue
        key="${line%%=*}"
        value="${line#*=}"
        EXISTING["$key"]="$value"
    done < "$ENV_FILE"
fi

# Preserve FIELD_ENCRYPTION_KEY and BACKUP_ENCRYPTION_KEY unless --rotate-encryption-keys
# was explicitly requested (rotating them without re-encrypting breaks existing data/backups).

# Required secrets to rotate
SECRETS=(
    "SECRET_KEY"
    "FIELD_ENCRYPTION_KEY"        # must be 32-byte url-safe base64 (Fernet)
    "BACKUP_ENCRYPTION_KEY"       # must be 32-byte url-safe base64 (Fernet)
    "GATEWAY_SECRET"
    "GITHUB_WEBHOOK_SECRET"
    "POSTGRES_PASSWORD"
    "RABBITMQ_PASSWORD"
    "REDIS_PASSWORD"
    "REGISTRY_PASSWORD"
    "GRAFANA_PASSWORD"
    "PGCAT_ADMIN_PASSWORD"
    "AUTOSCALER_API_TOKEN"
    "FRP_AUTH_TOKEN"
    "EDGE_JWT_SECRET"
    "CADDY_ASK_SECRET"
    "CROWDSEC_BOUNCER_KEY"
    "SENTINEL_PASSWORD"
    "REPLICATION_PASSWORD"
    "NAPD_SHARED_SECRET"
    "CRYPTD_SHARED_SECRET"
)

declare -A NEW_VALUES
for key in "${SECRETS[@]}"; do
    case "$key" in
        FIELD_ENCRYPTION_KEY|BACKUP_ENCRYPTION_KEY)
            if [ "$ROTATE_ENCRYPTION_KEYS" = "true" ] || [ -z "${EXISTING[$key]:-}" ]; then
                NEW_VALUES["$key"]="$(gen_fernet)"
            else
                NEW_VALUES["$key"]="${EXISTING[$key]}"
            fi
            ;;
        *_TOKEN)
            NEW_VALUES["$key"]="$(gen_token)"
            ;;
        *)
            NEW_VALUES["$key"]="$(gen_hex)"
            ;;
    esac
done
NEW_VALUES["RABBITMQ_DEFAULT_PASS"]="${NEW_VALUES[RABBITMQ_PASSWORD]}"

_rewrite_url_password() {
    local raw_url="$1" new_pass="$2"
    timeout 15 python3 - "$raw_url" "$new_pass" <<'PY'
import sys
from urllib.parse import urlsplit, urlunsplit, quote

raw = sys.argv[1].strip()
new_pw = sys.argv[2]
if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
    raw = raw[1:-1]
parts = urlsplit(raw)
if not parts.scheme or not parts.hostname:
    out = raw
else:
    host = parts.hostname
    if ":" in host:
        host = f"[{host}]"
    port = f":{parts.port}" if parts.port is not None else ""
    user = parts.username or ""
    if user:
        auth = f"{user}:{quote(new_pw, safe='')}@"
    else:
        auth = f":{quote(new_pw, safe='')}@"
    netloc = f"{auth}{host}{port}"
    out = urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
if any(c in out for c in ("&", ";")) or any(c.isspace() for c in out):
    escaped = out.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
    out = f'"{escaped}"'
print(out)
PY
}

# Write the rotated file preserving ALL existing keys and comments line-by-line
declare -A WRITTEN
{
    echo "# SMSLY Hosting — Rotated secrets (${TS})"
    echo "# This file was generated by scripts/rotate_secrets.sh"
    echo "# Review before applying: diff ${ENV_FILE} ${OUT}"
    echo "# Apply: mv ${OUT} ${ENV_FILE}"
    echo ""
    echo "# Upstream actions required after applying this file:"
    echo "#   FIELD_ENCRYPTION_KEY   -> re-encrypt all EncryptedField values (see runbook sec. 8)"
    echo "#   BACKUP_ENCRYPTION_KEY  -> re-encrypt or retain previous key for existing backups"
    echo "#   GITHUB_WEBHOOK_SECRET  -> update GitHub repo/org webhook settings"
    echo "#   POSTGRES_PASSWORD      -> align infrastructure/docker/postgres/init-primary.sh, restart db+pgcat+backend"
    echo "#   RABBITMQ_PASSWORD      -> align all broker URLs in compose, restart rabbitmq+celery"
    echo "#   REDIS_PASSWORD         -> align all cache URLs, restart redis+backend+celery"
    echo "#   REGISTRY_PASSWORD      -> regenerate .htpasswd, restart registry, re-login agents"
    echo "#   GRAFANA_PASSWORD       -> notify all dashboard users, restart grafana"
    echo "#   PGCAT_ADMIN_PASSWORD   -> align pgcat.toml, restart pgcat"
    echo "#   AUTOSCALER_API_TOKEN   -> re-issue on every lite/edge agent"
    echo "#   FRP_AUTH_TOKEN         -> re-issue on every FRP client + frps config"
    echo "#   GATEWAY_SECRET         -> restart gateway (no upstream)"
    echo "#   SECRET_KEY             -> restart backend (Django sessions/CSRF invalidated)"
    echo ""

    if [ -f "$ENV_FILE" ]; then
        while IFS= read -r line || [ -n "$line" ]; do
            line="${line%$'\r'}"
            if [[ -z "$line" || "$line" =~ ^[[:space:]]*# || "$line" != *"="* ]]; then
                echo "$line"
                continue
            fi
            key="${line%%=*}"
            val="${line#*=}"
            WRITTEN["$key"]=1
            if [[ -v NEW_VALUES["$key"] ]]; then
                echo "${key}=${NEW_VALUES[$key]}"
            elif [ "$key" = "DATABASE_URL" ] || [ "$key" = "DIRECT_DATABASE_URL" ]; then
                echo "${key}=$(_rewrite_url_password "$val" "${NEW_VALUES[POSTGRES_PASSWORD]}")"
            elif [ "$key" = "REDIS_URL" ] || [ "$key" = "CELERY_REDBEAT_REDIS_URL" ]; then
                echo "${key}=$(_rewrite_url_password "$val" "${NEW_VALUES[REDIS_PASSWORD]}")"
            elif [ "$key" = "CELERY_BROKER_URL" ]; then
                if [[ "$val" == redis* ]]; then
                    echo "${key}=$(_rewrite_url_password "$val" "${NEW_VALUES[REDIS_PASSWORD]}")"
                else
                    echo "${key}=$(_rewrite_url_password "$val" "${NEW_VALUES[RABBITMQ_PASSWORD]}")"
                fi
            elif [ "$key" = "CELERY_RESULT_BACKEND" ]; then
                if [[ "$val" == redis* ]]; then
                    echo "${key}=$(_rewrite_url_password "$val" "${NEW_VALUES[REDIS_PASSWORD]}")"
                elif [[ "$val" == *postgres* ]]; then
                    echo "${key}=$(_rewrite_url_password "$val" "${NEW_VALUES[POSTGRES_PASSWORD]}")"
                elif [[ "$val" == amqp* || "$val" == rpc* ]]; then
                    echo "${key}=$(_rewrite_url_password "$val" "${NEW_VALUES[RABBITMQ_PASSWORD]}")"
                else
                    echo "${key}=${val}"
                fi
            else
                echo "${key}=${val}"
            fi
        done < "$ENV_FILE"
    fi

    # Append any rotated secrets that were not already present in ENV_FILE
    for key in "${SECRETS[@]}"; do
        if [[ ! -v WRITTEN["$key"] ]]; then
            echo "${key}=${NEW_VALUES[$key]}"
        fi
    done
} > "$OUT"

chown root:1000 "$OUT" 2>/dev/null || true
chmod 640 "$OUT"

# Verify the new file parses without executing it.
if ! bash -n "$OUT" ; then
    echo "ERROR: rotated file failed parse check: $OUT" >&2
    exit 1
fi

echo "Wrote rotated secrets to: $OUT"
echo "Review with: diff ${ENV_FILE} ${OUT}"
echo "Apply with:  mv ${OUT} ${ENV_FILE} && chown root:1000 ${ENV_FILE} && chmod 640 ${ENV_FILE}"
