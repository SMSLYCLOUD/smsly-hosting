# Trulay Grid security gap review — 2026-11-19 (Updated)

## Scope and evidence
Full L1–L7 and R4 control-plane remediation across `smsly-hosting`, verified by the `tests/e2e_security/` suite (`181` tests), focused unit/integration suites, and live deployment verification on `trulay-vps` and `aws-full-2`.

## 1. Critical: global recovery phrase generation, rotation, and single-use consumption — FIXED
- `backend/apps/core/views/recovery.py` enforces `POST`-only generation (`405` on `GET`), requires an active superuser with full token management privileges (`CanManageAPITokens`), embeds `created_at` and `expires_at` (`RECOVERY_PHRASE_TTL_DAYS`, default 90 days), consumes and clears `recovery_phrase_hash` atomically under `select_for_update()` upon successful verification (`single_use: True`), provides `POST/DELETE /api/v1/auth/recovery/revoke/`, and records `AuditLog` events.

## 2. High: server backup download authorization — FIXED
- `backend/apps/deployments/views/server_backup.py` requires `IsAdminUser` on `download` and `download_key`; signed query parameters no longer bypass the admin gate, and malformed UUIDs or empty local paths fail closed with `404`.

## 3. Medium: public route recheck & trusted-proxy client IP / atomic rate limiting — FIXED
- `backend/apps/deployments/views/route.py` is read-only (does not invoke `reset_restart_state` or `_check_service_health`) and rate-limits atomically via `cache.add`.
- `backend/apps/core/rate_limiting.py` (`get_trusted_client_ip`, `TrustedIdentMixin`) rejects spoofed `CF-Connecting-IP`, `X-Real-IP`, and `X-Forwarded-For` headers from untrusted public peers, supports CIDR entries in `TRUSTED_PROXY_IPS`, and backs `RateLimitMiddleware`, `node_exchange.py`, `agent_registrar_auth.py`, `health_webhook.py`, and `tunnels/rate_limit.py` (upgraded to atomic `cache.add`/`cache.incr`).
- HMAC replay nonces in `middleware/security.py`, `agent_registrar_auth.py`, `deployments/views/_helpers.py`, and `health_webhook.py` verify signatures before consuming nonces atomically via `cache.add` (fail-closed on cache errors).

## 4. Medium: merged-Compose and Nixpacks build containment — FIXED
- `backend/apps/deployments/services/pipeline/compose_networking.py` and `build.py` interpolate `${VAR}` expressions against the merged build environment, reject top-level volume `driver_opts.device` bind escapes, reject external or protected platform networks (`smsly-platform-net`, `smsly-build-net`, `traefik-proxy-net`, `socket-proxy`, `host`), reject `build.network: host`, `build.privileged: true`, `network_mode: container:*`, `cgroup_parent`, and `env_file` path traversal, and enforce effective `mem_limit`, `memswap_limit`, `cpus`, `pids_limit`, `cap_drop: ["ALL"]`, and `no-new-privileges:true` on every Compose service.
- `backend/apps/cloud/services/builder.py` (`NixpacksBuilder.build_image`), `pipeline/build.py::_build_with_nixpacks`, and `build_nixpacks.py` run builds inside ephemeral resource-capped BuildKit containers on isolated `smsly-build-net` (`icc=false`), block dangerous builder environment overrides (`DOCKER_HOST`, `LD_PRELOAD`, `BUILDX_BUILDER`, etc.), and validate Nixpacks config files against symlink escape and host-escape flags.

