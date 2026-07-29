# SMSLY Ecosystem Deploy — Failure Analysis & Jules Strategy

## Background

32 connected microservices were deployed via "Ecosystem Deploy" (one-click). **All failed.** This analysis is based on full codebase review of:

- [tasks_ecosystem.py](backend/apps/deployments/tasks_ecosystem.py) — 789 lines, wave-based deployment orchestrator
- [ecosystem.py](backend/apps/deployments/models/ecosystem.py) — 612 lines, AI scan + heuristic analysis
- [ecosystem_graph.py](backend/apps/deployments/services/ecosystem_graph.py) — 207 lines, live dependency graph
- [tasks.py](backend/apps/deployments/tasks.py) — deployment execution (smart_deploy_task)
- [local.py](backend/apps/cloud/adapters/local.py) — Docker adapter

---

## Root Cause Analysis

### 1. Cascading Wave Failure (CRITICAL)

**Problem:** One service failure in Wave 1 **cancels ALL remaining waves.**

```python
# tasks_ecosystem.py:511-522
failed_states = {Deployment.Status.FAILED, Deployment.Status.CANCELLED}
if any(status in failed_states for status in statuses):
    cancelled = _cancel_remaining_waves(waves, from_wave_index=wave_index,
        reason="upstream dependency deployment failed")
```

**Impact:** If 1 of 10 wave-1 services fails, the other 22 services are immediately cancelled — even if they don't depend on the failed service.

**Fix:** Only cancel services that actually *depend* on the failed service. Services without dependency on the failed one should continue.

---

### 2. STAGED Status Incompatibility (CRITICAL)

**Problem:** The new blue-green bake time adds a `STAGED` status between HEALTH_CHECK and ACTIVE. But the wave gating only checks for `ACTIVE`:

```python
# tasks_ecosystem.py:524
if all(status == Deployment.Status.ACTIVE for status in statuses):
    # release next wave
```

**Impact:** With the new blue-green flow, services go to STAGED for 30 minutes before ACTIVE. The wave gater will **never release wave 2** because it's waiting for ACTIVE but sees STAGED.

**Fix:** Accept both `STAGED` and `ACTIVE` as "success" for wave gating:
```python
success_states = {Deployment.Status.ACTIVE, Deployment.Status.STAGED}
if all(status in success_states for status in statuses):
```

---

### 3. Env Var Resolution Uses Hardcoded Placeholders (HIGH)

**Problem:** Placeholder resolution in `_resolve_env_placeholders()` uses hardcoded URLs:

```python
# tasks_ecosystem.py:161-167
if value_text == "{{POSTGRES_URL}}":
    resolved[key] = "postgresql://smsly:smsly@postgres:5432/smsly"
if value_text == "{{REDIS_URL}}":
    resolved[key] = "redis://redis:6379/0"
```

These point to `postgres:5432` and `redis:6379` — containers that **don't exist** in the smsly-hosting Docker network. Real addon containers are named `smsly-addon-postgres-{hash}` and `smsly-addon-redis-{hash}`.

**Fix:** Use `ecosystem_graph.py`'s `build_ecosystem_graph()` to resolve real addon URLs from the live service graph. The graph already tracks shared addons:
```python
graph = build_ecosystem_graph(service)
postgres_url = graph['shared_addons'].get('POSTGRES', '')
redis_url = graph['shared_addons'].get('REDIS', '')
```

---

### 4. No Shared Addon Provisioning (HIGH)

**Problem:** The ecosystem blueprint references shared postgres/redis addons:
```json
"addons": [
    {"type": "POSTGRES", "name": "shared-postgres"},
    {"type": "REDIS", "name": "shared-redis"}
]
```

But `ecosystem_deploy_task()` **never provisions addons**. It only creates services and env vars. So all services that need DATABASE_URL or REDIS_URL get broken placeholder values.

**Fix:** Before deploying wave 1, check if shared addons exist. If not, provision them via the addon system and store their real connection URLs for env var injection.

---

### 5. Health Check Downtime (HIGH)

**Problem:** During deployment, the new container gets Traefik labels immediately, replacing the old container's routing before health checks pass. This causes downtime.

