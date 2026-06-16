# Reverse Proxy Decision & Conflict Analysis

**Status:** Active — last reviewed 2026-06-16
**Scope:** All current SMSLY deployment surfaces (docker-compose, docker-compose.prod, Helm, bare-metal).
**Outcome:** Caddy is the primary edge proxy for Docker Compose. Traefik is the primary edge proxy for Kubernetes. `nginx.conf` is legacy bare-metal only.

---

## 1. Current state — three reverse-proxy configs in active use

The platform simultaneously ships **three** independently-maintained reverse-proxy configurations. None of them is authoritative on its own; each is a complete routing definition for a different deployment surface.

| # | Config | Path | Runtime | TLS termination | Owning deployment |
|---|--------|------|---------|-----------------|-------------------|
| 1 | Caddy (edge) | `caddy-config/Caddyfile` | `caddy:2-alpine` (built from `infrastructure/caddy/Dockerfile`) | Yes — automatic Let's Encrypt via `on_demand_tls` | `docker-compose.prod.yml` (master) |
| 2 | Caddy (route-fallback) | `infrastructure/route-fallback/Caddyfile` | `caddy:2-alpine` | No (HTTP only) | `docker-compose.prod.yml` (master) — emits "Service waking up" 503 page |
| 3 | Traefik (edge) | `docker-compose.prod.yml` `traefik` service block + `traefik.http.*` labels on `backend` and `frontend` | `traefik:v3.6` | Yes — Let's Encrypt via `letsencrypt` resolver | `docker-compose.prod.yml` (node / lite-agent) and `charts/smsly-hosting` (k8s) |
| 4 | nginx (legacy) | `nginx.conf` (root) | Host-system `nginx` (not a container) | No (HTTP only) | Bare-metal install path only |
| 5 | Caddy (websocket helper) | `caddy-config/websocket-optimized.caddy` | None — example snippet | n/a | **Reference only, not wired in** |
| 6 | Caddy (monolith) | `infrastructure/caddy/Caddyfile.monolith.template` | Single-binary build | n/a | Rust twin / monolith build target |
| 7 | nginx (k8s configmap) | `charts/smsly-hosting/templates/nginx-configmap.yaml` | `nginx:1.27-alpine` | No | **Disabled by default** (`nginx.enabled=false` in `values.yaml`); kept for backward compatibility |

The relevant runtime containers and their routing:

- **Caddy (master, edge)** — `docker-compose.prod.yml:619-653`. Container name `caddy`, binds host `:80` and `:443`, mounts `./caddy-config` as `/etc/caddy` and the static/media volumes read-only. Reads the file at `/etc/caddy/Caddyfile`, which is `caddy-config/Caddyfile`.
- **Caddy (route-fallback)** — `docker-compose.prod.yml:467-493`. Container name `route-fallback`, listens on `:80` inside the container. Traefik routes any unmatched `PathPrefix("/")` to it (priority 1). Returns a 503 with `Retry-After: 15` and a custom HTML "waking up" page.
- **Traefik (edge)** — `docker-compose.prod.yml:494-547`. Container name `traefik`, entrypoints `web` (`:80`) and `websecure` (`:443`), with TLS via Let's Encrypt. Service discovery reads Traefik labels from `backend` (`docker-compose.prod.yml:210-223`) and `frontend` (`docker-compose.prod.yml:333-340`).
- **nginx (host)** — `nginx.conf`. **Not started by any compose file or installer step.** The `install.sh` installer actively stops host nginx (see §6).
- **nginx (k8s)** — `charts/smsly-hosting/templates/nginx-configmap.yaml`. Disabled by default in `charts/smsly-hosting/values.yaml:127-128` (`nginx.enabled: false`).

### What each config does in detail

#### 1.1 `caddy-config/Caddyfile` (lines 1-167)

