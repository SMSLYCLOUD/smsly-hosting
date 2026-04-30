# Emergency Deployment, Navigation, and Build Fix

## Root Causes
1. **Deployment API Collision:** The `bulk_cancel` endpoint returned `405 Method Not Allowed` due to being evaluated as a detail URL (where `pk="bulk-cancel"`) before the intended action route was hit. Furthermore, `approve`, `cancel`, and `bulk_cancel` were erroneously nested inside the `PlatformResourcesView` instead of `DeploymentViewSet`, preventing proper lookup and responses.
2. **Broken JSON Payloads:** The `approve`, `cancel`, and `bulk_cancel` endpoints previously lacked explicit success/error structures. They failed to return `"ok": true/false`, breaking the frontend expectations and resulting in vague errors.
3. **Hidden Pages in Navigation:** Many core frontend paths like backups, logs, autoscalers, replication, mesh, databases, and monitoring were hidden globally if their feature flags were inactive, even breaking development/testing setups. There was no clean override mechanism.
4. **Primary Server Inclusion in Deployments:** The user deployment selector allowed deployments directly to the control-plane / primary server, risking production stability.
5. **Frontend Build Errors:** TypeScript type errors caused strict checks (`tsc --noEmit`) to fail.

## Files Changed
- `backend/apps/deployments/urls.py` (explicit `bulk-cancel` path)
- `backend/apps/deployments/views.py` (moved `approve`/`cancel`/`bulk_cancel`, structured responses, and primary server guard)
- `backend/apps/deployments/tests/test_emergency_fix.py` (new tests for API contract validation)
- `backend/apps/deployments/tests/test_primary_server_fix.py` (new tests for primary server exclusion guard)
- `frontend/src/lib/nav-visibility.ts` (new centralized utility for checking testing overrides)
- `frontend/src/components/layout/Navbar.tsx` (unlocked authLinks arrays, added `shouldShowAllNav`)
- `frontend/src/components/sidebar.tsx` (unlocked `HIDDEN_BY_FLAG`, added missing platform links)
- `frontend/src/components/settings/DeploymentsTab.tsx` (surface backend errors properly)
- `frontend/src/app/new/page.tsx` (disabled primary servers in the deploy target UI list)
- `frontend/src/app/servers/page.tsx` & `frontend/src/app/servers/[id]/page.tsx` (renamed "Primary" to "Control Plane")
- `frontend/src/app/accounts/[[...slug]]/page.tsx` (resolved TS error)
- `frontend/.env.example` (documented testing flags)
- `docs/ROUTE_NAV_AUDIT.md` (listed current route links)

## Test Commands and Results
- **Backend Tests:** Ran `/home/jules/.pyenv/versions/3.12.13/bin/python backend/manage.py test apps.deployments.tests.test_emergency_fix apps.deployments.tests.test_primary_server_fix`. **Result:** All 9 tests passed.
- **Frontend Typecheck:** `cd frontend && npm run typecheck`. **Result:** Compiled with 0 errors.
- **Frontend Lint:** `cd frontend && npm run lint`. **Result:** Ran successfully.
- **Frontend Build:** `cd frontend && npm run build`. **Result:** Optimized production build created successfully.

## Final Manual Smoke Checklist
- [x] Sidebar shows all testing routes with `NEXT_PUBLIC_SHOW_ALL_NAV=true`
- [x] Navbar does not hide core platform pages
- [x] Deployment approval works from UI
- [x] Deployment cancellation works from UI
- [x] Bulk cancellation works from UI
- [x] Failed bulk items show readable reasons
- [x] Primary server is visible in server overview
- [x] Primary server is not selectable for user deployments
- [x] Worker/app server remains selectable
- [x] `npm run typecheck` passes
- [x] `npm run lint` passes
- [x] `npm run build` passes
- [x] backend tests passes
