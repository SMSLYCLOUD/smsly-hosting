#!/bin/bash
# Auto-cleanup script for Docker images, build cache, volumes, and journal logs.
# Run via cron: 0 */6 * * * /opt/smsly-hosting/scripts/docker_cleanup.sh
set -euo pipefail

LOG="/var/log/docker_cleanup.log"
KEEP_FRONTEND=2
KEEP_MARKETER=2

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOG"; }

df_before=$(df / --output=used -B1 | tail -1 | tr -d ' ')
log "--- Cleanup started (used: ${df_before}B) ---"

# 1. Remove dangling (untagged) images
docker image prune -f >> "$LOG" 2>&1

# 2. Remove old frontend images — keep only latest N per repo
for repo in smsly/smsly-frontend-qozao smsly/smsly-frontend-px7ut \
            registry:5000/smsly/smsly-frontend-qozao registry:5000/smsly/smsly-frontend-px7ut; do
    # Get image IDs sorted by creation time (newest first), skip first N
    ids=$(docker images --format '{{.ID}} {{.CreatedAt}}' "$repo" 2>/dev/null \
        | sort -k2 -r | tail -n +$((KEEP_FRONTEND + 1)) | awk '{print $1}')
    if [ -n "$ids" ]; then
        echo "$ids" | xargs -r docker rmi >> "$LOG" 2>&1 || true
        log "Pruned old images for $repo"
    fi
done

# 3. Remove old marketer images — keep only latest N per repo
for repo in smsly/smsly-marketer-rim1u registry:5000/smsly/smsly-marketer-rim1u; do
    ids=$(docker images --format '{{.ID}} {{.CreatedAt}}' "$repo" 2>/dev/null \
        | sort -k2 -r | tail -n +$((KEEP_MARKETER + 1)) | awk '{print $1}')
    if [ -n "$ids" ]; then
        echo "$ids" | xargs -r docker rmi >> "$LOG" 2>&1 || true
        log "Pruned old images for $repo"
    fi
done

# 4. Remove unused images (not used by any container)
docker image prune -af >> "$LOG" 2>&1

# 5. Remove build cache older than 24 hours
docker builder prune -af --filter "until=24h" >> "$LOG" 2>&1

# 6. Remove dangling volumes
docker volume prune -f >> "$LOG" 2>&1

# 7. Trim journal logs to 200MB max
journalctl --vacuum-size=200M >> "$LOG" 2>&1 || true

df_after=$(df / --output=used -B1 | tail -1 | tr -d ' ')
freed=$(( (df_before - df_after) / 1024 / 1024 ))
log "--- Cleanup done (freed ~${freed}MB, used: ${df_after}B) ---"
