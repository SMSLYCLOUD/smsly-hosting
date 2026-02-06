# SMSLY Hosting (Universal PaaS)

[![CI/CD](https://github.com/SMSLYCLOUD/smsly-hosting/actions/workflows/test.yml/badge.svg)](https://github.com/SMSLYCLOUD/smsly-hosting/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Production Ready](https://img.shields.io/badge/production-ready-brightgreen.svg)](https://github.com/SMSLYCLOUD/smsly-hosting)

**The Self-Healing, Multi-Cloud PaaS. 100% Production-Ready.**

SMSLY Hosting is an open-source alternative to Vercel/Railway/Heroku that runs on **your own infrastructure** (AWS, Azure, GCP, or bare metal). Enterprise-grade security, zero-downtime deployments, and automated disaster recovery out of the box.

## 🔐 Production-Ready Security

- ✅ **Fail-Closed Configuration** - No insecure defaults (SECRET_KEY, ALLOWED_HOSTS)
- ✅ **Docker Socket Isolation** - Secured via read-only proxy (no root-equivalent access)
- ✅ **Multi-Tenancy Tested** - Users cannot access each other's resources
- ✅ **SSL/TLS by Default** - Automatic Let's Encrypt certificates via Traefik
- ✅ **Zero-Downtime Deployments** - Health checks enable rolling updates
- ✅ **Webhook Signature Validation** - HMAC-SHA256 for GitHub webhooks

## 🚀 Key Features

### 1. ⚡ Serverless Functions ("Hot Functions")
Deploy code, not containers. SMSLY wraps your Python/Node.js handlers in high-performance micro-containers that scale to zero.

### 2. 👁️ AI-Driven Observability
Statistical anomaly detection (Z-Score) and Generative AI (Gemini) for **automated diagnostics** and **root cause analysis**.

### 3. 🔄 GitHub Integration & PR Previews
- **Auto-Deploy**: Push to `main` deploys to production
- **Preview Environments**: Every PR gets an ephemeral environment

### 4. 🛡️ Enterprise-Grade Reliability
- **Disaster Recovery**: Automated backups with documented recovery procedures (RTO < 15 min)
- **Health Monitoring**: `/health` endpoint with DB + Redis validation
- **Comprehensive Testing**: 80%+ coverage on critical security paths

### 5. 🌍 Multi-Cloud & Hybrid
Works on AWS, Azure, GCP, or bare metal. Air-gapped deployments supported.

## 📦 Installation

### One-Line Production Install (Fresh VPS)

```bash
curl -fsSL https://raw.githubusercontent.com/SMSLYCLOUD/smsly-hosting/main/install.sh | sudo bash
```

### Production Deployment (Manual)

```bash
# 1. Clone repository
git clone https://github.com/SMSLYCLOUD/smsly-hosting.git
cd smsly-hosting

# 2. Configure .env
cp .env.example .env
# Edit .env with your secrets and domain settings

# 3. Deploy
# Option A: IP Mode (http://IP:8090)
docker compose -f docker-compose.prod.yml up -d

# Option B: SSL Mode (https://domain.com)
docker network create smsly-proxy
docker compose -f docker-compose.traefik.yml up -d
docker compose -f docker-compose.prod.yml -f docker-compose.traefik-adapter.yml up -d

# 4. Initialize
docker compose -f docker-compose.prod.yml exec backend python manage.py migrate
docker compose -f docker-compose.prod.yml exec backend python manage.py createsuperuser

# 5. Verify
curl http://localhost:8090/health
```

See [PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md) for complete instructions.

### Local Development

```bash
docker-compose -f docker-compose.prod.yml up -d
# Access: http://localhost:8090
```

### Production (Kubernetes/Helm)

```bash
helm install smsly-hosting ./charts/smsly-hosting --namespace smsly --create-namespace
```

## 🛠️ Tech Stack

- **Backend**: Django (Python 3.12), Celery, Redis
- **Frontend**: Next.js 14 (TypeScript), Tailwind CSS
- **Orchestration**: Docker / Kubernetes
- **Database**: PostgreSQL 16
- **SSL**: Traefik + Let's Encrypt
- **Monitoring**: Prometheus, Grafana, Loki (optional)

## 📚 Documentation

- [Production Deployment Guide](PRODUCTION_DEPLOYMENT.md)
- [Operations Runbook](RUNBOOK.md) - Disaster recovery, backups, monitoring
- [Contributing Guidelines](CONTRIBUTING.md)

## 🤝 Contributing

We welcome global contributors! See [CONTRIBUTING.md](CONTRIBUTING.md) to get started.
