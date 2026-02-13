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
| Fail-closed config (`SECRET_KEY`, `ALLOWED_HOSTS` crash if missing) | ✅ |
| Docker socket isolation via read-only proxy | ✅ |
| Multi-tenancy tested (user-level data isolation) | ✅ |
| Automatic SSL/TLS via Let's Encrypt (Caddy) | ✅ |
| Zero-downtime rolling deployments | ✅ |
| GitHub webhook HMAC-SHA256 signature validation | ✅ |
| Fernet encryption for secrets at rest | ✅ |
| OAuth social login (GitHub, Google) | ✅ |
| Inter-service HMAC V2 authentication | ✅ |
| App-layer rate limiting | ✅ |

---

## 🚀 Features

### ⚡ Serverless Functions ("Hot Functions")
Deploy code, not containers. CloudNeuron wraps your Python/Node.js handlers in high-performance micro-containers that scale to zero.

### 👁️ AI-Driven Observability
Statistical anomaly detection (Z-Score) and generative AI (Gemini / OpenAI / Grok) for **automated diagnostics** and **root cause analysis**.

### 🔄 GitHub Integration & PR Previews
- **Auto-Deploy**: Push to `main` → production deploy
- **Preview Environments**: Every PR gets an ephemeral environment

### 🔑 OAuth Social Login
- **GitHub & Google**: One-click sign-in for your users
- **Admin UI**: Configure credentials directly from the Settings page
- **Management Command**: `python manage.py setup_social_apps` for automated setup
- **Secure Callback Flow**: OAuth callback exchanges authenticated session for API token via `/api/v1/auth/session-token/` (no token in URL query)

### 🛡️ Enterprise Reliability
- **Disaster Recovery**: Automated backups (RTO < 15 min)
- **Health Monitoring**: `/health` with DB + Redis validation
- **80%+ Test Coverage** on critical security paths

### 🌍 Multi-Cloud & Hybrid
Works on AWS, Azure, GCP, or bare metal. Air-gapped deployments supported.

### ⚙️ Full Config UI
Every platform setting is visible in the Settings page — **8 tabs** covering Profile, Alerts, Security, Cloud Providers, AI, OAuth, Infrastructure, and System config. Secrets are never exposed.

---

## 📦 Installation

### Quick Install (Fresh Ubuntu VPS)

```bash
curl -fsSL https://raw.githubusercontent.com/SMSLYCLOUD/smsly-hosting/main/install.sh -o /tmp/install.sh
sudo bash /tmp/install.sh
```

The installer prompts you to choose:
1. **IP Mode** — Quick start at `http://YOUR_IP` (no domain needed)
2. **SSL Mode** — Production-ready at `https://cloud.smsly.cloud` (requires DNS A record)

> **Note:** Do NOT pipe directly from `curl` (`curl ... | bash`). The installer requires interactive input unless you pre-seed SSL env vars.

### What the Installer Does

| Step | Description |
|------|-------------|
| 1. Pre-flight | Checks OS, root access, system resources |
| 2. Dependencies | Installs Docker, Python, required packages |
| 3. Configuration | Generates Fernet keys, DB passwords, Redis auth, HMAC secrets |
| 4. Deployment | Builds and starts all containers (backend, frontend, celery, DB, Redis, nginx) |
| 5. Database | Waits for PostgreSQL, syncs passwords, runs Django migrations |
| 6. Admin | Creates admin superuser (credentials saved to `/opt/smsly-hosting/.credentials`) |
| 7. Reverse Proxy | Installs and configures Caddy (HTTP or HTTPS with auto-SSL) |
| 8. Memory Hardening | Adds swap/sysctl tuning + OOM protection for critical containers |
| 9. Verification | Health checks, proxy validation, container status report |

### Update an Existing Installation

```bash
cd /opt/smsly-hosting

# Full update (frontend + backend)
sudo bash install.sh --update

# Frontend only
sudo bash install.sh --update-frontend

# Backend only (includes migrations)
sudo bash install.sh --update-backend
```

### Wipe Existing Installation (New VPS Reset)

```bash
cd /opt/smsly-hosting
sudo bash install.sh --wipe
```

This removes the existing SMSLY installation directory and SMSLY Docker containers, volumes, and networks.
For non-interactive automation, use: `FORCE_WIPE=1 sudo bash install.sh --wipe`

### Manual Deployment

