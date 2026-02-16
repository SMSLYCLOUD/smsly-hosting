# Gap Closure Sprint — All 5 Features

Close every competitive gap vs Railway/Render: Team RBAC, Auto-Scaling UI, Nixpacks Buildpacks, CI/CD Pipeline, Edge Functions.

## Key Finding

> [!TIP]
> **3 of 5 features already have working backends.** The gaps are mostly missing frontend UI and wiring.

| Feature | Backend | Frontend | Work Needed |
|---------|---------|----------|-------------|
| Team/RBAC | ✅ Full (Team, TeamMember, views, serializers) | ⚠️ Settings page exists but untested | Wire UI ↔ API, add team switcher |
| Auto-Scaling | ✅ Full (`autoscaler.py`, HPA fields on Service) | ❌ No UI | Build scaling config panel |
| Nixpacks | ✅ Already used in `smart_deploy_task` | ❌ No visibility | Expose buildpack selection UX |
| CI/CD Pipeline | ❌ No pipeline model | ❌ No UI | **New feature end-to-end** |
| Edge Functions | ❌ Nothing | ❌ Nothing | **New feature end-to-end** |

---

## Phase 1: Team/RBAC (Frontend Wiring)

Backend already has: `Team`, `TeamMember` models + `TeamViewSet` (CRUD, invite, remove) + serializers.

### Frontend

#### [MODIFY] [page.tsx](file:///c:/Users/osaretin/Downloads/smslycloud-master/smsly-hosting/frontend/src/app/settings/team/page.tsx)
- Wire to `/api/v1/teams/` endpoints
- Add invite member form (email + role dropdown: ADMIN/MEMBER/VIEWER)
- Display members list with role badges and remove button
- Add team creation modal

#### [NEW] TeamSwitcher component
- Dropdown in the sidebar/header for switching active team context
- Persist selected team in localStorage

### Backend

#### [MODIFY] [views.py](file:///c:/Users/osaretin/Downloads/smslycloud-master/smsly-hosting/backend/apps/deployments/views.py)
- Filter `ServiceViewSet.get_queryset()` by active team (currently filters by `owner` only)

---

## Phase 2: Auto-Scaling UI

Backend already has: `check_autoscale_task` + Service fields (`min_replicas`, `max_replicas`, `autoscale_cpu_target`, `vpa_enabled`).

### Frontend

#### [NEW] ScalingTab.tsx
- Add to service settings: slider for min/max replicas, CPU target %, VPA toggle
- Display current replica count and recent scaling events
- PATCH to `/api/v1/services/{id}/` to update scaling fields

---

## Phase 3: Nixpacks Buildpack Visibility

Backend already uses Nixpacks in `smart_deploy_task`. The gap is user control.

### Backend

#### [MODIFY] [models.py](file:///c:/Users/osaretin/Downloads/smslycloud-master/smsly-hosting/backend/apps/deployments/models.py)
- Add `buildpack` field to Service: `CharField(choices=[('auto','Auto-detect'),('nixpacks','Nixpacks'),('docker','Dockerfile'),('static','Static')])` 

#### [MODIFY] [tasks.py](file:///c:/Users/osaretin/Downloads/smslycloud-master/smsly-hosting/backend/apps/deployments/tasks.py)
- Respect `service.buildpack` choice in `smart_deploy_task` build logic

### Frontend

#### [NEW] BuildpackSelector component
- Show detected framework with option to override
- Display in service creation wizard and service settings

---

## Phase 4: CI/CD Pipeline UI

New feature. Shows deployment pipeline stages visually (Clone → Build → Push → Deploy → Health Check).

### Backend

#### [MODIFY] Deployment model
- Add `pipeline_stages` JSONField — array of `{name, status, started_at, finished_at, logs}`
- Update `smart_deploy_task` to write stage transitions as deployment progresses

### Frontend

#### [NEW] PipelineView.tsx
- Visual pipeline with horizontal stages (circles connected by lines)
- Each stage shows status (pending/running/success/failed), duration, expandable logs
- Real-time updates via existing build-log WebSocket

---

## Phase 5: Edge Functions (Lightweight)

> [!IMPORTANT]
> Full edge function runtime (V8 isolates / Deno Deploy style) is **weeks of work**. Recommend a pragmatic MVP: user writes a JS function, we wrap it in a lightweight container and deploy it like any service but with special UX.

### Backend

#### [MODIFY] Service model
- Add `service_type` field: `choices=[('APP','Application'),('FUNCTION','Function'),('STATIC','Static')]`
- Edge functions = tiny services with a boilerplate wrapper

#### [NEW] function_runtime.py
- Celery task that wraps user JS code in a Node.js container template
- Auto-generates Dockerfile from function code

### Frontend

#### [NEW] functions/page.tsx
- Function editor (CodeMirror/Monaco) with deploy button
- Shows invocation URL, request count, latency metrics
- Uses existing deployment pipeline under the hood

---

## Execution Order

```
Phase 1 (Team/RBAC) → Phase 2 (Scaling UI) → Phase 3 (Buildpacks) → Phase 4 (CI/CD Pipeline) → Phase 5 (Edge Functions)
```

Each phase ships independently. Phase 1 is the highest-impact, lowest-effort win.

## Verification Plan

### Per Phase
- `npx tsc --noEmit` — zero errors
- `python -m py_compile` — all modified .py files
- Visual check via browser screenshots

### Integration
- Create team → invite member → verify filtered service list
- Deploy service → verify pipeline stages render correctly
- Set autoscaling → verify Celery task respects new limits
