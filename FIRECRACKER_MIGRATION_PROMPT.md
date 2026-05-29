# Firecracker MicroVM Migration — Prompt for Google Jules

## Context

SMSLY is an enterprise PaaS platform that currently uses Docker as its sole container runtime across all operations — building, deploying, orchestrating, networking, monitoring, and backing up customer workloads. The platform consists of:

- **Master node**: Full stack with Caddy (SSL edge), Traefik (internal routing), PostgreSQL, Redis, RabbitMQ, Prometheus/Grafana/Loki, build pipelines, API backend, Celery workers
- **Agent-lite nodes**: Lightweight nodes running Traefik + backend + celery-worker, connecting to master's database over WireGuard VPN mesh
- **WireGuard full-mesh VPN**: `10.100.0.x/24` — every node peers every other node

The goal is to migrate **customer workloads** (user-deployed services, addons, functions) from Docker containers to Firecracker microVMs for stronger multi-tenant isolation, while optionally keeping core platform services (backend, databases, caches) in Docker to minimize blast radius.

---

## Architecture Overview (Current Docker-Based)

### Adapter Layer
- **`backend/apps/cloud/adapters/local.py`** — `LocalAdapter` class (1195 lines): Central orchestrator. Every container lifecycle operation flows through here: `deploy_container()` creates/runs containers with labels, volumes, networks, health checks, resource limits, blue-green promotion. `_deploy_docker_function()` deploys serverless functions as containers.
- **`backend/apps/cloud/docker_client.py`** — Factory returning `docker.DockerClient` with configurable timeouts, respecting `DOCKER_HOST` env var.

### Build Pipeline
- **`backend/apps/deployments/services/pipeline.py`** — `PipelineManager` (2238 lines): Orchestrates Clone → AI Analysis → Env Injection → Build → Push. Build strategies: Dockerfile (`docker buildx build`), Nixpacks (`nixpacks build` subprocess), Docker Compose (`docker compose build`).
- **`backend/apps/cloud/services/builder.py`** — `NixpacksBuilder`: Runs `nixpacks build`, pushes via `client.images.push()` to internal registry.
- **`backend/services/builders.py`** — `BuildManager`: Legacy build path with `docker build` + BuildKit cache management.
- **Registry**: `registry:2` container with htpasswd auth + TLS, port 5000. Docker-mirror at port 5001 as Docker Hub pull-through cache.

### Networking
- **Caddy** (`backend/services/caddy_manager.py`, 1133 lines): SSL termination edge proxy. Generates Caddyfile from PlatformConfig + service domains. On-Demand TLS, wildcard DNS-01 via Cloudflare. Routes to Traefik via `traefik:80`.
- **Traefik** (docker-compose.prod.yml lines 384-412): Internal router using `--providers.docker=true` with `--providers.docker.network=smsly-net`. Discovers services via Docker container labels.
- **Traefik labels** (`backend/services/traefik_labels.py`): Generated as Docker container labels — router rules, load balancer config, health checks, rate limiting, TLS.
- **Service DNS**: Container names resolve as DNS within `smsly-net` overlay network. Network aliases set at container creation.
- **WireGuard** (`backend/apps/deployments/services/wireguard_service.py`): Full mesh VPN, Python keygen, deployed via SSH or `docker run --privileged`.
- **Tunnels** (`backend/services/tunnels/`): WebSocket-based tunnel service (ngrok-like) — mostly Docker-independent.

### Service Lifecycle
- **Deployment tasks** (`backend/apps/deployments/tasks.py`): `smart_deploy_task` orchestrates full flow. `_deploy_container` pulls image, builds runtime env, calls adapter, waits for healthy, verifies route, runs post-deploy hooks.
- **Health checks**: Docker-native HEALTHCHECK with `docker.types.Healthcheck`, plus HTTP probes through Caddy→Traefik→app chain.
- **Post-deploy monitor**: 30-second crash detection loop with AI escalation.
- **Addon provisioning** (`backend/services/addon_provisioner.py`): 40+ addon types (Postgres, Redis, MongoDB, Kafka, MinIO, Qdrant, etc.) created as Docker containers via `docker run -d`.
- **Backup/restore** (`backend/apps/deployments/services/backup_service.py`): Uses `container.commit()`, `image.save()`, `images.load()`, `put_archive()`, `exec_run()`.
- **SafeDeploy**: Canary deployments with preview environments.

