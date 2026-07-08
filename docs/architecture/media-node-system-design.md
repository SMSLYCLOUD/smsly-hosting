# SMSLY Media Node — System Design

## 1. Overview

SMSLY-VOICE and SMSLY-VIDEO are production-grade Rust workspaces delivering carrier telephony and WebRTC SFU capabilities. This document defines how they are provisioned and managed on baremetal servers by the Django control plane (`smsly-hosting`).

### Design Principles

- **No Docker on media nodes.** Every service runs as a native systemd unit for maximum performance — no bridge networking overhead on SIP packets, no iptables NAT translation on RTP streams, no container runtime tax on media processing.
- **C++ Drogon as the node-local management daemon.** Replaces Python agent scripts. Provides WebSocket push of real-time metrics, parallel service health checks via coroutines, hot config reload, and unified service lifecycle management over HTTP.
- **Rust on the hot path.** `smsly-voice-api` (Axum) and `smsly-video` (Axum + webrtc-rs) handle all voice/video signaling and media forwarding. C++ manages; Rust serves.
- **OpenResty at the edge.** TLS termination, WebSocket upgrade, TURN port routing — all in Nginx with Lua scripting for sub-millisecond request handling.
- **Zero external dependencies per node.** PostgreSQL, Redis, NATS, and MinIO run locally on each media node. No cross-node database sharing — each node is self-contained.
- **Django is the control plane.** Provisioning, credential management, capacity routing, room lifecycle, monitoring dashboards — all via the existing Django stack with a new `apps/media` module.

---

## 2. Node Architecture

```
┌─────────────────── BAREMETAL MEDIA NODE ───────────────────────────────┐
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  openresty.service  (edge — ports 80, 443, 3478, 5349)          │   │
│  │                                                                  │   │
│  │  Lua routing table:                                              │   │
│  │  /voice/*       → upstream voice-api   (127.0.0.1:3000)         │   │
│  │  /video/*       → upstream video-sfu   (127.0.0.1:8000)         │   │
│  │  /rtc/*         → upstream livekit     (127.0.0.1:7880)         │   │
│  │  /mgmt/*        → upstream media-mgmt  (127.0.0.1:9090)         │   │
│  │  /health        → upstream media-mgmt  (127.0.0.1:9090)         │   │
│  │  /metrics       → upstream media-mgmt  (127.0.0.1:9090)         │   │
│  │  ws://*         → WebSocket upgrade    (passthrough to upstream) │   │
│  │                                                                  │   │
│  │  TLS: Let's Encrypt (managed by smsly-hosting domain sync)       │   │
│  │  STUN/TURN: coturn shares OpenResty TLS certs via host mount     │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  smsly-media-mgmt.service  (Drogon C++ — port 9090)              │   │
│  │                                                                  │   │
│  │  ┌─────────────┐  ┌──────────────┐  ┌────────────────────────┐  │   │
│  │  │  GET /health │  │  WS /live    │  │  POST /service/{name}  │  │   │
│  │  │  All 10 svcs │  │  Real-time   │  │  start/stop/restart/   │  │   │
│  │  │  parallel    │  │  metrics     │  │  reload all systemd    │  │   │
│  │  │  coroutines  │  │  push over   │  │  units                 │  │   │
│  │  │             │  │  WebSocket   │  │                        │  │   │
│  │  └─────────────┘  └──────────────┘  └────────────────────────┘  │   │
│  │                                                                  │   │
│  │  ┌─────────────┐  ┌──────────────┐  ┌────────────────────────┐  │   │
│  │  │ GET /metrics │  │ POST /config │  │  Agent heartbeat       │  │   │
│  │  │ Prometheus   │  │ /reload      │  │  HMAC-authenticated   │  │   │
│  │  │ scrape       │  │ Hot-reload   │  │  POST → Django master │  │   │
│  │  │ endpoint     │  │ Kamailio cfg │  │  every 10s            │  │   │
│  │  └─────────────┘  └──────────────┘  └────────────────────────┘  │   │
│  │                                                                  │   │
│  │  C++20 coroutines for parallel health checks across all units.   │   │
│  │  Drogon's built-in WebSocket server pushes live stats: active    │   │
│  │  SIP calls, RTP sessions, CPU load per core, memory pressure,    │   │
│  │  room counts, participant counts. Django control plane opens     │   │
│  │  a single WS connection per node — no polling.                   │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                    SYSTEMD SERVICE LAYER                          │   │
│  │                                                                  │   │
│  │  RUST APPLICATION SERVICES                                       │   │
│  │  ├─ smsly-voice-api.service     :3000   (Axum HTTP + WebSocket)  │   │
│  │  └─ smsly-video.service         :8000   (Axum HTTP + WebRTC)    │   │
│  │                                                                  │   │
│  │  SIP & MEDIA INFRASTRUCTURE                                       │   │
│  │  ├─ kamailio.service            :5060-5061  (SIP proxy, UDP/TCP) │   │
│  │  ├─ freeswitch.service          :5080, :8021  (media + ESL)      │   │
│  │  └─ rtpengine.service           :22223, :30000-31000  (RTP)      │   │
│  │                                                                  │   │
│  │  WEBRTC & NAT TRAVERSAL                                          │   │
│  │  ├─ livekit-server.service      :7880-7882  (SFU, tcp/udp)      │   │
│  │  ├─ livekit-egress.service                  (recording)          │   │
│  │  └─ coturn.service              :3478, :5349  (STUN/TURN)        │   │
│  │                                                                  │   │
│  │  DATA & INFRASTRUCTURE                                            │   │
│  │  ├─ postgresql.service          :5432   (CDR, sessions, config)  │   │
│  │  ├─ redis.service               :6379   (ephemeral state, cache) │   │
│  │  ├─ nats.service                :4222   (event bus, JetStream)   │   │
│  │  └─ minio.service               :9000   (S3 recordings, voicemail)│   │
│  │                                                                  │   │
│  │  NETWORKING                                                       │   │
│  │  └─ wireguard.service          10.100.0.x  (mesh to master)      │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  All services depend on postgresql.service and redis.service.          │
│  All systemd units have After=network-online.target.                   │
│  Restart=on-failure with RestartSec=5s for application services.       │
│  Restart=always for infrastructure (db, redis, nats, minio).           │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Service Dependency Graph

```
network-online.target
    │
    ├─ postgresql.service
    │      └─ redis.service
    │             └─ nats.service
    │                    └─ minio.service
    │
    ├─ wireguard.service
    │
    ├─ kamailio.service     ← after: network-online.target
    ├─ rtpengine.service    ← after: network-online.target
    ├─ freeswitch.service   ← after: rtpengine.service, kamailio.service
    ├─ livekit-server.service ← after: redis.service, postgresql.service
    ├─ livekit-egress.service ← after: livekit-server.service
    ├─ coturn.service       ← after: network-online.target
    │
    ├─ smsly-voice-api.service ← after: postgresql.service, redis.service, nats.service, minio.service, kamailio.service
    ├─ smsly-video.service     ← after: redis.service
    │
    ├─ smsly-media-mgmt.service ← after: smsly-voice-api.service, smsly-video.service, livekit-server.service
    │
    └─ openresty.service    ← after: smsly-media-mgmt.service (needs upstreams healthy)
