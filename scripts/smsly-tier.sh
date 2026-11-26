#!/usr/bin/env bash
# smsly-tier.sh — on-demand lifecycle control for SMSLY PaaS infra tiers.
#
# A "tier" is a named group of compose services that can be stopped together
# and woken on demand. Definitions live in $SMSLY_TIER_DIR/<tier>.conf so this
# script stays service-agnostic — the PaaS supports any hosted service, so no
# tier name or service list is hardcoded here.
#
#   check   <tier>   systemd ExecCondition: is this tier defined?
#   up|start <tier>  create-if-absent + start (never recreates unless asked)
#   down|stop <tier> compose stop
#   status  <tier>   compose ps for the tier's services
#   request <tier>   drop a sentinel that smsly-tier-request@.path watches
#   resleep           re-apply "down" to every tier currently marked asleep
#   list              list defined tiers and their sleep state
#
# Why resleep exists: any `docker compose up -d` on the stack starts every
# service in the active profile set — including tiers that were deliberately
# stopped. Run this after install.sh --update to restore the sleep state.
#
# Deliberately never uses `compose down` or `--remove-orphans`: `down` removes
# containers and networks, taking named volumes (caddy_data/acme.json,
# media_volume, static_volume, backups_data) with it.

set -uo pipefail

INSTALL_DIR="${SMSLY_INSTALL_DIR:-/opt/smsly-hosting}"
TIER_DIR="${SMSLY_TIER_DIR:-/etc/smsly/tiers.d}"
STATE_DIR="${SMSLY_TIER_STATE_DIR:-/var/lib/smsly/tiers}"
WAKE_DIR="${SMSLY_TIER_WAKE_DIR:-/run/smsly-tier-wake}"

die()  { printf 'smsly-tier: %s\n' "$*" >&2; exit 1; }
note() { printf 'smsly-tier: %s\n' "$*"; }

PROFILE_ARGS=()
SMSLY_TIER_COMPOSE_FILE=""
SMSLY_TIER_SERVICES=""
SMSLY_TIER_PROJECT=""
TIER_NAME=""
TIER_RECREATE=0

load_tier() {
  local tier="${1:-}"
  [ -n "$tier" ] || die "tier name required"
  [[ "$tier" =~ ^[a-z0-9][a-z0-9_-]{0,63}$ ]] || die "invalid tier name '$tier'"
  local conf="$TIER_DIR/${tier}.conf"
  [ -f "$conf" ] || die "unknown tier '$tier' (expected $conf)"
  # Reset everything first: resleep() loads many tiers in one process and
  # sourcing must not leak one tier's file/project/profiles into the next.
  SMSLY_TIER_COMPOSE_FILE=""
  SMSLY_TIER_PROJECT=""
  SMSLY_TIER_PROFILES=""
  SMSLY_TIER_RECREATE=0
  SMSLY_TIER_SERVICES=""
  SMSLY_TIER_AUTOSLEEP=""
  SMSLY_TIER_IDLE_SECS=""
  # shellcheck disable=SC1090
  . "$conf" || die "failed to load $conf"
  [ -n "$SMSLY_TIER_SERVICES" ] || die "tier '$tier' defines no SMSLY_TIER_SERVICES"
  SMSLY_TIER_COMPOSE_FILE="${SMSLY_TIER_COMPOSE_FILE:-docker-compose.prod.yml}"
  SMSLY_TIER_PROJECT="${SMSLY_TIER_PROJECT:-}"
  TIER_RECREATE="${SMSLY_TIER_RECREATE:-0}"
  PROFILE_ARGS=()
  local p
  # shellcheck disable=SC2086
  for p in ${SMSLY_TIER_PROFILES:-}; do PROFILE_ARGS+=(--profile "$p"); done
  TIER_NAME="$tier"
}

compose() {
  [ -d "$INSTALL_DIR" ] || die "install dir not found: $INSTALL_DIR"
  cd "$INSTALL_DIR" || die "cannot cd $INSTALL_DIR"
  local -a env_args=()
  [ -f "$INSTALL_DIR/.env" ] && env_args=(--env-file "$INSTALL_DIR/.env")
  # SMSLY_TIER_PROJECT pins the compose project name when the tier lives
  # in a separate compose file brought up with -p (e.g. smsly-infisical).
  # Without it compose derives the project from the file's parent dir and
  # `up` would create DUPLICATE containers next to the installer's ones.
  if [ -n "$SMSLY_TIER_PROJECT" ]; then
    timeout -k 5 "${SMSLY_TIER_COMPOSE_TIMEOUT:-240}" docker compose -p "$SMSLY_TIER_PROJECT" "${env_args[@]}" -f "$SMSLY_TIER_COMPOSE_FILE" \
      ${PROFILE_ARGS[@]+"${PROFILE_ARGS[@]}"} "$@"
  else
    timeout -k 5 "${SMSLY_TIER_COMPOSE_TIMEOUT:-240}" docker compose "${env_args[@]}" -f "$SMSLY_TIER_COMPOSE_FILE" \
      ${PROFILE_ARGS[@]+"${PROFILE_ARGS[@]}"} "$@"
  fi
}

no_recreate_supported() {
  timeout -k 5 10 docker compose up --help 2>&1 | grep -q -- '--no-recreate'
}

