# Trulay Grid - Developer Guide

> Internal reference for contributors and AI agents. Covers architecture decisions, recent changes, and coding conventions.

Product copy uses **Trulay Grid**. Legacy `SMSLY_*` environment variables, `smsly-*` resources, module paths, task names, and persisted identifiers remain unchanged unless a migration explicitly replaces them.

---

## Backend Architecture

### Django Apps

| App | Purpose |
|-----|---------|
| `apps.deployments` | Service deployment lifecycle, build pipeline, webhooks, container orchestration, servers, mesh, backups, templates, ecosystem, SafeDeploy |
| `apps.cloud` | Cloud provider abstraction (Docker, AWS, Azure, GCP), code analysis, ecosystem deploy |
| `apps.teams` | Multi-tenancy, team membership, RBAC |
| `apps.organizations` | Organization-level multi-tenancy, SSO |
| `apps.billing` | Stripe/Flutterwave/Cryptomus subscriptions, usage metering, invoices |
| `apps.intelligence` | AI-powered diagnostics (multi-provider: OpenAI, Gemini, Claude, DeepSeek, Mistral, Ollama, etc.) |
| `apps.domains` | Custom domain management, DNS validation, SSL provisioning |
| `apps.core` | Auth (JWT/cookies), rate limiting, observability, health checks, admin users |
| `apps.notifications` | Multi-channel dispatch (email/SMS/webhook/in-app) |
| `apps.addons` | Database/cache addon provisioning, DB proxy, maintenance |
| `apps.autoscaler` | Container autoscaling engine (metrics → decisions → scale) |
| `apps.licensing` | Offline license validation, tier enforcement |
| `apps.permissions` | 28 fine-grained permission codes, permission audit middleware |
| `apps.mcp` | Model Context Protocol server for AI tool integration (18 tools) |
| `apps.media` | Media node management (LiveKit), attestation, telemetry |

### Request Flow

```
Client → Caddy (SSL) → Gunicorn (:8000) → Django
                                                            ↓
                                        Middleware Chain (20 layers):
                                        1.  PrometheusBeforeMiddleware
                                        2.  DynamicAllowedHostsMiddleware
                                        3.  CorsMiddleware
                                        4.  SecurityMiddleware (Django — HSTS, SSL redirect)
                                        5.  WhiteNoiseMiddleware
                                        6.  SessionMiddleware
                                        7.  CommonMiddleware
                                        8.  CsrfViewMiddleware
                                        9.  AuthenticationMiddleware
                                        10. OTPMiddleware (2FA)
                                        11. MessageMiddleware
                                        12. XFrameOptionsMiddleware
                                        13. PermissionAuditMiddleware
                                        14. SecurityMiddleware (HMAC V2 — inter-service auth)
                                        15. RateLimitMiddleware (IP-based, anon only)
                                        16. DeviceTrustMiddleware [Beta]
                                        17. GracefulShutdownMiddleware
                                        18. TierLimitsMiddleware (license enforcement)
                                        19. AccountMiddleware (allauth)
                                        20. PrometheusAfterMiddleware
```

### Middleware Stack (order matters)

| # | Middleware | Layer | Purpose |
|---|-----------|-------|---------|
| 1 | `PrometheusBeforeMiddleware` | Metrics | Request metrics collection |
| 2 | `DynamicAllowedHostsMiddleware` | Host sync | Syncs `ALLOWED_HOSTS` from PlatformConfig DB |
| 3 | `CorsMiddleware` | CORS | Cross-origin headers |
| 4 | `SecurityMiddleware` (Django) | Transport | HSTS, SSL redirect |
| 5 | `WhiteNoiseMiddleware` | Static | Static file serving |
| 6 | `SessionMiddleware` | Session | Session management |
| 7 | `CommonMiddleware` | URL | URL normalization |
| 8 | `CsrfViewMiddleware` | CSRF | CSRF protection |
| 9 | `AuthenticationMiddleware` | Auth | User authentication |
| 10 | `OTPMiddleware` | Auth | Two-factor authentication |
| 11 | `MessageMiddleware` | UI | Flash messages |
| 12 | `XFrameOptionsMiddleware` | Security | Clickjacking protection |
| 13 | `PermissionAuditMiddleware` | Audit | Records every 403 for authenticated users |
| 14 | `SecurityMiddleware` (custom) | HMAC V2 | Inter-service authentication (skips already-authenticated users) |
| 15 | `RateLimitMiddleware` | IP-based | DDoS protection for anonymous requests only |
| 16 | `DeviceTrustMiddleware` | Device | Hardware fingerprint enforcement (beta) |
| 17 | `GracefulShutdownMiddleware` | Lifecycle | SIGTERM handling |
| 18 | `TierLimitsMiddleware` | Licensing | License tier enforcement (pruned in agent mode) |
| 19 | `AccountMiddleware` | allauth | Account management |
| 20 | `PrometheusAfterMiddleware` | Metrics | Response metrics |

