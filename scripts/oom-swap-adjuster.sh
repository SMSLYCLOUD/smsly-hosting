#!/usr/bin/env bash
# oom-swap-adjuster.sh
#
# Monitors the system for Out Of Memory (OOM) kills. If one is detected within the last
# X minutes, it automatically increases the swap space by 200MB up to a maximum of 4x RAM.
# This serves as an auto-maintenance feature to prevent recurring build crashes.

set -euo pipefail

LOG_FILE="/var/log/smsly-oom-adjuster.log"
MINUTES_BACK=10
SWAPFILE_PREFIX="/swapfile-smsly-auto"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
}

# Check for OOM events in the last N minutes using journalctl
OOM_COUNT=$(journalctl -k --since "${MINUTES_BACK} minutes ago" | grep -i "out of memory" | wc -l || true)

if [ "$OOM_COUNT" -eq 0 ]; then
    # No OOM detected recently, exit quietly.
    exit 0
fi

log "Detected $OOM_COUNT OOM events in the last $MINUTES_BACK minutes. Evaluating swap size."

# Get RAM size in MB
RAM_MB=$(free -m | awk '/^Mem:/{print $2}')
CURRENT_SWAP_MB=$(free -m | awk '/^Swap:/{print $2}')

# Maximum allowed swap is 4x RAM
MAX_SWAP_MB=$((RAM_MB * 4))

if [ "$CURRENT_SWAP_MB" -ge "$MAX_SWAP_MB" ]; then
    log "Swap is already at or above the maximum allowed limit (4x RAM = ${MAX_SWAP_MB}MB). No further auto-adjustment will be made."
    exit 0
fi

# Calculate new swap chunk to add (200MB)
ADD_SWAP_MB=200
NEW_TOTAL_MB=$((CURRENT_SWAP_MB + ADD_SWAP_MB))

# Cap at max if we would overshoot
if [ "$NEW_TOTAL_MB" -gt "$MAX_SWAP_MB" ]; then
    ADD_SWAP_MB=$((MAX_SWAP_MB - CURRENT_SWAP_MB))
    NEW_TOTAL_MB=$MAX_SWAP_MB
fi

if [ "$ADD_SWAP_MB" -le 0 ]; then
    exit 0
fi

# Skip if root filesystem has less than 2GB headroom beyond the new swapfile.
AVAIL_DISK_MB=$(df -Pm / | awk 'NR==2{print $4}')
if [ "${AVAIL_DISK_MB:-0}" -lt $((ADD_SWAP_MB + 2048)) ]; then
    log "Insufficient disk space (${AVAIL_DISK_MB}MB free). Skipping swap adjustment."
    exit 0
fi

# Guard against kernel MAX_SWAPFILES exhaustion (unactivated files would leak disk).
ACTIVE_SWAPS=$(swapon --noheadings 2>/dev/null | wc -l || echo 0)
if [ "${ACTIVE_SWAPS:-0}" -ge 24 ]; then
    log "Active swap area count ($ACTIVE_SWAPS) at safety cap. Skipping swap adjustment."
    exit 0
fi

NEW_SWAPFILE="${SWAPFILE_PREFIX}-$(date '+%s')"
log "Increasing swap by ${ADD_SWAP_MB}MB. Creating ${NEW_SWAPFILE}..."

if ! fallocate -l "${ADD_SWAP_MB}M" "$NEW_SWAPFILE" 2>/dev/null; then
    log "fallocate failed, trying dd..."
    if ! dd if=/dev/zero of="$NEW_SWAPFILE" bs=1M count="$ADD_SWAP_MB" status=none; then
        log "Failed to create swap file."
        rm -f "$NEW_SWAPFILE"
        exit 1
    fi
fi

chmod 600 "$NEW_SWAPFILE"
if ! mkswap "$NEW_SWAPFILE" >/dev/null 2>&1 || ! (swapon -p 10 "$NEW_SWAPFILE" 2>/dev/null || swapon "$NEW_SWAPFILE" 2>/dev/null); then
    log "Failed to activate swap file ${NEW_SWAPFILE}; removing it to prevent disk leak."
    rm -f "$NEW_SWAPFILE"
    exit 1
fi

if ! grep -q "$NEW_SWAPFILE" /etc/fstab ; then
    echo "$NEW_SWAPFILE none swap sw,pri=10 0 0" >> /etc/fstab
fi

log "Successfully added ${ADD_SWAP_MB}MB of swap. Total swap is now approx ${NEW_TOTAL_MB}MB."
