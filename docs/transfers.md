# Server Transfers

Grid's server transfer feature moves running services (or an entire platform) from one node to another with minimal downtime. Transfers preserve service state — environment variables, persistent volumes, and the running container image — and re-attach the service to its public domain once the target is healthy.

## Overview

A server transfer is an orchestrated migration that runs as a background task. It captures a snapshot of the source workload, ships it to the target over SSH, restores it, and (when applicable) updates DNS so traffic follows the container to the new host.

Use transfers when you need to:

- Rebalance workloads across a multi-server fleet.
- Move a service off a primary/control-plane node to a dedicated worker.
- Migrate from one Grid host to another (full server transfer).
- Move a single service between two non-primary nodes in your mesh.
- Repatriate a service that was previously running on a remote node.

Transfers run asynchronously. The API returns the new transfer record immediately; status, progress, and live logs are polled through `GET /api/v1/transfers/{id}/`.

## Transfer Types

| Type | Scope | Use when |
| --- | --- | --- |
| `SERVICE` | One service (and its addons, by association) | Moving a single workload between two nodes. Addons follow their parent service automatically. |
| `FULL` | The entire platform (database, all services, configuration) | Migrating a complete Grid instance to a new primary or migrating between providers. The target is reinstalled with `install.sh` and the platform database is restored. |

Choose `SERVICE` for the common case. Use `FULL` only when you are relocating the entire platform, not individual workloads. The `FULL` path also re-runs the WireGuard mesh bring-up so existing managed nodes reconnect to the new master.

## Prerequisites

Before initiating a transfer, ensure:

- **Target server is registered and ONLINE.** Add it under **Servers → Connect Existing** with its public IP/domain and SSH credentials. Only workload-enabled servers (`allow_user_workloads=True`, `is_primary=False`) appear as transfer targets in the UI.
- **SSH credentials are available.** Either stored on the connected target server (preferred), or supplied inline in the transfer request. Both SSH keys (PEM-encoded private key) and passwords are supported. Password takes precedence if both are present.
- **Target server is reachable on TCP/22.** Bidirectional reachability is recommended so the target can confirm connectivity back to the source during the verification stage.
- **Target has a working Grid backend.** For `SERVICE` transfers, the target must have the Grid backend container running (the transfer engine starts it if it is down). For `FULL` transfers, only Docker is required on the target; the platform itself is installed by the transfer.
- **Domain is configured on the source.** Cloudflare DNS updates are only emitted when `PlatformConfig.cloudflare_api_token` and `PlatformConfig.domain` are set. Without these, the transfer still completes but you must update DNS yourself.
- **Encryption key is set.** `BACKUP_ENCRYPTION_KEY` must be available on the source if any of its backups are encrypted. The transfer will refuse to start otherwise.

## Step-by-Step Usage

### Using the UI (Transfers page)