> **Important**: `RateLimitMiddleware` (position 15) runs *after* `AuthenticationMiddleware` (position 9). It skips authenticated users — DRF handles their throttling instead. The HMAC V2 middleware (position 14) also skips already-authenticated users.

---

## Rate Limiting Architecture

Two layers, each with a distinct purpose:

### Layer 1: Middleware (IP-based, anonymous only)
- File: `apps/core/middleware/ratelimit.py`
- Limit: **10,000 requests per minute** per IP (`API_RATE_LIMIT` setting)
- Scope: Only `/api/` requests from **unauthenticated** users
- Backend: Redis cache (`ratelimit:{ip}:{window}`)
- Fail mode: Configurable via `API_RATE_LIMIT_FAIL_CLOSED` (default: open/degraded)

### Layer 2: DRF Throttle (user-based, authenticated)
- File: `config/settings.py` → `REST_FRAMEWORK.DEFAULT_THROTTLE_RATES`
- Rates:
  - `anon`: 10,000/hour
  - `user`: 5,000/hour (~83/min)
  - `deployments`: 10,000/minute
  - `deployment_burst`: 1,000/minute
  - `transfers`: 30/minute
  - `server_run_command`: 10/minute
  - `server_run_command_burst`: 2/minute
  - `server_commands`: 2/minute
  - `server_heal`: 10/minute
  - `server_proxy`: 30/minute
  - `server_check_all`: 2/minute
  - `server_provision`: 30/hour
  - `caddy_ask`: 60/minute
  - `ai_chat`: 30/minute
  - `ai_analysis`: 10/minute
  - `ecosystem_bulk_env`: 10/hour

### Why Two Layers
- Middleware runs **after** authentication but only acts on **anonymous** requests — catches DDoS from IPs that never log in
- DRF throttle runs per-user — fairness for legitimate authenticated users
- Dashboard polling (deployments: 5s, env_vars, services) consumes ~36 req/min from a single page

---

## Deployment Pipeline

### Flow

```
User clicks Deploy → Deployment(QUEUED)
                          ↓
                   smart_deploy_task
                          ↓
              ┌── skip_review=True? ──┐
              │                       │
              No                     Yes
              ↓                       ↓
    AI Analysis Phase           Build Phase
    (resource recs)             (Docker/Nixpacks)
              ↓                       ↓
    Deployment(REVIEW)          Deployment(BUILDING)
    [User: Approve/Cancel]            ↓
              ↓                 Deployment(DEPLOYING)
    Deployment(BUILDING)              ↓
              ↓                 Deployment(HEALTH_CHECK)
    Deployment(DEPLOYING)             ↓
              ↓                 Deployment(ACTIVE)
    Deployment(HEALTH_CHECK)
              ↓
    Deployment(ACTIVE)
```

### `skip_review` Flag

The `smart_deploy_task` accepts `skip_review=True` to bypass the AI analysis + review gate:

```python
smart_deploy_task.delay(str(deployment.id), str(provider.id), skip_review=True)
```

**When `skip_review=True` is used:**
- Service restarts (`views/service/core.py` restart action)
- GitHub webhook push events (`webhooks/github.py`)
- Preview environment updates