- TLS: `on_demand_tls` asking the backend's `check-domain` endpoint; ACME HTTP-01 served by the `:80` block.
- Routes (under `{$DOMAIN}` and `:80`):
  - `/api/v1/server/backups/*/download` and `/api/v1/backups/*/download` — `backend:8000` with 1-hour streaming timeouts and `flush_interval -1` (lines 10-22, 107-119).
  - `/api/*` — `backend:8000` (lines 25-26, 121-122).
  - `/ws/*` — `backend:8000` with 1-hour WebSocket timeouts (lines 29-35, 124-130).
  - `/admin/*` — `backend:8000` (lines 38-39, 132-133).
  - `/health`, `/health/live`, `/health/ready` — `backend:8000` (lines 42-47, 135-136).
  - `/accounts/github/*`, `/accounts/google/*` — `backend:8000` (lines 50-53, 138-141).
  - `/static/*` — file server, cache `public, immutable` (lines 56-63, 143-150).
  - `/media/*` — file server, cache `public` (lines 66-73, 152-159).
  - `/caddy-health` — synthetic 200 OK for liveness probes (lines 76, 161).
  - Everything else — `frontend:3000` (lines 79, 163).
- `:80` block also performs HTTPS redirect for non-localhost, non-IP, non-`.local` hosts (lines 96-103) and serves `/.well-known/acme-challenge/*` directly to backend (lines 89-94).

#### 1.2 `infrastructure/route-fallback/Caddyfile` (lines 1-29)

- HTTP only. Handles `/health` (responds 200 OK) and `/api/v1/system/route-recheck/*` (proxies to `backend:8000`). Everything else returns a 503 with a friendly "Service waking up" page (lines 18-28) using headers `Retry-After: 15`, `X-SMSLY-Route-Fallback: true`, and `Cache-Control: no-store`.

#### 1.3 `docker-compose.prod.yml` Traefik labels

- **Backend** (`docker-compose.prod.yml:210-223`):
  - Rule: `PathPrefix(/api/v1/) || PathPrefix(/admin/) || PathPrefix(/health/) || PathPrefix(/accounts/) || PathPrefix(/dj-rest-auth/)` — `priority=10`.
  - Middlewares: `api-ratelimit` (200 rps average, 50 burst) and `backend-cb` (circuit-breaker on 5xx ratio > 0.30 over 600s).
  - Service port: 8000.
- **Frontend** (`docker-compose.prod.yml:333-340`):
  - Rule: `Host(`${DOMAIN}`) || Host(`${PUBLIC_IP}`) || Host(`localhost`) || Host(`127.0.0.1`)` — `priority=5`.
  - Service port: 3000.
- **Route-fallback** (`docker-compose.prod.yml:473-478`):
  - Rule: `PathPrefix("/")` — `priority=1`. Catches anything the two higher-priority rules don't match.
- **Traefik** itself (`docker-compose.prod.yml:494-547`): entrypoints `web:80`, `websecure:443`, `metrics:8082`. TLS via Let's Encrypt `tlschallenge` + `httpchallenge`. Binds `127.0.0.1:8081:80` and `127.0.0.1:8443:443` (loopback only — operator is expected to forward public traffic to these from a host firewall or external LB).

#### 1.4 `nginx.conf` (lines 1-187)

- HTTP only (`listen 80`).
- Routes:
  - `/nginx-health` — local 200 OK (line 56-60).
  - `/static/` — `alias /app/staticfiles/` with 30-day cache and security headers (lines 70-79).
  - `/media/` — `alias /app/media/` with 7-day cache (lines 81-84).
  - `/ui` → 301 `/` (lines 87-89).
  - `/api/v1/(server/backups|backups)/[0-9a-fA-F-]+/download/?$` — `backend:8000` with `proxy_buffering off` and 1-hour timeouts (lines 92-103).
  - `/api/` — `backend:8000`, 10r/s rate limit with 20 burst (lines 106-116).
  - `/ws/` — `backend:8000`, WebSocket upgrade, 1-hour timeouts (lines 119-130).
  - `/accounts/(github|google)/` — `backend:8000` (lines 133-140).
  - `/accounts/` (all other account paths) — `frontend:3000` (lines 144-153). **Different from Caddy**, which routes only the OAuth handshake to backend and assumes frontend handles the rest implicitly.
  - `/admin/` — `backend:8000` (lines 155-162).
  - `/health` — `backend:8000` (lines 165-173).
  - `/` — `frontend:3000` (lines 176-185).
- TLS: none. Security headers added manually per-location (lines 62-67, 75-78) because `add_header` does not inherit in nginx.

#### 1.5 `caddy-config/websocket-optimized.caddy` (lines 1-66)