check_stack_tier_compat() {
  [ "${SMSLY_FORCE_TIER_WAKE:-0}" = "1" ] && return 0
  [ -n "${SMSLY_TIER_PROFILES:-}" ] || return 0
  local env_file="$INSTALL_DIR/.env"
  [ -f "$env_file" ] || return 0

  local host_stack_tier="" host_profiles="" line=""
  line=$(grep -E '^STACK_TIER=' "$env_file" | tail -n 1 || true)
  host_stack_tier="${line#STACK_TIER=}"
  host_stack_tier="${host_stack_tier%\"}"
  host_stack_tier="${host_stack_tier#\"}"
  host_stack_tier=$(printf '%s' "$host_stack_tier" | tr '[:upper:]' '[:lower:]' | tr -d "'\r ")

  line=$(grep -E '^COMPOSE_PROFILES=' "$env_file" | tail -n 1 || true)
  host_profiles="${line#COMPOSE_PROFILES=}"
  host_profiles="${host_profiles%\"}"
  host_profiles="${host_profiles#\"}"
  host_profiles=$(printf '%s' "$host_profiles" | tr ',' ' ' | tr '[:upper:]' '[:lower:]' | tr -d "'\r")

  case "$host_stack_tier" in
    lite|medium) ;;
    *) return 0 ;;
  esac

  local allowed_profiles="$host_profiles"
  if [ "$host_stack_tier" = "medium" ]; then
    allowed_profiles="$allowed_profiles medium"
  fi

  local req="" ap="" matched=0
  for req in $SMSLY_TIER_PROFILES; do
    req=$(printf '%s' "$req" | tr '[:upper:]' '[:lower:]')
    for ap in $allowed_profiles; do
      if [ "$req" = "$ap" ]; then
        matched=1
        break 2
      fi
    done
  done

  if [ "$matched" -eq 0 ]; then
    die "tier '$TIER_NAME' requires profiles ($SMSLY_TIER_PROFILES) excluded on STACK_TIER=$host_stack_tier (set SMSLY_FORCE_TIER_WAKE=1 to override)"
  fi
}

tier_up() {
  check_stack_tier_compat || return 1
  local -a args=(up -d --no-deps)
  # Default is create-if-absent + start, NOT recreate: waking a tier should
  # never swap the running image out from under a service mid-flight.
  # Image/config changes are the installer's job, not the waker's.
  if [ "$TIER_RECREATE" != "1" ] && no_recreate_supported; then
    args+=(--no-recreate)
  fi
  # shellcheck disable=SC2086
  compose "${args[@]}" $SMSLY_TIER_SERVICES || return 1
  rm -f "$STATE_DIR/$TIER_NAME.asleep"
  note "tier '$TIER_NAME' awake: $SMSLY_TIER_SERVICES"
}

tier_down() {
  # `stop` only. See header note on why `down` is off-limits.
  # shellcheck disable=SC2086
  compose stop $SMSLY_TIER_SERVICES || return 1
  mkdir -p "$STATE_DIR"
  : > "$STATE_DIR/$TIER_NAME.asleep"
  if [ -z "${INVOCATION_ID:-}" ] && command -v systemctl >/dev/null 2>&1; then
    if systemctl is-active --quiet "smsly-tier@$TIER_NAME.service" 2>/dev/null; then
      timeout -k 5 30 systemctl stop "smsly-tier@$TIER_NAME.service" >/dev/null 2>&1 || true
    fi
  fi
  note "tier '$TIER_NAME' asleep: $SMSLY_TIER_SERVICES"
}

tier_status() {
  # shellcheck disable=SC2086
  compose ps $SMSLY_TIER_SERVICES
}

tier_resleep() {
  local f="" tier="" changed=0
  shopt -s nullglob
  for f in "$STATE_DIR"/*.asleep; do
    tier=$(basename "$f" .asleep)
    [ -f "$TIER_DIR/$tier.conf" ] || continue
    load_tier "$tier" || continue
    note "restoring sleep for '$tier' (an 'up' elsewhere woke it)"
    tier_down && changed=1
  done
  (( changed )) || note "no tiers marked asleep"
}

tier_list() {
  local f="" tier="" state=""
  shopt -s nullglob
  printf '%-18s %-9s %s\n' TIER STATE SERVICES
  for f in "$TIER_DIR"/*.conf; do
    tier=$(basename "$f" .conf)
    state=$([ -e "$STATE_DIR/$tier.asleep" ] && printf 'asleep' || printf 'awake')
    SMSLY_TIER_SERVICES=""
    # shellcheck disable=SC1090
    . "$f" 2>/dev/null || true
    printf '%-18s %-9s %s\n' "$tier" "$state" "${SMSLY_TIER_SERVICES:-?}"
  done
}

tier_request() {
  local tier="${1:-}"
  # Validate before writing the sentinel: an unknown tier would otherwise
  # leave a stray marker that fires smsly-tier-request@.path later, and
  # ExecStartPost (which clears it) never runs because ExecCondition skips.
  load_tier "$tier"
  if [ ! -d "$WAKE_DIR" ]; then
    mkdir -p "$WAKE_DIR" 2>/dev/null || true
    chmod 01733 "$WAKE_DIR" 2>/dev/null || true
  fi
  if [ ! -e "$STATE_DIR/$tier.asleep" ] && systemctl is-active --quiet "smsly-tier@$tier.service" 2>/dev/null; then
    # Already awake — clear any stale sentinel so it can't fire on the next stop.
    rm -f "$WAKE_DIR/$tier"
    note "tier '$tier' already awake"
  else
    : > "$WAKE_DIR/$tier"
    note "wake requested: $tier"
  fi
}

mkdir -p "$STATE_DIR" 2>/dev/null || true

cmd="${1:-}"
tier="${2:-}"
case "$cmd" in
  check)        load_tier "$tier" ;;
  up|start)     load_tier "$tier"; tier_up ;;
  down|stop)    load_tier "$tier"; tier_down ;;
  status)       load_tier "$tier"; tier_status ;;
  request)      tier_request "$tier" ;;
  resleep)      tier_resleep ;;
  list)         tier_list ;;
  *) die "usage: smsly-tier.sh {check|up|down|status|request|resleep|list} <tier>" ;;
esac