```

`smsly-media-mgmt` starts last — it assumes all upstreams are ready before it opens its health check endpoints. OpenResty starts after the management daemon so its upstream blocks reference healthy backends.

---

## 4. Drogon Management Daemon — API Design

### 4.1 Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/health` | None | Parallel health check of all 10 services. Returns JSON per-service status. |
| `WS` | `/live` | HMAC | WebSocket stream of real-time metrics. Pushes every 5s. |
| `GET` | `/metrics` | None | Prometheus text format scrape endpoint. Aggregates from all managed services. |
| `POST` | `/service/{name}/start` | HMAC | Start a systemd unit. |
| `POST` | `/service/{name}/stop` | HMAC | Stop a systemd unit. |
| `POST` | `/service/{name}/restart` | HMAC | Restart a systemd unit. |
| `POST` | `/service/{name}/reload` | HMAC | Reload config (Kamailio, FreeSWITCH, OpenResty). |
| `POST` | `/config/reload` | HMAC | Hot-reload Kamailio `kamailio.cfg` and OpenResty `nginx.conf`. |
| `GET` | `/rtp/sessions` | HMAC | Query RTPEngine for active session count. |
| `GET` | `/livekit/rooms` | HMAC | Proxy to LiveKit Server API — list active rooms. |
| `GET` | `/livekit/rooms/{name}` | HMAC | Proxy to LiveKit — room details + participants. |
| `POST` | `/livekit/rooms/{name}/egress/start` | HMAC | Start recording via LiveKit Egress. |
| `POST` | `/livekit/rooms/{name}/egress/stop` | HMAC | Stop recording. |
| `GET` | `/capacity` | HMAC | Node capacity score (0.0–1.0) for routing decisions. |

### 4.2 `GET /health` Response

```json
{
  "node": "media-3.iad1.smsly.com",
  "timestamp": "2026-07-08T14:23:01Z",
  "uptime_seconds": 120451,
  "services": {
    "postgresql":     { "status": "healthy", "latency_ms": 2 },
    "redis":          { "status": "healthy", "latency_ms": 1 },
    "nats":           { "status": "healthy", "latency_ms": 3 },
    "minio":          { "status": "healthy", "latency_ms": 5 },
    "kamailio":       { "status": "healthy", "latency_ms": 4, "active_calls": 1243 },
    "rtpengine":      { "status": "healthy", "latency_ms": 2, "active_sessions": 2486 },
    "freeswitch":     { "status": "healthy", "latency_ms": 8, "active_channels": 412 },
    "livekit-server": { "status": "healthy", "latency_ms": 4, "active_rooms": 87 },
    "coturn":         { "status": "healthy", "latency_ms": 1 },
    "smsly-voice-api":   { "status": "healthy", "latency_ms": 3 },
    "smsly-video":       { "status": "healthy", "latency_ms": 3 },
    "smsly-media-mgmt":  { "status": "healthy", "latency_ms": 0 }
  }
}
```

All health checks run in parallel via C++20 coroutines. Total latency = max(slowest service), not sum of all.

### 4.3 WebSocket `/live` — Push Payload (every 5s)

