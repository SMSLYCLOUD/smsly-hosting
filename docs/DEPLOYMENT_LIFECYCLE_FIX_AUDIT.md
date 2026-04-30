# Deployment Lifecycle Fix Audit

## Broken route map (before)
- Frontend used:
  - `POST /api/v1/deployments/<id>/approve/`
  - `POST /api/v1/deployments/<id>/cancel/`
  - `POST /api/v1/deployments/bulk-cancel/`
- Backend actions existed, but behavioral mismatches caused production breakage:
  - cancel was not idempotent for terminal states
  - cancel/approve supported too few lifecycle states
  - bulk-cancel filtered wrong states and did not return affected IDs

## Correct route map (after)
- `POST /api/v1/deployments/<uuid:id>/approve/` (review + awaiting_approval)
- `POST /api/v1/deployments/<uuid:id>/cancel/` (idempotent; non-terminal cancellation)
- `POST /api/v1/deployments/bulk-cancel/` (`deployment_ids[]`, optional `service_id`)

## State machine standardization
- Blocking/in-progress statuses now include:
  - `QUEUED`, `AWAITING_APPROVAL`, `REVIEW`, `BUILDING`, `DEPLOYING`, `HEALTH_CHECK`, `TRAFFIC_SHIFTING`, `STAGED`
- Terminal statuses include:
  - `ACTIVE`, `FAILED`, `CANCELLED`, `ROLLED_BACK`

## Cleanup model
- Service cleanup no longer assumes `service.slug`.
- Runtime name now derived via canonical helper (`get_service_runtime_name`) for deterministic container lookup.

## Duplicate green root cause
- Runtime naming and cleanup used inconsistent identifiers, which caused misses during container cleanup.
- Cancel path and service deletion path did not consistently target staged/green artifacts across status variants.

## Git auth root cause
- Existing deployment flow can fail clone with invalid HTTPS credentials and leave misleading state transitions/log outputs.
- Follow-up hardening still required in task-level clone auth path.

## Files changed
- `backend/apps/deployments/views.py`
- `backend/apps/deployments/runtime_naming.py`
- `backend/apps/deployments/tests/test_service_lifecycle.py`
- `docs/DEPLOYMENT_LIFECYCLE_FIX_AUDIT.md`

## Tests added
- approve route contract + status transition
- cancel idempotency for terminal state
- bulk-cancel route existence and behavior
