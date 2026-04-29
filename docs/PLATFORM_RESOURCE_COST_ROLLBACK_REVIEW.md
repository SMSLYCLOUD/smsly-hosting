# Platform Resource/Cost/Rollback Review
- Dashboard service grid: `frontend/src/app/services/page.tsx` + `frontend/src/components/dashboard/ServicesGrid.tsx`.
- Service data contract: `GET /api/v1/services/` via `ServiceSerializer`.
- Service status source: latest deployment in `ServiceSerializer.get_latest_deployment`.
- VPS metadata source: `Service.server` -> `ManagedServer`.
- Rollback trigger: `POST /api/v1/deployments/{id}/rollback/`.
- Root rollback failure class found: endpoint returned generic/unstructured errors and lacked mandatory confirmation payload from frontend.
- Dirty/noisy artifacts: multiple root-level `*.txt`, temp scripts, and debug outputs; cleanup policy documented in cleanup report.
- Proposed changes: add platform resource endpoint, add cost fields to service payload, structured rollback errors, and dashboard rendering.
