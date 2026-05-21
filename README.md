<p align="center">
  <img src="docs/cloudneuron-logo.png" alt="CloudNeuron" width="400" />
</p>

<h1 align="center">CloudNeuron</h1>
<p align="center"><strong>by SMSLY</strong></p>

<p align="center">
  <a href="https://github.com/SMSLYCLOUD/smsly-hosting/actions"><img src="https://github.com/SMSLYCLOUD/smsly-hosting/actions/workflows/test.yml/badge.svg" alt="CI/CD" /></a>
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT" /></a>
  <a href="https://github.com/SMSLYCLOUD/smsly-hosting"><img src="https://img.shields.io/badge/production-ready-brightgreen.svg" alt="Production Ready" /></a>
</p>

<p align="center"><em>The Self-Healing, Multi-Cloud PaaS — Deploy Anything, Anywhere.</em></p>

---

**CloudNeuron** is an open-source alternative to Vercel, Railway, and Heroku that runs on **your own infrastructure** — AWS, Azure, GCP, or bare metal. Enterprise-grade security, zero-downtime deployments, AI-powered observability, and automated disaster recovery, all out of the box.

---

## 🔐 Security

| Feature | Status |
|---------|--------|
| Fail-closed config (`SECRET_KEY`, `FIELD_ENCRYPTION_KEY` crash if missing) | ✅ |
| Docker socket isolation via read-only proxy | ✅ |
| Multi-tenancy tested (user-level data isolation) | ✅ |
| Automatic SSL/TLS via Let's Encrypt (Caddy) | ✅ |
| IP-mode SSL guard (forces HTTP for raw IPs, prevents HTTPS/IP mismatch) | ✅ |
| Scheme-appropriate CORS/CSRF origins (no `https://IP` generated) | ✅ |
| DNS challenge fallback (DoH via Google when `host` unavailable) | ✅ |
| ACME staging validation pre-flight check | ✅ |
| Caddy HTTPS redirect excludes localhost/internal/.local hostnames | ✅ |
| GitHub webhook HMAC-SHA256 signature validation | ✅ |
| Fernet encryption for secrets at rest | ✅ |
| OAuth social login (GitHub, Google) | ✅ |
| Inter-service HMAC V2 authentication | ✅ |
| App-layer rate limiting | ✅ |
| Unified secret generation (`scripts/generate_env_secrets.py`) | ✅ |

### Zero-Trust Multi-Server Audit Status

All identified gaps from the zero-trust audit have been addressed:

| Gap | Status | Fix |
|-----|--------|-----|
| Global shared `GATEWAY_SECRET` | **Fixed** | Per-agent unique `GATEWAY_SECRET` generated during provisioning (`provisioner.py`). Each `ManagedServer` gets its own HMAC signing key. |
| Unauthenticated election protocol | **Fixed** | `heartbeat_receive`/`vote_request` now require HMAC V2 signature verification using sender's per-node `gateway_secret` (`views_election.py`, `election_service.py`). |
| Lite Agent has master credentials | **Fixed** | Per-node PostgreSQL users with scoped GRANTs. Unique `GATEWAY_SECRET` per agent. Credential blast radius limited to individual node (`provisioner.py:168-185`). |
| No TLS for inter-server traffic | **Fixed** | `SMSLY_ENFORCE_INTERSERVER_TLS` skips HTTP candidate URLs. `SMSLY_REMOTE_VERIFY` controls SSL cert verification (`remote_orchestrator.py`). |
| SSH host key verification off by default | **Fixed** | `ALLOW_SSH_AUTOADD` defaults to `false`. `SMSLY_STRICT_SSH_HOST_KEY_CHECK` defaults to `true`. Warnings emitted when disabled (`ssh_client.py:75-88`, `provisioner.py:389-396`). |
| No server identity attestation | **Fixed** | Challenge-response protocol at `/api/v1/internal/attest/challenge/` and `/verify/`. Servers sign a nonce with their `gateway_secret` to prove identity (`views_attestation.py`). |
| Secrets in Celery broker | **Fixed** | `task_encryption.py` provides `encrypt_arg()`/`decrypt_arg()` using Fernet. Replication tasks transparently decrypt `enc:`-prefixed arguments (`tasks_replication.py:76-86`). |
| Inline Python injection via `docker exec` | **Fixed** | `run_node_operation` management command. Validates operation names against allowlist (`management/commands/run_node_operation.py`). |
| No TLS for Patroni/etcd replication | **Accepted** | Runs over WireGuard encrypted mesh. Application-layer TLS tracked for future enhancement. |