**When `skip_review=False` (default):**
- Manual deployments from the dashboard
- File upload deployments

### Deployment Statuses

| Status | Description | UI |
|--------|-------------|-----|
| `QUEUED` | Waiting to start | Gray, Cancel button |
| `REVIEW` | AI analysis done, awaiting approval | Amber pulsing eye, Approve + Cancel |
| `BUILDING` | Docker build in progress | Blue spinner, Cancel button |
| `BUILD_FAILED` | Build step failed | Red X |
| `AWAITING_APPROVAL` | SafeDeploy approval gate | Amber, Approve + Cancel |
| `BACKUP_RUNNING` | Pre-deploy backup in progress | Blue spinner |
| `BACKUP_FAILED` | Pre-deploy backup failed | Red X |
| `MIGRATION_PLANNING` | Migration validation in progress | Blue spinner |
| `MIGRATION_RUNNING` | Database migration running | Blue spinner |
| `MIGRATION_FAILED` | Migration failed | Red X |
| `DEPLOYING` | Container deployment in progress | Blue spinner |
| `HEALTH_CHECK` | Post-deploy health check | Blue spinner |
| `HEALTH_CHECK_FAILED` | Health check failed (may trigger rollback) | Red X |
| `ACTIVE` | Successfully deployed | Green checkmark |
| `FAILED` | Build or deploy failed | Red X |
| `CANCELLED` | User cancelled | Gray ban icon |
| `INACTIVE` | Previous deployment superseded | Gray |
| `ROLLING_BACK` | Rollback in progress | Orange spinner |
| `ROLLED_BACK` | Rolled back to previous | Orange checkmark |

### Key Files

| File | Purpose |
|------|---------|
| `apps/deployments/tasks/` | Task package (29+ modules): `tasks_deploy.py`, `tasks_build.py`, `tasks_ai.py`, etc. |
| `apps/deployments/views/` | View package: `views/service/core.py`, `views/deployment/core.py`, etc. |
| `apps/deployments/webhooks/github.py` | `GitHubWebhookHandler`, push + PR events |
| `apps/deployments/signals.py` | `post_save` for Service: creates default env vars, updates metrics |

---

## Celery Configuration

File: `config/celery.py`

```python
CELERY_TASK_SOFT_TIME_LIMIT = 7200   # 2 hours soft limit
CELERY_TASK_TIME_LIMIT = 7500        # 2h 5m hard limit
```

These were bumped from 3600s/5400s to accommodate heavy builds (PyTorch, Playwright).

### Broker

- **Message Broker**: RabbitMQ (AMQP) — primary task queue
- **Result Backend**: django-db (PostgreSQL)
- **Beat Scheduler**: `SentinelRedBeatScheduler` (Redis-locked, multi-instance safe)
- **Cache/Channels**: Redis (DB 0 = general, DB 1 = channels, DB 2 = cache, DB 3 = redbeat)

### Worker Queues

| Queue | Purpose |
|-------|---------|
| `celery` | Default — general background tasks |
| `deploy` | Heavy operations — Docker builds, provisioning, backups |
| `fast` | Low-latency — heartbeats, metrics, log collection |

### Key Tasks

| Task | Purpose |
|------|---------|
| `smart_deploy_task` | Full deployment pipeline (analyze → review → build → deploy) |
| `analyze_failure_task` | AI-powered failure analysis after build errors |
| `monitor_health_task` | Periodic service health monitoring |
| `detect_anomalies_task` | AI anomaly detection (regex-based log pattern scanning) |
| `recover_stalled_deletions` | Recovers services stuck in DELETION_PENDING |

---

## Frontend Architecture

### Stack
- **Framework**: Next.js 15 (App Router, TypeScript)
- **Styling**: Tailwind CSS v3 (`^3.3.0`) with shadcn/ui
- **Components**: Radix UI primitives + custom (28 UI primitives)
- **API Client**: `src/lib/api.ts` (Axios-based, 2,494 lines, 41 API groups)
- **Auth**: HttpOnly cookie-only (no localStorage tokens)

### Key Components