**Status:** We just implemented blue-green bake time (commit `05bd741`) to fix this:
- New container starts with `traefik.enable: false`
- Goes to STAGED status after health checks pass
- Old container keeps serving until promotion

**Remaining issue for ecosystem:** The wave gater needs to be updated to accept STAGED (see #2 above).

---

## Strategy for Jules

### Phase 1: Fix Wave Gating (Critical Path)

**Files:** `tasks_ecosystem.py`

1. Accept `STAGED` + `ACTIVE` as success states in `ecosystem_release_wave_task()`
2. Only cancel services that **transitively depend** on the failed service, not entire waves
3. Add retry logic: if a service fails, retry it once before cancelling dependents
4. Add partial-success handling: report per-service status, don't just "all or nothing"

### Phase 2: Smart Env Var Resolution

**Files:** `tasks_ecosystem.py`, `ecosystem_graph.py`

1. Before `_resolve_env_placeholders()`, call `build_ecosystem_graph(service)` to get real addon URLs
2. Replace hardcoded `postgres:5432` / `redis:6379` with actual addon container URLs
3. Auto-provision shared addons if they don't exist (create POSTGRES and REDIS addons)
4. For each service, auto-generate a unique database name: `CREATE DATABASE {service_slug};`
5. Auto-assign unique Redis DB numbers using `next_available_redis_db()`

### Phase 3: Shared Secret Propagation

**Files:** `tasks_ecosystem.py`, `ecosystem_graph.py`

1. Generate shared secrets once (e.g., `INTERNAL_API_SECRET`, `JWT_SECRET`) and propagate to all services that need them
2. Use `get_sibling_env_value()` to read secrets from already-deployed siblings
3. Auto-detect cross-service env vars like `BACKEND_URL`, `IDENTITY_SERVICE_URL` and resolve them to real container:port addresses

### Phase 4: Frontend Config Panel

**Files:** `frontend/src/app/ecosystem/`

1. Show the full deploy plan with all env vars **before** deploying
2. Allow editing individual env vars per service
3. Add bulk env var import (paste `.env` file or JSON blob)
4. Show wave progression with per-service status (not just wave-level)
5. Add retry button for individual failed services within a wave

### Phase 5: Parallel Build Optimization

**Files:** `tasks_ecosystem.py`, `tasks.py`

1. Increase wave size from 10 to match available CPU cores
2. Add build queue throttling (max N simultaneous builds based on server resources)
3. Add progress streaming via WebSocket so frontend shows real-time build logs per service

---

## Bulk Env Var Import (Review)

Currently, env var upsert is done per-service via `POST /api/v1/services/{id}/env_vars/` with a `vars` array. The frontend `upsertEnvVars()` in [api.ts](file:///c:/Users/osaretin/Downloads/smslycloud-master/smsly-hosting/frontend/src/lib/api.ts) handles this.

**Issues:**
- No way to set the same env var across ALL services at once (e.g., shared REDIS_URL)
- No `.env` file paste support
- No validation that referenced service URLs (e.g., `http://smsly-backend:8000`) actually resolve

**Fix:** Add a `POST /api/v1/ecosystem/bulk-env/` endpoint that accepts env vars + service filter (all/by-tag/by-wave) and upserts across multiple services at once.

---

## SSH Server Access

> [!IMPORTANT]
> SSH to `163.245.216.248` got rate-limited after multiple rapid connection attempts. Wait ~10 minutes before retrying. When reconnecting, run:
>
> ```bash
> ssh root@163.245.216.248
> # password: agbonsalo
>
> # Get all failed deployments:
> cd /opt/smsly-hosting
> python3 manage.py shell -c "
> from apps.deployments.models import Deployment
> for d in Deployment.objects.filter(status='FAILED').order_by('-created_at')[:30]:
>     print(f'{d.service.name} | {d.commit_hash[:7]} | {d.created_at} | {(d.build_logs or \"\")[-200:]}')"
>
> # Get all container statuses:
> docker ps -a --format '{{.Names}} | {{.Status}}'
> ```