---

## 🚀 Features

### ⚡ Serverless Functions ("Hot Functions")
Deploy code, not containers. CloudNeuron wraps your Python/Node.js handlers in high-performance micro-containers that scale to zero.

### 👁️ AI-Driven Observability & Live Topology
- **3D City Topology**: Visualize your infrastructure as a living, breathing digital city with animated traffic and real-time connectivity states.
- **Automated Diagnostics**: Statistical anomaly detection (Z-Score) and generative AI (Gemini / OpenAI / Grok) for root cause analysis.

### 🔄 GitHub Integration & PR Previews
- **Auto-Deploy**: Push to `main` → production deploy
- **Preview Environments**: Every PR gets an ephemeral environment

### 🛡️ Enterprise Reliability
- **Disaster Recovery**: Automated backups (RTO < 15 min)
- **Health Monitoring**: `/health` with DB + Redis validation
- **80%+ Test Coverage** on critical security paths

### 🌍 Multi-Cloud & Hybrid
Works on AWS, Azure, GCP, or bare metal. Air-gapped deployments supported.

---

## 📦 Installation

### Quick Install (Fresh Ubuntu VPS)

```bash
curl -fsSL https://raw.githubusercontent.com/SMSLYCLOUD/smsly-hosting/main/install.sh -o /tmp/install.sh && sudo bash /tmp/install.sh
```

The installer prompts you to choose:
1. **IP Mode** — Quick start at `http://YOUR_IP` (no domain needed)
2. **SSL Mode** — Production-ready at `https://your-domain.com` (requires DNS A record; set `DOMAIN=your-domain.com`)

> **Note:** Do NOT pipe directly from `curl` (`curl ... | bash`). The installer requires interactive input unless you pre-seed SSL env vars.

### Generate Secrets (Standalone)

```bash
python scripts/generate_env_secrets.py           # print to stdout
python scripts/generate_env_secrets.py --env .env # append to .env
```

Generates all required secrets: `SECRET_KEY`, `FIELD_ENCRYPTION_KEY`, `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `RABBITMQ_PASSWORD`, `GATEWAY_SECRET`, `GITHUB_WEBHOOK_SECRET`, `AUTOSCALER_API_TOKEN`, `FRP_AUTH_TOKEN`, `PGCAT_ADMIN_PASSWORD`.

### Update an Existing Installation

```bash
cd /opt/smsly-hosting

# Full update
sudo bash install.sh --update

# Frontend only
sudo bash install.sh --update-frontend

