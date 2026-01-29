# SMSly Hosting Platform

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/docker-ready-brightgreen.svg)](docker-compose.yml)
[![K3s](https://img.shields.io/badge/kubernetes-K3s-blue.svg)](scripts/install-worker-node.sh)

**The only PaaS with native SMS, Voice, and Video APIs built-in.**

SMSly Hosting is an open-source Platform-as-a-Service (PaaS) that makes deploying applications with communication capabilities effortless. Deploy any app and instantly get access to SMS, Voice calls, and Video APIs without any configuration.

---

## ⚡ Install in 2 Minutes

```bash
curl -fsSL https://get.smsly.cloud/hosting | sudo bash
```

Or with options:

```bash
curl -fsSL https://raw.githubusercontent.com/SMSLYCLOUD/smsly-hosting/main/scripts/quick-install.sh | sudo bash -s -- --auto --domain=hosting.yoursite.com --email=admin@yoursite.com
```

> **That's it!** The script installs Docker, configures SSL, starts all services, and runs migrations automatically.

---

## ✨ Key Features

### 🚀 Deployment

- **Git-based Deployments** - Push to deploy from GitHub, GitLab, Bitbucket
- **Blue/Green Deployments** - Zero-downtime updates
- **Preview Environments** - Every PR gets its own environment
- **Auto-scaling (HPA)** - Scale based on CPU/memory/custom metrics
- **Real-time Build Logs** - Stream logs via WebSocket

### 📱 Native SMSLY Integration

- **Auto-injected API Keys** - Your SMSLY credentials are automatically available
- **Zero Configuration** - Just use `process.env.SMSLY_API_KEY`
- **SMS Failure Alerts** - Get notified instantly when deployments fail
- **Voice Call Alerts** - Critical failures trigger phone calls
- **Built-in SDKs** - SMS, Voice, Video APIs ready to use

### 🛡️ Security

- **Encrypted Environment Variables** - Secrets at rest with Fernet encryption
- **RBAC** - Role-based access control (coming soon)
- **Vulnerability Scanning** - Trivy integration for container scanning
- **Enterprise Compliance** - HIPAA, GDPR, SOC2 profiles
- **Rate Limiting** - Built-in API throttling

### 🔧 Developer Experience

- **One-click Add-ons** - PostgreSQL, Redis, MySQL, MongoDB with a single click
- **CronJobs** - Scheduled tasks via Kubernetes CronJobs
- **Persistent Volumes** - Stateful workloads supported
- **Web Terminal** - SSH into running containers
- **AI Diagnosis** - Automatic failure analysis and fix suggestions
- **Templates** - Start from Django, Express, Next.js templates

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Control Plane                           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │   Frontend   │  │   Backend    │  │     Celery       │  │
│  │  (Next.js)   │  │  (Django)    │  │    (Workers)     │  │
│  └──────────────┘  └──────────────┘  └──────────────────┘  │
│           │                │                  │             │
│           ▼                ▼                  ▼             │
│  ┌────────────────────────────────────────────────────┐    │
│  │          PostgreSQL  │  Redis  │  Docker           │    │
│  └────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼ (K8s API)
┌─────────────────────────────────────────────────────────────┐
│                      Worker Nodes (K3s)                      │
│  ┌────────┐  ┌────────┐  ┌────────┐  ┌────────┐           │
│  │ App A  │  │ App B  │  │ App C  │  │  ...   │           │
│  └────────┘  └────────┘  └────────┘  └────────┘           │
└─────────────────────────────────────────────────────────────┘
```

---

## 🚀 Manual Installation

### Prerequisites

- Ubuntu 22.04+ (or compatible Linux)
- 4GB RAM minimum (8GB recommended)
- Docker installed
- Domain name pointed to your server

### Installation

```bash
# Clone the repository
git clone https://github.com/SMSLYCLOUD/smsly-hosting.git
cd smsly-hosting

# Run the installer (as root)
sudo ./scripts/quick-install.sh --domain=yourdomain.com --email=you@email.com
```

The installer will:

1. Install Docker and dependencies
2. Configure firewall and security
3. Set up Nginx reverse proxy with SSL
4. Generate encryption keys
5. Start all services
6. Run database migrations

---

## 📦 Development Setup

```bash
# Clone the repo
git clone https://github.com/SMSLYCLOUD/smsly-hosting.git
cd smsly-hosting

# Copy environment template
cp .env.example .env

# Generate secrets
python -c "from django.core.management.utils import get_random_secret_key; print(f'SECRET_KEY={get_random_secret_key()}')"
python -c "from cryptography.fernet import Fernet; print(f'FIELD_ENCRYPTION_KEY={Fernet.generate_key().decode()}')"

# Start services
docker compose up -d

# Run migrations
docker compose exec backend python manage.py migrate

# Create superuser
docker compose exec backend python manage.py createsuperuser
```

Access the dashboard at `http://localhost:3000`

---

## 📚 API Reference

### Services

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/services/` | GET | List all services |
| `/api/v1/services/` | POST | Create a new service |
| `/api/v1/services/{id}/deploy/` | POST | Trigger deployment |
| `/api/v1/services/{id}/env-vars/` | GET/POST | Manage env vars |

### Deployments

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/deployments/` | GET | List deployments |
| `/api/v1/deployments/{id}/` | GET | Deployment details |
| `/api/v1/deployments/{id}/logs/` | GET | Build logs |
| `/api/v1/deployments/{id}/rollback/` | POST | Rollback |

### Add-ons

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/addons/` | GET/POST | Manage add-ons (Postgres, Redis) |

### Templates

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/templates/` | GET | List starter templates |

---

## 🔐 Environment Variables

See [.env.example](.env.example) for all available options.

### Required

| Variable | Description |
|----------|-------------|
| `SECRET_KEY` | Django secret key |
| `FIELD_ENCRYPTION_KEY` | Fernet key for encrypting secrets |

### SMSLY Integration

| Variable | Description |
|----------|-------------|
| `SMSLY_INTERNAL_API_KEY` | Service-to-service auth key |
| `ALERT_PHONE_NUMBER` | Phone for SMS alerts (E.164) |
| `CRITICAL_ALERT_PHONE` | Phone for voice call alerts |

---

## 🐳 Docker Services

| Service | Port | Description |
|---------|------|-------------|
| `backend` | 8000 | Django REST API |
| `frontend` | 3000 | Next.js Dashboard |
| `celery` | - | Background task worker |
| `celery-beat` | - | Scheduled task scheduler |
| `db` | 5432 | PostgreSQL database |
| `redis` | 6379 | Redis cache/broker |

---

## 🔧 Worker Node Setup

For running user workloads, set up a separate K3s worker node:

```bash
# On a separate VPS (8GB+ RAM recommended)
sudo ./scripts/install-worker-node.sh
```

Copy the generated kubeconfig to the Control Plane and restart services.

---

## 📈 Monitoring

- **Prometheus Metrics**: Available at `/api/v1/metrics/prometheus/`
- **JSON Logging**: Structured logs for easy parsing
- **Health Checks**: K8s liveness/readiness probes

---

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

---

## 📄 License

MIT License - see [LICENSE](LICENSE) for details.

---

## 🆘 Support

- **Documentation**: [docs.smsly.cloud](https://docs.smsly.cloud)
- **Issues**: [GitHub Issues](https://github.com/SMSLYCLOUD/smsly-hosting/issues)
- **Community**: [Discord](https://discord.gg/smsly)

---

**Built with ❤️ by the SMSLY Team**