```json
{
  "type": "telemetry_snapshot",
  "node_id": "uuid-abc-123",
  "timestamp": "2026-07-08T14:23:05Z",
  "system": {
    "cpu_percent": 42.3,
    "memory_used_mb": 3847,
    "memory_total_mb": 16384,
    "disk_used_gb": 12.4,
    "disk_total_gb": 100,
    "load_1m": 2.1,
    "load_5m": 1.8,
    "load_15m": 1.6
  },
  "voice": {
    "active_calls": 1243,
    "calls_per_second": 47,
    "active_ivr_sessions": 89,
    "queued_calls": 3
  },
  "video": {
    "active_rooms": 87,
    "total_participants": 512,
    "ai_forwarder_active": true
  },
  "media": {
    "rtp_sessions": 2486,
    "rtp_bitrate_mbps": 312,
    "freeswitch_channels": 412,
    "livekit_rooms": 87,
    "livekit_participants": 298
  },
  "capacity": {
    "score": 0.62,
    "max_voice_calls": 5000,
    "max_video_participants": 2000,
    "max_rtp_sessions": 10000
  }
}
```

Django opens one WebSocket connection per media node. The management daemon pushes this payload every 5s. Django writes it to Redis for the dashboard and updates `MediaNodeProfile` DB fields. No polling, no API hammering.

### 4.4 Agent Heartbeat (to Django Master)

```
POST https://master.smsly.com/api/v1/servers/{id}/agent-heartbeat/
Authorization: Bearer smsly_<api_token>
X-Gateway-Signature-V2: <HMAC-SHA256>
X-Timestamp: 1720456800
X-Nonce: <random-uuid>

{
  "node_type": "media",
  "livekit_api_key": "<encrypted>",
  "docker_version": "",
  "smsly_images": [],
  "host_uptime": 120451,
  "disk_percent": 12,
  "mem_percent": 23,
  "capacity_score": 0.62,
  "active_calls": 1243,
  "active_rooms": 87,
  "active_participants": 512
}
```

Same pattern as existing `agent-registrar` heartbeat. Uses `gateway_secret` for HMAC signing. Django validates, updates `MediaNodeProfile`, and exposes via the dashboard.

---

## 5. OpenResty Routing

### 5.1 `nginx.conf` — Lua Routing Rules

```nginx
worker_processes auto;
error_log /var/log/openresty/error.log warn;
pid /var/run/openresty.pid;

events {
    worker_connections 65535;
    multi_accept on;
    use epoll;
}

stream {
    # ── SIP (UDP passthrough) ──────────────────────────────────
    server { listen 5060 udp; proxy_pass 127.0.0.1:5060; }
    server { listen 5060;     proxy_pass 127.0.0.1:5060; }

    # ── STUN/TURN (UDP and TCP passthrough) ─────────────────────
    server { listen 3478 udp; proxy_pass 127.0.0.1:3478; }
    server { listen 3478;     proxy_pass 127.0.0.1:3478; }

    # ── RTP range (UDP passthrough to RTPEngine) ────────────────
    server { listen 30000-31000 udp; proxy_pass 127.0.0.1:30000-31000; }
}

http {
    include mime.types;
    default_type application/octet-stream;

    # ── Upstream definitions ────────────────────────────────────
    upstream voice_api  { server 127.0.0.1:3000; }
    upstream video_sfu  { server 127.0.0.1:8000; }
    upstream livekit    { server 127.0.0.1:7880; }
    upstream media_mgmt { server 127.0.0.1:9090; }

    server {
        listen 80;
        listen 443 ssl http2;
        server_name _;

        ssl_certificate     /etc/openresty/certs/fullchain.pem;
        ssl_certificate_key /etc/openresty/certs/privkey.pem;
        ssl_protocols       TLSv1.2 TLSv1.3;

        # ── WebSocket upgrade (before most routing) ─────────────
        location /ws/ {
            proxy_pass http://voice_api;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
        }

        # ── Voice API ───────────────────────────────────────────
        location /voice/ {
            proxy_pass http://voice_api;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
        }

        # ── Video SFU ───────────────────────────────────────────
        location /video/ {
            proxy_pass http://video_sfu;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
        }

        # ── WebRTC signaling (LiveKit) ──────────────────────────
        location /rtc/ {
            proxy_pass http://livekit;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
        }

        # ── Management API (exposed to Django via WireGuard) ────
        location /mgmt/ {
            proxy_pass http://media_mgmt;
            proxy_set_header Host $host;

            access_by_lua_block {
                local hmac = require "resty.hmac"
                local secret = os.getenv("GATEWAY_SECRET")
                local sig = ngx.req.get_headers()["X-Gateway-Signature-V2"]
                if not sig or not hmac:new(secret):verify(sig) then
                    ngx.exit(403)
                end
            }
        }

        # ── Health (no auth, used by load balancers) ────────────
        location /health {
            proxy_pass http://media_mgmt/health;
        }

        # ── Prometheus metrics ──────────────────────────────────
        location /metrics {
            proxy_pass http://media_mgmt/metrics;
        }

        # ── Deny everything else ────────────────────────────────
        location / {
            return 404;
        }
    }
}
```