# Backend only
sudo bash install.sh --update-backend
```

### Wipe Existing Installation (New VPS Reset)

```bash
cd /opt/smsly-hosting
sudo bash install.sh --wipe
```

For non-interactive automation, use: `FORCE_WIPE=1 sudo bash install.sh --wipe`

---

## 🛠️ Tech Stack

| Layer | Technology |
|-------|------------|
| **Backend** | Django 5.x, Python 3.11, Celery, Redis |
| **Frontend** | Next.js 15 (TypeScript), Tailwind CSS v4 |
| **Database** | PostgreSQL 16 |
| **Orchestration** | Docker Compose |
| **SSL/Proxy** | Caddy (automatic Let's Encrypt, containerized, routes directly to backend/frontend) |
| **Builder** | Nixpacks (auto-detect buildpacks) |

---

## 🏗️ Architecture

```
                    ┌────────────────────────────────────┐
  Internet ───────▶ │  Caddy (:80 / :443)                │
                     │  SSL, On-Demand TLS, reverse proxy  │
                     │  /api/* /ws/* /health /admin       │
                     │  /static/ /media/ → backend:8000    │
                     │  /* (catch-all) → frontend:3000     │
                     └──────────┬─────────────────────────┘
                                │
               ┌────────────────┼────────────────┐
               │                │                │
      ┌────────▼───────┐ ┌─────▼──────┐ ┌───────▼───────┐
      │   Backend      │ │  Frontend  │ │  Celery       │
      │   Django /     │ │  Next.js   │ │  Workers +    │
      │   Gunicorn     │ │  SSR       │ │  Beat         │
     └────────┬───────┘ └────────────┘ └───────┬───────┘
              │                                │
     ┌────────▼───────┐                ┌───────▼───────┐
     │  PostgreSQL    │                │  Redis        │
     │  (persistent)  │                │  (auth'd)     │
     └────────────────┘                └───────────────┘

### Multi-Server Topology

                    ┌──────────────────────────────────┐
                    │           MASTER NODE             │
                    │  (Caddy → Django + Celery)         │
                    │  PostgreSQL / Redis / RabbitMQ    │
                    └──────────┬───────────────────────┘
                               │
              ┌────────────────┼────────────────────┐
              │                │                    │
     ┌────────▼───────┐ ┌─────▼────────┐ ┌─────────▼────────┐
     │  FULL AGENT    │ │  LITE AGENT  │ │  LITE AGENT      │
     │  (Django+WS)   │ │  (Django     │ │  (Django         │
     │  Local Postgres│ │   only)      │ │   only)          │
     │  WireGuard     │ │  Master's DB │ │  Master's DB     │
     └────────────────┘ └──────────────┘ └──────────────────┘
```

### Lite Agent Mode
Lite Agents are lightweight worker nodes that connect to the master's database, Redis, and message queue directly (no local infrastructure). They receive deployment tasks via a node-specific Celery queue and build/run containers locally while reporting back to the master.

> **Security Note:** Lite Agents share the master's infrastructure credentials. See the [Zero-Trust Gaps](#known-zero-trust-gaps-multi-server-features) table above.

---

## 📂 Project Structure

```
smsly-hosting/
├── backend/                  # Django API + Celery tasks
├── frontend/                 # Next.js 15 frontend
├── docs/                     # Documentation (Setup, Reports, Proposals)
├── scripts/                  # Automation, deployment, audit scripts
│   ├── generate_env_secrets.py  # Unified secret generator (single source of truth)
│   ├── caddy-reload.sh          # Caddy config watcher
│   ├── caddy-health-guard.sh    # Caddy health guard
│   ├── smsly-autoscaler.py      # VPS autoscaler
│   └── ...
├── infrastructure/           # Docker, Caddy, Traefik, Monitoring configs
│   ├── caddy/                # Caddy configuration
│   ├── docker/               # Docker compose fragments
│   └── monitoring/           # Prometheus, Grafana configs
├── docker-compose.prod.yml   # Production stack
├── install.sh                # Universal installer (SEC-002: IP-mode SSL guard)
```

---

## 📚 Documentation

- [Developer Guide](docs/DEVELOPER_GUIDE.md)
- [Frontend Guide](docs/FRONTEND_GUIDE.md)
- [Production Deployment](docs/setup/PRODUCTION_DEPLOYMENT.md)
- [Operations Runbook](docs/setup/RUNBOOK.md)
- [Security Audit](docs/SECURITY_AUDIT.md)
- [Topology Graph Builder](docs/TOPOLOGY.md)
- [Zero-Downtime Updates](docs/ZERO_DOWNTIME_UPDATES.md)
- [VPN Mesh](docs/VPN_MESH.md)
- [Terraform](docs/TERRAFORM.md)

---

## Recent Security Improvements

| Date | Change | Files |
|------|--------|-------|
| 2026-05 | **Fail-closed secrets**: `SECRET_KEY` and `FIELD_ENCRYPTION_KEY` now raise `ImproperlyConfigured` instead of falling back to hardcoded values | `backend/config/settings.py:25-55` |
| 2026-05 | **SITE_URL respects IP mode**: No longer generates `https://<ip>` when domain is a raw IP | `backend/config/settings.py:75,182` |
| 2026-05 | **DB domain sync includes USE_SSL**: `PlatformConfig` syncs `use_ssl` to environment, keeping security settings consistent | `backend/config/settings.py:168-185` |
| 2026-05 | **Scheme-appropriate origins**: `.env` generation uses `http://` for IPs, `https://` only for real domains with SSL | `install.sh:3946-3954` |
| 2026-05 | **IP-mode SSL guard (SEC-002)**: `USE_SSL=true` env var forcibly downgraded when `DOMAIN` is a raw IP | `install.sh:1243-1253,3846-3855` |
| 2026-05 | **DNS fallback via DoH**: DNS check uses Google DNS-over-HTTPS when `host` (dnsutils) unavailable | `install.sh:3490-3517` |
| 2026-05 | **ACME staging validation**: Caddy HTTPS-ready check before going live | `install.sh:4321-4360` |
| 2026-05 | **Nginx removed from routing chain**: Caddy now proxies directly to backend:8000 and frontend:3000 instead of via nginx | `caddy-config/Caddyfile`, `caddy_manager.py`, `docker-compose.prod.yml` |
| 2026-05 | **Caddy redirect excludes localhost**: `:80` HTTPS redirect skips IPs, localhost, `.local` hostnames | `caddy_manager.py:516-531` |
| 2026-05 | **Token file hardening**: Cloudflare token files created with `0o600`, config dir restricted to `0o700` | `caddy_manager.py:596-616` |
| 2026-05 | **Unified secret generator**: `scripts/generate_env_secrets.py` is the single source of truth for all secret generation | `scripts/generate_env_secrets.py` |
| 2026-05 | **Election HMAC auth (SEC-ZT-001)**: Heartbeat/vote endpoints require HMAC V2 signatures using per-node `gateway_secret` | `views_election.py`, `election_service.py` |
| 2026-05 | **SSH host key verification (SEC-ZT-002)**: Default changed to strict verification. `ALLOW_SSH_AUTOADD=false`, `SMSLY_STRICT_SSH_HOST_KEY_CHECK=true` | `ssh_client.py`, `provisioner.py` |
| 2026-05 | **Per-agent GATEWAY_SECRET (SEC-ZT-003)**: Unique HMAC keys per ManagedServer instead of global shared secret | `provisioner.py:168-185` |
| 2026-05 | **Server identity attestation (SEC-ZT-004)**: Challenge-response protocol proves server identity via signed nonce | `views_attestation.py` |
| 2026-05 | **Inter-server TLS enforcement (SEC-ZT-005)**: `SMSLY_ENFORCE_INTERSERVER_TLS` setting; `SMSLY_REMOTE_VERIFY` for cert validation | `remote_orchestrator.py` |
| 2026-05 | **Celery task encryption (SEC-ZT-006)**: `encrypt_arg()`/`decrypt_arg()` for sensitive task arguments using Fernet | `task_encryption.py`, `tasks_replication.py` |
| 2026-05 | **Safe node operations**: `run_node_operation` management command replaces inline Python injection via `docker exec` | `management/commands/run_node_operation.py` |

---

<p align="center">
  <strong>CloudNeuron</strong> by <a href="https://github.com/SMSLYCLOUD">SMSLY</a><br />
  <em>Deploy anything. Own everything.</em>
</p>
