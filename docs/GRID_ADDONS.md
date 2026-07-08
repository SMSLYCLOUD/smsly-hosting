# Grid Addons: Custom Infrastructure Bundles

## Overview

`grid.addons` is a manifest file that lives in your service repository root. It declares both standard addon dependencies (Postgres, Redis, NATS, etc.) and custom infrastructure bundles (Kamailio, FreeSWITCH, LiveKit, etc.) that Grid provisions automatically when you deploy.

**Key benefits:**
- Single source of truth for all infrastructure requirements
- Custom bundles get the same lifecycle features as standard addons (logs, status, health checks, backup/restore, rebuild, teardown)
- Bundle components appear in your service's Addons tab alongside standard addons
- No need to expose internal infrastructure as public addon types
- Infra changes ship with your service code, not a separate Grid deploy

## File Format

Place a file named `grid.addons` (no extension) in your repository root:

```yaml
version: "1"
service_type: smsly-voice    # optional, for display purposes

# Standard addons — handled by Grid's existing AddonProvisioner
addons:
  postgres:
    type: POSTGRES
  redis:
    type: REDIS
  nats:
    type: NATS

# Custom bundles — NOT public addon types, handled by BundleProvisioner
bundles:
  sip-stack:
    network: smsly-voice-sip    # isolated Docker network name
    services:
      kamailio:
        image: kamailio/kamailio:5.7-stretch
        ports: ["5060:5060/udp", "5060:5060/tcp"]
        volumes:
          - ./infrastructure/kamailio/kamailio.cfg:/etc/kamailio/kamailio.cfg
        healthcheck:
          test: ["CMD-SHELL", "kamctl stats || exit 1"]
          interval: 30s
          timeout: 5s

      rtpengine:
        image: sipwise/rtpengine:12.5
        ports: ["30000:30000/udp"]
        cap_add: ["NET_ADMIN"]

      freewitch:
        image: signalwire/freeswitch:1.10
        ports: ["8021:8021", "10000:10000/udp"]

  voicebot:
    services:
      # Pre-built Docker image
      whisper:
        image: fedirg/whispercpp:latest
        ports: ["8080:8080"]

      # Git repository — Grid builds it into a Docker container
      ai-orchestrator:
        repo: https://github.com/smsly/ai-orchestrator.git
        branch: main
        build: dockerfile
        ports: ["8090:8090"]
        env:
          OPENAI_API_KEY: "${OPENAI_API_KEY}"

      # Git repository with Nixpacks build
      drachtio:
        repo: https://github.com/smsly/drachtio-bridge.git
        build: nixpacks
        ports: ["5070:5070"]
```

## Supported Service Sources

### Docker Image
```yaml
services:
  my-service:
    image: nginx:alpine
    ports: ["80:80"]
```

### Git Repository (Dockerfile)
```yaml
services:
  my-service:
    repo: https://github.com/org/repo.git
    branch: main
    build: dockerfile
    dockerfile: ./Dockerfile    # optional, auto-detected
    context: .                  # optional, defaults to repo root
    ports: ["8080:8080"]
```

### Git Repository (Nixpacks)
```yaml
services:
  my-service:
    repo: https://github.com/org/repo.git
    build: nixpacks
    ports: ["3000:3000"]
```

## Service Properties

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `image` | string | one of `image`/`repo` | Docker image to pull |
| `repo` | string | one of `image`/`repo` | Git repository URL |
| `branch` | string | no | Git branch (default: `main`) |
| `build` | string | no | `"dockerfile"` or `"nixpacks"` |
| `dockerfile` | string | no | Path to Dockerfile relative to context |
| `context` | string | no | Build context relative to repo root |
| `ports` | list[string] | no | Port mappings (`host:container`) |
| `volumes` | list[string] | no | Volume mounts |
| `environment` | map[string,string] | no | Environment variables |
| `env` | map[string,string] | no | Alias for `environment` |
| `healthcheck` | object | no | Docker healthcheck config |
| `labels` | list[string] | no | Container labels |
| `cap_add` | list[string] | no | Linux capabilities |
| `command` | string/list | no | Override container command |
| `depends_on` | list[string] | no | Service dependencies |
| `restart` | string | no | Restart policy (default: `unless-stopped`) |

