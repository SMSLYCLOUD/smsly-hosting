#!/usr/bin/env bash
# scripts/wait_for_build_slots_free.sh
#
# Wait until no fleet build slot is held, so host-side Docker work
# (`docker compose build`, image prunes, daemon restarts) never contends
# with app builds on the same daemon. Concurrent builders on one
# containerd store produce CreateDiff mount-callback and lease failures
# ("No such image", 2026-09-22) — including host builds racing app builds
# that the in-app fleet lock cannot see.
#
# Usage:
#   scripts/wait_for_build_slots_free.sh [--timeout SECS] [--poll SECS] [--force]
#   --timeout SECS  max seconds to wait (default 1800)
#   --poll SECS     poll interval (default 15)
#   --force         skip waiting entirely (operator accepts the risk)
#
# Exit 0 when the fleet is idle (or --force). Exit 1 on timeout.
# Redis unreadable: warn loudly and proceed (fail-open for manual ops —
# an infra hiccup must not wedge an operator; the app-side gates stay
# fail-closed independently).
#
# Run this before any host-side image build, e.g.:
#   scripts/wait_for_build_slots_free.sh && \
#     docker compose -f docker-compose.prod.yml up -d --build backend

set -u
INSTALL_DIR="${INSTALL_DIR:-/opt/smsly-hosting}"

TIMEOUT=1800
POLL=15
FORCE=0

while [ $# -gt 0 ]; do
    case "$1" in
        --timeout) TIMEOUT="${2:-1800}"; shift 2 ;;
        --poll) POLL="${2:-15}"; shift 2 ;;
        --force) FORCE=1; shift ;;
        -h|--help)
            sed -n '2,/^$/p' "$0" 2>/dev/null || echo "usage: $0 [--timeout SECS] [--poll SECS] [--force]"
            exit 0
            ;;
        *) echo "unknown flag: $1 (see --help)" >&2; exit 2 ;;
    esac
done

case "$TIMEOUT" in ''|*[!0-9]*) TIMEOUT=1800 ;; esac
case "$POLL" in ''|*[!0-9]*) POLL=15 ;; esac
[ "$POLL" -lt 1 ] && POLL=1

if [ "$FORCE" = "1" ]; then
    echo "[build-guard] --force: skipping fleet-idle wait (operator accepts contention risk)"
    exit 0
fi

command -v docker >/dev/null 2>&1 || {
    echo "[build-guard] docker CLI missing — cannot inspect locks; proceeding (fail-open)" >&2
    exit 0
}

# Resolve the cache Redis: first non-sentinel redis container.
_redis_container=""
_redis_container=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -i -E 'redis' | grep -v -i -E 'sentinel' | head -n 1) || true
if [ -z "$_redis_container" ]; then
    echo "[build-guard] no redis container found — cannot inspect locks; proceeding (fail-open)" >&2
    exit 0
fi

# DB number from REDIS_CACHE_URL (path component), default 0.
_db="0"
if [ -f "$INSTALL_DIR/.env" ]; then
    _cache_url=$(grep -E '^REDIS_CACHE_URL=' "$INSTALL_DIR/.env" 2>/dev/null | tail -n 1 | cut -d= -f2-)
    case "$_cache_url" in
        */[0-9]*)
            _db=$(printf '%s' "$_cache_url" | sed 's#.*/\([0-9][0-9]*\).*#\1#')
            case "$_db" in ''|*[!0-9]*) _db="0" ;; esac
            ;;
    esac
fi
_pass=""
if [ -f "$INSTALL_DIR/.env" ]; then
    _pass=$(grep -E '^REDIS_PASSWORD=' "$INSTALL_DIR/.env" 2>/dev/null | tail -n 1 | cut -d= -f2-)
fi

_locks_held() {
    local keys
    if [ -n "$_pass" ]; then
        keys=$(timeout -k 5 10 docker exec "$_redis_container" redis-cli -a "$_pass" -n "$_db" --scan --pattern 'smsly_fleet_build_lock*' 2>/dev/null) || return 2
    else
        keys=$(timeout -k 5 10 docker exec "$_redis_container" redis-cli -n "$_db" --scan --pattern 'smsly_fleet_build_lock*' 2>/dev/null) || return 2
    fi
    [ -n "$keys" ]
}

_start=$(date +%s)
while true; do
    _locks_held
    _rc=$?
    if [ "$_rc" -eq 2 ]; then
        echo "[build-guard] redis query failed — cannot inspect locks; proceeding (fail-open)" >&2
        exit 0
    fi
    if [ "$_rc" -eq 0 ]; then
        _now=$(date +%s)
        if [ $((_now - _start)) -ge "$TIMEOUT" ]; then
            echo "[build-guard] TIMEOUT after ${TIMEOUT}s: app build(s) still hold fleet slot(s); host build deferred (re-run or pass --force)" >&2
            exit 1
        fi
        sleep "$POLL"
    else
        echo "[build-guard] fleet idle — safe to build host images"
        exit 0
    fi
done