| Component | Purpose |
|-----------|---------|
| `src/components/settings/` | 26 settings tab components (OAuthTab, SecurityTab, TeamsTab, AlertsTab, etc.) |
| `src/components/topology/` | 8 topology visualization components (3D, Solar System, City views) |
| `src/components/deployments/` | Pipeline visualizer, buildpack selector, SafeDeploy panel |
| `src/components/ai/` | AI assistant, floating AI, repo analyzer |

### API Client

The API client (`src/lib/api.ts`) has **41 API groups** covering all platform domains:

Key groups: `servicesApi` (50+ methods), `serversApi`, `deployApi`, `addonsApi`, `billingApi`, `aiApi`, `previewApi`, `teamsApi`, `tunnelsApi`, `autoscalerApi`, `scalingApi`, `tokensApi`, `backupsApi`, `organizationsApi`, `licensingApi`, `domainsApi`, `notificationsApi`, `alertsApi`, `ecosystemApi`, `databaseReplicasApi`, `registryCredentialsApi`

### API Client Methods

```typescript
servicesApi.deploy(id, ref='HEAD', targetServerId?)  // Trigger deployment
servicesApi.rollback(deploymentId)                     // Rollback to previous
servicesApi.cancelDeployment(dId)                      // Cancel QUEUED/REVIEW/BUILDING
servicesApi.approveDeployment(deploymentId, overrides?) // Approve REVIEW → BUILDING
```

---

## Docker Services (Production)

`docker-compose.prod.yml` contains **30+ services** organized in layers:

### Data Layer (never restarted on update)

| Service | Image | Purpose |
|---------|-------|---------|
| `postgres-primary` | postgres:16-alpine | Primary database with WAL replication |
| `postgres-replica` | postgres:16-alpine | Streaming replica (pg_basebackup) |
| `pgcat` | Custom PgCat | Connection pooler (transaction mode for API, session for Celery) |
| `redis-primary` | redis:7-alpine | Cache, channels, rate limits (AOF persistence) |
| `redis-replica` | redis:7-alpine | Read-only replica with diskless sync |
| `redis-sentinel-1/2/3` | redis:7-alpine | 3-node sentinel for automatic Redis failover |
| `rabbitmq` | rabbitmq:3-management-alpine | Celery message broker |

### App Layer (force-recreated on update)

| Service | Image | Purpose |
|---------|-------|---------|
| `backend` | Custom (Python 3.11) | Django + Gunicorn (ASGI) |
| `frontend` | Custom (Node 20) | Next.js standalone |
| `celery` | Same as backend | Default queue worker |
| `celery-2`, `celery-3` | Same as backend | Additional default queue workers |
| `celery-fast` | Same as backend | Low-latency queue (heartbeats, metrics) |
| `celery-deploy` | Same as backend | High-latency queue (builds, cleanup) |
| `celery-beat` | Same as backend | Periodic task scheduler (RedBeat) |

### Edge Layer

| Service | Image | Purpose |
|---------|-------|---------|
| `caddy` | Custom Caddy (xcaddy + Cloudflare DNS plugin) | TLS termination, main routing |
| `traefik` | traefik:v3.6 | Wildcard subdomain routing via Docker labels |
| `socket-proxy` | tecnativa/docker-socket-proxy | Read-only Docker socket for backend |
| `traefik-socket-proxy` | tecnativa/docker-socket-proxy | Read-only Docker socket for Traefik |
| `route-fallback` | Custom Caddy | Catch-all for unmatched routes (503 page) |

### Infrastructure Services

| Service | Image | Purpose |
|---------|-------|---------|
| `registry` | registry:2.8.3 | Private Docker registry (TLS + htpasswd auth) |
| `docker-mirror` | registry:2.8.3 | Pull-through cache for Docker Hub |
| `buildkitd` | moby/buildkit | Image builds via BuildKit |
| `apt-cacher` | apt-cacher-ng | APT package cache |
| `verdaccio` | verdaccio:5.31 | npm registry cache |
| `frps` | frp | FRP tunnel relay server |
| `crowdsec` | crowdsecurity/crowdsec:v1.7.8 | WAF & IPS with Traefik bouncer |

