# SMSLY Hosting Production Deployment Guide

## Prerequisites

Before deploying to production, ensure you have:

- [ ] Domain name pointing to your server (A record)
- [ ] Server with Ubuntu 20.04+ or similar
- [ ] Docker & Docker Compose installed
- [ ] Ports 80, 443, 8090 open in firewall

## 1. Environment Configuration

### Generate Required Secrets

```bash
# SECRET_KEY
python3 -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"

# FIELD_ENCRYPTION_KEY
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# POSTGRES_PASSWORD
openssl rand -base64 32
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

# Domain
DOMAIN=smsly.cloud
ACME_EMAIL=admin@smsly.cloud

# Hosts
ALLOWED_HOSTS=hosting.smsly.cloud
CSRF_TRUSTED_ORIGINS=https://hosting.smsly.cloud
CORS_ALLOWED_ORIGINS=https://hosting.smsly.cloud

# Database
DATABASE_URL=postgres://smsly_admin:<POSTGRES_PASSWORD>@db:5432/smsly_hosting

# Production mode
DEBUG=False
CORS_ALLOW_ALL=False
```

## 2. Deploy with SSL (Traefik)

### Create External Network

```bash
docker network create smsly-proxy
```

### Deploy Services

```bash
# Deploy Traefik (SSL termination)
docker compose -f docker-compose.traefik.yml up -d

# Deploy main application
# NOTE: We use the traefik-adapter.yml to attach the nginx service to the proxy network
docker compose -f docker-compose.prod.yml -f docker-compose.traefik-adapter.yml up -d

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

# Test health endpoint (should return 200 OK)
curl http://localhost:8090/health

# Check SSL is working
curl https://hosting.smsly.cloud/health
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
- [ ] Admin panel accessible at https://hosting.smsly.cloud/admin/
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

## 6. Disaster Recovery

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

## 7. Monitoring & Alerts

### Health Check Monitoring

```bash
# Add to cron (every 5 minutes)
*/5 * * * * curl -f https://hosting.smsly.cloud/health || /usr/local/bin/alert.sh "SMSLY Hosting Down"
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
- **Health**: `https://hosting.smsly.cloud/health`