Key decisions:
- SIP (5060) and STUN/TURN (3478) use `stream` blocks — raw TCP/UDP passthrough, no HTTP processing overhead
- RTP port range (30000–31000) passes through to RTPEngine directly
- `/mgmt/*` path is Lua-HMAC-protected — only Django can call management endpoints
- `/health` and `/metrics` are open — load balancers and Prometheus need access
- Everything else returns 404 — media nodes are not general-purpose web servers

---

## 6. Provisioning Flow

```
Django Admin / API
    │
    │  POST /api/v1/media-nodes/
    │  { hostname: "media-3.iad1", ip: "198.51.100.10", ... }
    │
    ▼
provision_media_node_task (Celery)
    │
    ├─ 1. Create ManagedServer record (node_type = "media")
    ├─ 2. Create MediaNodeProfile (1:1 → ManagedServer)
    │      livekit_api_key     = placeholder
    │      livekit_api_secret  = placeholder
    │      turn_secret         = generated
    │      max_rooms           = 100
    │      max_participants    = 2000
    │
    ├─ 3. SSH into baremetal node [paramiko SSHClient]
    │      ├─ Detect OS (Ubuntu 24.04 / Debian 12)
    │      ├─ Upload install.sh + lib/ + infrastructure/media/
    │      │
    │      └─ Execute: sudo bash install.sh --mode=media-node \
    │           DOMAIN=media-3.iad1.smsly.com \
    │           PUBLIC_IP=198.51.100.10 \
    │           MASTER_IP=203.0.113.5 \
    │           MASTER_MESH_IP=10.100.0.1 \
    │           MASTER_WG_PUBKEY=mVz+... \
    │           GATEWAY_SECRET=<generated> \
    │           POSTGRES_PASSWORD=<generated> \
    │           REDIS_PASSWORD=<generated> \
    │           LIVEKIT_API_KEY=<generated> \
    │           SMSLY_MEDIA_REGISTRY_URL=registry.smsly.com:5000 \
    │           COSIGN_PUBLIC_KEY_PATH=<provisioned>
    │
    ├─ 4. Wait for install to complete (timeout: 30 minutes)
    │      └─ Poll: SSH check `systemctl is-active smsly-media-mgmt`
    │
    ├─ 5. Wait for agent registration
    │      └─ Poll: GET /api/v1/servers/{id}/ until agent_ready == true
    │      └─ Management daemon sends HMAC heartbeat → Django marks ready
    │
    ├─ 6. Store credentials from node response
    │      ├─ livekit_api_key    → MediaNodeProfile
    │      ├─ livekit_api_secret → MediaNodeProfile (encrypted)
    │      ├─ turn_secret        → MediaNodeProfile
    │      └─ api_token          → ManagedServer
    │
    ├─ 7. Verify node health
    │      └─ HTTP GET http://10.100.0.X:9090/health (via WireGuard)
    │
    └─ 8. Open WebSocket to node
           └─ ws://10.100.0.X:9090/live
           └─ Streams real-time telemetry into Redis + DB
```

---

## 7. Installer Flow (`install.sh --mode=media-node`)

