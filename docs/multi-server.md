# Multi-Server & Remote Deployment

Grid's multi-server / remote deployment feature lets a single platform span many machines: a primary control plane, full-stack follower nodes that run their own services, and lightweight agents that share the master's database. A central dashboard, a WireGuard mesh, and a leader-election protocol hold the cluster together.

Use multi-server when you need to:

- Spread workloads across multiple VPSes to fit capacity, geography, or cost.
- Repatriate a service that lives on a remote node and bring it under your dashboard.
- Mix control-plane (where the database, broker, and Caddy live) with edge nodes that only run containers.
- Keep one source of truth for state while running compute closer to your users.

## Overview

A `ManagedServer` is the unit of fleet membership. Each remote node is registered, has a status (`ONLINE` / `OFFLINE` / `UNKNOWN`), and may optionally run a self-healing orchestrator that diagnoses and recovers from failures automatically. Remote reads (services, deployments, domains) and the inter-node control plane ride on a small but rigorous wire format:

- **Token auth** (`Authorization: Token <smsly_…>`) for normal API calls.
- **HMAC V2 signing** (`X-Gateway-Signature-V2`, `X-Request-Timestamp`) when only the per-node `gateway_secret` is available.
- **A nonce-less but timestamp-bounded envelope** for replay protection: every signed request is rejected if its timestamp drifts more than 300 seconds from the receiver's clock.
- **WireGuard mesh** as the preferred transport for inter-node traffic, with a public-IP fallback for nodes that are not yet on the mesh.

The platform provides two ways to add a node:

- **Connect Existing** — you already have a VPS, you bring the SSH credentials (and optionally an API URL/token and a gateway secret). Use this when you bought a VPS from any provider and you want Grid to manage it.
- **Provision New** — you only have SSH credentials. Grid's `install.sh` runs over SSH, installs Docker, lays down the platform files, exposes an API, and auto-fills `api_url` / `api_token` on the server record.

Either path produces a `ManagedServer` row. From that point on, the server is part of the fleet and can be a target for transfers, deployments, and self-healing.

## Architecture