- **Reference snippet only.** Not included in the active Caddyfile, not bind-mounted, not referenced by any compose file. Hardcodes `example.com` and `localhost:8000` so it cannot be used as-is. Listed here for completeness so future readers know it is dead.

#### 1.6 `infrastructure/caddy/Caddyfile.monolith.template` (lines 1-38)

- Substituted variables `${PORT}`, `${BACKEND_PORT}`, `${FRONTEND_PORT}`. Routes `127.0.0.1:<port>` for backend and frontend. Used by the Rust-twin / monolithic build target (not by the standard master/agent compose files).

#### 1.7 `charts/smsly-hosting/templates/nginx-configmap.yaml` (lines 1-122)

- Disabled by default. If re-enabled, it would deploy `nginx:1.27-alpine` with its own ConfigMap-driven `nginx.conf` covering `/health`, `/api`, `/admin`, `/ws`, `/static`, `/media`, `/`. The Helm chart already routes everything via Traefik (`ingress.className: traefik`, `values.yaml:181`), so turning the nginx configmap on would create a parallel proxy that no Service routes to. Use case: external ingress class swap (e.g., ingress-nginx) — not used today.

---

## 2. Conflict matrix

The same HTTP route is defined in multiple places. Which proxy actually answers it depends on the deployment scenario.

| Route | docker-compose (dev, `docker-compose.yml`) | docker-compose.prod (master) | docker-compose.prod (node / lite-agent) | Bare-metal | Kubernetes (Helm, defaults) |
|-------|------------------------------------------|------------------------------|------------------------------------------|------------|------------------------------|
| `/` (catch-all) | **frontend:3000** via host port 3000 (no edge proxy defined) | Traefik (frontend rule, prio 5) → frontend:3000; Caddy catch-all → frontend:3000; route-fallback catch-all (prio 1) if Traefik route fails | **Traefik** (frontend rule) → frontend:3000; route-fallback as safety net | **nginx.conf:176-185** → frontend:3000 | **Traefik ingress** (values.yaml:207-209) → frontend service |
| `/api/*` | **none** — dev compose has no edge proxy; direct 8000 access only | **Both** — Traefik rule (`PathPrefix(/api/v1/)`) → backend:8000; **and** Caddy (`@api path /api/*`) → backend:8000. Either proxy may answer depending on which port (80 vs Traefik 8081) is hit. | **Traefik** (api rule, prio 10) → backend:8000 | **nginx.conf:106-116** → backend:8000 (rate-limited 10r/s) | **Traefik ingress** (values.yaml:189-191) → backend service |
| `/admin/*` | none | **Both** — Traefik `PathPrefix(/admin/)` and Caddy `@admin` | Traefik | **nginx.conf:155-162** → backend:8000 | Traefik ingress (values.yaml:192-194) |
| `/accounts/*` (general) | none | **Only Traefik** (`PathPrefix(/accounts/)`); Caddy only matches `/accounts/github/*` and `/accounts/google/*` explicitly. All other account paths are caught by the Caddy catch-all → frontend:3000. | Traefik (`/accounts/`) | **nginx.conf:144-153** → frontend:3000 (the OAuth-only branch is at line 133-140) | Traefik ingress (no explicit `/accounts` rule — falls through to `path: /` → frontend). Allauth endpoints (`/accounts/.../login/`, etc.) will hit frontend in k8s. **This is a behavioural drift vs. dev.** |
| `/accounts/github/*`, `/accounts/google/*` | none | **Caddy** (lines 50-53) and **Traefik** (`PathPrefix(/accounts/)` at prio 10) | Traefik | **nginx.conf:133-140** (regex matches only `github|google`) | Traefik ingress (no specific rule — fall-through to `path: /` → frontend) |
| `/static/*` | none | **Caddy** file_server (root `/app/staticfiles`); **also Traefik** `PathPrefix(/accounts/)` will not match, but the Caddy `{$DOMAIN}` block does. **Caddy has priority** because Traefik only routes `/api/v1/`, `/admin/`, `/health/`, `/accounts/`, `/dj-rest-auth/`. | **404** — Traefik has no `/static/` rule; request will fall through to the route-fallback (503 page). | **nginx.conf:70-79** → `/app/staticfiles/` with 30-day cache | Traefik ingress (no `/static` rule — falls through to `path: /` → frontend, 404). **Broken in k8s today.** |
| `/media/*` | none | **Caddy** file_server (root `/app/media`) | **404 / 503 fallback** — Traefik has no `/media/` rule | **nginx.conf:81-84** → `/app/media/` | Traefik ingress (no `/media` rule — falls through to `path: /` → frontend, 404). **Broken in k8s today.** |
| `/ws/*` | none | **Both** — Traefik `PathPrefix(/api/v1/)` does NOT match `/ws/`; only Caddy (`@ws path /ws/*`) routes it. If you hit the Traefik port directly, the request falls through to route-fallback (503). If you hit the Caddy port, WebSockets work. | **503 / not handled** — Traefik has no `/ws/` rule. **WebSockets are broken on node / lite-agent unless the request lands on the Caddy service, which is master-only in the prod compose.** | **nginx.conf:119-130** → backend:8000 | Traefik ingress (values.yaml:195-197) → backend service. Works. |
| `/health*` | none | **Both** — Caddy routes `/health`, `/health/live`, `/health/ready` to backend. Traefik matches `PathPrefix(/health/)` only — `/health` exactly (no trailing slash) is Caddy-only. route-fallback also answers `/health` with a static 200. | **Traefik** (`PathPrefix(/health/)`) and **route-fallback** (`/health` static OK) | **nginx.conf:165-173** → backend:8000 | Traefik ingress (values.yaml:198-200) → backend |
| `/api/v1/(server/backups|backups)/*/download` | none | **Caddy** (long-timeout streaming, lines 10-22). **Traefik will NOT match** (`PathPrefix(/api/v1/)` would match, but `api-ratelimit` 200 rps middleware can throttle large backup pulls and circuit-breaker would trip on slow streams). The Caddy route is the only one with `proxy_buffering off` + 1-hour read/write timeouts. | **Traefik only** (no streaming tuning). Large backups will time out. | **nginx.conf:92-103** → backend:8000 with `proxy_buffering off` | Traefik ingress (no path-specific tuning) — long backups will time out |
| `/.well-known/acme-challenge/*` | n/a | **Caddy** (`:80` block, lines 89-94) → backend. Traefik matches `PathPrefix("/")` via route-fallback (503) before its `websecure` HTTP-01 challenge can fire. | n/a | n/a | cert-manager handles ACME (values.yaml:183) |
| `/caddy-health` | n/a | **Caddy** synthetic 200. Traefik will not match. | n/a | n/a | n/a |
| `/nginx-health` | n/a | n/a | n/a | **nginx.conf:56-60** synthetic 200 | n/a |