```
Phase 0: Pre-flight
  ├─ Check: baremetal (not container), Ubuntu 24.04+
  ├─ Check: ≥4 CPU cores, ≥8GB RAM, ≥50GB disk
  ├─ Check: ports 80, 443, 5060, 3478, 30000-31000 available
  └─ Acquire install lock

Phase 1: Core Infrastructure
  ├─ apt update && apt install -y postgresql-15 redis-server nats-server minio
  ├─ Start: postgresql.service, redis.service, nats.service
  ├─ Wait for healthy: pg_isready, redis-cli PING
  ├─ Create databases: smsly_voice, smsly_video
  ├─ Run migrations: sqlx migrate run (from bundled .sqlx/)
  └─ Configure MinIO: create bucket smsly-voice-recordings

Phase 2: WireGuard Mesh
  ├─ apt install -y wireguard-tools
  ├─ Generate keypair (private + public)
  ├─ Configure wg0 interface (10.100.0.X)
  ├─ Peer master: add [Peer] block with MASTER_WG_PUBKEY
  └─ Start: wg-quick up wg0

Phase 3: SIP & Media Infrastructure
  ├─ apt install -y kamailio freeswitch rtpengine coturn
  ├─ Deploy configs:
  │   ├─ infrastructure/media/kamailio/kamailio.cfg → /etc/kamailio/
  │   ├─ infrastructure/media/freeswitch/           → /etc/freeswitch/
  │   ├─ infrastructure/media/rtpengine/rtpengine.conf → /etc/rtpengine/
  │   └─ infrastructure/media/coturn/turnserver.conf  → /etc/coturn/
  ├─ Template env vars into configs: ${PUBLIC_IP}, ${TURN_SECRET}
  ├─ Start: kamailio, rtpengine, freeswitch, coturn
  └─ Verify: sip OPTIONS ping, rtpengine stats, fs_cli status

Phase 4: WebRTC Infrastructure
  ├─ Download livekit-server binary (from GitHub releases)
  ├─ Deploy config: infrastructure/media/livekit/livekit.yaml → /etc/livekit/
  ├─ Template env vars: ${LIVEKIT_API_KEY}, ${LIVEKIT_API_SECRET}
  ├─ Start: livekit-server.service, livekit-egress.service
  └─ Verify: curl http://127.0.0.1:7880/ (health endpoint)

Phase 5: Rust Application Binaries
  ├─ Pull from private registry:
  │   docker pull registry.smsly.com:5000/smsly/smsly-voice-api:latest
  │   docker pull registry.smsly.com:5000/smsly/smsly-video:latest
  ├─ Cosign verify both binaries:
  │   cosign verify --key /opt/smsly-hosting/cosign-keys/cosign.pub \
  │     registry.smsly.com:5000/smsly/smsly-voice-api:latest
  ├─ Extract binaries from images (docker cp):
  │   docker create --name tmp-voice registry.smsly.com:5000/smsly/smsly-voice-api:latest
  │   docker cp tmp-voice:/app/smsly-voice-api /usr/local/bin/
  │   docker rm tmp-voice
  ├─ chmod 755 /usr/local/bin/smsly-voice-api
  ├─ chmod 755 /usr/local/bin/smsly-video
  ├─ Create systemd units from templates
  ├─ Start: smsly-voice-api.service, smsly-video.service
  └─ Verify: curl http://127.0.0.1:3000/health, curl http://127.0.0.1:8000/health

Phase 6: Management Daemon
  ├─ Build or pull smsly-media-mgmt binary (Drogon C++)
  ├─ Create systemd unit
  ├─ Template env vars: ${GATEWAY_SECRET}, ${MASTER_API_URL}, ${NODE_ID}
  ├─ Start: smsly-media-mgmt.service
  └─ Wait for ready: curl http://127.0.0.1:9090/health (retry 30s)

Phase 7: OpenResty Edge Proxy
  ├─ apt install -y openresty
  ├─ Deploy config: infrastructure/media/openresty/nginx.conf → /usr/local/openresty/nginx/conf/
  ├─ Symlink TLS certs from /etc/openresty/certs/ (managed by domain sync)
  ├─ Start: openresty.service
  └─ Verify: curl -k https://localhost/health

Phase 8: Finalize
  ├─ systemctl enable all services
  ├─ Write .env file: /opt/smsly-hosting-media/.env
  │   NODE_TYPE=media-node
  │   MEDIA_NODE_ID=<uuid>
  │   MASTER_API_URL=https://master.smsly.com/api/v1
  │   GATEWAY_SECRET=<generated>
  │   LIVEKIT_API_KEY=<generated>
  │   (all passwords, endpoints, secrets)
  ├─ Start smsly-media-agent heartbeat
  └─ Release install lock
```

---

## 8. Update Flow (`install.sh --update` on media node)

```
Phase 0: Pre-check
  ├─ Acquire update lock
  ├─ Source /opt/smsly-hosting-media/.env
  └─ Check git version (update.sh bundled in install)

Phase 1: Registry Auth Self-Heal
  ├─ Verify TLS certs (registry.crt/key match, SANs present)
  ├─ Verify htpasswd exists, REGISTRY_PASSWORD set
  ├─ Generate missing secrets (COSIGN_PASSWORD, REGISTRY_HTTP_SECRET, etc.)

Phase 2: Pull & Verify Binaries
  ├─ docker pull registry.smsly.com:5000/smsly/smsly-voice-api:latest
  ├─ docker pull registry.smsly.com:5000/smsly/smsly-video:latest
  ├─ cosign verify --key cosign-keys/cosign.pub <both images>
  ├─ Extract updated binaries:
  │   docker cp tmp-voice:/app/smsly-voice-api /usr/local/bin/smsly-voice-api.new
  ├─ Atomic swap:
  │   mv /usr/local/bin/smsly-voice-api.new /usr/local/bin/smsly-voice-api
  └─ Restart: systemctl restart smsly-voice-api smsly-video

Phase 3: Pull Management Daemon
  ├─ docker pull registry.smsly.com:5000/smsly/smsly-media-mgmt:latest
  ├─ cosign verify
  ├─ Extract binary, swap, restart: systemctl restart smsly-media-mgmt

Phase 4: Config Hot-Reload
  ├─ Check if kamailio.cfg or nginx.conf changed in bundle
  ├─ If changed: POST /mgmt/config/reload (via management daemon)
  └─ No restart needed — Kamailio and Nginx support hot reload

Phase 5: Restart Media Infra (if base images updated)
  ├─ apt update && apt upgrade kamailio freeswitch rtpengine coturn (if available)
  └─ systemctl restart kamailio freeswitch rtpengine

Phase 6: Verify
  ├─ Health check via management daemon: GET /mgmt/health
  ├─ All 10 services must report "healthy"
  └─ Release update lock
```

---

## 9. Self-Heal Flow (`ops.sh` on media node)

Triggered by cron, systemd timer, or Django-triggered SSH command.

