# Multi-Server Manual Smoke Checklist

**Last reviewed: 2026-06-12**

## External VPS Connection

| Check | Status |
| --- | --- |
| Add server with public IP/domain | Working via fallback |
| Add SSH user, password, or SSH key | Working via fallback |
| Add optional private IP for mesh endpoint selection | Working via fallback |
| Mark server as primary and verify workloads are disabled | Working |
| Verify `has_ssh_credentials` appears without exposing secrets | Working |

## Deployment Selection

| Check | Status |
| --- | --- |
| Open New Service target selection | Working |
| Confirm control-plane server is disabled | Working |
| Select worker A and worker B | Working |
| Start multi-deploy and inspect structured local/remote responses | Working |

## Transfer

| Check | Status |
| --- | --- |
| Open Transfers page | Working |
| Confirm only workload-enabled servers appear as targets | Working |
| Drag a local service to worker B | Working |
| Watch transfer progress/status polling | Working |
| Force a transfer failure and confirm source service remains on worker A | Working in local multi-server harness |

## VPN Mesh

| Check | Status |
| --- | --- |
| Create mesh | Working |
| Add local, worker A, worker B peers | Working |
| Deploy mesh | Working in local multi-server harness |
| Check mesh health/status in Network page | Working |
| Confirm private keys never appear in logs or API responses | Working |

## Replication

| Check | Status |
| --- | --- |
| Deploy replication with non-default replication password | Working |
| View `replication_status` on the mesh | Working |
| Run Sync Now | Working |
| Disable replication | Working |
| Confirm DB-backed result/error fields update | Working in local multi-server harness |

## Commands

```powershell
python scripts/multi_server_local_harness.py
pytest
python manage.py test || true

cd frontend
npm run typecheck
npm run lint
npm run build
npm test || true
```
