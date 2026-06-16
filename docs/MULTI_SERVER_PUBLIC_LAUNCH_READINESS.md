# Multi-Server Public Launch Readiness

## Launch Status

| Requirement | Status |
| --- | --- |
| Add/connect external server works | Working via fallback |
| Manual InterServer VPS connection path works or is fully supported | Working via fallback |
| Multi-server deployment target selection works | Working |
| Primary server is never selectable for user workloads | Working |
| Transfer worker A to worker B works | Working in local multi-server harness |
| Transfer failure preserves source deployment | Working in local multi-server harness |
| Replication worker A to worker B works | Working in local multi-server harness |
| Replication status is visible | Working |
| VPN mesh peer/status flow works | Working in local multi-server harness |
| Frontend buttons call correct backend APIs | Working |
| Backend routes return structured responses | Working |
| Celery/tasks update DB state correctly | Working |
| Frontend displays loading, progress, success, and backend errors | Working |

## Public Launch Path

Direct provider API: deferred behind feature flag.

Launch-safe fallback:

1. Customer buys an InterServer VPS.
2. Customer opens Servers > Connect Existing.
3. Customer enters the VPS public IP/domain, optional private IP, SSH user, SSH password or SSH key, and API/gateway credentials when present.
4. SMSLY validates and stores the server record.
5. Workload flows use the server only when it is a workload target.
6. Transfer, mesh, and replication actions persist DB-backed progress and status.

Status: Working via fallback.

## Required Verification

Run before public release:

```powershell
pytest
pytest -q || true

cd frontend
npm run typecheck
npm run lint
npm run build
npm test || true

git status --short
git diff --stat
```

Additional local harness:

```powershell
python scripts/multi_server_local_harness.py
```
