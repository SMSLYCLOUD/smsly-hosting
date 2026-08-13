# Trulay Grid Production Deployment Guide

## Prerequisites

Before deploying to production, ensure you have:

- [ ] Domain name pointing to your server (A record)
- [ ] Server with Ubuntu 20.04+ or similar
- [ ] Docker & Docker Compose installed
- [ ] Ports **80, 443** open in firewall

> **Note:** Port **8090** was the legacy nginx+Caddy bridge port. It is **no longer used** — Caddy now binds 80/443 directly and routes to `backend:8000`. The active port list is just 80 + 443.

## DNS Configuration

1. Create an `A` record in your DNS provider (example for `grid.example.com`):

```text
Type: A
Name: grid
Value: <YOUR_SERVER_IP>
TTL: 300
```

2. Wait for propagation and verify:

```bash
dig +short grid.example.com
```

## 1. Environment Configuration

### Generate Required Secrets

```bash
# SECRET_KEY
python3 -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"

# FIELD_ENCRYPTION_KEY
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# POSTGRES_PASSWORD
openssl rand -base64 32

# REDIS_PASSWORD
openssl rand -base64 32

# GITHUB_WEBHOOK_SECRET
openssl rand -hex 32
```

### Create `.env` File

```bash
cd /opt/smsly-hosting
cp .env.example .env
nano .env
```

**Required variables:**

```bash
# Security (REQUIRED - NO DEFAULTS)
SECRET_KEY=<generated-secret-key>
FIELD_ENCRYPTION_KEY=<generated-encryption-key>
POSTGRES_PASSWORD=<generated-db-password>
REDIS_PASSWORD=<generated-redis-password>
GITHUB_WEBHOOK_SECRET=<generated-webhook-secret>

# Domain (set to the dashboard hostname)
DOMAIN=grid.example.com
# Optional: override if your frontend origin differs from https://DOMAIN
# SITE_URL=https://grid.example.com
ACME_EMAIL=admin@example.com

# Hosts (keep these in sync with DOMAIN/SITE_URL)
ALLOWED_HOSTS=grid.example.com
CSRF_TRUSTED_ORIGINS=https://grid.example.com
CORS_ALLOWED_ORIGINS=https://grid.example.com

# Database
DATABASE_URL=postgres://smsly_admin:<POSTGRES_PASSWORD>@db:5432/smsly_hosting

# Redis / Celery Broker (passwords required in production)
REDIS_URL=redis://:<REDIS_PASSWORD>@redis:6379/0
CELERY_BROKER_URL=amqp://smsly_user:<RABBITMQ_PASSWORD>@rabbitmq:5672//

# Production mode
DEBUG=False
CORS_ALLOW_ALL=False
```

## 2. Deploy with SSL (Recommended: Installer + Caddy)

The universal installer provisions **Caddy** and configures automatic Let's Encrypt SSL.

```bash
curl -fsSL https://raw.githubusercontent.com/SMSLYCLOUD/smsly-hosting/main/backend/install.sh -o /tmp/install.sh
sudo USE_SSL=true DOMAIN=grid.example.com ACME_EMAIL=admin@example.com bash /tmp/install.sh
```

Admin credentials are written to: `/opt/smsly-hosting/.credentials`

### Alternative: Traefik (Optional)

### Traefik: Create External Network

```bash
docker network create smsly-proxy
```

### Production Compose: Deploy Services

```bash
# docker-compose.prod.yml already includes traefik and socket-proxy.
# Do not stack docker-compose.traefik.yml or docker-compose.socket-proxy.yml
# on top of it, or Compose will reject the config due to duplicate services.
docker compose -f docker-compose.prod.yml up -d

# Run database migrations
docker compose -f docker-compose.prod.yml exec backend python manage.py migrate

# Create superuser
docker compose -f docker-compose.prod.yml exec backend python manage.py createsuperuser

# Collect static files
docker compose -f docker-compose.prod.yml exec backend python manage.py collectstatic --noinput
```

### Verify Health

```bash
# Check all services are running
docker compose -f docker-compose.prod.yml ps

# Test health endpoint (Caddy listens on host :80 and routes /health → backend:8000)
curl http://localhost/health

# Check SSL is working
curl https://grid.example.com/health
```

## 3. Enable Monitoring (Optional)

```bash
# Deploy observability stack
docker compose -f docker-compose.observability.yml up -d

# Access dashboards:
# - Prometheus: http://localhost:9090
# - Grafana: http://localhost:3001 (admin/admin)
# - Loki: http://localhost:3100
```

## 4. Post-Deployment Checklist

- [ ] `/health` endpoint returns 200 OK
- [ ] SSL certificate issued by Let's Encrypt
- [ ] Admin panel accessible at https://grid.example.com/admin/
- [ ] Test deployment creation via API
- [ ] Verify Docker socket proxy is working (no direct socket mount)
- [ ] Check logs: `docker compose -f docker-compose.prod.yml logs -f`

## 5. Zero-Downtime Updates

```bash
# Pull latest code
cd /opt/smsly-hosting
git pull origin main

# Rebuild and restart (zero downtime with healthchecks)
docker compose -f docker-compose.prod.yml up -d --build

# Run migrations
docker compose -f docker-compose.prod.yml exec backend python manage.py migrate
```

## 6. Full Reset (Prepare for New VPS)

```bash
cd /opt/smsly-hosting
sudo bash install.sh --wipe
```

- Deletes `/opt/smsly-hosting`
- Removes Trulay Grid Docker containers, volumes, and networks (many retain legacy `smsly-*` names)
- For automation/non-interactive runs: `FORCE_WIPE=1 sudo bash install.sh --wipe`

## 7. Disaster Recovery

### Backup

```bash
# Database backup (automated via cron - see RUNBOOK.md)
bash scripts/backup.sh

# Manual backup
docker compose -f docker-compose.prod.yml exec db pg_dump -U smsly_admin smsly_hosting | gzip > backup_$(date +%Y%m%d).sql.gz
```

### Restore

```bash
# Stop services
docker compose -f docker-compose.prod.yml stop backend celery

# Restore database
gunzip -c backup_20260206.sql.gz | docker compose -f docker-compose.prod.yml exec -T db psql -U smsly_admin -d smsly_hosting

# Restart services
docker compose -f docker-compose.prod.yml start backend celery
```

## 8. Monitoring & Alerts

### Health Check Monitoring

```bash
# Add to cron (every 5 minutes)
*/5 * * * * curl -f https://grid.example.com/health || /usr/local/bin/alert.sh "Trulay Grid Down"
```

### View Logs

```bash
# All services
docker compose -f docker-compose.prod.yml logs -f

# Specific service
docker compose -f docker-compose.prod.yml logs -f backend

# JSON structured logs (production)
docker compose -f docker-compose.prod.yml logs backend | jq
```

## Security Checklist

-  **Secrets** — All required env vars set (no defaults used)
- ✅ **SSL/TLS** — Let's Encrypt certificate auto-renewal enabled
- ✅ **Docker Socket** — Secured via socket-proxy (read-only, restricted)
- ✅ **Health Checks** — Enabled for zero-downtime deployments
- ✅ **CORS** — Configured for specific origins only
- ✅ **Debug Mode** — Disabled (`DEBUG=False`)
- ✅ **Firewall** — Only ports 80, 443 exposed publicly

## Troubleshooting

See [RUNBOOK.md](RUNBOOK.md) for common issues and solutions.

## Production Support

- **Logs**: `/opt/smsly-hosting/` (mount Loki for centralized logging)
- **Metrics**: Prometheus endpoint at `:8082/metrics`
- **Health**: `https://grid.example.com/health`
