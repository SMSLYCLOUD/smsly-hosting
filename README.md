# SMSLY Hosting v2 - The Hyperscale PaaS

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Next.js 15](https://img.shields.io/badge/Next.js-15-black.svg)](frontend/)
[![Django 5](https://img.shields.io/badge/Django-5.0-green.svg)](backend/)

**The Universal PaaS for Hyperscale Infrastructure.**

SMSLY Hosting v2 is a complete rewrite of the hosting platform, designed to be the "Control Plane for the Internet". It unifies AWS, Azure, GCP, Railway, and Local/K3s deployments into a single, beautiful dashboard with AI-driven observability.

---

## ⚡ Quick Install (Local)

Run this single command on Ubuntu 22.04+ to install Docker, Build Services, and the Platform:

```bash
curl -fsSL https://get.smsly.cloud/v2/install | sudo bash
```

Or manually:

```bash
git clone https://github.com/SMSLYCLOUD/smsly-hosting.git
cd smsly-hosting
sudo ./install-v2.sh
```

---

## 🌐 Accessing Your Platform

Once installed, the services are available at:

| Service | URL | Description |
|---------|-----|-------------|
| **Dashboard** | `http://localhost:3000` | The main UI for managing deployments. |
| **API** | `http://localhost:8000` | The backend REST API. |
| **Traefik** | `http://localhost:8080` | Edge Router Dashboard. |
| **Deployed Apps** | `http://<app-name>.localhost` | Your deployed services (auto-routed). |

> **Note:** If deploying to a remote server, replace `localhost` with your server's IP or Domain.

---

## ✨ Key Features (v2)

### 🌍 Multi-Cloud Orchestration (Universal Adapter)
Manage resources across all major providers from one UI:
- **AWS:** ECS Fargate, Lambda, RDS, S3, IAM, WAF.
- **Azure:** Container Apps, Functions, SQL, Blob Storage, Entra ID.
- **GCP:** Cloud Run, Functions, Cloud SQL, BigQuery.
- **Railway/Vercel:** Direct API integration for zero-config deploys.
- **Local:** Self-hosted Docker/K3s clusters.

### 🧠 Intelligence Engine
- **Predictive Failure Analysis:** AI detects OOM kills and crash loops before they escalate.
- **Auto-Remediation:** "One-click fix" suggestions (e.g., "Scale memory to 1GB").
- **Cost Advisor:** Real-time comparison of AWS vs GCP vs Railway pricing.

### 🛡️ Enterprise Security (Zero Trust)
- **Device Binding:** Access restricted by `X-Device-Fingerprint`.
- **Audit Trail:** Merkle-tree backed immutable logs.
- **Secrets Management:** Fernet/KMS encryption for environment variables.
- **WAF & DDoS Shield:** Integrated protection rules.

### 🚀 Developer Experience
- **Project Canvas:** Visual graph view of your architecture.
- **Web Terminal:** SSH into any container directly from the browser.
- **Real-time Logs:** WebSocket-based live streaming.
- **Marketplace:** One-click deploy for the full SMSLY Ecosystem (30+ services).

---

## 🏗️ Architecture

```mermaid
graph TD
    User[Developer] -->|Next.js 15 Dashboard| UI[Frontend]
    UI -->|API / WebSocket| API[Django 5 Backend]

    subgraph "Control Plane"
        API -->|Task Queue| Celery[Celery Workers]
        API -->|Cache/Broker| Redis
        API -->|State| PG[PostgreSQL]
        API -->|Registry| Reg[Docker Registry :5000]
    end

    subgraph "Compute Fabric"
        Celery -->|Build & Push| Reg
        Celery -->|Deploy| AWS[AWS ECS/Lambda]
        Celery -->|Deploy| Azure[Azure Apps]
        Celery -->|Deploy| Local[Docker / K3s]
    end

    Local -->|Route| Traefik[Traefik Ingress]
```

---

## 📦 Stack

- **Frontend:** Next.js 15 (App Router), React 19, Tailwind CSS, Shadcn UI.
- **Backend:** Django 5.0 (Async), Django Channels, Celery, Redis.
- **Database:** PostgreSQL 16.
- **Infrastructure:** Docker Compose (Local), K3s (Production).

---

## 🤝 Contributing

We welcome contributions! Please read `CONTRIBUTING.md` for details.

1. Fork the repo.
2. Create your feature branch (`git checkout -b feature/amazing-feature`).
3. Commit your changes.
4. Push to the branch.
5. Open a Pull Request.

---

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.