```bash
# 1. Clone
git clone https://github.com/SMSLYCLOUD/smsly-hosting.git /opt/smsly-hosting
cd /opt/smsly-hosting

# 2. Configure
cp .env.example .env   # Edit with your secrets and domain settings

# 3. Deploy
docker compose -f docker-compose.prod.yml up -d --build

# 4. Initialize
docker compose -f docker-compose.prod.yml exec backend python manage.py migrate
docker compose -f docker-compose.prod.yml exec backend python manage.py createsuperuser

# 5. Verify
curl http://localhost:8090/health
```

---

## 🛠️ Tech Stack

| Layer | Technology |
|-------|------------|
| **Backend** | Django 5.x, Python 3.11, Celery, Redis |
| **Frontend** | Next.js 15 (TypeScript), Tailwind CSS v4 |
| **Database** | PostgreSQL 16 |
| **Orchestration** | Docker Compose |
| **SSL/Proxy** | Caddy (automatic Let's Encrypt) |
| **Builder** | Nixpacks (auto-detect buildpacks) |
| **Internal Proxy** | Nginx (routes `/api` → backend, `/` → frontend) |
| **Auth** | django-allauth (GitHub, Google OAuth) |

---

## 🏗️ Architecture

```
                    ┌─────────────────────────┐
  Internet ───────▶ │  Caddy (:80 / :443)     │
                    │  (SSL termination)       │
                    └──────────┬──────────────┘
                               │
                    ┌──────────▼──────────────┐
                    │  Nginx (:8090)           │
                    │  /api  → backend:8000    │
                    │  /     → frontend:3000   │
                    └──────────┬──────────────┘
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
```

---

## 📂 Project Structure

```
smsly-hosting/
├── backend/                  # Django API + Celery tasks
│   ├── apps/                 # Django apps (deployments, cloud, teams, billing, intelligence)
│   ├── config/               # Django settings, WSGI, URLs
│   ├── Dockerfile
│   └── entrypoint.sh
├── frontend/                 # Next.js 15 frontend
│   ├── src/app/              # App Router pages
│   ├── src/components/       # React components (ui, settings, layout, ai)
│   └── Dockerfile
├── docs/                     # Documentation & assets
├── docker-compose.prod.yml   # Production stack
├── nginx.conf                # Internal routing
├── install.sh                # Universal installer (v3.0)
└── .env                      # Generated secrets (chmod 600)
```

---

## 🔧 Configuration

All configuration is stored in `.env` (generated by the installer):

| Variable | Description |
|----------|-------------|
| `SECRET_KEY` | Django secret key (50 chars) |
| `FIELD_ENCRYPTION_KEY` | Fernet key for encrypting sensitive DB fields |
| `POSTGRES_PASSWORD` | Database authentication |
| `REDIS_PASSWORD` | Redis authentication |
| `REDIS_URL` | Redis connection URI (must include password in production) |
| `CELERY_BROKER_URL` | Celery broker URI (Redis with auth) |
| `GATEWAY_SECRET` | Inter-service HMAC authentication |
| `GITHUB_WEBHOOK_SECRET` | GitHub webhook signature secret (required in production) |
| `DOMAIN` | Your domain or public IP |
| `USE_SSL` | `true` for HTTPS mode, `false` for HTTP |
| `ALLOWED_HOSTS` | Django allowed hosts (auto-populated) |
| `CSRF_TRUSTED_ORIGINS` | CSRF protection (auto-populated) |
| `GITHUB_CLIENT_ID` | OAuth: GitHub app Client ID |
| `GITHUB_CLIENT_SECRET` | OAuth: GitHub app Client Secret |
| `GOOGLE_CLIENT_ID` | OAuth: Google app Client ID |
| `GOOGLE_CLIENT_SECRET` | OAuth: Google app Client Secret |

> **All configuration is also visible in the Settings UI** (Settings → System / Infra / OAuth tabs). Secrets are displayed as "Set" / "Not set" only.

---

## 📚 Documentation

- [Features Overview](docs/FEATURES.md)
- [Production Deployment Guide](PRODUCTION_DEPLOYMENT.md)
- [Operations Runbook](RUNBOOK.md) — Disaster recovery, backups, monitoring
- [Security Audit](docs/SECURITY_AUDIT.md)
- [Global Deployment Guide](docs/GLOBAL_DEPLOY.md)
- [Infrastructure Audit](audit_reports/INFRASTRUCTURE_AUDIT.md)
- [QA Report](QA_REPORT.md)

---

## 🤝 Contributing

We welcome contributors! See [CONTRIBUTING.md](CONTRIBUTING.md) to get started.

---

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.

---

<p align="center">
  <strong>CloudNeuron</strong> by <a href="https://github.com/SMSLYCLOUD">SMSLY</a><br />
  <em>Deploy anything. Own everything.</em>
</p>