A Grid fleet is a leader-elected cluster of `ManagedServer` records, all reading from a `MeshNetwork` of `WireGuardPeer` entries. The local "primary" server is the control plane; remote nodes are either full-stack followers or lightweight agents. The cluster has a single source of truth (the master's database) and a small but complete security model: token auth, HMAC signing, command allow-lists, encrypted credentials, and a hash-chained audit trail.

### Roles

| Role | Description |
| --- | --- |
| **Master / Controller** | The platform's primary server. Runs the database (PgCat, PostgreSQL, Redis, RabbitMQ), the Caddy reverse proxy, the frontend build, the management API, and the Celery workers. Receives heartbeats from followers, schedules the leader-election term, and stores every `ManagedServer`, `Service`, `Deployment`, and `AuditLog` row. There is exactly one master per cluster. |
| **Follower (Full-Stack Node)** | A remote `ManagedServer` that runs the entire platform stack locally — its own Traefik, RabbitMQ, and (optionally) PostgreSQL — but no frontend or Caddy. The dashboard proxies reads and writes to it. Transfers to and from a follower move entire service containers. |
| **Lite Agent** | A compute-only worker that does not run a local database. Connects to the master's PostgreSQL, RabbitMQ, and Redis over the WireGuard mesh, executes builds and deploys locally, and reports results back. Ideal for edge or low-resource nodes. |
| **Media Node** | A specialized node for telephony (SMSLY-VOICE) and WebRTC SFU (SMSLY-VIDEO) workloads. Provisioned with LiveKit and media-specific tooling. Uses `node_type='media'` on the `ManagedServer` model. |

### Side-by-side comparison

| Property | Primary (Master) | Follower (Full Node) | Lite Agent |
| --- | --- | --- | --- |
| Runs PostgreSQL | Yes | Yes (own) | No — uses master's via WireGuard |
| Runs RabbitMQ / Redis | Yes | Yes (own) | Local Redis + RabbitMQ; master for shared state |
| Runs Caddy | Yes | No — Traefik on port 80 | No — Traefik on port 80 |
| Runs the frontend | Yes | No | No |
| Accepts user deployments | No (`allow_user_workloads=False`) | Yes | Yes |
| Is a transfer source / target | No (ServerGuard rejects) | Yes | Yes |
| `is_primary` flag | `True` | `False` | `False` |
| `is_lite_agent` flag | `False` | `False` | `True` |
| WireGuard mesh member | Yes (local peer) | Yes | Yes |
| Cluster role | `LEADER` (elected) | `FOLLOWER` (elected) | `FOLLOWER` (elected) |
| Connection strategy | Direct | Token + HMAC V2 fallback | Local-DB reads + mesh-VPN for upstream |

## Node Modes

### Primary (Master)

The master is the source of truth and the orchestrator. It is installed by running `install.sh` with no `--mode` flag, which produces the default platform stack. The installer writes a `NODE_TYPE=master` marker into `.env` and a corresponding `ManagedServer` row with `is_primary=True, allow_user_workloads=False`. The master:

- Hosts the WireGuard `default` mesh as the local peer.
- Issues API tokens and gateway secrets that the dashboard uses to sign requests on behalf of remote nodes.
- Owns the leader-election term, sends heartbeats to followers, and demotes itself on stale-term detection.
- Holds the encryption keys used by all nodes (Fernet-encrypted credentials, `BACKUP_ENCRYPTION_KEY`).

You cannot disable the master. If you want a different machine to be the master, run a `FULL` transfer to relocate the platform database, or use the standard migration procedure.

### Follower (Full-Stack Node)

A follower is a `ManagedServer` with `is_primary=False, is_lite_agent=False, allow_user_workloads=True`. It runs its own Docker Compose stack using `docker-compose.prod.yml` (no frontend, no Caddy) and serves containers via Traefik on port 80. The dashboard proxies reads through to it; writes are emitted to the local Celery worker queue, which talks to the follower over the mesh.

Use followers when:

- The remote VPS has enough resources to run its own database and broker.
- You want each region to be self-contained for performance or data-residency reasons.
- You are running a multi-tenant fleet and want to isolate tenants onto dedicated hosts.

### Lite Agent

A Lite Agent is a `ManagedServer` with `is_lite_agent=True`. It runs `docker-compose.agent-lite.yml`: a subset of the platform that includes the backend, worker, and a local Redis/RabbitMQ, but **not** PostgreSQL. The agent's database connection points at the master over the WireGuard mesh (`MASTER_MESH_IP`), and its reads (services, deployments) hit the shared master database directly rather than through a proxy. Image pulls are routed through the master's insecure registry on `MASTER_MESH_IP:5000`.

The dashboard treats a Lite Agent the same as a follower for transfers, deployments, and self-healing — with one important difference: a Lite Agent's `services_count` is computed locally (the master already has the data) instead of being pulled over the network.

Use Lite Agents when:

- The remote VPS is small (1-2 vCPU, 1-2 GB RAM) and you do not want to run PostgreSQL on it.
- The agent is in a private subnet and can reach the master over WireGuard but not the public internet.
- You want to add a node quickly without provisioning a database.

## Connecting a New Server

There are two flows. They differ only in who runs `install.sh` on the remote host: you (with `Connect Existing`) or Grid (with `Provision New`).

### Connect an existing server (UI)

1. Open **Servers** in the sidebar and click **Connect Existing**.
2. Enter a friendly name, the public IP or domain, and (optionally) the private IP. The private IP is used as the WireGuard endpoint when the operator explicitly opts in via `provider_metadata.mesh_endpoint=private`.
3. Choose an auth strategy:
   - **API + token only** — paste the remote server's `api_url` and `api_token`. SSH is not required.
   - **API + gateway secret (HMAC)** — paste the `api_url` and the per-node `gateway_secret`. The dashboard will sign each request.
   - **SSH only** — leave the API fields blank and provide SSH credentials. The dashboard will derive the API URL by running a small bootstrap on first connect.
4. Set `is_primary=False` (the default) and `allow_user_workloads=True` to make the node a workload target.
5. Submit. A background thread runs `_refresh_managed_server_health`: it probes candidate API URLs (WireGuard mesh first, public IP fallback), detects the platform version, exchanges a token if needed, and updates `status`, `last_health_check`, `services_count`, and the WireGuard mesh membership.

### Connect an existing server (API)

```bash
curl -sS http://localhost:8000/api/v1/servers/ \
  -H "Authorization: Token $SMSLY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Worker EU",
    "host": "203.0.113.10",
    "private_ip": "10.0.5.10",
    "api_url": "http://203.0.113.10",
    "api_token": "smsly_…",
    "ssh_user": "root",
    "ssh_password": "REDACTED",
    "is_primary": false,
    "allow_user_workloads": true
  }'
```

The response is the standard `ManagedServerSerializer` payload. Once the record is saved, the dashboard immediately starts a health-refresh and mesh-membership background job.

### Provision a new server (UI)

1. Open **Servers** and click **Provision New**.
2. Enter name, public IP, SSH port, SSH user, and either a password or a PEM-encoded private key.
3. Optionally toggle `is_lite_agent=True` to install the agent-lite compose profile instead of the full stack.
4. Submit. The serializer validates the SSH key format (`-----BEGIN ... PRIVATE KEY-----` … `-----END ... PRIVATE KEY-----`) and creates the `ManagedServer` with `provision_status=PENDING`.
5. A Celery task (`provision_server.delay`) SSHes into the VPS, uploads `install.sh`, and runs it. Logs stream into `provision_logs` and are viewable from the Servers page.
6. When the installer emits `INSTALLATION SUCCESSFUL!` (or all verification checks pass), the platform auto-fills `api_url` and `api_token` on the server record. `provision_status` becomes `DONE`.

If the install fails, `provision_status` becomes `FAILED` and the full stdout/stderr is preserved in `provision_logs`. Use **Retry Provision** to re-run the installer — the script is idempotent and re-running it is safe.

### Provision a new server (API)

```bash
curl -sS -X POST http://localhost:8000/api/v1/servers/provision/ \
  -H "Authorization: Token $SMSLY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Worker US",
    "host": "198.51.100.20",
    "ssh_port": 22,
    "ssh_user": "root",
    "ssh_auth_method": "key",
    "ssh_key": "-----BEGIN OPENSSH PRIVATE KEY-----\n…\n-----END OPENSSH PRIVATE KEY-----",
    "is_primary": false,
    "allow_user_workloads": true
  }'
```

The response is HTTP 202 (Accepted) with the freshly created server in `PENDING` provisioning state.

## Connecting an Existing Server

This is the typical path for users with a VPS they bought from any cloud provider.

### Prerequisites

- **Root SSH access** to the VPS. The installer needs to install Docker, lay down `/opt/smsly-hosting`, and start containers.
- **TCP/22 reachable** from the master (and from your browser if you are connecting the master to a remote node behind a firewall).
- **A supported Linux distribution.** Ubuntu 20.04 / 22.04 / 24.04 LTS are the only fully-tested images. Other distros may work if Docker is installable, but the installer assumes `apt-get` or `yum` is present.
- **Sufficient resources.** 2 vCPU and 4 GB RAM is the recommended floor for a follower; 1 vCPU and 1 GB RAM is the recommended floor for a Lite Agent.

### What the dashboard does for you

Once the server is saved, the following background work runs without manual intervention:

- **Health refresh.** Candidate API URLs are tried in priority order — WireGuard mesh IP first, public IP next, and a small list of port variants. The first one that returns a non-5xx response is recorded as `api_url` and the server is marked `ONLINE`.
- **Token auto-exchange.** If the server has a `gateway_secret` but no `api_token`, the dashboard POSTs to `/api/v1/auth/node-token-exchange-hmac/` with an HMAC V2 signature and stores the returned `smsly_…` token. If the server has a stored SSH password and `ALLOW_REMOTE_PASSWORD_EXCHANGE=1`, the dashboard will fall back to credential exchange.
- **Service count sync.** For Lite Agents, the dashboard reads the count directly from the shared database. For full followers, it queries the remote API.
- **WireGuard mesh membership.** If the server is not the primary and has SSH credentials, `WireGuardService.ensure_server_in_default_mesh` adds the server (and the local server) to the `default` mesh and queues a mesh deploy.

## Cluster Role & Election

Grid runs a simplified Raft-like protocol for 2-5 server clusters. The state is held in `ClusterState` (one record per mesh) and uses the WireGuard IP as the stable peer identifier.

### Roles

| Role | Meaning |
| --- | --- |
| `LEADER` | Sends heartbeats to every follower on a fixed interval. The only role that may write to leadership-bound state. |
| `FOLLOWER` | Receives heartbeats from the leader. Starts an election if it does not see a heartbeat within `election_timeout_ms` (default 15000ms). |
| `CANDIDATE` | A follower that has incremented its term and is asking peers for votes. Becomes `LEADER` if it wins a majority. |

### Promotion

Promotion happens in `ElectionService.promote_to_leader(cluster)`:

1. Set `cluster.state = "STABLE"` and record the local peer's WireGuard IP as `leader_wg_address`.
2. Set the local server's `role = "LEADER"`.
3. Trigger an immediate heartbeat broadcast to all peers.

A single-node cluster is auto-elected as leader on first boot.

### Election

When a follower detects a timeout (no heartbeat within `election_timeout_ms`):

1. Increment `cluster.term`, set `cluster.state = "ELECTION"`, set local `role = "CANDIDATE"`.
2. Vote for self, then `POST /api/v1/internal/vote/` to every other peer over WireGuard. Each request is HMAC V2 signed with the sender's `gateway_secret`.
3. If a majority of votes is received (or the cluster is 2 nodes and the peer is unreachable, in which case a solo-win heuristic applies), call `promote_to_leader`. Otherwise revert to `FOLLOWER`.

A higher-term heartbeat from any peer demotes the current leader to `FOLLOWER` (split-brain resolution).

### Forced re-election

A superuser can call `POST /api/v1/clusters/{id}/force-election/` to bump the term and let a new election run. This is useful after restoring a node from backup.

## Mesh Networking

The WireGuard mesh is the secure transport for inter-node traffic. Each fleet has a `MeshNetwork` (default name `default`, default subnet `10.100.0.0/24`, default listen port `51820`) and one `WireGuardPeer` per node. Peers are assigned the next free IP in the subnet.

### Setup

When a remote server joins the fleet, `WireGuardService.ensure_server_in_default_mesh` runs:

1. Creates the mesh if it does not exist.
2. Adds the local server (or returns the existing local peer).
3. Adds the remote server and assigns it the next available IP.
4. Persists the `wg_address` on the `ManagedServer` row.
5. Queues a `deploy_mesh_task` to ship updated `wg0.conf` files to all peers over SSH.

The deploy task writes `/etc/wireguard/wg0.conf`, runs `wg-quick up wg0`, and verifies the interface. The local peer writes the config via the Docker socket proxy so the unprivileged API can still apply a privileged `wg-quick` invocation.

### When the mesh is used

The mesh is the **preferred** transport for inter-node API calls. The `api_url` resolution order in `views_servers._candidate_api_urls` is:

1. WireGuard mesh IP (`http://<wg_address>` — Caddy on the remote node binds 80/443 directly; the legacy nginx-bridge URL on the deprecated follower port is no longer used).
2. Public IP / domain (`http://<host>` or `https://<host>` if a domain is present).
3. Loopback shortcut (`http://127.0.0.1:8000`) when the host is `localhost`.

Liveness is confirmed by hitting `/health` or `/health/live` with a 10-second timeout and a non-5xx response.

### Endpoint selection (private vs public)

By default, peers use the public IP as their WireGuard endpoint so that arbitrary VPS fleets can connect. If you operate nodes that share a routable private network (e.g. an AWS VPC with a private subnet), set `provider_metadata.mesh_endpoint=private` (or `wireguard_endpoint=private`) on the server record. The mesh service will use `server.private_ip` as the endpoint and log a `MESH_ENDPOINT_SELECTION` event to the audit trail.

### Fallback to public IPs

If the WireGuard mesh is down (peer not in mesh, mesh deploy failed, host kernel missing the `wireguard` module), the candidate-URL resolver falls through to the public IP. Health, proxy, and token-exchange calls all behave the same way: try the mesh first, then the public IP. The audit trail records which base URL was actually used.

### Failure mode: kernel module missing

On hosts where the WireGuard kernel module is not loaded, the local deploy raises `WireGuard kernel module is not loaded on the host VPS. Run 'sudo modprobe wireguard' on the host.` The mesh deploy task leaves `mesh_status=FAILED` with the redacted error in `mesh_last_error`. SSH into the host and `sudo modprobe wireguard` (or install `wireguard-dkms` and reboot) before retrying.

## API Reference

All endpoints are mounted under `/api/v1/servers/`. Authentication is session- or token-based for user endpoints, and HMAC V2-signed for the internal node-to-node sync endpoints. Filter by `?status=ONLINE|OFFLINE|UNKNOWN` on the list endpoint.

### `GET /api/v1/servers/`

List every `ManagedServer` owned by the authenticated user, ordered with the primary first and then by name.

**Query parameters:**

| Parameter | Type | Notes |
| --- | --- | --- |
| `status` | `ONLINE` \| `OFFLINE` \| `UNKNOWN` | Optional. Filter by health status. |

**Example request:**

```bash
curl -sS http://localhost:8000/api/v1/servers/ \
  -H "Authorization: Token $SMSLY_TOKEN"
```

**Example response (abridged):**

```json
[
  {
    "id": "7d3b1a8e-2c5f-4a6d-8e9b-0c1a2b3c4d5e",
    "name": "Primary",
    "host": "198.51.100.5",
    "private_ip": null,
    "api_url": "http://198.51.100.5:8000",
    "ssh_port": 22,
    "ssh_user": "root",
    "is_primary": true,
    "allow_user_workloads": false,
    "status": "ONLINE",
    "last_health_check": "2026-06-12T11:42:18Z",
    "server_version": "2026.06.0",
    "services_count": 12,
    "created_at": "2026-04-01T09:00:00Z",
    "provision_status": "DONE",
    "role": "LEADER",
    "wg_address": "10.100.0.1",
    "has_ssh_credentials": true,
    "is_lite_agent": false
  }
]
```

### `POST /api/v1/servers/`

Create a `ManagedServer` record. The serializer accepts both the **Connect Existing** shape (full API + token + optional SSH) and a partial shape (SSH only) — `api_url`, `api_token`, `gateway_secret`, and the SSH fields are all optional. A background health-refresh is started immediately on save.

**Required fields:**

| Field | Type | Notes |
| --- | --- | --- |
| `name` | string (≤100) | Human-readable label. |
| `host` | string (≤255) | Public IP or domain. Protocol and trailing slashes are stripped. |

**Optional fields:**

| Field | Type | Notes |
| --- | --- | --- |
| `private_ip` | IPv4 | Used as the WireGuard endpoint when the operator opts in to private mesh. |
| `api_url` | URL | Full API URL. Auto-prefixed with `http://` for bare IPs and `https://` for hostnames. |
| `api_token` | string (write-only) | API token for token auth. Stored encrypted. |
| `gateway_secret` | string (write-only) | HMAC V2 secret. Stored encrypted. |
| `ssh_user` | string | Default `root`. |
| `ssh_password` | string (write-only) | Stored encrypted. |
| `ssh_key` | PEM (write-only) | Validated as `-----BEGIN ... PRIVATE KEY-----` / `-----END ... PRIVATE KEY-----`. |
| `ssh_port` | int | Default `22`. |
| `is_primary` | bool | Default `false`. Setting `true` automatically forces `allow_user_workloads=False` after save. |
| `allow_user_workloads` | bool | Default `true`. |
| `is_lite_agent` | bool | Default `false`. |
| `provider_metadata` | JSON | Free-form metadata (e.g. `{ "mesh_endpoint": "private" }`). |

**Example request:**

```bash
curl -sS http://localhost:8000/api/v1/servers/ \
  -H "Authorization: Token $SMSLY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Worker EU",
    "host": "203.0.113.10",
    "private_ip": "10.0.5.10",
    "api_url": "http://203.0.113.10",
    "api_token": "smsly_a3b8c1d2e3f4…",
    "ssh_user": "root",
    "ssh_password": "REDACTED",
    "is_primary": false,
    "allow_user_workloads": true
  }'
```

**Error responses:**

| Status | Cause |
| --- | --- |
| 400 | Invalid SSH key format, malformed `host`, invalid `api_url`, or invalid JSON. |
| 401 | No session or token. |

### `GET /api/v1/servers/{id}/`

Retrieve a single server. The `has_ssh_credentials` flag is derived and never exposes the credentials themselves.

**Example request:**

```bash
curl -sS http://localhost:8000/api/v1/servers/7d3b1a8e-2c5f-4a6d-8e9b-0c1a2b3c4d5e/ \
  -H "Authorization: Token $SMSLY_TOKEN"
```

**Error responses:**

| Status | Cause |
| --- | --- |
| 404 | Server not found or not owned by the caller. |

### `PATCH /api/v1/servers/{id}/`

Partial update of a server. Useful for editing the SSH credentials, changing `allow_user_workloads`, attaching to a project, or rotating `api_token` / `gateway_secret`. Saving also kicks off a fresh health-refresh, so updating the API URL or token will pick up the new credentials on the next probe.

**Example request:**

```bash
curl -sS -X PATCH http://localhost:8000/api/v1/servers/7d3b1a8e-2c5f-4a6d-8e9b-0c1a2b3c4d5e/ \
  -H "Authorization: Token $SMSLY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "allow_user_workloads": true,
    "api_token": "smsly_NEW…"
  }'
```

### `DELETE /api/v1/servers/{id}/`

Remove a server from the fleet. The record is deleted; the underlying VPS is **not** powered down and its WireGuard peer is **not** removed automatically. Use `POST /api/v1/mesh/{id}/remove-peer/` to tear down the mesh entry first if needed.

**Example request:**

```bash
curl -sS -X DELETE http://localhost:8000/api/v1/servers/7d3b1a8e-2c5f-4a6d-8e9b-0c1a2b3c4d5e/ \
  -H "Authorization: Token $SMSLY_TOKEN"
```

**Error responses:**

| Status | Cause |
| --- | --- |
| 404 | Server not found or not owned by the caller. |

### `POST /api/v1/servers/provision/`

Provision a brand-new node. The endpoint validates the SSH credentials, creates the `ManagedServer` in `provision_status=PENDING`, and dispatches the `provision_server` Celery task. The response is HTTP 202 with the new server record.

**Body fields:** identical to `POST /api/v1/servers/` plus the non-model `ssh_auth_method` discriminator (`"password"` or `"key"`).

**Example request:**

```bash
curl -sS -X POST http://localhost:8000/api/v1/servers/provision/ \
  -H "Authorization: Token $SMSLY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Worker US",
    "host": "198.51.100.20",
    "ssh_port": 22,
    "ssh_user": "root",
    "ssh_auth_method": "key",
    "ssh_key": "-----BEGIN OPENSSH PRIVATE KEY-----\n…\n-----END OPENSSH PRIVATE KEY-----",
    "is_primary": false,
    "allow_user_workloads": true,
    "is_lite_agent": true
  }'
```

**Error responses:**

| Status | Cause |
| --- | --- |
| 400 | Invalid SSH key, missing password for password auth, or missing key for key auth. |

### `GET /api/v1/servers/{id}/provision-logs/`

Stream the live provisioning logs. Useful for the "Provisioning…" spinner in the UI.

**Example request:**

```bash
curl -sS http://localhost:8000/api/v1/servers/7d3b1a8e-2c5f-4a6d-8e9b-0c1a2b3c4d5e/provision-logs/ \
  -H "Authorization: Token $SMSLY_TOKEN"
```

**Example response:**

```json
{
  "provision_status": "PROVISIONING",
  "provision_logs": "🚀 Starting Grid provisioning...\n📡 Connecting to root@198.51.100.20:22\n…"
}
```

### `POST /api/v1/servers/{id}/retry-provision/`

Reset the provision state to `PENDING` and re-dispatch the `provision_server` task. The previous logs are replaced with a header that records the user and timestamp. The installer is idempotent: existing containers and `.env` values are preserved.

**Example request:**

```bash
curl -sS -X POST http://localhost:8000/api/v1/servers/7d3b1a8e-2c5f-4a6d-8e9b-0c1a2b3c4d5e/retry-provision/ \
  -H "Authorization: Token $SMSLY_TOKEN"
```

### `POST /api/v1/servers/{id}/update-server/`

Run the same idempotent `install.sh` flow that is used for fresh installs, with `skip_reboot=True` so the target does not reboot during the update. The endpoint clears any stalled `PROVISIONING` / `PENDING` / `UPDATING` state before re-dispatching the task.

**Example request:**

```bash
curl -sS -X POST http://localhost:8000/api/v1/servers/7d3b1a8e-2c5f-4a6d-8e9b-0c1a2b3c4d5e/update-server/ \
  -H "Authorization: Token $SMSLY_TOKEN"
```

**Error responses:**

| Status | Cause |
| --- | --- |
| 400 | The server has no SSH credentials configured. |

### `POST /api/v1/servers/{id}/health_check/`

Probe a single server's API, refresh its `status`, `last_health_check`, `server_version`, `services_count`, and (if needed) `api_url`. The probe is synchronous on the caller side; the actual work is fast (a single `GET /health` round-trip per candidate URL).

**Example request:**

```bash
curl -sS -X POST http://localhost:8000/api/v1/servers/7d3b1a8e-2c5f-4a6d-8e9b-0c1a2b3c4d5e/health_check/ \
  -H "Authorization: Token $SMSLY_TOKEN"
```

**Example response:** the standard `ManagedServerSerializer` payload with refreshed `status` and `last_health_check`.

### `POST /api/v1/servers/check_all/`

Run the health probe against every server owned by the caller. Useful for the "Refresh All" button on the Servers page.

**Example request:**

```bash
curl -sS -X POST http://localhost:8000/api/v1/servers/check_all/ \
  -H "Authorization: Token $SMSLY_TOKEN"
```

**Example response:**

```json
{
  "servers": [ { "id": "…", "status": "ONLINE", "…" } ]
}
```

### `POST /api/v1/servers/{id}/proxy/`

Forward a request to a remote server through the dashboard. The endpoint is the same shape as a generic proxy: the caller specifies a `method`, a `path`, and an optional `body`, and the dashboard adds the appropriate token or HMAC V2 headers before dispatching.

**Body fields:**

| Field | Type | Notes |
| --- | --- | --- |
| `method` | `GET` \| `POST` \| `PUT` \| `PATCH` \| `DELETE` | Defaults to `GET`. Other methods are rejected. |
| `path` | string | Must start with `/api/`. The endpoint normalizes the path and rejects any `..` segment. |
| `body` | object \| null | JSON-serializable. Encoded with `sort_keys=True` for the HMAC envelope. |

**Lite-agent fast path:** for `GET /api/v1/services`, `GET /api/v1/deployments`, and `GET /api/v1/services/{id}/`, the proxy serves the response directly from the shared database when the target is a Lite Agent. No HTTP call to the remote is made.

**Example request:**

```bash
curl -sS -X POST http://localhost:8000/api/v1/servers/7d3b1a8e-2c5f-4a6d-8e9b-0c1a2b3c4d5e/proxy/ \
  -H "Authorization: Token $SMSLY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "method": "GET",
    "path": "/api/v1/services/",
    "body": null
  }'
```

**Example response:**

```json
{
  "status_code": 200,
  "data": { "results": [ … ], "count": 7 }
}
```

**Error responses:**

| Status | Cause |
| --- | --- |
| 400 | Disallowed method, `..` in path, path not under `/api/`, or non-HTTP scheme on the stored `api_url`. |
| 502 | Remote server unreachable. The response carries `remote_unreachable=true` and the upstream error. |

### `GET /api/v1/servers/{id}/services/`

List the services running on a managed server. For Lite Agents, the response is built from the shared database. For full followers, the response is the remote `/api/v1/services/` page, fetched with token or HMAC V2 fallback.

**Example request:**

```bash
curl -sS http://localhost:8000/api/v1/servers/7d3b1a8e-2c5f-4a6d-8e9b-0c1a2b3c4d5e/services/ \
  -H "Authorization: Token $SMSLY_TOKEN"
```

**Example response:**

```json
{
  "results": [ { "id": "…", "name": "web-api", "…" } ],
  "count": 7
}
```

When the remote is unreachable the response degrades to `{ "remote_unreachable": true, "results": [], "count": 0, "error": "..." }` so the dashboard can render a "temporarily unavailable" badge without breaking the page.

### `GET /api/v1/servers/{id}/deployments/`

List the most recent 50 deployments on a managed server. Same Lite-Agent / follower routing as `/services/`.

**Example request:**

```bash
curl -sS http://localhost:8000/api/v1/servers/7d3b1a8e-2c5f-4a6d-8e9b-0c1a2b3c4d5e/deployments/ \
  -H "Authorization: Token $SMSLY_TOKEN"
```

### `GET /api/v1/servers/{id}/domains/`

Aggregate every custom domain across every service on a managed server. For full followers, the endpoint paginates through `/api/v1/services/` (up to 50 pages) and collects `custom_domains` plus `domain_verified` and `verification_token` from each service. For Lite Agents, the aggregation runs in-database.

**Example request:**

```bash
curl -sS http://localhost:8000/api/v1/servers/7d3b1a8e-2c5f-4a6d-8e9b-0c1a2b3c4d5e/domains/ \
  -H "Authorization: Token $SMSLY_TOKEN"
```

**Example response:**

```json
{
  "domains": [
    {
      "domain": "app.example.com",
      "service_id": "9c8b4b1a-7d1c-4a2b-9a55-2e8c3d4f9b21",
      "service_name": "web-api",
      "public_domain": "web-api.localhost.grid.host",
      "verified": true,
      "verification_token": "smsly-verify-…"
    }
  ],
  "count": 1
}
```

### `POST /api/v1/servers/{id}/heal/`

Trigger self-healing on a remote server. Requires SSH credentials to be stored on the server record.

**Body fields:**

| Field | Type | Notes |
| --- | --- | --- |
| `action` | `restart_container` \| `restart_stack` \| `restart_docker_daemon` \| `diagnose` \| `full` | Defaults to `full`. |
| `deployment_id` | UUID | Optional. When present, the action is scoped to a single deployment. |

**Example request — full node heal:**

```bash
curl -sS -X POST http://localhost:8000/api/v1/servers/7d3b1a8e-2c5f-4a6d-8e9b-0c1a2b3c4d5e/heal/ \
  -H "Authorization: Token $SMSLY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{ "action": "full" }'
```

**Example request — diagnose only:**

```bash
curl -sS -X POST http://localhost:8000/api/v1/servers/7d3b1a8e-2c5f-4a6d-8e9b-0c1a2b3c4d5e/heal/ \
  -H "Authorization: Token $SMSLY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{ "action": "diagnose" }'
```

**Example request — heal a specific deployment:**

```bash
curl -sS -X POST http://localhost:8000/api/v1/servers/7d3b1a8e-2c5f-4a6d-8e9b-0c1a2b3c4d5e/heal/ \
  -H "Authorization: Token $SMSLY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "action": "restart_container",
    "deployment_id": "1f4a2c63-9b6e-4f01-b6a5-7c5d0a44a1a9"
  }'
```

**Error responses:**

| Status | Cause |
| --- | --- |
| 400 | Unknown action or no SSH credentials stored. |
| 404 | `deployment_id` not found. |

### `GET /api/v1/servers/{id}/diagnostics/`

Read-only alias for `POST /heal/` with `action=diagnose`. Returns Docker status, disk / memory usage, network reachability, failure classification, and any exited containers — without performing recovery.

**Example request:**

```bash
curl -sS http://localhost:8000/api/v1/servers/7d3b1a8e-2c5f-4a6d-8e9b-0c1a2b3c4d5e/diagnostics/ \
  -H "Authorization: Token $SMSLY_TOKEN"
```

**Example response:**

```json
{
  "server": { "id": "…", "name": "Worker US", "host": "198.51.100.20" },
  "docker_running": true,
  "disk_usage_pct": 38.2,
  "memory_usage_pct": 61.5,
  "network_reachable": true,
  "failure_type": "container_crashed",
  "container_state": "exited",
  "error_details": "",
  "suggested_actions": ["restart_container"],
  "exited_containers": "web-api: Exited (1) 5 minutes ago"
}
```

### `POST /api/v1/servers/{id}/run_command/`

Run a single diagnostic or recovery command on the remote node over SSH. The endpoint enforces a strict allow-list — only safe, read-only or recovery-oriented commands are accepted. See the **Run-command allow-list** section below for the exact prefixes.

**Body fields:**

| Field | Type | Notes |
| --- | --- | --- |
| `command` | string | Must start with one of the allowed prefixes. |

**Example request:**

```bash
curl -sS -X POST http://localhost:8000/api/v1/servers/7d3b1a8e-2c5f-4a6d-8e9b-0c1a2b3c4d5e/run_command/ \
  -H "Authorization: Token $SMSLY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{ "command": "docker ps -a" }'
```

**Example response:**

```json
{
  "command": "docker ps -a",
  "exit_code": 0,
  "stdout": "CONTAINER ID   IMAGE     COMMAND   CREATED   STATUS   PORTS   NAMES\n…",
  "stderr": ""
}
```

`stdout` is truncated to 10 000 characters and `stderr` to 5 000. Use the diagnostics endpoint for the structured view.

## Self-Healing Actions

The self-healing orchestrator classifies failures into `FailureType` enums and chooses a `RecoveryAction`. When the user requests a node-level heal, the action is mapped to the orchestrator's recovery surface.

### Action mapping

| User-facing action | Internal `RecoveryAction` | What it does |
| --- | --- | --- |
| `restart_container` | `RESTART_CONTAINER` | `docker restart <container>` and re-checks `docker inspect` after a 20s wait. |
| `restart_stack` | `RESTART_STACK` | `docker compose up -d` (or `docker-compose up -d` on older installs) in `/opt/smsly-hosting`. |
| `restart_docker_daemon` | `RESTART_DOCKER_DAEMON` | `systemctl restart docker`, then `docker info` to confirm. |
| `diagnose` | — | Runs `run_full_diagnostics()` only. No recovery. |
| `full` | `RESTART_STACK` (orchestrator runs the full suggested-action chain) | Equivalent to `restart_stack` for node-level heals. For deployment-level heals, the orchestrator walks the `suggested_actions` list (e.g. `RESTART_CONTAINER` → `RESTART_STACK` → `RESTART_DOCKER_DAEMON`) and escalates to AI after `MAX_HEAL_ATTEMPTS=5`. |

### Failure classification

| Failure type | How it's detected | First suggested action |
| --- | --- | --- |
| `DOCKER_DAEMON_DOWN` | `docker info` returns non-zero | `RESTART_DOCKER_DAEMON` → `RESTART_STACK` |
| `DISK_FULL` | `df -h /` reports >90% | `PRUNE_IMAGES` → `PRUNE_VOLUMES` |
| `OUT_OF_MEMORY` | `free` reports >90%, or `oom` / `killed` / `signal 9` in container logs | `INCREASE_RESOURCES` |
| `CONTAINER_CRASHED` | `docker inspect` shows `exited` / `dead` | `RESTART_CONTAINER` |
| `CONTAINER_RESTARTING` | `docker inspect` shows `restarting` | `RESTART_CONTAINER` |
| `PORT_CONFLICT` | `bind: address already in use` in logs | (escalate to AI) |
| `IMAGE_PULL_FAILED` | `pull access denied` / `manifest unknown` in logs | (escalate to AI) |
| `CONFIG_ERROR` | `permission denied` / `eacces` / container missing | `REBUILD_CONTAINER` |
| `NETWORK_UNREACHABLE` | `ping 8.8.8.8` fails or logs contain `network unreachable` | `FIX_NETWORK` |
| `DEPLOYMENT_TIMEOUT` | Heal exhausts all `suggested_actions` | `ESCALATE_TO_AI` |
| `UNKNOWN` | Default | `RESTART_CONTAINER` |

### Cooldowns and rate-limits

- `HEAL_COOLDOWN_SECONDS=120` — the orchestrator refuses to start a new heal for the same scope within two minutes of a successful one.
- `MAX_HEAL_ATTEMPTS=5` — after five attempts, the orchestrator returns `ESCALATE_TO_AI` and the platform intelligence layer takes over.

## Run-Command Allow-List

The `POST /api/v1/servers/{id}/run_command/` endpoint enforces a strict prefix allow-list. The intent is to give the operator enough surface to debug a node (`docker ps`, `docker logs`, `df`, `free`) without exposing the SSH channel to arbitrary commands.

### Allowed prefixes

| Prefix | Use case |
| --- | --- |
| `docker ` | Any `docker` subcommand (`ps`, `logs`, `inspect`, `stats`, `images`, `network ls`, …). |
| `cd /opt/smsly-hosting && docker ` | Same as above, but pinned to the platform install directory. Use this for `compose` commands. |
| `df ` | Disk-usage reports. |
| `free ` | Memory reports. |
| `ping ` | ICMP reachability tests. |
| `systemctl status docker` | Docker daemon health check. |
| `cat /opt/smsly-hosting/.env \| grep -v SECRET \| grep -v PASSWORD \| grep -v KEY` | Render the local `.env` with secrets redacted. |

### Blocked

Anything not matching the above prefixes returns HTTP 403 with `Command not allowed. Only Docker, diagnostic, and safe recovery commands are permitted.` Shell metacharacters and command chaining are not interpreted differently — the check is purely a string-prefix match — so be aware that `docker <anything>` is accepted, including destructive verbs like `docker rm -f` or `docker system prune`. The endpoint is intended for operators, not for untrusted automation.

## Security

The inter-node surface is the most security-sensitive part of the platform. The implementation is hardened at five layers.

### HMAC V2 signing with timestamp-bounded replay protection

Every node-to-node call carries three headers:

- `X-SMSLY-Remote-Sync: 1` — declares the request as a node-to-node sync.
- `X-Request-Timestamp` — UNIX seconds. The receiver rejects any request whose timestamp drifts more than **300 seconds** from its own clock.
- `X-Gateway-Signature-V2` — HMAC-SHA256 over `METHOD|path|ts|sha256(body)` using either the per-node `gateway_secret` or, as a last-resort fallback, the platform-wide `GATEWAY_SECRET` / `SECRET_KEY`.

Signature comparison uses constant-time `hmac.compare_digest`. There are no nonces — the 5-minute window plus the constant-time compare are the replay defenses.

The same scheme is reused for the election protocol (`X-Election-Signature`), where the receiver looks up the sender's `gateway_secret` by `wg_address` against the local `WireGuardPeer` records.

### Token auth

For nodes where an API token has already been exchanged, the dashboard uses `Authorization: Token <smsly_…>` instead. The token is opaque to the receiver — it is matched against the SHA-256 hash stored on the `APIToken` row. Tokens are revocable from the dashboard.

### TLS enforcement flags

`ssl_verifier` checks each remote's certificate before any cross-node call. Lite Agents are exempted for the `/health` liveness probe (so the orchestrator can recover them when they are partially up); all other calls are expected to use TLS when a domain is present and to fall back to the WireGuard mesh IP (which is never public) when TLS is not available.

### Command allow-list

See the **Run-command allow-list** section above. Only a small, fixed set of prefixes is accepted by `POST /run_command/`. Anything else returns 403.

### Encrypted credential storage

`api_token`, `gateway_secret`, `ssh_password`, and `ssh_key` are all stored in `EncryptedCharField` / `EncryptedTextField` (Fernet) on the `ManagedServer` model. They are never returned by the API. The `has_ssh_credentials` boolean is the only credential-derived field in the public serializer.

### Audit trail

Every meaningful state change is recorded through `log_event(...)` with a stable action code and a metadata payload. Examples:

- `MESH_ENDPOINT_SELECTION` — recorded when a peer chooses a private or public endpoint.
- `MESH_DEPLOY_SUCCESS` / `MESH_DEPLOY_FAILED` — per-peer mesh deploy outcomes.
- `MESH_PEER_UNREACHABLE` — when a peer's last handshake times out.

The `AuditLog` table is hash-chained and protected by `BEFORE UPDATE OR DELETE` triggers (see migration `0070_auditlog_database_triggers`) so audit records cannot be silently tampered with.

## Troubleshooting

### "Server 'X' is currently OFFLINE. Transfers are only allowed to ONLINE nodes."

The connected server is registered but the health probe has not received a non-5xx response recently. Open the Servers page, run `POST /api/v1/servers/{id}/health_check/`, and watch for which candidate URL succeeds. The most common causes are: a wrong public IP, a firewall blocking port 8090, or the WireGuard mesh not yet converged.

### "No SSH credentials available for target server"

The `ManagedServer` has neither `ssh_key` nor `ssh_password` set. Open the server in the dashboard and re-save the SSH key or password — the `PATCH /api/v1/servers/{id}/` endpoint accepts write-only fields. Token-only auth does not enable SSH-dependent operations (transfers, mesh deploys, healing).

### Health check reports HTTP 200 but the server still shows OFFLINE

The probe rejects 5xx as "reachable but unhealthy." Check whether `/health` on the remote is actually returning 200; some lite-agent configs expose `/health/live` only. Both paths are tried in order.

### "Mesh deployment failed: WireGuard kernel module is not loaded on the host VPS."

The remote kernel does not have the `wireguard` module. SSH into the host, run `sudo modprobe wireguard`, and re-queue the mesh deploy. On hosts without DKMS, the module is provided by the kernel itself on most Ubuntu LTS images; on custom kernels, install `wireguard-dkms` and reboot.

### Token auto-exchange fails with 401 / 403

The remote rejected the bootstrap. Verify that `gateway_secret` on the source matches the `GATEWAY_SECRET` on the target. If the remote uses credential exchange, ensure `ALLOW_REMOTE_PASSWORD_EXCHANGE=1` on the **target** (the one issuing the token) and that the SSH password is the admin password.

### "Provisioning FAILED — INSTALLATION FAILED"

The remote installer exited non-zero. Open `provision_logs` for the full stdout. The most common causes are: an unsupported Linux distribution, no Docker installable, no `apt-get` or `yum` present, or insufficient RAM. Re-run with `retry-provision` after fixing the underlying issue — the script is idempotent.

### "Conflict: another provisioning task is already running for this host"

A second `provision` was attempted against the same `host` while the first was still in `PROVISIONING`. The second call is recorded as `FAILED` to prevent two `install.sh` runs from racing on the same VPS. Wait for the first run to finish (or `FAILED`), then retry.

### Self-heal never converges

`MAX_HEAL_ATTEMPTS=5` triggers after the fifth attempt. When that happens, the orchestrator returns `next_action=ESCALATE_TO_AI`. If the platform intelligence is configured, the AI Senate analyzes the diagnostic context and proposes commands. If the AI path is not configured, the heal log is the only artifact — open it from the heal endpoint and address the root cause manually.

### A remote node is "ONLINE" but the proxy returns `remote_unreachable`

The health probe found a working base URL, but the proxy candidate-URL rotation tried a different URL and the remote is no longer answering. The proxy falls through the candidate list with multiple auth modes (token, then HMAC, then none) and surfaces `remote_unreachable=true` with the upstream error and status. This is usually transient — re-run the call.

### Domain aggregation truncates at 50 pages

The full-follower implementation paginates through `/api/v1/services/` with a hard cap of 50 pages. A node with more than 50 pages of services (≥500 services at the default page size) will not have all of its domains listed. Use the per-service `/api/v1/servers/{id}/services/` endpoint for exhaustive listings, or the master DB directly for Lite Agents.

### Audit log shows "MESH_PEER_UNREACHABLE" repeatedly

The mesh is up (the peer was added) but `wg show` is not seeing handshakes. Check `wg_address` reachability from the local peer. Common causes: the peer's `endpoint` points at a port that is firewalled, or the kernel module is not loaded on the peer (see the mesh failure case above).

## Limitations

- **No hard cap on cluster size.** There is no `MAX_NODES` / `MAX_LITE_AGENTS` constant in the codebase. Practical limits come from the database connection budget, the WireGuard subnet size (default `/24` = 253 usable IPs), and the operator's tolerance for `wg show` latency at scale. The election protocol is targeted at 2-5 server clusters; larger fleets may see longer election rounds.
- **Lite Agent restrictions.** Lite Agents cannot serve a Transfer `FULL` (the platform-wide database restore is not safe to run on a node that shares the master's DB). The `SERVICE` transfer path is fully supported. The mesh is mandatory for Lite Agents — there is no public-IP-only fallback for database, RabbitMQ, or registry connections because those ports are firewalled on the master.
- **Single-master constraint.** There is exactly one primary per cluster. Failover is a transfer (`FULL` type) followed by DNS cutover, not a hot standby. If the master goes down, the dashboard at `https://<master-domain>` becomes unreachable; the followers continue running services that are already deployed.
- **Same-architecture transfers.** A `SERVICE` transfer preserves the source service's Docker image. ARM64 → x86_64 (or vice-versa) does not work — use the same architecture on both nodes, or rebuild the image after the transfer.
- **2-node election heuristic.** A 2-node cluster that loses one node triggers a "solo win" heuristic so the surviving node can become leader. This is logged as a `WARNING`. In a healthy 2-node cluster, the normal majority vote still applies.
- **No concurrent updates on the same host.** A second `provision` or `update-server` against a `host` whose previous run is still `PROVISIONING` is marked `FAILED` rather than queued. The dashboard surfaces this as "another task is already running for this host."
- **In-process audit chain.** The audit-log hash chain is enforced at the database layer (immutability triggers). It is **not** replicated to a separate witness, so a host-level root compromise could still truncate the chain. Pair Grid with a separate log-aggregator (e.g. Loki, ELK) for defense in depth.
- **Mesh is IPv4-only.** The default subnet is `10.100.0.0/24`; the models use `GenericIPAddressField(protocol="IPv4")`. IPv6 WireGuard is not supported.
- **Concurrent node-membership migrations are not atomic.** Adding a peer queues a Celery mesh deploy, but the deploy runs separately from the `ManagedServer` save. If the mesh deploy fails, the peer record exists but the WireGuard config is stale — re-run `POST /api/v1/mesh/{id}/deploy/` to retry.
- **Encrypted credentials require Fernet rotation discipline.** `BACKUP_ENCRYPTION_KEY` and the column-level Fernet key must be rotated together. If they diverge, the API surfaces `InvalidToken` exceptions on read — schedule rotation outside of business hours.