1. Open **Transfers** in the sidebar. Connected workload-enabled servers appear as columns; the local primary node appears on the left.
2. Optional: enter a **New domain** in the top bar. This sets `target_public_domain` and is used for cross-platform migration (the service's `public_domain` is rewritten to `<subdomain>.<target_domain>` after the transfer completes).
3. Drag a service or addon from one column and drop it onto the target column. Addons are moved by moving their parent service — dropping an addon transfers the service it belongs to.
4. The UI optimistically updates immediately and POSTs the transfer. The transfer enters the pipeline and begins progressing through its stages.
5. Watch the **Active Stream** panel on the right. Each in-progress transfer shows a progress bar, current step, and the live status. The list polls every 5 seconds.
6. When the status reaches `COMPLETED`, the service is live on the target. A **Rollback** button is available for 48 hours.
7. To abort a transfer that has not yet completed, click **Cancel**. The transfer moves to `CANCELLED` and the source workload is left untouched (the backup is cleaned up by the worker).

### Using the API

The transfer pipeline is fully scriptable. The minimal flow:

1. **Identify endpoints.** Resolve the target `target_server_id` (UUID of the connected `ManagedServer`) and the source `service_id` (UUID of the `Service` record).
2. **POST the transfer request** to `/api/v1/transfers/` with `transfer_type`, `service_id`, `source_server_id`, and `target_server_id`.
3. **Poll for status.** `GET /api/v1/transfers/{id}/` returns the record, including `status`, `progress_percent`, `current_step`, and live `logs`.
4. **Decide follow-up.** When status is `COMPLETED`, optionally POST `/api/v1/transfers/{id}/rollback/` to revert. When status is `FAILED` mid-pipeline, the source workload remains in place; investigate `error_message`.

## API Reference

All endpoints are mounted under `/api/v1/transfers/`. Authentication is session- or token-based for user endpoints, and HMAC-signed for the node-to-node sync endpoint.

### `GET /api/v1/transfers/`

List transfers owned by the authenticated user (or transfers targeting their services). Returns the most recent transfers first.

**Query parameters:** None. The view filters by `owner` and `service__owner` and orders by `-created_at`.

**Example request:**

```bash
curl -sS http://localhost:8000/api/v1/transfers/ \
  -H "Authorization: Token $SMSLY_TOKEN"
```

**Example response (abridged):**

```json
[
  {
    "id": "1f4a2c63-9b6e-4f01-b6a5-7c5d0a44a1a9",
    "status": "VERIFYING",
    "transfer_type": "SERVICE",
    "service": "9c8b4b1a-7d1c-4a2b-9a55-2e8c3d4f9b21",
    "source_server_ip": "10.0.0.10",
    "target_server_ip": "10.0.0.20",
    "progress_percent": 88,
    "current_step": "Verifying services on target server...",
    "can_rollback": true,
    "rollback_deadline": "2026-06-14T15:23:11Z",
    "is_incoming": false,
    "created_at": "2026-06-12T15:23:11Z",
    "completed_at": null
  }
]
```

### `POST /api/v1/transfers/`

Create and queue a new transfer. The transfer is persisted immediately and dispatched to a Celery worker; the response is the full `ServerTransferSerializer` payload with `status="PREPARING"`.

**Required fields:**

| Field | Type | Notes |
| --- | --- | --- |
| `transfer_type` | `SERVICE` \| `FULL` | See Transfer Types. |
| `service_id` | UUID | Required when `transfer_type=SERVICE`. Must be owned by the authenticated user. |
| `source_server_id` | UUID | Optional. When omitted, the source is the local node. |
| `target_server_id` | UUID | Optional. When omitted, the target is the local node. |
| `target_server_ip` | string (IP) | Required only when not selecting a `target_server_id` and the local node IP is unset in `PlatformConfig`. |
| `target_ssh_key` | string (PEM) | Optional if the target server has stored credentials or is the local node. |
| `target_ssh_password` | string | Optional if the target server has stored credentials or is the local node. |
| `target_public_domain` | string | Optional. Cross-platform migration target base domain. |
| `source_ssh_key` / `source_ssh_password` | string | Optional. Required for node-to-node transfers when source credentials are not stored on the `ManagedServer` record. |

**Example request (SERVICE transfer, local → connected node):**

```bash
curl -sS http://localhost:8000/api/v1/transfers/ \
  -H "Authorization: Token $SMSLY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "transfer_type": "SERVICE",
    "service_id": "9c8b4b1a-7d1c-4a2b-9a55-2e8c3d4f9b21",
    "source_server_id": null,
    "target_server_id": "7d3b1a8e-2c5f-4a6d-8e9b-0c1a2b3c4d5e"
  }'
```

**Example request (FULL transfer, with cross-platform domain remap):**

```bash
curl -sS http://localhost:8000/api/v1/transfers/ \
  -H "Authorization: Token $SMSLY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "transfer_type": "FULL",
    "target_server_id": "7d3b1a8e-2c5f-4a6d-8e9b-0c1a2b3c4d5e",
    "target_ssh_password": "REDACTED",
    "target_public_domain": "app.example.com"
  }'
```

**Example response (HTTP 201):**

```json
{
  "id": "1f4a2c63-9b6e-4f01-b6a5-7c5d0a44a1a9",
  "status": "PREPARING",
  "transfer_type": "SERVICE",
  "service": "9c8b4b1a-7d1c-4a2b-9a55-2e8c3d4f9b21",
  "source_server_ip": "10.0.0.10",
  "target_server_ip": "10.0.0.20",
  "progress_percent": 0,
  "current_step": "",
  "is_incoming": false,
  "can_rollback": true,
  "created_at": "2026-06-12T15:23:11Z"
}
```

**Error responses:**

| Status | Cause |
| --- | --- |
| 400 | Invalid payload, missing `service_id` for SERVICE, missing SSH credentials for non-local target, unsafe target IP (SSRF), or target server is not `ONLINE`. |
| 404 | Source service or target server not owned by caller. |
| 409 | An active transfer for the same target already exists. |
| 503 | Celery worker unavailable; transfer was persisted in `FAILED` state for retry. |

### `POST /api/v1/transfers/{id}/rollback/`

Roll back a completed transfer. Only valid for transfers in `COMPLETED` status, within the `rollback_deadline` (48 hours after completion by default). On success, the transfer status becomes `ROLLED_BACK` and the service is reassigned to the source node.

**Example request:**

```bash
curl -sS -X POST \
  http://localhost:8000/api/v1/transfers/1f4a2c63-9b6e-4f01-b6a5-7c5d0a44a1a9/rollback/ \
  -H "Authorization: Token $SMSLY_TOKEN"
```

**Example response:**

```json
{ "status": "rollback_started" }
```

**Error responses:**

| Status | Cause |
| --- | --- |
| 400 | `can_rollback=False` (transfer never completed, or rollback window has expired). |

### `POST /api/v1/transfers/{id}/cancel/`

Cancel an in-progress transfer. The endpoint accepts transfers in any of the active stages (`PREPARING`, `UPLOADING`, `RESTORING`, `DNS_CUTOVER`, `VERIFYING`). Once accepted, the transfer is moved to `CANCELLED` and the source workload remains on the source node. The transfer worker observes the cancel flag and stops the in-flight operation at the next safe checkpoint.

**Example request:**

```bash
curl -sS -X POST \
  http://localhost:8000/api/v1/transfers/1f4a2c63-9b6e-4f01-b6a5-7c5d0a44a1a9/cancel/ \
  -H "Authorization: Token $SMSLY_TOKEN"
```

**Example response:**

```json
{ "status": "cancellation_requested", "id": "1f4a2c63-9b6e-4f01-b6a5-7c5d0a44a1a9" }
```

**Error responses:**

| Status | Cause |
| --- | --- |
| 400 | Transfer is in a terminal state (`COMPLETED`, `FAILED`, `ROLLED_BACK`, `CANCELLED`) and cannot be cancelled. |
| 404 | Transfer does not exist or is not owned by the caller. |

### `POST /api/v1/transfers/register-incoming/`

Internal node-to-node endpoint used by the source platform to notify the target platform that an incoming transfer has been queued. The call carries the same payload the source will use for the transfer, so the target's dashboard can render the incoming operation even before the data plane has finished moving files. This endpoint is also used to render the transfer on the target node's Transfers page when the source controller initiated the move.

**Authentication:** No session or token is required. The endpoint requires:

- Header `X-SMSLY-Remote-Sync: 1`
- Header `X-Request-Timestamp` (UNIX seconds, within 300 seconds of now)
- Header `X-Gateway-Signature-V2` (HMAC-SHA256 over `METHOD|path|ts|sha256(body)` using the source node's `gateway_secret` or the platform-wide `GATEWAY_SECRET`)

The source IP must correspond to a `ManagedServer` record already known to the target. If the source's `ManagedServer` has an owner, that owner is assigned to the incoming transfer; otherwise the first admin user is used.

**Request body:**

| Field | Type | Notes |
| --- | --- | --- |
| `source_ip` | string (IP) | Required. Must resolve to a known `ManagedServer`. |
| `target_ip` | string (IP) | Required. The IP of this node. |
| `transfer_type` | `SERVICE` \| `FULL` | Required. |
| `service_name` | string | Optional. Human-readable name for the telemetry log. |

**Example request:**

```bash
curl -sS -X POST \
  http://10.0.0.20:8000/api/v1/transfers/register-incoming/ \
  -H "Content-Type: application/json" \
  -H "X-SMSLY-Remote-Sync: 1" \
  -H "X-Request-Timestamp: 1749741791" \
  -H "X-Gateway-Signature-V2: $HMAC_SIG" \
  -d '{
    "source_ip": "10.0.0.10",
    "target_ip": "10.0.0.20",
    "transfer_type": "SERVICE",
    "service_name": "web-api"
  }'
```

**Example response:**

```json
{ "id": "1f4a2c63-9b6e-4f01-b6a5-7c5d0a44a1a9", "status": "PREPARING" }
```

**Error responses:**

| Status | Cause |
| --- | --- |
| 400 | Missing or malformed `source_ip` / `target_ip`, or invalid `transfer_type`. |
| 401 | Missing or invalid HMAC, timestamp drift > 300s, or source IP not registered. |

## Status States

A transfer transitions through the following pipeline. Each stage persists a `progress_percent` and a `current_step` so the UI can render a live progress bar without polling logs.

```
PREPARING  →  UPLOADING  →  RESTORING  →  DNS_CUTOVER  →  VERIFYING  →  COMPLETED
                                                                         │
                                                                         ├── ROLLED_BACK (manual revert)
                                                                         └── FAILED     (any stage can short-circuit here)
```

| Status | What happens | Terminal? |
| --- | --- | --- |
| `PREPARING` | The source backup is created (`SERVICE` → `ServiceBackup`, `FULL` → `ServerBackup`). On the target, Docker is verified and the Grid backend is started if needed. | No |
| `UPLOADING` | The backup is shipped to the target over SSH. For `FULL`, the `install.sh` script and `.env` are also uploaded. | No |
| `RESTORING` | The target unpacks the backup, hydrates the database row, loads the Docker image, restores volumes, and starts the container. For `FULL`, the platform database is dropped and restored from the SQL dump and the installer runs on the target. | No |
| `DNS_CUTOVER` | Cloudflare A records are updated for `FULL` (apex + wildcard) or, for `SERVICE` on a Lite Agent target, a per-service A record is created. | No |
| `VERIFYING` | Health checks run on the target (HTTP 200 on `/health` for `FULL`, container running + Traefik route returning a non-5xx for `SERVICE`). WireGuard mesh is interconnected so source and target can communicate post-cutover. | No |
| `COMPLETED` | The transfer has finished. The service is reassigned to the target `ManagedServer`, the source container is stopped, and `rollback_deadline` is set to `completed_at + 48h`. | Yes |
| `FAILED` | A stage errored. The source workload remains on the source node. `error_message` is set to a redacted, human-readable summary. The transfer can be retried by creating a new one. | Yes |
| `ROLLED_BACK` | A successful transfer was reverted. The service is reassigned back to the source and DNS is restored (when Cloudflare is configured). | Yes |
| `CANCELLED` | A user cancelled an in-progress transfer via `POST /api/v1/transfers/{id}/cancel/`. The source workload remains on the source node. | Yes |

`PREPARING`, `UPLOADING`, `RESTORING`, `DNS_CUTOVER`, and `VERIFYING` are the **active** statuses. Only one active transfer can exist for a given `(owner, target_ip, transfer_type[, service])` tuple — creating a second one returns HTTP 409.

## Security

Transfers handle SSH credentials and the ability to execute commands on remote hosts. The transfer pipeline is hardened at three layers.

### SSRF Protection

Public transfer requests validate the resolved target IP against `is_safe_ip()`. Loopback, link-local, multicast, reserved, and unspecified ranges are always rejected. Private ranges (RFC 1918) are accepted only when the target is a known `ManagedServer` (i.e. the caller explicitly opted in by selecting a connected server). This prevents an unauthenticated caller from coercing the backend into opening SSH connections to internal infrastructure.

### HMAC Node-to-Node Auth

The `POST /api/v1/transfers/register-incoming/` endpoint never accepts session or token credentials. It requires:

- `X-SMSLY-Remote-Sync: 1` — declares the request as a node-to-node sync.
- `X-Request-Timestamp` — UNIX seconds, must be within 300 seconds of now to prevent replay.
- `X-Gateway-Signature-V2` — HMAC-SHA256 over `METHOD|path|ts|sha256(body)` using either the source `ManagedServer.gateway_secret` or the platform `GATEWAY_SECRET` / `SECRET_KEY` as a fallback. Signature comparison uses constant-time `hmac.compare_digest`.

The source IP must resolve to a `ManagedServer` row that already exists in the target's database; otherwise the request is rejected with 401.

### Encrypted Credential Storage

SSH keys and passwords submitted with a transfer are written to `ServerTransfer.source_ssh_key`, `source_ssh_password`, `target_ssh_key`, and `target_ssh_password`. These fields use `EncryptedTextField` / `EncryptedCharField` (Fernet) at the application layer — values are encrypted at rest in the database.

In addition, the transfer worker scrubs these fields as soon as the transfer reaches a terminal state:

- `target_ssh_key` and `target_ssh_password` are cleared on `COMPLETED`, `FAILED`, and `ROLLED_BACK`.
- `source_ssh_key` and `source_ssh_password` are cleared on `FAILED`.
- When the Celery worker fails to enqueue the transfer, all four fields are cleared on the `FAILED` record.

Transfer logs are also redacted before persistence. The redactor strips PEM private key blocks, `*_TOKEN` / `*_SECRET` / `*_PASSWORD` / `*_KEY` / `*_DSN` / `*_URL` assignments, `Authorization` / `X-API-Key` / `X-Auth-Token` headers, and `user:password@` segments in URLs. The redactor is applied to both the per-transfer `logs` field and the application log stream.

## Troubleshooting

### "Target server IP is in a forbidden range (SSRF protection)"

The resolved target IP is in a loopback, link-local, or RFC 1918 range, and you did not select a `ManagedServer` for it. Use a connected `ManagedServer` (`target_server_id`) when transferring to a private LAN address, or supply a public IP.

### "No SSH credentials available for target server"

Neither `target_ssh_key` nor `target_ssh_password` was supplied, and the `ManagedServer` for the target has no stored credentials. Open **Servers → Edit** on the target and re-save the SSH key or password, or pass credentials in the API request body.

### "Target server 'X' is currently OFFLINE. Transfers are only allowed to ONLINE nodes."

The connected server is registered but not currently online (the mesh heartbeat has not been received recently). Bring the target back online, wait for the next mesh probe to mark it `ONLINE`, then re-queue the transfer.

### "Source SSH credentials required for node-to-node transfer."

The source is a connected (non-local) `ManagedServer` with no stored SSH credentials. Either pass `source_ssh_key` / `source_ssh_password` in the request, or edit the source server and save its SSH credentials.

### "Encrypted backup detected but BACKUP_ENCRYPTION_KEY is not set."

The source's backup is encrypted, but the controller's environment does not have the matching key. Set `BACKUP_ENCRYPTION_KEY` in the source `.env` to the same value used at backup time, restart the backend, and re-create the transfer.

### "Target Grid backend did not become ready before restore."

The target's Grid backend container started but the health endpoint did not return 200 within the readiness window. SSH into the target and run `docker ps` to confirm the backend is up. Check `docker logs smsly-hosting-backend-1` (or whichever container the target is using) for migration or database errors. Then retry.

### "Could not install Docker on target server."

The `install_docker()` step failed. The target must be a fresh Ubuntu 20.04/22.04/24.04 LTS image with root SSH access. If the target has a non-standard OS, install Docker manually before initiating the transfer.

### Transfer hangs in `RESTORING`

The remote Django restore script is waiting on the database. Inside the target backend container, run:

```bash
docker exec -it smsly-hosting-backend-1 python manage.py shell -c "from django.db import connection; connection.ensure_connection()"
```

If the connection fails, the target's PostgreSQL is unreachable. Restart the database with `docker compose -f docker-compose.prod.yml restart db` on the target and let the transfer retry.

### "RESTORE_FAILED: …" in transfer logs

The remote restore script reported an unrecoverable error. The full traceback is in the transfer's `logs` field. The most common cause is a mismatched owner email on the target — the script falls back to a superuser but logs a warning. Verify the source's service owner has a corresponding account on the target.

### Rollback button is missing

`can_rollback` is `False` because either (a) the transfer did not complete, (b) the 48-hour rollback window has passed, or (c) rollback was already used. After the deadline the source state is no longer guaranteed to be intact and a rollback could corrupt the source.

### Service is live on target but DNS still points to source

Cloudflare DNS is only updated automatically when `PlatformConfig.cloudflare_api_token` and `PlatformConfig.domain` are set on the source. If either is missing, update the A record manually (for `SERVICE` on a Lite Agent target, point the service subdomain at the target; for `FULL`, point the apex + wildcard at the target).

## Limitations

- **Different Docker base images.** A `SERVICE` transfer preserves the source service's Docker image by reloading the saved `image.tar` on the target. If the target's Docker engine, kernel, or storage driver is incompatible with the source image (e.g. ARM64 → x86_64), the container will fail to start. Use the same architecture on both nodes, or rebuild the image from source after the transfer.
- **Cross-platform Docker socket access.** The `FULL` path runs `install.sh` on the target and assumes a clean host. If the target is already a Grid node, do not use `FULL` to migrate from one primary to another on the same machine — use `SERVICE` for individual workloads.
- **Rollback window is finite.** After 48 hours, `can_rollback` is no longer available even if `status` is `COMPLETED`. Plan any planned rollback inside that window.
- **No concurrent transfers to the same target.** The active-transfer uniqueness check (`(owner, target_ip, transfer_type, [service])`) prevents racing the same data plane. Wait for one transfer to reach a terminal state before queuing the next.
- **Encrypted backups require the key.** The transfer will not proceed if a `BACKUP_ENCRYPTION_KEY` is needed but missing on the source.
- **Addon transfers follow the parent service.** You cannot transfer an addon independently. Moving the parent service moves all addons attached to it.
- **Local-only in IP mode without a domain.** Transfers that rely on DNS cutover (apex + wildcard) require Cloudflare credentials. Without a configured domain, only the source/target workload state is moved — you are responsible for updating DNS.
- **In-memory and ephemeral state is lost.** Containers do not migrate. The transfer starts a fresh container on the target; any in-process state (open WebSocket sessions, in-memory caches not persisted to disk) is reset during the DNS cutover window.
