# SMSLY Hosting — Developer Guide

> Internal reference for contributors and AI agents. Covers architecture decisions, recent changes, and coding conventions.

---

## Backend Architecture

### Django Apps

| App | Purpose |
|-----|---------|
| `apps.deployments` | Service deployment lifecycle, build pipeline, webhooks, container orchestration |
| `apps.cloud` | Cloud provider abstraction (Docker, AWS, Azure, GCP) |
| `apps.teams` | Multi-tenancy, team membership, permissions |
| `apps.billing` | Subscription tiers, usage metering |
| `apps.intelligence` | AI-powered diagnostics (Gemini/OpenAI/Grok integration) |
| `apps.domains` | Custom domain management, DNS validation |
| `apps.core` | Shared middleware, base models, security utilities |

### Request Flow

```
Client → Caddy (SSL) → Gunicorn (:8000) → Django
                                                            ↓
                                        Middleware Chain:
                                        1. SecurityMiddleware (HMAC V2)
                                        2. RateLimitMiddleware (anon only)
                                        3. AuthenticationMiddleware
                                        4. DRF Throttle (user-based)
                                        5. View
```

### Middleware Stack (order matters)

| Middleware | Layer | Purpose |
|-----------|-------|---------|
| `SecurityMiddleware` | HMAC V2 | Inter-service authentication |
| `RateLimitMiddleware` | IP-based | DDoS protection for anonymous requests only |
| DRF `UserRateThrottle` | User-based | Per-user throttling for authenticated API consumers |

> **Important**: `RateLimitMiddleware` skips authenticated users (as of 2026-02-18). DRF handles their throttling instead.

---

## Rate Limiting Architecture

Two layers, each with a distinct purpose:

### Layer 1: Middleware (IP-based, anonymous only)
- File: `apps/core/middleware/ratelimit.py`
- Limit: 1000 requests per 60-second sliding window per IP
- Scope: Only `/api/` requests from **unauthenticated** users
- Backend: Redis cache (`ratelimit:{ip}:{window}`)
- Fail mode: **Closed** (cache failure = deny request)

### Layer 2: DRF Throttle (user-based, authenticated)
- File: `config/settings.py` → `REST_FRAMEWORK.DEFAULT_THROTTLE_RATES`
- Rates:
  - `anon`: 200/hour
  - `user`: 5000/hour (~83/min)
  - `deployments`: 10/hour (intentionally restrictive)
  - `deployment_burst`: 3/minute

### Why Two Layers
- Middleware runs **before** authentication — catches DDoS from IPs that never log in
- DRF throttle runs **after** authentication — per-user fairness for legitimate users
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
              ↓                 Deployment(LIVE)
    Deployment(BUILDING)
              ↓
    Deployment(LIVE)
