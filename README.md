# SMSLY Hosting (Universal PaaS)

[![CI/CD](https://github.com/SMSLYCLOUD/smsly-hosting/actions/workflows/test.yml/badge.svg)](https://github.com/SMSLYCLOUD/smsly-hosting/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**The Self-Healing, Multi-Cloud PaaS.**

SMSLY Hosting is an open-source alternative to Vercel/Railway/Heroku that runs on **your own infrastructure** (AWS, Azure, GCP, or bare metal). It brings the developer experience of a managed PaaS to your own cloud account.

## 🚀 Key Features (God-Mode Enabled)

### 1. ⚡ Serverless Functions ("Hot Functions")
Deploy code, not containers. SMSLY wraps your Python/Node.js handlers in high-performance micro-containers that scale to zero.
- **Python**: Flask-wrapped dynamic entrypoints.
- **Node.js**: Express-wrapped dynamic entrypoints.
- **Local/K8s**: Works on Docker Compose and Kubernetes.

### 2. 👁️ AI-Driven Observability
Not just logs. SMSLY uses statistical anomaly detection (Z-Score) and Generative AI (Gemini) to **diagnose failures** and **auto-suggest fixes**.
- **Anomaly Detection**: "CPU usage spiked 3σ above mean."
- **Root Cause Analysis**: "Build failed because `requirements.txt` is missing."

### 3. 🔄 GitHub Integration & PR Previews
- **Auto-Deploy**: Push to `main` deploys to production.
- **Preview Environments**: Opening a Pull Request spins up an ephemeral environment (e.g., `pr-101.smsly-hosting.cloud`). Merging/Closing destroys it.

### 4. 🛡️ High Availability & Self-Healing
- **Control Plane**: Deployable via Helm (`charts/smsly-hosting`) for K8s HA.
- **Database**: Supports Spilo (Patroni + Etcd) for automated PostgreSQL failover.
- **Self-Healing**: Kubernetes Liveness/Readiness probes ensure zero downtime updates.

### 5. 🌍 Multi-Cloud & Hybrid
- **AWS/Azure/GCP**: Native adapters for VMs, Storage, and Load Balancers.
- **Local/On-Prem**: Fully functional "Simulation Mode" for air-gapped or local VPS deployments.
- **Global Latency**: Optimized defaults for low-latency regions worldwide.

## 📦 Installation

### Quick Start (Local Docker)
```bash
./install.sh
```

### Production (Kubernetes/Helm)
```bash
helm install smsly-hosting ./charts/smsly-hosting --namespace smsly --create-namespace
```

## 🛠️ Tech Stack
- **Backend**: Django (Python 3.12), Celery, Redis.
- **Frontend**: Next.js 14 (TypeScript), Tailwind CSS.
- **Orchestration**: Docker / Kubernetes.
- **Database**: PostgreSQL 16.

## 🤝 Contributing
We welcome global contributors! See [CONTRIBUTING.md](CONTRIBUTING.md) to get started.
