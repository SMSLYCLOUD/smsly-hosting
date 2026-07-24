<p align="center">
  <img src="docs/grid-logo.png" alt="Grid" width="400" />
</p>

<h1 align="center">Grid</h1>
<p align="center"><strong>by SMSLY</strong></p>

<p align="center">
  <a href="https://github.com/SMSLYCLOUD/smsly-hosting/actions"><img src="https://github.com/SMSLYCLOUD/smsly-hosting/actions/workflows/test.yml/badge.svg" alt="CI/CD" /></a>
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT" /></a>
  <a href="https://github.com/SMSLYCLOUD/smsly-hosting"><img src="https://img.shields.io/badge/production-ready-brightgreen.svg" alt="Production Ready" /></a>
</p>

<p align="center"><em>The Self-Healing, Multi-Cloud PaaS — Deploy Anything, Anywhere.</em></p>

---

**Grid** is an open-source alternative to Vercel, Railway, and Heroku that runs on **your own infrastructure** — AWS, Azure, GCP, or bare metal. Enterprise-grade security, zero-downtime deployments, AI-powered observability, and automated disaster recovery, all out of the box.

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
| OAuth social login (GitHub, GitLab, Bitbucket, Google) | ✅ |
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
Deploy code, not containers. Grid wraps your Python/Node.js handlers in high-performance micro-containers that scale to zero.

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

Generates all required secrets: `SECRET_KEY`, `FIELD_ENCRYPTION_KEY`, `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `RABBITMQ_PASSWORD`, `GATEWAY_SECRET`, `GITHUB_WEBHOOK_SECRET`, `AUTOSCALER_API_TOKEN`, `FRP_AUTH_TOKEN`, `PGCAT_ADMIN_PASSWORD`, `REPLICATION_PASSWORD`, `SENTINEL_PASSWORD`, `REGISTRY_HTTP_SECRET`, `CROWDSEC_BOUNCER_KEY`, `COSIGN_PASSWORD`.

Additional flags: `--shell` (output as `KEY=VALUE` lines), `--dry-run` (show without writing).

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
| **Frontend** | Next.js 15 (TypeScript), Tailwind CSS v3 |
| **Database** | PostgreSQL 16 |
| **Orchestration** | Docker Compose |
| **SSL/Proxy** | Caddy (Compose master, automatic Let's Encrypt, routes directly to backend/frontend); Traefik (Compose node / lite-agent, label-driven); nginx (bare-metal, **legacy**) |
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
                     │  *.domain → Traefik (wildcard SSL)  │
                     └──────────┬─────────────────────────┘
                                │
               ┌────────────────┼────────────────┐
               │                │                │
      ┌────────▼───────┐ ┌─────▼──────┐ ┌───────▼───────┐
      │   Backend      │ │  Frontend  │ │  Celery       │
      │   Django /     │ │  Next.js   │ │  Workers (3   │
      │   Gunicorn     │ │  SSR       │ │  queues) +    │
      └────────┬───────┘ └────────────┘ │  Beat         │
               │                        └───────┬───────┘
               │                                │
     ┌─────────┼────────────────────────────────┘
     │         │
     │  ┌──────▼───────┐  ┌──────────────┐  ┌───────────────┐
     │  │  PostgreSQL  │  │  Redis       │  │  RabbitMQ     │
     │  │  16 (primary │  │  7 (primary  │  │  3 (Celery    │
     │  │  + replica)  │  │  + replica + │  │  broker)      │
     │  │  + PgCat     │  │  3 sentinels)│  └───────────────┘
     │  └──────────────┘  └──────────────┘
     │
     │  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐
     │  │  CrowdSec    │  │  Docker      │  │  Observability│
     │  │  WAF/IPS     │  │  Registry    │  │  Prometheus + │
     │  │              │  │  2.8.3       │  │  Grafana +    │
     │  └──────────────┘  └──────────────┘  │  Loki +       │
     │                                       │  Promtail     │
     │                                       └───────────────┘
     └── Docker Socket Proxy (read-only)
```

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

## 🌐 Reverse Proxy

SMSLY runs three different edge proxies, one per deployment surface. The active configuration for each is the single source of truth for that surface; do not mix them.

| Deployment surface | Edge proxy | Source of truth | TLS |
|--------------------|------------|-----------------|-----|
| Docker Compose — master | **Caddy** | `caddy-config/Caddyfile` | Automatic Let's Encrypt via `on_demand_tls` |
| Docker Compose — node / lite-agent | **Traefik** | `docker-compose.prod.yml` Traefik labels + `traefik` service | Automatic Let's Encrypt via the `letsencrypt` resolver |
| Bare-metal (legacy) | **nginx** | `nginx.conf` at repo root (**marked LEGACY** — see banner) | None (operator terminates TLS in front) |

Compose master mode also runs a small **route-fallback Caddy** (`infrastructure/route-fallback/Caddyfile`) that returns a "Service waking up" 503 page for any path the platform's Traefik rules and platform's backend/frontend services have not yet picked up. It is a safety net, not a primary route.