```
Phase 0: Detect issues
  ├─ For each service: systemctl is-active $name
  ├─ If dead: systemctl restart $name (retry 3x, escalate to systemctl reset-failed)
  └─ Check disk space: warn at 80%, critical at 90% (clean old recordings)

Phase 1: PostgreSQL
  └─ pg_isready || systemctl restart postgresql

Phase 2: Redis
  └─ redis-cli PING || systemctl restart redis

Phase 3: Kamailio
  ├─ Check: nc -zvu 127.0.0.1 5060 || restart
  └─ Check dispatcher: psql -c "SELECT count(*) FROM dispatcher" || reload

Phase 4: RTPEngine
  ├─ Check: curl http://127.0.0.1:8080/stats || restart
  └─ Check port range: ss -ulnp | grep 30000 || restart

Phase 5: FreeSWITCH
  ├─ Check: fs_cli -x 'status' || restart
  └─ Clear stuck sessions: fs_cli -x 'hupall' if channel count > threshold

Phase 6: LiveKit
  ├─ Check: curl http://127.0.0.1:7880/ || restart
  └─ Verify API key: curl http://127.0.0.1:7880/rooms -H "Authorization: Bearer $LIVEKIT_KEY" || rotate

Phase 7: Coturn
  ├─ Check TLS cert expiry: openssl x509 -checkend 86400 || regenerate
  └─ Recycle: systemctl restart coturn (daily, to clear stale allocations)

Phase 8: Rust Binaries
  ├─ Check: curl http://127.0.0.1:3000/health && curl http://127.0.0.1:8000/health
  └─ Restart if unresponsive

Phase 9: Management Daemon
  ├─ Check: curl http://127.0.0.1:9090/health
  └─ Restart if unresponsive

Phase 10: Credential Self-Heal
  ├─ Check .env has all required vars (REGISTRY_HTTP_SECRET, COSIGN_PASSWORD, etc.)
  ├─ Generate any missing via Python secrets module
  └─ Cosign keypair check: generate if missing
```

---

## 10. Django Control Plane — `apps/media`

### 10.1 Models

```python
# backend/apps/media/models.py

class MediaNodeProfile(models.Model):
    server = models.OneToOneField(
        "deployments.ManagedServer",
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="media_profile"
    )
    # LiveKit credentials (encrypted at rest)
    livekit_api_key = models.CharField(max_length=128)
    livekit_api_secret = models.CharField(max_length=256)  # encrypted
    livekit_host = models.CharField(max_length=255, default="127.0.0.1")
    livekit_port = models.PositiveIntegerField(default=7880)

    # TURN configuration
    turn_secret = models.CharField(max_length=128)         # encrypted
    turn_realm = models.CharField(max_length=255, default="smsly.com")
    turn_port_tcp = models.PositiveIntegerField(default=3478)
    turn_port_tls = models.PositiveIntegerField(default=5349)

    # Capacity limits
    max_voice_calls = models.PositiveIntegerField(default=5000)
    max_video_rooms = models.PositiveIntegerField(default=100)
    max_participants = models.PositiveIntegerField(default=2000)
    max_rtp_sessions = models.PositiveIntegerField(default=10000)

    # Live metrics (updated via WebSocket push)
    active_calls = models.PositiveIntegerField(default=0)
    active_rooms = models.PositiveIntegerField(default=0)
    active_participants = models.PositiveIntegerField(default=0)
    active_rtp_sessions = models.PositiveIntegerField(default=0)
    capacity_score = models.FloatField(default=1.0)

    # Telemetry
    cpu_percent = models.FloatField(default=0.0)
    memory_percent = models.FloatField(default=0.0)
    disk_percent = models.FloatField(default=0.0)

    last_telemetry_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)


class MediaRoom(models.Model):
    room_id = models.CharField(max_length=128, unique=True)
    node = models.ForeignKey(MediaNodeProfile, on_delete=models.CASCADE)
    service = models.ForeignKey("deployments.Service", on_delete=models.SET_NULL, null=True)
    room_type = models.CharField(max_length=20, default="video")  # voice | video
    status = models.CharField(max_length=20, default="active")     # active | ended
    participant_count = models.PositiveIntegerField(default=0)
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True)


class MediaParticipant(models.Model):
    room = models.ForeignKey(MediaRoom, on_delete=models.CASCADE)
    participant_id = models.CharField(max_length=128)
    user = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True)
    joined_at = models.DateTimeField(auto_now_add=True)
    left_at = models.DateTimeField(null=True)
```

### 10.2 Services

```python
# backend/apps/media/services/livekit_admin.py

class LiveKitAdminService:
    """Client for LiveKit Server API. One instance per media node."""

    def __init__(self, node: MediaNodeProfile):
        self.base_url = f"http://{node.server.wg_address}:7880"
        self.api_key = node.livekit_api_key
        self.api_secret = decrypt(node.livekit_api_secret)

    def create_room(self, name: str, max_participants: int = 100) -> dict: ...
    def delete_room(self, name: str) -> None: ...
    def list_rooms(self) -> list[dict]: ...
    def get_room(self, name: str) -> dict: ...
    def list_participants(self, room: str) -> list[dict]: ...
    def remove_participant(self, room: str, participant: str) -> None: ...
    def start_egress(self, room: str) -> dict: ...
    def stop_egress(self, egress_id: str) -> None: ...
```

