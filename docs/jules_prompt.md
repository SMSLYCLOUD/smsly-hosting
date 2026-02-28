# Jules Prompt — Fix Ecosystem Deploy for 32 Connected Microservices

## Context

SMSLY Hosting is a PaaS (like Railway/Render) with an "Ecosystem Deploy" feature that scans all GitHub repos, builds a dependency graph, and deploys them in dependency-aware waves. **32 microservices were deployed with one click and all failed.**

## Codebase Layout

```
smsly-hosting/backend/
├── apps/deployments/
│   ├── tasks_ecosystem.py     # Wave-based ecosystem deploy orchestrator (789 lines)
│   ├── tasks.py               # Individual service deploy (smart_deploy_task)
│   ├── models.py              # Service, Deployment, EnvironmentVariable models
│   └── views.py               # REST API endpoints
├── apps/cloud/adapters/
│   └── local.py               # Docker adapter (container lifecycle + Traefik)
├── services/
│   ├── ecosystem.py           # AI scan + heuristic analysis (612 lines)
│   └── ecosystem_graph.py     # Live service dependency graph (207 lines)
└── blueprints/
    └── smsly-ecosystem.json   # Static blueprint with 6 core services
```

## 5 Critical Bugs to Fix

### Bug 1: Cascading Wave Failure
**File:** `tasks_ecosystem.py`, function `ecosystem_release_wave_task()`  
**Line 511-522:** If ANY service in a wave fails, ALL remaining waves are cancelled — even services with no dependency on the failed one.  
**Fix:** Only cancel services that transitively depend on the failed service. Independent services should continue deploying.

### Bug 2: STAGED Status Blocks Waves
**File:** `tasks_ecosystem.py`, line 524  
**Problem:** `if all(status == Deployment.Status.ACTIVE for status in statuses)` — waves only release when ALL previous services reach ACTIVE. But we just added a STAGED status (blue-green bake time, 30 min). Services sit in STAGED for 30 min before becoming ACTIVE, so wave 2 never releases.  
**Fix:** Change to `success_states = {Deployment.Status.ACTIVE, Deployment.Status.STAGED}; if all(s in success_states for s in statuses):`

### Bug 3: Broken Env Var Placeholders
**File:** `tasks_ecosystem.py`, function `_resolve_env_placeholders()`  
**Lines 161-167:** `{{POSTGRES_URL}}` resolves to `postgresql://smsly:smsly@postgres:5432/smsly` and `{{REDIS_URL}}` to `redis://redis:6379/0`. These container names DON'T EXIST. Real containers are named `smsly-addon-postgres-{uuid}` and `smsly-addon-redis-{uuid}`.  
**Fix:** Use `ecosystem_graph.py`'s `build_ecosystem_graph()` to get real addon connection URLs. The graph already tracks `graph['shared_addons']['POSTGRES']` and `graph['shared_addons']['REDIS']`.

### Bug 4: Addons Never Provisioned
**File:** `tasks_ecosystem.py`, function `ecosystem_deploy_task()`  
**Problem:** The deploy plan specifies shared addons (postgres, redis) but the task never provisions them. Services get broken DATABASE_URL values because no addon containers are created.  
**Fix:** Before deploying wave 1, check `CloudProvider.addons` for existing postgres/redis. If none exist, provision them via the addon provisioning system (`Service.addons.create(addon_type='POSTGRES')`), then inject their real connection URLs into all services.

### Bug 5: No Retry on Failure
**File:** `tasks_ecosystem.py`  
**Problem:** If a build fails (e.g., transient network error pulling a dependency), the service is marked FAILED and everything downstream is cancelled. No retry.  
**Fix:** Add a single retry: if a service fails, re-queue it once with `smart_deploy_task`. Only mark as permanently failed after the retry also fails.

## Additional Improvements

### 6. Cross-Service Env Var Auto-Resolution
When deploying service B that depends on service A, auto-set:
- `BACKEND_URL` → `http://{service_a_container}:{service_a_port}`
- `DATABASE_URL` → rewrite base postgres URL for service B's specific database
- `REDIS_URL` → use `next_available_redis_db()` to assign unique Redis DB number

### 7. Bulk Env Import Endpoint
Add `POST /api/v1/ecosystem/bulk-env/` that accepts:
```json
{
  "service_ids": ["id1", "id2"],  // or "all"
  "vars": [
    {"key": "SHARED_SECRET", "value": "...", "is_secret": true}
  ]
}
```
This enables setting shared env vars across all services at once.

### 8. Frontend Ecosystem Config Panel
In `frontend/src/app/ecosystem/`, add:
- Pre-deploy review screen showing all services + env vars
- Editable env vars per service before hitting "Deploy"
- `.env` file paste/import
- Wave progress visualization with per-service status
- Retry button for individual failed services

## Testing

After fixes, verify:
1. Deploy 3-service chain (A → B → C) — B should get correct A URL
2. Fail service A intentionally, verify B/C are cancelled but independent D continues
3. Verify STAGED services don't block wave release
4. Verify DATABASE_URL and REDIS_URL point to real addon containers
5. Verify retry: kill network during build, confirm retry succeeds

## Server Access

```
ssh root@163.245.216.248
password: agbonsalo
cd /opt/smsly-hosting

# Failed deployments:
python3 manage.py shell -c "from apps.deployments.models import Deployment; [print(f'{d.service.name}|{d.status}|{d.build_logs[-200:]}') for d in Deployment.objects.filter(status='FAILED').order_by('-created_at')[:20]]"

# Container status:
docker ps -a --format '{{.Names}} | {{.Status}}'
```
