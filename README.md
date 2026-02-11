# SMSLY Hosting (Universal PaaS)

[![CI/CD](https://github.com/SMSLYCLOUD/smsly-hosting/actions/workflows/test.yml/badge.svg)](https://github.com/SMSLYCLOUD/smsly-hosting/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Production Ready](https://img.shields.io/badge/production-ready-brightgreen.svg)](https://github.com/SMSLYCLOUD/smsly-hosting)

**The Self-Healing, Multi-Cloud PaaS. 100% Production-Ready.**

SMSLY Hosting is an open-source alternative to Vercel/Railway/Heroku that runs on **your own infrastructure** (AWS, Azure, GCP, or bare metal). Enterprise-grade security, zero-downtime deployments, and automated disaster recovery out of the box.

---

## 🔐 Production-Ready Security

- ✅ **Fail-Closed Configuration** — No insecure defaults (`SECRET_KEY`, `ALLOWED_HOSTS` crash if missing)
- ✅ **Docker Socket Isolation** — Secured via read-only proxy (no root-equivalent access)
- ✅ **Multi-Tenancy Tested** — Users cannot access each other's resources
- ✅ **Automatic SSL/TLS** — Let's Encrypt certificates via Caddy (zero configuration)
- ✅ **Zero-Downtime Deployments** — Health checks enable rolling updates
- ✅ **Webhook Signature Validation** — HMAC-SHA256 for GitHub webhooks
- ✅ **Fernet Encryption** — Sensitive fields encrypted at rest

---

## 🚀 Key Features

### ⚡ Serverless Functions ("Hot Functions")
Deploy code, not containers. SMSLY wraps your Python/Node.js handlers in high-performance micro-containers that scale to zero.

### 👁️ AI-Driven Observability
Statistical anomaly detection (Z-Score) and Generative AI (Gemini/OpenAI/Grok) for **automated diagnostics** and **root cause analysis**.

### 🔄 GitHub Integration & PR Previews
- **Auto-Deploy**: Push to `main` deploys to production
- **Preview Environments**: Every PR gets an ephemeral environment

### 🛡️ Enterprise-Grade Reliability
- **Disaster Recovery**: Automated backups with documented recovery procedures (RTO < 15 min)
- **Health Monitoring**: `/health` endpoint with DB + Redis validation
- **Comprehensive Testing**: 80%+ coverage on critical security paths

### 🌍 Multi-Cloud & Hybrid
Works on AWS, Azure, GCP, or bare metal. Air-gapped deployments supported.

---

## 📦 Installation

### Quick Install (Fresh Ubuntu VPS)

Download and run the installer interactively:

```bash
curl -fsSL https://raw.githubusercontent.com/SMSLYCLOUD/smsly-hosting/main/install.sh -o /tmp/install.sh
sudo bash /tmp/install.sh
```

The installer will prompt you to choose:
1. **IP Mode** — Quick start at `http://YOUR_IP` (no domain needed)
2. **SSL Mode** — Production-ready at `https://your-domain.com` (requires DNS A record)

> **Note:** Do NOT pipe directly from `curl` (`curl ... | bash`). The installer requires interactive input for mode selection, domain, and email configuration.

### What the Installer Does

| Step | Description |
|------|-------------|
| 1. Pre-flight | Checks OS, root access, system resources |
| 2. Dependencies | Installs Docker, Python, required packages. **Stops conflicting services** (nginx, apache2, caddy) |
| 3. Configuration | Generates Fernet keys, DB passwords, Redis auth, HMAC secrets |
| 4. Deployment | Builds and starts all containers (backend, frontend, celery, DB, Redis, nginx) |
| 5. Database | Waits for PostgreSQL, syncs passwords, runs Django migrations |
| 6. Admin | Creates admin superuser (default password: `smslyhosting`) |
| 7. Reverse Proxy | Installs and configures Caddy (HTTP or HTTPS with auto-SSL) |
| 8. Verification | Health checks, container status report |

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
     │   Django/      │ │  Next.js   │ │  Workers +    │
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
│   ├── apps/                 # Django apps (services, deployments, cloud, teams)
│   ├── config/               # Django settings, WSGI, URLs
│   ├── Dockerfile
│   └── entrypoint.sh
├── frontend/                 # Next.js 15 frontend
│   ├── src/app/              # App Router pages
│   ├── src/components/       # React components
│   └── Dockerfile
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
| `GATEWAY_SECRET` | Inter-service HMAC authentication |
| `DOMAIN` | Your domain or public IP |
| `USE_SSL` | `true` for HTTPS mode, `false` for HTTP |
| `ALLOWED_HOSTS` | Django allowed hosts (auto-populated) |
| `CSRF_TRUSTED_ORIGINS` | CSRF protection (auto-populated) |

---

## 📚 Documentation

- [Production Deployment Guide](PRODUCTION_DEPLOYMENT.md)
- [Operations Runbook](RUNBOOK.md) — Disaster recovery, backups, monitoring

---

## 🤝 Contributing

We welcome global contributors! See [CONTRIBUTING.md](CONTRIBUTING.md) to get started.

---

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.