### Multi-Node
- **Remote orchestrator** (`backend/apps/deployments/services/remote_orchestrator.py`): Syncs services/env vars to nodes via HTTP API, triggers redeploys, SSH auto-healing.
- **Fleet orchestrator** (`backend/services/fleet_orchestrator.py`): Federated updates with canary.
- **Autoscaler** (`backend/apps/autoscaler/views.py`): Uses `docker stats --no-stream` for metrics.

### Monitoring
- **cAdvisor**: Mounts `/var/lib/docker/` — deeply Docker-dependent container metrics.
- **Promtail**: Uses `docker_sd_configs` to discover container logs.
- **Post-deploy log access**: `container.logs(tail=200)` in tasks.py.

---

## Migration Plan — 4 Phases

### Phase 1: Adapter Abstraction + FVM Runtime

**Goal**: Introduce a Firecracker runtime adapter alongside the existing Docker adapter, with a shared base interface. No existing functionality changes.

**Tasks**:

1. **Extend `BaseCloudAdapter`** (`backend/apps/cloud/adapters/base.py`) to include all methods needed by both Docker and Firecracker:
   - `create_instance(name, image, env, resources, volumes, network, labels, healthcheck) -> instance_id`
   - `start_instance(instance_id)`
   - `stop_instance(instance_id, timeout)`
   - `remove_instance(instance_id, force)`
   - `get_instance(instance_id) -> instance_info`
   - `get_instance_logs(instance_id, tail) -> str`
   - `wait_instance_healthy(instance_id, timeout) -> bool`
   - `exec_in_instance(instance_id, cmd) -> (exit_code, stdout, stderr)`
   - `pull_image(image) -> bool`
   - `push_image(image) -> bool`
   - `commit_instance(instance_id) -> image_ref`
   - `save_image(image_ref, path)`
   - `load_image(path) -> image_ref`
   - `create_volume(name, size)`
   - `remove_volume(name)`
   - `create_network(name, driver)`
   - `connect_to_network(instance_id, network, aliases)`
   - `get_instance_stats(instance_id) -> metrics_dict`

2. **Create `FirecrackerAdapter`** (`backend/apps/cloud/adapters/firecracker.py`) implementing the base interface:
   - Use Firecracker's REST API socket (`/tmp/firecracker/{id}.sock`) for VM lifecycle
   - VM configuration via JSON API: kernel image path, rootfs path, vCPU count, memory, network interfaces (TAP devices)
   - Boot from pre-built kernel + rootfs (ext4 disk image)
   - TAP device setup: create TAP, bridge to `smsly-fvm` bridge, assign static IP from allocation pool
   - vsock for guest-host communication (exec, file transfer, health reporting)
   - Serial console capture for log streaming
   - Disk snapshot for commit/backup instead of Docker commit
   - Resource limits via cgroups (CPU bandwidth, memory hard limit)
   - Firecracker process management: track PIDs, handle crashes, respawn on restart policy

3. **Rootfs Builder** — replace `docker build` for target images:
   - Accept Dockerfile or Nixpacks output
   - Produce: linux kernel binary (vmlinux) + ext4 rootfs image
   - Toolchain: `nixpacks build` with a custom builder that outputs rootfs instead of OCI image, or:
   - Convert OCI image -> ext4 via `docker export | mkfs.ext4` pipeline, or
   - Use `linuxkit` or `buildah` to produce kernel+initrd+rootfs
   - Store rootfs templates in shared filesystem (NFS mount) or object storage, not Docker registry

4. **Image Distribution**:
   - Replace `registry:2` container with NFS share at `/opt/smsly-hosting/fvm-images/`
   - On push: copy rootfs image + metadata JSON to shared storage
   - On pull: SCP/rsync from master to agent nodes
   - Pre-warm cache: shadow-pull rootfs to agent nodes before deployment

5. **Networking Bridge**:
   - Create `smsly-fvm` Linux bridge on host at boot
   - IP allocation pool (e.g. `172.30.0.0/16`), managed by the adapter
   - iptables/nftables rules for NAT and inter-VM routing
   - Each FVM gets a static IP; Traefik routes to VM IPs instead of container IPs
   - WireGuard mesh stays at host level; VMs route through host