For the full per-route conflict matrix, failure modes, and migration plan, see **[`docs/REVERSE_PROXY_DECISION.md`](docs/REVERSE_PROXY_DECISION.md)**.

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
├── lib/                      # Shell library modules (sourced by install.sh)
├── infrastructure/           # Docker, Caddy, Traefik, Monitoring configs
│   ├── caddy/                # Caddy configuration (built with Cloudflare DNS plugin)
│   ├── docker/               # Docker compose fragments
│   ├── monitoring/           # Prometheus, Grafana, Loki, Promtail configs
│   ├── crowdsec/             # CrowdSec WAF/IPS configuration
│   ├── frps/                 # FRP tunneling server config
│   └── pgcat/                # PgCat connection pooler
├── charts/                   # Helm chart for Kubernetes deployment
├── cli/                      # Node.js CLI tool
├── archive/                  # Dead code quarantine (rust_twin, custom-addons, console)
├── tests/                    # Integration/E2E tests
├── caddy-config/             # Runtime Caddy configuration (auto-generated)
├── certs/                    # Registry TLS certificates
├── postgres/                 # PostgreSQL HA configs
├── docker-compose.yml        # Development stack
├── docker-compose.prod.yml   # Production stack (30+ services)
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
| 2026-05 | **Reverse proxy topology clarified**: Caddy is the primary edge proxy for the Compose master (`caddy-config/Caddyfile`); Traefik is the primary edge proxy for the Compose node / lite-agent (`docker-compose.prod.yml` Traefik labels); `nginx.conf` is now marked LEGACY and used only for the bare-metal install path. See [`docs/REVERSE_PROXY_DECISION.md`](docs/REVERSE_PROXY_DECISION.md) |
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
| 2026-06 | **Deep-sweep remediation (W1–W4 in flight)** — see [`SECURITY.md`](SECURITY.md), [`docs/REVERSE_PROXY_DECISION.md`](docs/REVERSE_PROXY_DECISION.md), [`docs/CLI_UNIFICATION_DECISION.md`](docs/CLI_UNIFICATION_DECISION.md), [`archive/DEAD_CODE_QUARANTINE.md`](archive/DEAD_CODE_QUARANTINE.md) | repo-wide |
| 2026-06 | **SSH MITM backdoor killed**: `install.sh` no longer patches `paramiko.AutoAddPolicy` onto production Python; `SMSLY_STRICT_SSH_HOST_KEY_CHECK` defaults to `true` | `install.sh`, `ssh_client.py`, `provisioner.py` |
| 2026-06 | **Committed secrets untracked + rotated**: `certs/registry.key`, `auth/htpasswd`, caddy-config runtime files moved out of git | `.gitignore`, `certs/`, `auth/`, `caddy-config/` |
| 2026-06 | **`.secrets.tmp` plaintext replaced** with in-memory process substitution | `install.sh`, `scripts/generate_env_secrets.py` |
| 2026-06 | **Auth moved to HttpOnly cookies**; dev-fallback auth provider removed | `frontend/src/components/auth-provider.tsx`, `backend/apps/teams/auth.py` |
| 2026-06 | **Container hardening**: `USER smsly` in monolithic `Dockerfile`; `cap_add: NET_ADMIN` dropped from backend; `privileged: true` dropped from cAdvisor | `Dockerfile`, `docker-compose.prod.yml` |
| 2026-06 | **Traefik `--api.insecure=true` removed**; Grafana anonymous disabled; all `:latest` image tags pinned | `docker-compose.prod.yml`, `infrastructure/monitoring/*` |
| 2026-06 | **Internal service ports bound to `127.0.0.1`** | `docker-compose.prod.yml` |
| 2026-06 | **Helm chart hardening**: `securityContext` defaults, `NetworkPolicy` (default-deny + intra-namespace), `PodDisruptionBudget`, per-component `ServiceAccount` with `automountServiceAccountToken: false`, `change-me`/`latest` sentinels in `_validators.tpl` | `charts/smsly-hosting/templates/_securitycontext.tpl`, `networkpolicy.yaml`, `pdb.yaml`, `serviceaccount.yaml`, `_validators.tpl` |
| 2026-06 | **`tls_verify.py` centralised policy**; 18+ scattered `verify=False` calls replaced | `backend/services/tls_verify.py`, `backend/apps/**/services/*.py` |
| 2026-06 | **Caddy HSTS, Permissions-Policy, `on_demand_tls` allowlist** | `caddy-config/Caddyfile` |
| 2026-06 | **GitHub Actions pinned to commit SHAs** with `permissions:` block; `pip-audit`, `bandit`, `gitleaks`, `npm audit` added to CI | `.github/workflows/*` |
| 2026-06 | **`SECURITY.md`**, **`CODEOWNERS`**, **`dependabot.yml`**, **`.pre-commit-config.yaml`** added | repo root, `.github/`, `.pre-commit-config.yaml` |
| 2026-06 | **`pytest-cov` + custom markers** (slow / integration / security / e2e / smoke); `--exit-zero` removed from pylint; `tsc --noEmit` added to CI | `pytest.ini`, `.pylintrc`, `.github/workflows/*` |
| 2026-06 | **Reverse-proxy topology**: Caddy = Compose master, Traefik = Compose node / lite-agent; `nginx.conf` marked LEGACY | `docs/REVERSE_PROXY_DECISION.md` |
| 2026-06 | **CLI unification**: Node CLI wins (Click CLI moved to `archive/`) | `docs/CLI_UNIFICATION_DECISION.md` |
| 2026-06 | **Dead-code quarantine**: `custom-addons/`, `rust_twin/`, `console/`, Click CLI moved to `archive/` | `archive/DEAD_CODE_QUARANTINE.md` |
| 2026-06 | **Refactor plan for god files** (`views.py` 5,827 lines, `tasks.py` 5,400 lines) | `docs/REFACTOR_PLAN_VIEWS_TASKS.md` |

---

<p align="center">
  <strong>Grid</strong> by <a href="https://github.com/SMSLYCLOUD">SMSLY</a><br />
  <em>Deploy anything. Own everything.</em>
</p>
