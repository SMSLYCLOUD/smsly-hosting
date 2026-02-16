# Prompt for Jules: Gap Closure Sprint

You are tasked with implementing 5 key features to close the competitive gap with Railway/Render.

**Context:**
- **Backend is 60% done**: Teams, Autoscaling, and Nixpacks logic already exist in Django (`apps/teams`, `autoscaler.py`, `smart_deploy_task`).
- **Frontend is the main gap**: Wiring these existing backends to Next.js UI.
- **New Features**: CI/CD Pipeline visualizer and Edge Functions MVP need end-to-end implementation.

---

## Phase 1: Team & RBAC (Frontend Wiring)
**Goal:** Enable multi-user teams.
1.  **Backend**: Verify `apps/teams` endpoints (`/api/v1/teams/`, `/invite_member/`) work.
2.  **Frontend**:
    -   Page: `src/app/settings/team/page.tsx`
    -   Features: List members, Invite Member (email + role), Remove Member.
    -   Global: Create a `TeamSwitcher` in the sidebar to switch active context.
    -   State: Store `activeTeamId` in functionality.

## Phase 2: Auto-Scaling UI
**Goal:** UI for existing HPA backend.
1.  **Frontend**:
    -   Component: `src/components/settings/ScalingTab.tsx`
    -   Inputs: Min/Max Replicas (slider), CPU Target % (slider), VPA (toggle).
    -   Action: PATCH service endpoint.
2.  **Backend**: Ensure `check_autoscale_task` respects these fields (already implemented, just verify).

## Phase 3: Nixpacks Visibility
**Goal:** User control over build packs.
1.  **Backend**:
    -   Model: Add `buildpack` field to Service (`choices=['NIXPACKS', 'DOCKER', 'STATIC']`).
    -   Task: Update `smart_deploy_task` to check `service.buildpack` before building.
2.  **Frontend**:
    -   Wizard: Add `BuildpackSelector` to `new/page.tsx`.
    -   Settings: Add to `BuildTab`.

## Phase 4: CI/CD Pipeline UI
**Goal:** Visual deployment progress.
1.  **Backend**:
    -   Model: Add `pipeline_stages` JSONField to `Deployment`.
    -   Task: In `smart_deploy_task`, update `pipeline_stages` as it progresses (Clone -> Build -> Push -> Deploy).
2.  **Frontend**:
    -   Component: `PipelineVisualizer.tsx`
    -   UI: Horizontal steps (circles + lines). Green = success, Blue = running, Red = failed.
    -   Real-time: Listen to `build-logs` WebSocket for status updates.

## Phase 5: Edge Functions (MVP)
**Goal:** "Serverless" function deployment.
1.  **Backend**:
    -   Model: Add `service_type='FUNCTION'` to Service.
    -   Task: Create `function_provisioner.py` that wraps raw JS code in a lightweight Node.js Alpine container.
2.  **Frontend**:
    -   Page: `src/app/functions/page.tsx`
    -   UI: Monaco Editor for code. "Deploy Function" button.
    -   Result: Returns a URL (`/api/v1/functions/{id}/invoke`).

---

**Execution Rule:**
Implement these strictly in order: **Team -> Scaling -> Nixpacks -> Pipeline -> Edge Functions**.
Verify each phase with `npx tsc --noEmit` before moving to the next.