## Template Variables

You can reference standard addon connection URLs in bundle service env vars:

```yaml
addons:
  postgres:
    type: POSTGRES

bundles:
  sip-stack:
    services:
      kamailio:
        environment:
          DBHOST: "{{addons.postgres.host}}"
          DBPORT: "{{addons.postgres.port}}"
          DBNAME: "{{addons.postgres.database}}"
```

Available variables:
- `{{addons.<name>.url}}` — full connection URL
- `{{addons.<name>.host}}` — hostname only
- `{{addons.<name>.port}}` — port only
- `{{addons.<name>.user}}` — username
- `{{addons.<name>.password}}` — password
- `{{addons.<name>.database}}` — database name

## Lifecycle Operations

Bundle components receive the same lifecycle management as standard addons:

### Logs
```bash
# Get logs for all components in a bundle
GET /api/v1/bundles/{id}/logs/

# Get logs for a specific component
GET /api/v1/bundles/{id}/logs/?component=kamailio&tail=500
```

### Status Check
```bash
GET /api/v1/bundles/{id}/status_check/
# Returns: { running: true, components: [...] }
```

### Reprovision (Rebuild & Restart)
```bash
POST /api/v1/bundles/{id}/reprovision/
# Rebuilds repo-based images and recreates all containers
```

### Deprovision (Tear Down)
```bash
POST /api/v1/bundles/{id}/deprovision/
# Stops containers, removes network, cleans up compose files
```

### Backup
```bash
POST /api/v1/bundles/{id}/backup/
Body: { "component": "kamailio" }
```

### Restore
```bash
POST /api/v1/bundles/{id}/restore/
Body: { "backup_id": "uuid" }
```

### Metrics
```bash
GET /api/v1/bundles/{id}/metrics/
# Returns per-component CPU%, memory, network I/O
```

### Network Check
```bash
GET /api/v1/bundles/{id}/network_check/
# Verifies and repairs the bundle's Docker network
```

### Credentials
```bash
GET /api/v1/bundles/{id}/credentials/
# Returns parsed connection info for all components
```

## Unified Addons Tab

To show both standard addons and bundle components in one list:

```bash
GET /api/v1/services/{service_id}/addons-all/
```

Returns a unified list where each item has a `_type` field:
- `"addon"` — standard addon (Postgres, Redis, etc.)
- `"bundle_component"` — component from a custom bundle

## How It Works

### Deploy Pipeline Integration

When Grid deploys your service, the pipeline:

1. **Detects** `grid.addons` in the repo root
2. **Parses** the manifest (YAML or JSON)
3. **Phase 1 — Standard Addons**: Provisions Postgres, Redis, etc. via existing `AddonProvisioner`
4. **Phase 2 — Custom Bundles**: 
   - Builds repo-based services (clone → build → push to registry)
   - Generates `docker-compose.yml` for each bundle
   - Creates isolated Docker network
   - Runs `docker compose up -d`
   - Waits for healthchecks
5. **Injects** all connection URLs as env vars into the service container

### Bundle Isolation

Each bundle gets:
- **Own Docker network** — services within a bundle can reach each other by service name
- **Own compose file** — stored at `/app/bundles/{service_id}/{bundle_name}/grid-addons-compose.yml`
- **Container naming** — `smsly-bundle-{bundle_name}-{component_name}-{service_id}`
- **Labels** — for Prometheus scraping, log aggregation, and Grid management

### Feature Parity with Standard Addons

| Feature | Standard Addon | Bundle Component |
|---------|---------------|------------------|
| Logs | `docker logs` | `docker compose logs` |
| Status | `docker inspect` | `docker compose ps` + inspect |
| Health | Connection test | Per-component healthcheck |
| Backup | pg_dump, redis-cli | User script or docker cp |
| Restore | psql, redis-cli | User script or docker cp |
| Reprovision | Re-run `docker run` | `docker compose up --force-recreate` |
| Deprovision | `docker stop/rm` | `docker compose down -v` |
| Metrics | Prometheus labels | Docker stats + Prometheus |
| Network | Docker network + aliases | Isolated compose network |
| Env injection | Via EnvironmentVariable model | Via EnvironmentVariable model |

