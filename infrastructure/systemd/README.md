# SMSLY PaaS — sleepable infra tiers
#
# On-demand stop/start for PaaS infra services on master and node servers.
# Tier definitions are plain shell fragments sourced by scripts/smsly-tier.sh,
# so the mechanism is service-agnostic: add a file here, no code changes.
#
# ── The one rule that makes this work ──────────────────────────────────────
# Only services behind a NON-DEFAULT compose profile can sleep durably.
# A default-profile service is started by any `docker compose up -d`,
# including install.sh --update, so it would silently wake again.
#
# ── Command surface ───────────────────────────────────────────────────────
#   systemctl start smsly-tier@<tier>.service     # wake
#   systemctl stop  smsly-tier@<tier>.service     # sleep
#   systemctl stop  smsly-idle.target             # sleep every tier at once
#   scripts/smsly-tier.sh list                    # tier + sleep state
#   scripts/smsly-tier.sh request <tier>          # wake without root
#   scripts/smsly-tier.sh resleep                 # re-apply after an update
#
# ── Install ───────────────────────────────────────────────────────────────
# Preferred: the installer does all of this (lib/tiers_sleep.sh +
# lib/napd.sh, warn-only, opt-in — no tier is started).
# Manual equivalent:
#   install -m 0755 scripts/smsly-tier.sh          /opt/smsly-hosting/scripts/
#   install -m 0644 infrastructure/systemd/smsly-* /etc/systemd/system/
#   install -m 0644 infrastructure/systemd/tiers/* /etc/smsly/tiers.d/
#   systemctl daemon-reload
#   systemctl enable --now smsly-paas.target
#   systemctl enable --now smsly-tier@buildcache.service
#   systemctl enable --now smsly-tier-autowake@buildcache.timer   # if AUTOWAKE=1
#
# ── napd: on-demand wake + idle reaper ──────────────────────────────────
# smsly-napd (napd/, host systemd unit) wakes tiers over HTTP and sleeps
# idle ones:  GET /wake?tier=X | GET /sleep?tier=X | GET /status
# (X-Napd-Secret header, fail-closed without NAPD_SHARED_SECRET).
# The backend wakes tiers cooperatively before using them
# (backend/apps/deployments/services/tiers.py: `secrets` before Infisical
# calls, `buildcache` before builds) — a sleeping tier degrades to the
# usual connection error, never to a new failure mode.
# Idle sleep is DURATION-based, not activity-based: tiers with
# SMSLY_TIER_AUTOSLEEP=1 + SMSLY_TIER_IDLE_SECS=N are stopped after N
# seconds awake. Only interruptible tiers may opt in (caches: 2h,
# dashboards: 30m). /sleep refuses non-autosleep tiers without force=1.
#
# ── Safety properties ─────────────────────────────────────────────────────
# • Never runs `compose down` and never passes `--remove-orphans`. `down`
#   destroys containers + networks and would take caddy_data/acme.json,
#   media_volume, static_volume and backups_data with it.
# • Wake uses `--no-recreate` by default, so waking a tier never swaps the
#   running image mid-flight. Image changes belong to the installer.
# • Tiers are opt-in per host. Nothing here enables itself.
# • Every service in this stack is `restart: unless-stopped`, so a tier that
#   is stopped stays stopped across reboots and dockerd restarts.
#
# ── What is deliberately NOT tiered ────────────────────────────────────────
#   Request path / state   socket-proxy, traefik, caddy, route-fallback,
#                          sablier, registry, coredns, db, redis-*, rabbitmq,
#                          pgbouncer-tenants, pgcat, frps, backend, celery*,
#                          frontend, crowdsec*
#   Log ingestion           loki, promtail, loki-log-bridge
#   Alerting                alertmanager (delivery lost while down)
#   SPIRE                   spire-agent*, spire-server* — join tokens are
#                          single-use and minted per boot by lib/harden.sh
#                          and apps/mtls/views.py; `restart: "no"`
#   HA data planes          etcd, patroni1-3, haproxy, db, postgres-replica
#
# On nodes and agent-lites there is nothing to gate: `docker-compose.node.yml`
# has exactly one profile-gated service (spire-agent, which is excluded) and
# `docker-compose.agent-lite.yml` has none. This mechanism is master-only
# unless those stacks are re-profiled first.