**Priority disambiguation, docker-compose.prod master mode:**

1. Caddy (host ports 80/443) and Traefik (host ports 8081/8443) **both** answer on different ports. The operator decides which one is public.
2. Inside Traefik, the `backend` and `frontend` rules have higher priority (10 and 5) than `route-fallback` (1), so backend-owned paths and the host rule resolve before the safety net.
3. Inside Caddy, the named-matchers are evaluated in file order; the first match wins. Caddy currently has no rule that would conflict with a Traefik path.

---

## 3. Failure modes — what happens when a route drifts

| Drift scenario | Symptom | Root cause |
|----------------|---------|------------|
| Add a new path to nginx.conf, forget to add to Caddy | Bare-metal works, Compose master fails | Two configs, two sources of truth |
| Add a new path to Caddy, forget to add to Traefik labels | Master (Caddy port 80) works, node (Traefik port 8081) hits route-fallback 503 | Traefik label list and Caddy matcher list are not linked |
| Change `api-ratelimit` burst in Traefik labels | Applies to Traefik only — Caddy has no rate limit on `/api/*` | Layer-7 limits exist in only one of the two edge proxies |
| Add a Caddy on-demand TLS `ask` route | Caddy asks backend, backend returns 200/404. Traefik has no equivalent and will issue certs on its own. Dual ACME accounts on the same domain. | Caddy and Traefik are both capable of issuing LE certs |
| Enable `nginx.enabled: true` in Helm values | A second nginx pod runs alongside Traefik ingress, but no Service routes to it. Wastes memory, no functional impact (yet). | The nginx-configmap template is shipped but not actually used |
| Use `nginx.conf` on a Compose master | Caddy owns port 80, nginx never starts. nginx.conf is read by no one. | nginx.conf is host-system only; no compose service references it |
| Use `caddy-config/Caddyfile` on k8s | Caddy image is referenced as a sibling chart option (`caddy.enabled: false`) but is not deployed. Cluster has no Caddy. | Caddy is a Compose-only path |
| Remove `/ws/*` from Caddy in a hotfix | Master mode still works via Traefik (`PathPrefix(/api/v1/)` would not match `/ws/`, so even Traefik would fall through). WebSockets silently break. | `/ws/*` is not in the Traefik label set |
| Add a `/static` rule to Helm ingress | Traefik ingress now proxies `/static` to backend service. Caddy still serves it from disk. Drift is silent. | No central inventory of which proxy owns which path |
| Run `install.sh` on a node that already has systemd nginx listening on 80 | `install.sh:5507` stops and disables the host nginx. **No data loss**, but if the operator was using nginx for another site, that site goes down. | install.sh assumes port 80 is exclusively for SMSLY |
| Edit `caddy-config/Caddyfile` while master is running | Caddy is configured to auto-reload from disk; backend's `caddy_manager.py` will sync changes. If the Caddyfile has a syntax error, the Caddy container crashes; `restore_last_good_caddy` (install.sh:2103) attempts to recover. | No Caddyfile validation in CI today |