```

### `skip_review` Flag

The `smart_deploy_task` accepts `skip_review=True` to bypass the AI analysis + review gate:

```python
smart_deploy_task.delay(str(deployment.id), str(provider.id), skip_review=True)
```

**When `skip_review=True` is used:**
- Service restarts (`views.py` restart action)
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
| `LIVE` | Successfully deployed | Green checkmark |
| `FAILED` | Build or deploy failed | Red X |
| `CANCELLED` | User cancelled | Gray ban icon |

### Key Files

| File | Purpose |
|------|---------|
| `apps/deployments/tasks.py` | `smart_deploy_task`, `_build_review_summary`, `_handle_failure` |
| `apps/deployments/views.py` | REST API views, restart action, cancel/approve actions |
| `apps/deployments/webhooks/github.py` | `GitHubWebhookHandler`, push + PR events |
| `apps/deployments/signals.py` | `post_save` for Service: creates default env vars |

---

## Celery Configuration

File: `config/celery.py`

```python
task_soft_time_limit = 3600   # 60 min soft limit
task_time_limit = 5400        # 90 min hard limit
```

These were bumped from 300s/600s to accommodate heavy builds (PyTorch, Playwright).

### Key Tasks

| Task | Purpose |
|------|---------|
| `smart_deploy_task` | Full deployment pipeline (analyze → review → build → deploy) |
| `analyze_failure_task` | AI-powered failure analysis after build errors |
| `health_check_task` | Periodic service health monitoring |

---

## Frontend Architecture

### Stack
- **Framework**: Next.js 15 (App Router, TypeScript)
- **Styling**: Tailwind CSS v4
- **Components**: shadcn/ui + custom
- **API Client**: `src/lib/api.ts` (Axios-based, typed)

### Key Components

| Component | Purpose |
|-----------|---------|
| `DeploymentsTab.tsx` | Deployment history with status badges, Cancel/Approve buttons |
| `SettingsPage.tsx` | 8-tab settings panel |
| `ServiceOverview.tsx` | Service dashboard with metrics |

### API Client Methods

```typescript
servicesApi.deploy(serviceId)          // Trigger deployment
servicesApi.rollback(deploymentId)     // Rollback to previous
servicesApi.cancelDeployment(dId)      // Cancel QUEUED/REVIEW/BUILDING
servicesApi.approveDeployment(dId)     // Approve REVIEW → BUILDING
```

---

## Docker Services (Production)

| Service | Image | Purpose |
|---------|-------|---------|
| `backend` | Custom (Python 3.11) | Django + Gunicorn |
| `frontend` | Custom (Node 20) | Next.js standalone |
| `celery` | Same as backend | Async task workers |
| `celery-beat` | Same as backend | Periodic task scheduler |
| `db` | postgres:16-alpine | Primary database |
| `redis` | redis:7-alpine | Cache + Celery broker |
| `caddy` | caddy:2.7-alpine | Reverse proxy, SSL termination, routing |
| `socket-proxy` | tecnativa/docker-socket-proxy | Read-only Docker socket |
| `registry` | registry:2 | Private Docker registry for user images |
| `traefik` | traefik:v3.6 | Dynamic routing for deployed services |

### Build Commands

```bash
# Full stack (both compose files required for build context)
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build

# Backend only
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build backend

# Frontend only
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build frontend
```

---

## Security Conventions

1. **Secrets**: Never hardcoded. `os.environ['KEY']` (crash if missing, no defaults)
2. **Docker socket**: Never mounted directly — always via `socket-proxy` (read-only)
3. **Database**: Internal network only, no port exposure
4. **Webhooks**: HMAC-SHA256 signature validation required
5. **Encryption**: Fernet for secrets at rest in DB
6. **Auth**: Session + Token (dj-rest-auth + allauth)

---

## Common Development Patterns

### Adding a New API Endpoint

1. Create serializer in `apps/<app>/serializers.py`
2. Create view in `apps/<app>/views.py`
3. Wire URL in `apps/<app>/urls.py`
4. Add to `config/urls.py` if new app
5. Write tests in `apps/<app>/tests/`

### Adding a New Celery Task

1. Define in `apps/<app>/tasks.py`
2. Register in `config/celery.py` autodiscover
3. For periodic tasks, add to `CELERY_BEAT_SCHEDULE` in settings
4. Set appropriate `soft_time_limit` if build-heavy

### Adding Frontend API Methods

1. Add method to `src/lib/api.ts` in the relevant API group
2. Use in component with proper error handling via `toast`
3. Invalidate/refetch after mutations

---

## Recent Changes Log

| Date | Change | Files |
|------|--------|-------|
| 2026-02-18 | Rate limit fix: bump user throttle 1000→5000/hr, skip middleware for auth users | `settings.py`, `ratelimit.py` |
| 2026-02-17 | Deploy review gate: skip_review for restarts + webhooks | `tasks.py`, `views.py`, `github.py` |
| 2026-02-17 | Cancel/Approve buttons in deployment UI | `DeploymentsTab.tsx`, `api.ts` |
| 2026-02-15 | Celery time limits bumped to 3600s/5400s for heavy builds | `celery.py` |
