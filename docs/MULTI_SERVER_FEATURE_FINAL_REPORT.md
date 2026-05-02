# Multi-Server Feature Final Report

Date: 2026-05-02

## Status Summary

| Feature | Status | Proof |
| --- | --- | --- |
| Add/connect external server | Working via fallback | `ManagedServerCreateSerializer` accepts IP/domain, API URL/token, gateway secret, SSH user, password, key, private IP, and workload-target flags. |
| Manual InterServer VPS connection | Working via fallback | User buys an InterServer VPS, installs or provides access, then connects it in Servers with IP, SSH credentials, API URL/token when present. Direct provider API: deferred behind feature flag. Launch-safe fallback: connect existing VPS manually. Status: Working via fallback. |
| Multi-server deployment target selection | Working | Frontend disables control-plane/workloads-off targets; backend `ServerGuard` rejects the same targets in create, deploy, rollback, transfer, restore, task dispatch, and autoscale paths. |
| Primary server exclusion | Working | `allow_user_workloads=False` is enforced for primary servers and `ServerGuard` is used by API and task paths. |
| Transfer worker A to worker B | Working in local multi-server harness | `test_transfer_worker_a_to_worker_b_and_primary_rejection` queues worker A to worker B and rejects the control plane. |
| Transfer failure preserves source deployment | Working in local multi-server harness | Harness forces backup failure and verifies the service remains on worker A. |
| Replication worker A to worker B | Working in local multi-server harness | Harness deploys Patroni state across local, worker A, worker B with patched command execution and persists `replication_status=ACTIVE`. |
| Replication status visibility | Working | Mesh API returns `replication_status`, result, error, and timestamp fields; frontend displays them and supports Sync Now, Enable, Disable. |
| VPN mesh peer/status flow | Working in local multi-server harness | Harness registers local, worker A, worker B peers; deploy task validates config and persists `mesh_status=ACTIVE`. |
| Frontend action flows | Working | Servers, New Service, Transfers, Network, and Replication pages call the backend APIs with corrected payloads, loading states, status displays, polling, and backend error messages. |
| Backend structured responses | Working | Transfer, mesh, replication, server, deployment, and guard responses return structured JSON. |
| Celery/task DB state | Working | Transfer state, mesh state, and replication state are persisted on models, not only Celery result state. |

## Implementation Notes

The external VPS launch path is:

1. User buys an InterServer VPS.
2. User opens Servers and chooses Connect Existing.
3. User enters name, public IP/domain, optional private IP, API URL/token if installed, gateway secret if using HMAC, and SSH user/password or SSH key.
4. SMSLY stores encrypted credentials and exposes `has_ssh_credentials` without returning secrets.
5. Transfer, mesh, replication, and deployment flows use the connected server as a workload target only when it is not primary and `allow_user_workloads=True`.

The provider API path is kept separate from public launch. Direct provider API: deferred behind feature flag. Launch-safe fallback: connect existing VPS manually. Status: Working via fallback.

## Proof Commands

Focused proof used during this pass:

```powershell
$env:DJANGO_SETTINGS_MODULE='config.settings'; $env:PYTHONPATH='backend'; python -m pytest backend/apps/deployments/tests/test_servers.py backend/apps/deployments/tests/test_transfer.py backend/apps/deployments/tests/test_transfer_hardening.py backend/apps/deployments/tests/test_transfer_strict_mode.py backend/apps/deployments/tests/test_primary_server_fix.py backend/apps/deployments/tests/test_mesh.py backend/apps/deployments/tests/test_replication.py backend/apps/deployments/tests/test_replication_hardening.py -q
$env:DJANGO_SETTINGS_MODULE='config.settings'; $env:PYTHONPATH='backend'; python -m pytest backend/apps/deployments/tests/test_multi_server_local_harness.py -q
```

Results:

```text
48 passed
6 passed
```