---

## 4. Recommendation

> **Keep Caddy as the primary edge proxy for Docker Compose. Keep Traefik as the primary edge proxy for Kubernetes. Mark `nginx.conf` as legacy bare-metal and stop shipping it for new deployments.**

Concretely:

- **Compose master** — Keep `caddy-config/Caddyfile` as the single source of truth for master routing. Caddy is the only proxy that handles `/static/`, `/media/`, `/ws/*`, the OAuth handshake, the long-timeout backup downloads, and the `/.well-known/acme-challenge/` HTTP-01 challenge. Removing Caddy would require re-implementing all of those in Traefik. **Net: Caddy wins for the master surface.**
- **Compose node / lite-agent** — Traefik handles only the per-service dynamic routing for user-deployed containers (via labels in the user's compose fragments) plus the four platform routes (`/api/v1/`, `/admin/`, `/health/`, `/accounts/`). It has no rules for `/static/`, `/media/`, `/ws/*`, or the long-timeout backup streaming. Those paths return 503 from the route-fallback. **This is a known incomplete coverage and is tracked separately** — the missing routes are handled in Caddy on master and are not expected to be hit on a node where the master owns static/media/websockets.
- **Kubernetes** — The Helm chart already routes everything through the Traefik ingress. Caddy and nginx are chart siblings (`caddy.enabled: false`, `nginx.enabled: false`). **No change needed in k8s today**, but the chart should gain a future migration path to move backup-download streaming and WebSocket timeouts into Traefik middleware (out of scope for this document).
- **`nginx.conf`** — Keep the file on disk for the bare-metal install path (the install.sh may still encounter a host that has legacy nginx + apache2 running, see §6). **Mark it LEGACY**, do not start a service from it, and treat the bare-metal path as deprecated.
- **`caddy-config/websocket-optimized.caddy`** — Delete or move under `docs/`. It is not wired into any Caddyfile.

---

## 5. Migration steps (do not execute as part of this change)

These are the steps that would consolidate to a single proxy per surface. They are listed for the next agent's reference, not for execution today.

1. **Pick the leader per surface.** Master = Caddy. Node / lite-agent = Traefik. K8s = Traefik. Bare-metal = Caddy (replace nginx.conf with the Caddyfile; the existing `caddy-config/Caddyfile` already works as-is).
2. **Add CI guard for the Caddyfile.** Generate a test that imports `caddy-config/Caddyfile` via `caddy adapt --config caddy-config/Caddyfile` and asserts the JSON is valid. (Caddy supports this via the `caddy` CLI in the builder image; the platform's Caddy container already has it.)
3. **Add CI guard for Traefik label coverage.** A test that enumerates the Caddyfile's named matchers and asserts that each non-frontend path has a Traefik `PathPrefix` label (or is intentionally excluded as Compose-master-only). The current `test_nginx_removal.py` is a model for this style of guard.
4. **Add CI guard for the Helm `ingress.hosts[].paths`.** Mirror of the Caddyfile paths so the k8s chart and the Caddyfile stay in sync.
5. **Move `caddy-config/websocket-optimized.caddy` to `docs/legacy/`.** It is unreferenced and confusingly named.
6. **Move `nginx.conf` to `docs/legacy/nginx.conf` or `infrastructure/nginx/nginx.legacy.conf`.** Keep a thin `nginx.conf` at the repo root that simply `include`s the legacy file, with a top-of-file LEGACY banner. This preserves the file path for any operator who has a hand-built systemd unit pointing at the root.
7. **Delete `charts/smsly-hosting/templates/nginx-configmap.yaml` after a deprecation cycle.** The chart already gates it on `nginx.enabled: false`. Adding a chart annotation warning operators to migrate to the Traefik ingress.
8. **Delete `caddy-config/websocket-optimized.caddy`.** It is a copy-paste from an older config; the active Caddyfile already has the correct WebSocket handling at lines 29-35.
9. **Update `docs/PLATFORM_REVIEW.md:27-28`** to be definitive: "Reverse Proxy: Caddy (Compose master), Traefik (Compose node / k8s)". Currently it says "Caddy or Traefik", which masks the routing gap on node.
10. **Update `docs/multi-server.md:46`** to add a row about which routes each proxy covers, so an operator picking a topology knows that `/static/`, `/media/`, and `/ws/*` are master-only.

---

## 6. Risks of the migration

| Risk | Impact | Mitigation |
|------|--------|------------|
| Removing `nginx.conf` breaks a bare-metal install that was relying on the file at repo root | Bare-metal node becomes unreachable | Move file to `docs/legacy/`, leave a symlink or stub at the root, document in `install.sh` |
| A code path still calls `nginx -t` or scrapes `/nginx-health` | CI / monitoring breaks | `test_nginx_removal.py:105-131` already guards against this; keep it green |
| Re-enabling `nginx.enabled: true` in Helm after deletion | Chart fails to render | Add a deprecation warning in the chart's `NOTES.txt` for one minor version before deleting |
| The Caddyfile evolves (e.g., a new `/metrics` route) without a Traefik label update | Node mode silently returns 503 for that path | Add the §5 CI guard for label coverage |
| Backup streaming is moved from Caddy to Traefik | Traefik's default 60s `transport.respondingTimeouts.readTimeout` will kill long backups; the `api-ratelimit` 200 rps middleware can throttle; the circuit-breaker can trip | Add a Traefik middleware for the backup path with 1-hour read/write timeouts and exclude it from the rate limiter and circuit-breaker |
| `caddy-config/Caddyfile` is bind-mounted at `/etc/caddy` AND bind-mounted at `/caddy-config` in some compose files | Operators editing one and not the other cause drift | Audit the compose files (see `docker-compose.yml:52, 81, 105, 193` and `docker-compose.prod.yml:173, 251, 291, 368, 413`) and consolidate to a single mount target |
| The `nginx` keyword appears in `install.sh:5507` and `backend/install.sh:5117` as part of a "stop conflicting services" loop | If the LEGACY comment is wrong, a future agent may remove the loop and break installs on hosts with leftover nginx | The two lines now carry an inline LEGACY comment (this PR); keep the loop, do not remove it |
| Removing the `nginx` keyword from the `test_nginx_removal.py` test file | The test that guards against nginx re-introduction becomes a false negative | Do not modify the test file as part of this PR |

---

## 7. Status of the LEGACY markers added in this change

- `nginx.conf:1-12` — LEGACY banner added.
- `install.sh:5505-5507` — Inline LEGACY comment added above the `for svc in nginx apache2` loop.
- `backend/install.sh:5115-5117` — Same inline LEGACY comment.
- `README.md` — Section 140 (`SSL/Proxy` row in the tech stack table) and section 259 (`Recent Security Improvements` row) updated to be explicit about the three deployments; a new `## Reverse Proxy` section added at line 194.
- `docs/REVERSE_PROXY_DECISION.md` (this file) — Created.