```python
# backend/apps/media/services/capacity.py

class MediaCapacityService:
    """Selects the best media node for a new room or call."""

    def find_best_node(self, room_type: str = "video") -> MediaNodeProfile | None:
        """Returns node with highest capacity_score and matching type support."""
        return (
            MediaNodeProfile.objects
            .filter(server__agent_ready=True, server__provision_status="active")
            .order_by("-capacity_score")
            .first()
        )

    def calculate_score(self, node: MediaNodeProfile) -> float:
        """0.0 = fully loaded, 1.0 = empty."""
        voice_load = node.active_calls / max(node.max_voice_calls, 1)
        video_load = node.active_participants / max(node.max_participants, 1)
        rtp_load = node.active_rtp_sessions / max(node.max_rtp_sessions, 1)
        cpu_load = node.cpu_percent / 100

        # Weighted aggregate — media load weighs more than CPU
        return round(1.0 - max(voice_load * 0.3, video_load * 0.5, rtp_load * 0.1, cpu_load * 0.1), 4)
```

### 10.3 Views

```python
# backend/apps/media/views.py — REST API

# MediaNodeViewSet:
#   GET    /api/v1/media-nodes/              — list all nodes
#   POST   /api/v1/media-nodes/              — provision new node
#   GET    /api/v1/media-nodes/{id}/         — node detail + live stats
#   DELETE /api/v1/media-nodes/{id}/         — deprovision node
#   POST   /api/v1/media-nodes/{id}/restart/ — restart all services
#   GET    /api/v1/media-nodes/{id}/health/  — real-time health check

# MediaRoomViewSet (nested under media-nodes):
#   GET    /api/v1/media-nodes/{id}/rooms/          — list rooms
#   POST   /api/v1/media-nodes/{id}/rooms/          — create room (routed to best node)
#   GET    /api/v1/media-nodes/{id}/rooms/{name}/   — room detail + participants
#   DELETE /api/v1/media-nodes/{id}/rooms/{name}/   — end room
#   POST   /api/v1/media-nodes/{id}/rooms/{name}/egress/start — start recording
#   POST   /api/v1/media-nodes/{id}/rooms/{name}/egress/stop  — stop recording

# CapacityView:
#   GET    /api/v1/media/capacity/           — global capacity overview
#   GET    /api/v1/media/capacity/best-node/ — returns best node for routing

# WebSocket endpoint:
#   WS     /ws/media-nodes/{id}/live/        — proxy to node's WS stream
```

### 10.4 Tasks

```python
# backend/apps/media/tasks.py

@celery_app.task(bind=True, max_retries=3)
def provision_media_node(self, server_id: uuid.UUID) -> dict:
    """SSH into baremetal, run install, register, return credentials."""

@celery_app.task
def deprovision_media_node(server_id: uuid.UUID) -> None:
    """SSH in, stop all services, wipe data volumes, delete ManagedServer."""

@celery_app.task
def sync_media_credentials(server_id: uuid.UUID) -> None:
    """Re-sync LiveKit key, TURN secret from node if rotated."""

@celery_app.task
def check_media_node_health() -> None:
    """Periodic task: health-check all media nodes, alert on degraded."""

@celery_app.task
def collect_media_telemetry() -> None:
    """Process WebSocket telemetry batches from Redis, update DB fields."""
```

---

## 11. Systemd Unit Templates

### 11.1 `smsly-media-mgmt.service`

```ini
[Unit]
Description=SMSLY Media Management Daemon
After=network-online.target smsly-voice-api.service smsly-video.service livekit-server.service
Wants=network-online.target
Requires=smsly-voice-api.service smsly-video.service livekit-server.service

[Service]
Type=simple
User=smsly
Group=smsly
ExecStart=/usr/local/bin/smsly-media-mgmt --config /etc/smsly/media-mgmt.json
ExecReload=/bin/kill -HUP $MAINPID
Restart=on-failure
RestartSec=5
LimitNOFILE=65536

# Security hardening
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/log/smsly /run/smsly
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

### 11.2 `smsly-voice-api.service`

```ini
[Unit]
Description=SMSLY Voice API (Rust/Axum)
After=postgresql.service redis.service nats.service minio.service kamailio.service
Requires=postgresql.service redis.service

[Service]
Type=simple
User=smsly
Group=smsly
EnvironmentFile=/opt/smsly-hosting-media/.env
ExecStart=/usr/local/bin/smsly-voice-api
Restart=on-failure
RestartSec=5
LimitNOFILE=65535

ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/log/smsly /var/lib/smsly
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

### 11.3 `kamailio.service`

```ini
[Unit]
Description=Kamailio SIP Proxy
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=forking
PIDFile=/var/run/kamailio/kamailio.pid
ExecStart=/usr/sbin/kamailio -f /etc/kamailio/kamailio.cfg -P /var/run/kamailio/kamailio.pid -m 256 -M 32
ExecReload=/usr/sbin/kamailio -f /etc/kamailio/kamailio.cfg -P /var/run/kamailio/kamailio.pid -m 256 -M 32 -D 1
Restart=always
RestartSec=3
LimitNOFILE=65536
LimitCORE=infinity
AmbientCapabilities=CAP_NET_BIND_SERVICE CAP_NET_RAW

[Install]
WantedBy=multi-user.target
```

---

## 12. ManagedServer Model Extension