6. **Traefik Integration**:
   - Replace `--providers.docker=true` with file-based provider
   - Write a config watcher that generates `/opt/smsly-hosting/traefik-dynamic/fvm-routes.yml` whenever VMs are created/removed
   - Traefik watches the file and auto-reloads
   - Labels system stays: metadata stored in DB, converted to Traefik file config

---

### Phase 2: Service Workload Migration

**Goal**: User-deployed services (GIT/DOCKER deploy type) can run on Firecracker VMs. Dual runtime: Docker and Firecracker coexist; runtime selected per-service.

**Tasks**:

1. **Runtime selection**: Add `runtime` field to Service model (`docker` | `firecracker`). Existing services default to `docker`.

2. **Modify `LocalAdapter.deploy_container()`** to dispatch to `_deploy_firecracker()` when service.runtime == 'firecracker':
   - Build: pipeline produces rootfs image instead of Docker image
   - Pull: copy rootfs from image store to VM working directory
   - Create: boot Firecracker VM with kernel + rootfs + TAP + resource limits
   - Wait: poll HTTP health endpoint on VM IP
   - Promote: blue-green via VM swap (boot new VM before killing old one)
   - Post-deploy hooks: vsock exec into VM instead of `docker exec`

3. **Environment injection**: Pass env vars via Firecracker boot config (`boot_args` or metadata JSON served via vsock). No change to env var management in tasks.py.

4. **Volume mounts**: Map service volumes to virtio-blk devices or 9p/virtiofs mounts inside the VM. Volume creation allocates a disk image; volume mount attaches it to VM.

5. **Health checks**: Replace Docker-native HEALTHCHECK with HTTP/TCP probes from the host against VM IP + port. Same probe command logic, executed from host.

