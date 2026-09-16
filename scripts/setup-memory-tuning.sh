#!/usr/bin/env bash
# scripts/setup-memory-tuning.sh — host memory tuning (KSM + zram).
#
# Idle-minimal, burst-allowed at the kernel level:
#   KSM  — dedupes identical anonymous pages across the fleet of Python
#          workers (gunicorn + 3 celery pools fork near-identical images).
#          Typically 20-40% of Python RSS on such fleets at <1% CPU.
#   zram — compressed swap in RAM (zstd ~2.5:1) at higher priority than
#          disk swap, so cold pages compress instead of churning disk.
#          The disk swapfile remains as overflow.
#
# Idempotent and best-effort: every step probes capability first and never
# fails (VPS kernels without KSM/zram, containers without /sys write, or
# explicit SMSLY_DISABLE_KSM/SMSLY_DISABLE_ZRAM=1 all skip quietly).
# Safe to run on every boot (systemd) and every install/update.
set -uo pipefail

KSM_DIR="/sys/kernel/mm/ksm"
# Gentle defaults: converge gigabytes in minutes at negligible CPU.
KSM_SLEEP_MS="${SMSLY_KSM_SLEEP_MS:-500}"
KSM_PAGES_TO_SCAN="${SMSLY_KSM_PAGES_TO_SCAN:-500}"

log() { echo "[memory-tuning] $*"; }

setup_ksm() {
    if [ "${SMSLY_DISABLE_KSM:-0}" = "1" ]; then
        log "KSM disabled via SMSLY_DISABLE_KSM=1 — skipping"
        return 0
    fi
    if [ ! -w "$KSM_DIR/run" ]; then
        log "KSM not available (no $KSM_DIR/run) — skipping"
        return 0
    fi
    echo 1 > "$KSM_DIR/run" 2>/dev/null || { log "KSM enable denied — skipping"; return 0; }
    [ -w "$KSM_DIR/sleep_millisecs" ] && echo "$KSM_SLEEP_MS" > "$KSM_DIR/sleep_millisecs" 2>/dev/null || true
    [ -w "$KSM_DIR/pages_to_scan" ] && echo "$KSM_PAGES_TO_SCAN" > "$KSM_DIR/pages_to_scan" 2>/dev/null || true
    if [ -w "$KSM_DIR/merge_across_nodes" ]; then
        echo 1 > "$KSM_DIR/merge_across_nodes" 2>/dev/null || true
    fi
    log "KSM enabled (sleep=${KSM_SLEEP_MS}ms, pages_to_scan=${KSM_PAGES_TO_SCAN})"
    return 0
}

setup_zram() {
    if [ "${SMSLY_DISABLE_ZRAM:-0}" = "1" ]; then
        log "zram disabled via SMSLY_DISABLE_ZRAM=1 — skipping"
        return 0
    fi
    # Already active (e.g. previous boot's unit or a distro default)?
    if grep -q '^/dev/zram' /proc/swaps 2>/dev/null; then
        log "zram swap already active — skipping"
        return 0
    fi
    if [ "$(id -u)" -ne 0 ]; then
        log "not root — skipping zram"
        return 0
    fi
    command -v mkswap >/dev/null 2>&1 || { log "mkswap missing — skipping zram"; return 0; }
    modprobe zram 2>/dev/null || true
    if [ ! -b /dev/zram0 ] && [ ! -e /sys/block/zram0/disksize ]; then
        log "zram device unavailable (module missing?) — skipping"
        return 0
    fi
    # Refuse to reformat a device that is already in use.
    if [ -n "$(cat /sys/block/zram0/mm_stat 2>/dev/null | awk '{print $1}' || true)" ] && \
       [ "$(cat /sys/block/zram0/mm_stat 2>/dev/null | awk '{print $1}')" != "0" ]; then
        log "/dev/zram0 already holds data — skipping"
        return 0
    fi
    local ram_mb=""
    ram_mb="$(free -m 2>/dev/null | awk '/^Mem:/{print $2}' || true)"
    [ -n "$ram_mb" ] && [ "$ram_mb" -gt 0 ] || { log "RAM detection failed — skipping zram"; return 0; }
    # 50% of RAM, clamped to [512M, 8G]. disksize only caps the store —
    # unused capacity costs ~0.1% metadata, so generosity is free.
    local zram_mb=$((ram_mb / 2))
    [ "$zram_mb" -lt 512 ] && zram_mb=512
    [ "$zram_mb" -gt 8192 ] && zram_mb=8192
    if [ -w /sys/block/zram0/comp_algorithm ]; then
        if grep -q zstd /sys/block/zram0/comp_algorithm 2>/dev/null; then
            echo zstd > /sys/block/zram0/comp_algorithm 2>/dev/null || true
        fi
    fi
    swapoff /dev/zram0 2>/dev/null || true
    echo "${zram_mb}M" > /sys/block/zram0/disksize 2>/dev/null || { log "zram disksize write denied — skipping"; return 0; }
    mkswap /dev/zram0 >/dev/null 2>&1 || { log "zram mkswap failed — skipping"; return 0; }
    # Priority 100 beats the disk swapfile (priority 10 / default), so the
    # kernel compresses cold pages before touching disk.
    swapon -p 100 /dev/zram0 2>/dev/null || swapon /dev/zram0 2>/dev/null || { log "zram swapon failed — skipping"; return 0; }
    log "zram active: ${zram_mb}M (priority 100, above disk swap)"
    return 0
}

setup_ksm
setup_zram
log "done"
exit 0
