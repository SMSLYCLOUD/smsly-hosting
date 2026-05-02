# Multi-Server Local Test Harness

## Purpose

The local harness proves the public-launch multi-server flows without needing live VPS credentials. It simulates:

| Node | Role |
| --- | --- |
| control-plane | Primary server with `allow_user_workloads=False` |
| worker-a | Existing workload server with SSH credentials |
| worker-b | Existing workload server with SSH credentials and private IP |

## Run

```powershell
python scripts/multi_server_local_harness.py
```

Equivalent direct command:

```powershell
$env:DJANGO_SETTINGS_MODULE='config.settings'
$env:PYTHONPATH='backend'
python -m pytest backend/apps/deployments/tests/test_multi_server_local_harness.py -q
```

## Coverage

| Flow | Status |
| --- | --- |
| Workload target selection excludes control plane | Working in local multi-server harness |
| Transfer worker A to worker B queues a DB-backed transfer | Working in local multi-server harness |
| Transfer to control plane returns `PRIMARY_SERVER_DEPLOYMENT_BLOCKED` | Working in local multi-server harness |
| Transfer failure keeps service assigned to worker A | Working in local multi-server harness |
| WireGuard peer registration for local, worker A, worker B | Working in local multi-server harness |
| Mesh deploy task persists `mesh_status=ACTIVE` | Working in local multi-server harness |
| Replication deploy task persists `replication_status=ACTIVE` | Working in local multi-server harness |
| Replication Sync Now persists visible health state | Working in local multi-server harness |

## How It Works

The harness uses Django test DB records for `ManagedServer`, `Service`, `ServerTransfer`, `MeshNetwork`, and `WireGuardPeer`. SSH, WireGuard command application, and Patroni command execution are patched at the command boundary, so the harness exercises API payloads, serializers, services, guards, state machines, and DB persistence while staying deterministic on a developer machine.