6. **Port exposure**: Each VM gets a unique IP on the bridge. Traefik routes to `http://{vm_ip}:{port}`. No port mapping needed (unlike Docker's `-p host:container`).

7. **Log streaming**: Capture VM serial console output to files at `/var/log/smsly-fvm/{service_name}/console.log`. Promtail reads from file paths instead of Docker socket. Tasks.py `container.logs()` reads the log file.

---

### Phase 3: Addon Migration

**Goal**: Database/cache/storage addons run as Firecracker VMs with stronger isolation.

**Tasks**:

1. **Pre-built rootfs templates** for each addon type: Postgres, Redis, MongoDB, MySQL, MariaDB, RabbitMQ, Kafka, MinIO, Qdrant, Weaviate, Elasticsearch, etc.
   - Each template: kernel + ext4 rootfs with the addon software pre-installed
   - Templates stored in shared image store, cached on agent nodes

2. **Modify `addon_provisioner.py`** to use `FirecrackerAdapter` when runtime == 'firecracker':
   - Replace `docker run -d --name {name} {image}` with FVM boot
   - Resource limits map to vCPU + memory_mib in Firecracker config
   - Expose ports via VM IP on bridge (no host port mapping)
   - Network aliases: register VM IP in a DNS server or hosts file for service discovery

3. **Data persistence**: Addon data directories are on separate virtio-blk volumes (not in the rootfs). This separates OS from data, enabling rootfs updates without data loss.

4. **Connection URL generation**: Returns `{type}://{user}:{pass}@{vm_ip}:{port}/{db}` — same format, just VM IP instead of container name.

---

### Phase 4: Observability & Platform Hardening

**Goal**: Replace Docker-dependent monitoring with VM-native equivalents. Harden the multi-tenant boundary.

**Tasks**:

1. **cAdvisor replacement**: Write a Firecracker metrics exporter that reads VM resource usage from the Firecracker API socket (CPU, memory, I/O, network) and exposes Prometheus metrics. Or use process-level metrics via the FVM process PID.

2. **Promtail reconfiguration**: Replace `docker_sd_configs` with `static_configs` targeting `/var/log/smsly-fvm/*/console.log`. Label extraction from directory names.

3. **Autoscaler**: Replace `docker stats --no-stream` parsing with Firecracker API metrics or cgroup stats from the FVM process.

4. **Backup/restore**: Replace `container.commit()` with VM disk snapshot (pause VM, snapshot the ext4 image, resume). Replace `image.save()` with disk image copy. Replace `put_archive()` with agent-based file transfer over vsock.

5. **Security hardening**:
   - Each FVM runs as a separate Linux process with its own cgroup
   - seccomp profiles per VM (Firecracker supports this natively)
   - Jailer for additional chroot isolation (`firecracker --jailer`)
   - Rate limit Firecracker API socket access
   - Guest kernel hardened: minimal kernel config, no unnecessary drivers/modules

6. **Platform services decision**: Core platform services (backend API, celery workers, celery-beat, frontend, caddy, traefik) can remain in Docker to minimize migration risk. This keeps the operational tooling (`docker compose up`, `docker logs`) for platform debugging.

---

## Firecracker VM Specification

Each customer workload VM:

```
{
  "boot-source": {
    "kernel_image_path": "/opt/smsly-hosting/fvm-kernels/vmlinux-6.1",
    "boot_args": "console=ttyS0 reboot=k panic=1 pci=off root=/dev/vda rw"
  },
  "drives": [
    {
      "drive_id": "rootfs",
      "path_on_host": "/opt/smsly-hosting/fvm-instances/{id}/rootfs.ext4",
      "is_root_device": true,
      "is_read_only": false
    },
    {
      "drive_id": "vol-{vol_name}",
      "path_on_host": "/opt/smsly-hosting/fvm-volumes/{vol_name}.ext4",
      "is_root_device": false,
      "is_read_only": false
    }
  ],
  "network-interfaces": [
    {
      "iface_id": "eth0",
      "guest_mac": "AA:FC:00:00:00:{id_hex}",
      "host_dev_name": "tap-{id}"
    }
  ],
  "machine-config": {
    "vcpu_count": {cpu},
    "mem_size_mib": {memory_mb},
    "smt": false,
    "track_dirty_pages": true
  },
  "vsock": {
    "guest_cid": {cid},
    "uds_path": "/tmp/firecracker/{id}.vsock"
  }
}
```

---

## Key Design Decisions to Preserve

1. **Blue-green deployments**: Boot new VM before killing old VM — no downtime. VM swap instead of container swap.
2. **Traefik as internal router**: Keep Traefik. Just change its provider from Docker to file-based.
3. **Caddy as SSL edge**: Keep Caddy. Upstream targets become VM IPs instead of `traefik:80` (which stays for platform services).
4. **WireGuard mesh**: Unchanged — operates at host level.
5. **API-based remote orchestration**: Unchanged — sync, deploy trigger, status polling stay the same.
6. **Same env var injection**: No change needed — just deliver to VM boot config or vsock metadata service.
7. **Same AI pipeline**: Clone analysis, env filling, failure analysis — all stay the same.

---

## Files to Modify (Priority Order)

| Priority | File | Change |
|----------|------|--------|
| 1 | `backend/apps/cloud/adapters/base.py` | Extend adapter interface |
| 1 | `backend/apps/cloud/adapters/firecracker.py` | NEW — Firecracker adapter |
| 1 | `backend/apps/cloud/adapters/local.py` | Refactor Docker path, add dispatch to FVM |
| 2 | `backend/apps/cloud/services/builder.py` | Add rootfs builder mode |
| 2 | `backend/apps/deployments/services/pipeline.py` | Add rootfs build strategy |
| 2 | `backend/apps/deployments/tasks.py` | Update deploy flow for FVM |
| 3 | `backend/services/addon_provisioner.py` | FVM addon provisioning |
| 3 | `backend/services/traefik_labels.py` | File-based config generation |
| 4 | `backend/apps/deployments/services/backup_service.py` | Disk snapshot backup |
| 4 | `backend/apps/autoscaler/views.py` | FVM metrics |
| 5 | `docker-compose.prod.yml` | Add FVM bridge, update Traefik config |
| 5 | `infrastructure/monitoring/promtail-config.yml` | File-based log discovery |
| 5 | `infrastructure/monitoring/prometheus.yml` | FVM metrics scraper |

---

## Deliverables

Per phase, produce:
1. Working code changes in each file listed
2. Unit tests for the FirecrackerAdapter
3. Integration test: deploy a simple Python web service as a Firecracker VM
4. Updated docker-compose files with FVM bridge + Traefik file provider
5. Migration guide for existing services (how to flip the `runtime` field)

Start with Phase 1. Preserve all existing Docker functionality — both runtimes must coexist.
