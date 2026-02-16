# Gap Closure Sprint

## Phase 1: Team & RBAC (Frontend Wiring)
- [ ] Connect `settings/team/page.tsx` to `/api/v1/teams/`
- [ ] Implement `InviteMemberForm` (email + role)
- [ ] Build `MembersList` with removal actions
- [ ] Create `TeamSwitcher` for global context
- [ ] Update `ServiceViewSet` to filter by active team

## Phase 2: Auto-Scaling UI
- [ ] Create `ScalingTab` in service settings
- [ ] Add slider for `min_replicas` / `max_replicas`
- [ ] Add CPU target slider (HPA)
- [ ] Add toggle for Vertical Pod Autoscaling (VPA)
- [ ] Connect to `/api/v1/services/{id}/` PATCH

## Phase 3: Nixpacks Buildpacks
- [ ] Add `buildpack` field to Service model (choices: auto, nixpacks, docker, static)
- [ ] Update `smart_deploy_task` to respect selection
- [ ] Create `BuildpackSelector` in service creation wizard

## Phase 4: CI/CD Pipeline UI
- [ ] Add `pipeline_stages` JSONField to Deployment model
- [ ] Update backend tasks to log stage transitions
- [ ] Create `PipelineVisualizer` component (horizontal steps)
- [ ] Connect real-time updates via WebSocket

## Phase 5: Edge Functions MVP
- [ ] Add `service_type='FUNCTION'` to Service model
- [ ] Create `function_runtime.py` wrapper task
- [ ] Build `functions/page.tsx` with code editor
- [ ] Implement direct deploy action for raw code