Add `node_type` field to replace `is_lite_agent` boolean:

```python
# backend/apps/deployments/models_core.py

class ManagedServer(models.Model):
    # ... existing fields ...

    NODE_TYPE_CHOICES = [
        ("master", "Master (full stack)"),
        ("node", "Node (full stack, no Caddy)"),
        ("agent-lite", "Agent Lite (minimal)"),
        ("media", "Media Node (voice + video baremetal)"),
    ]
    node_type = models.CharField(
        max_length=20,
        choices=NODE_TYPE_CHOICES,
        default="agent-lite"
    )

    # Backward compat
    @property
    def is_lite_agent(self) -> bool:
        return self.node_type == "agent-lite"

    @property
    def is_media_node(self) -> bool:
        return self.node_type == "media"
```

---

## 13. Implementation Phases

### Phase 1: Infrastructure Templates (12 files)
Systemd units, OpenResty config, Kamailio/FreeSWITCH/RTPEngine/coturn configs.

### Phase 2: Drogon Management Daemon (C++ repo)
New repo: `smsly-media-mgmt` — C++20 Drogon binary with WebSocket, health checks, systemd DBus integration.

### Phase 3: Shell Scripts (smsly-hosting)
`lib/media-node.sh`, `install.sh`, `lib/utils.sh`, `lib/fresh.sh`, `lib/update.sh`, `lib/ops.sh`.

### Phase 4: Django Backend (smsly-hosting)
`apps/media/` — models, views, URLs, tasks, services, admin, migrations.

### Phase 5: Binary Build Pipeline
CI/CD for Rust binaries, Drogon binary, docker build + push + cosign sign.

### Phase 6: Tests
E2E provision test, LiveKit admin mock, capacity routing, Drogon health check, OpenResty config lint.

---

## 14. File Manifest

| # | File | Action | Phase |
|---|------|--------|-------|
| 1 | `scripts/systemd/smsly-media-mgmt.service` | Create | 1 |
| 2 | `scripts/systemd/smsly-voice-api.service` | Create | 1 |
| 3 | `scripts/systemd/smsly-video.service` | Create | 1 |
| 4 | `scripts/systemd/kamailio.service` | Create | 1 |
| 5 | `scripts/systemd/freeswitch.service` | Create | 1 |
| 6 | `scripts/systemd/rtpengine.service` | Create | 1 |
| 7 | `scripts/systemd/livekit-server.service` | Create | 1 |
| 8 | `scripts/systemd/coturn.service` | Create | 1 |
| 9 | `infrastructure/media/openresty/nginx.conf` | Create | 1 |
| 10 | `infrastructure/media/kamailio/kamailio.cfg` | Create | 1 |
| 11 | `infrastructure/media/freeswitch/freeswitch.xml` | Create | 1 |
| 12 | `infrastructure/media/rtpengine/rtpengine.conf` | Create | 1 |
| 13 | `infrastructure/media/livekit/livekit.yaml` | Create | 1 |
| 14 | `infrastructure/media/coturn/turnserver.conf` | Create | 1 |
| 15 | `lib/media-node.sh` | Create | 3 |
| 16 | `lib/utils.sh` | Modify | 3 |
| 17 | `install.sh` | Modify | 3 |
| 18 | `lib/fresh.sh` | Modify | 3 |
| 19 | `lib/update.sh` | Modify | 3 |
| 20 | `lib/ops.sh` | Modify | 3 |
| 21 | `backend/apps/media/__init__.py` | Create | 4 |
| 22 | `backend/apps/media/models.py` | Create | 4 |
| 23 | `backend/apps/media/views.py` | Create | 4 |
| 24 | `backend/apps/media/urls.py` | Create | 4 |
| 25 | `backend/apps/media/tasks.py` | Create | 4 |
| 26 | `backend/apps/media/services/__init__.py` | Create | 4 |
| 27 | `backend/apps/media/services/livekit_admin.py` | Create | 4 |
| 28 | `backend/apps/media/services/capacity.py` | Create | 4 |
| 29 | `backend/apps/media/provisioner.py` | Create | 4 |
| 30 | `backend/apps/media/admin.py` | Create | 4 |
| 31 | `backend/apps/media/migrations/0001_initial.py` | Create | 4 |
| 32 | `backend/config/settings.py` | Modify | 4 |
| 33 | `backend/config/urls.py` | Modify | 4 |
| 34 | `backend/apps/deployments/models_core.py` | Modify | 4 |

---

## 15. Performance Targets

| Metric | Target | How |
|--------|--------|-----|
| SIP call setup | <200ms | Kamailio baremetal, no Docker bridge |
| RTP latency (node internal) | <1ms | RTPEngine kernel-level forwarding |
| Voice API P99 | <50ms | Rust/Axum, no container runtime |
| WebSocket telemetry push | 5s interval | Drogon native WebSocket, zero-copy |
| Management daemon health check | <10ms | C++20 coroutines, parallel checks |
| Binary deploy + restart | <30s | Docker cp + systemctl restart |
| Provision new node | <15min | Automated apt + binary pull + config template |

---

*Document version: 1.0 — 2026-07-08*
*Next: Phase 1 implementation — systemd units and config templates*