### Observability Stack

| Service | Image | Purpose |
|---------|-------|---------|
| `prometheus` | prom/prometheus:v2.48.0 | Metrics collection (30d retention) |
| `grafana` | grafana:11.0.0 | Dashboards |
| `loki` | grafana/loki:2.9.3 | Log aggregation |
| `promtail` | grafana/promtail:2.9.3 | Log collection |
| `cadvisor` | gcr.io/cadvisor/cadvisor:v0.49.1 | Container resource metrics |
| `node-exporter` | prom/node-exporter:v1.6.1 | Host system metrics |

### Build Commands

`docker-compose.yml` is the dev override on top of `docker-compose.prod.yml`. Compose merges them, but the two files are **mutually exclusive at the CLI level** — `docker-compose.prod.yml` is a strict superset of `docker-compose.yml` (it includes the dev override already). Stack both `-f` flags and Compose will reject the config with duplicate-service errors.

```bash
# Dev (local — uses docker-compose.yml override)
docker compose -f docker-compose.yml up -d --build

# Prod (master / node / lite-agent — docker-compose.prod.yml is the single source of truth)
docker compose -f docker-compose.prod.yml up -d --build

# Backend only
docker compose -f docker-compose.prod.yml up -d --build backend

# Frontend only
docker compose -f docker-compose.prod.yml up -d --build frontend
```

---

## Security Conventions

1. **Secrets**: Never hardcoded. `os.environ['KEY']` (crash if missing, no defaults)
2. **Docker socket**: Never mounted directly — always via `socket-proxy` (read-only)
3. **Database**: Internal network only, no port exposure
4. **Webhooks**: HMAC-SHA256 signature validation required
5. **Encryption**: Fernet for secrets at rest in DB
6. **Auth**: HttpOnly cookies (no localStorage tokens), JWT + session, 2FA via `django-otp`
7. **Inter-service**: HMAC V2 signatures (`X-Gateway-Signature-V2`, `X-Request-Timestamp`, `X-Request-Nonce`)
8. **OAuth**: GitHub, GitLab, Bitbucket, Google (via `django-allauth`)

---

## Common Development Patterns

### Adding a New API Endpoint

1. Create serializer in `apps/<app>/serializers.py`
2. Create view in `apps/<app>/views.py`
3. Wire URL in `apps/<app>/urls.py`
4. Add to `config/urls.py` if new app
5. Write tests in `apps/<app>/tests/`

### Adding a New Celery Task

1. Define in `apps/<app>/tasks.py` (or `apps/<app>/tasks_<domain>.py` for specialized modules)
2. **Register in `config/celery.py` `register_extra_tasks`** if using a specialized module
3. For periodic tasks, add to `CELERY_BEAT_SCHEDULE` in `config/celery.py`
4. Set appropriate `soft_time_limit` if build-heavy
5. If the task uses Docker, add a route entry to `task_routes` for the `deploy` queue

### Adding Frontend API Methods

1. Add method to `src/lib/api.ts` in the relevant API group
2. Use in component with proper error handling via `toast`
3. Invalidate/refetch after mutations

---

## Recent Changes Log

| Date | Change | Files |
|------|--------|-------|
| 2026-06 | Security sweep: SSH MITM fix, secret rotation, HttpOnly cookies, container hardening | Multiple |
| 2026-06 | Dead code quarantine: rust_twin, custom-addons, console, Click CLI → `archive/` | Multiple |
| 2026-05 | Ecosystem deployment docs, SafeDeploy system | `tasks_ecosystem.py`, `views_safedeploy.py` |
| 2026-02-18 | Rate limit fix: bump user throttle to 5000/hr, middleware to 10000/min | `settings.py`, `ratelimit.py` |
| 2026-02-17 | Deploy review gate: skip_review for restarts + webhooks | `tasks.py`, `views.py`, `github.py` |
| 2026-02-17 | Cancel/Approve buttons in deployment UI | `DeploymentsTab.tsx`, `api.ts` |
| 2026-02-15 | Celery time limits bumped to 7200s/7500s for heavy builds | `celery.py` |