## Backup Scripts

You can provide custom backup scripts in the `grid.addons` file:

```yaml
bundles:
  sip-stack:
    backup:
      kamailio:
        backup_script: ./scripts/backup-kamailio.sh
        restore_script: ./scripts/restore-kamailio.sh
      elasticsearch:
        backup_script: ./scripts/backup-es.sh
```

The scripts receive two arguments:
1. Container name
2. Backup file path

```bash
#!/bin/bash
# scripts/backup-kamailio.sh
CONTAINER=$1
BACKUP_PATH=$2
docker exec "$CONTAINER" kamdbutil backup > "$BACKUP_PATH"
```

## Example: SMSLY-VOICE

```yaml
version: "1"
service_type: smsly-voice

addons:
  postgres:
    type: POSTGRES
  redis:
    type: REDIS
  nats:
    type: NATS

bundles:
  sip-stack:
    network: voice-sip
    services:
      kamailio:
        image: kamailio/kamailio:5.7-stretch
        ports: ["5060:5060/udp", "5060:5060/tcp"]
        volumes:
          - ./infrastructure/kamailio/kamailio.cfg:/etc/kamailio/kamailio.cfg
      rtpengine:
        image: sipwise/rtpengine:12.5
        ports: ["30000:30000/udp"]
        cap_add: ["NET_ADMIN"]
      freewitch:
        image: signalwire/freeswitch:1.10
        ports: ["8021:8021", "10000:10000/udp"]

  observability:
    services:
      homer:
        image: sipcapture/homer-app:latest
        ports: ["9080:80"]
      elasticsearch:
        image: elasticsearch:8.11.0
        volumes: ["es-data:/usr/share/elasticsearch/data"]
        healthcheck:
          test: ["CMD-SHELL", "curl -f http://localhost:9200/_cluster/health || exit 1"]
          interval: 30s

  voicebot:
    services:
      whisper:
        image: fedirg/whispercpp:latest
        ports: ["8080:8080"]
      ai-orchestrator:
        repo: https://github.com/smsly/ai-orchestrator.git
        branch: main
        build: dockerfile
        ports: ["8090:8090"]
        env:
          OPENAI_API_KEY: "${OPENAI_API_KEY}"
      drachtio:
        repo: https://github.com/smsly/drachtio-bridge.git
        build: nixpacks
        ports: ["5070:5070"]
      piper:
        image: rhasspy/piper:latest
        ports: ["5000:5000"]
```

## Example: SMSLY-VIDEO

```yaml
version: "1"
service_type: smsly-video

addons:
  redis:
    type: REDIS

bundles:
  media:
    services:
      minio:
        image: minio/minio:latest
        command: "server /data --console-address ':9001'"
        ports: ["9000:9000", "9001:9001"]
        environment:
          MINIO_ROOT_USER: minioadmin
          MINIO_ROOT_PASSWORD: minioadmin
      turn-server:
        image: coturn/coturn:4.6
        ports: ["3478:3478/udp", "3478:3478/tcp"]
        cap_add: ["NET_ADMIN"]
```

## FAQ

**Q: Can I use `grid.addons` with Docker Compose deploy mode?**
A: Yes. When `grid.addons` is present, it takes priority over auto-detection. The manifest is the authoritative source.

**Q: What happens if a bundle component fails to start?**
A: The bundle status is set to `FAILED`. The component's healthcheck determines whether it's marked unhealthy. You can check status via `GET /api/v1/bundles/{id}/status_check/`.

**Q: Can I reference env vars from the parent service in bundle services?**
A: Yes. Use `${VAR_NAME}` syntax. These are resolved at compose time from the service's environment.

**Q: How do bundle components appear in the frontend?**
A: Use the unified endpoint `GET /api/v1/services/{id}/addons-all/` which returns both standard addons and bundle components in a single list. Each item has a `_type` field (`"addon"` or `"bundle_component"`).

**Q: Can I provide custom backup scripts?**
A: Yes. Use the `backup` section in the bundle definition to specify `backup_script` and `restore_script` paths.

**Q: What build tools are supported for repo-based services?**
A: Dockerfile (auto-detected or explicit) and Nixpacks. Set `build: "dockerfile"` or `build: "nixpacks"`.
