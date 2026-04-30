# PaaS Feature Verification Matrix (Brutal Honesty Pass)

| Feature | UI path | API endpoint(s) | Backend handler | Async task | Status | Evidence | Fix required | Tests | Landing page claim allowed |
|---|---|---|---|---|---|---|---|---|---|
| Deployments approve/cancel/bulk-cancel | `/deployments`, service settings | `/api/v1/deployments/{id}/approve/`, `/cancel/`, `/bulk-cancel/` | `DeploymentViewSet.approve/cancel/bulk_cancel` | deploy tasks + cleanup | Partial | Routes exist in `views.py` and `urls.py` | complete runtime reconciliation commands + full regression coverage | Existing + additional targeted tests needed | Yes (with “hardened in progress”) |
| Replication | `/replication` | `/api/v1/replication/*` | `ReplicationViewSet`, `ReplicationService` | `tasks_replication.*` | Partial / risk | Health can report unreachable peers when mesh exists but runtime missing | Gate by default; keep explicit prerequisites and actionable errors | Add state-model and degraded/unreachable tests | No (until runtime-verified) |
| VPN Mesh | `/network` | `/api/v1/mesh/*` | `MeshNetworkViewSet`, `WireGuardService` | `tasks_mesh.*` | Partial / risk | Peer DB records != guaranteed handshake/connectivity | Gate by default pending handshake/route telemetry checks | Add handshake/bytes/ping tests | No |
| Tunnels | `/tunnels` | `/api/v1/tunnels/*` | `TunnelViewSet` | task-backed runtime operations | Partial / risk | Endpoint exists; runtime reachability not universally guaranteed | Gate by default pending process + endpoint health reconciliation | Add tunnel lifecycle tests | No |
| Transfers | `/transfers` | `/api/v1/transfers/` | `ServerTransferViewSet` | `execute_server_transfer_task` | Improved partial | Added structured validation errors + frontend parser; disabled with reason when prerequisites missing | keep adding rollback/runtime checks | Add frontend field-error tests + task-queue assertions | No |
| Autoscaler | `/autoscaler` | `/api/v1/autoscaler/*` | `apps.autoscaler.views` | `autoscaler_collect_stats` | Partial | Exists; real scale action safety varies by runtime | Gate by default unless explicitly enabled | add dry-run + cooldown + floor tests | No |
| Backups | `/backups`, service backup tabs | `/api/v1/backups/*`, `/api/v1/server/backups/*` | backup viewsets in `views.py` | backup/restore tasks | Partial | Task + model paths exist; artifact validity varies by env | keep explicit artifact validation messaging | add artifact existence + restore preflight tests | “Experimental” only |
| Topology | `/topology` | topology endpoints | topology views/services | n/a | Partial | Graph exists; runtime reconciliation needed for stale/orphan cases | implement DB+runtime reconcile markers | add stale/orphan tests | Experimental |
| Functions | `/functions` | function APIs | service/function handlers | n/a | Partial | UI present; runtime differs by environment | Gate by default | add route/runtime tests | No |

## Notes
- Feature flags were added to prevent exposed-but-unverified infra controls from appearing as production-ready.
- Transfers now return structured validation contract with `ok/error/details/retryable` for key failure paths.
