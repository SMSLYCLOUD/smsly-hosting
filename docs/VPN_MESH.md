# VPN Mesh

WireGuard peer-to-peer networks are established via `MeshNetwork` and `WireGuardPeer`.

## Security
- Configuration strings are base64-encoded to prevent shell injection via the CLI.
- Docker and SSH CLI invocations use strict argument quoting (`shlex`) and `shell=False`.
- The system automatically allocates unused IP subsets and synchronizes keys remotely.

## Mesh DNS (CoreDNS)

Raw mesh IPs (`10.100.0.x`) are stable but unreadable, so the master runs a
CoreDNS container that serves DNS names for the mesh over wg0
(`*.mesh.internal` by default, overridable via `MESH_DNS_DOMAIN`).

Served records (generated from the `MeshNetwork`/`WireGuardPeer` rows):

| Name | Target | Notes |
|---|---|---|
| `master.mesh.internal` | master's mesh IP | conventionally `10.100.0.1` |
| `registry.mesh.internal` | master's mesh IP | registry `:5000` |
| `postgres.mesh.internal` | master's mesh IP | Postgres `:5432` |
| `redis.mesh.internal` | master's mesh IP | Redis `:6379` |
| `rabbitmq.mesh.internal` | master's mesh IP | RabbitMQ `:5672` |
| `grid<N>.mesh.internal` | node N's mesh IP | from `ManagedServer.node_number` |
| `<server-name>.mesh.internal` | node N's mesh IP | sanitized `ManagedServer.name` |

How it fits together:

- **Zone writer** — `apps.deployments.services.mesh_dns.apply_mesh_dns()`
  renders `Corefile` + `mesh.hosts` into the `coredns_config` volume
  (bind-mounted at `/opt/smsly-hosting/coredns-config`, same single-writer
  pattern as the Caddyfile). Idempotent: unchanged content is skipped.
- **Configuration** — the zone name is `PlatformConfig.mesh_dns_domain`
  (default `mesh.internal`), editable in Settings → Platform → Mesh DNS
  (`PATCH /system/config/` → `MESH_DNS_DOMAIN`, validated server-side;
  blank keeps the existing value) or via the `MESH_DNS_DOMAIN` env var,
  which takes precedence. Changing it re-renders the zone on the next sync.
- **Status UI** — Network page → Mesh DNS tab reads
  `GET /mesh/{id}/dns-zone/` (read-only render) and `POST
  /mesh/{id}/sync-dns/` queues a rewrite and returns the fresh zone.
- **Sync triggers** — peer add/remove enqueue `sync_mesh_dns_task`
  (`deploy` queue); a 5-minute beat reconcile (`mesh-dns-sync-every-5min`)
  catches drift; backend startup seeds the zone. The CoreDNS `hosts`
  plugin re-reads the zone on change — no container restart.
- **Reachability** — CoreDNS binds the mesh IP only
  (`COREDNS_MESH_BIND_IP`, parked on `127.0.0.2` when wg0 is absent, same
  guard as the registry mesh bind). UFW allows UDP+TCP 53 on `wg0` only
  (`lib/harden_ufw.sh`) — never world-open.
- **Node resolvers** — nodes configure a per-link wg0 resolver at install
  (`lib/mesh_dns.sh`: `resolvectl dns wg0 <master-ip>` +
  `resolvectl domain wg0 ~<domain>`). The `~` routing-only domain means
  global host DNS is untouched; only `*.mesh.internal` goes over the mesh.

Multi-master prep: failover only needs to repoint these records at the new
master's mesh IP. Nothing existing is rewired — consumers keep working on
raw IPs today and can migrate to names incrementally.
