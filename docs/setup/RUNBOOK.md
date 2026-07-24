# SMSLY (Grid) Operations Runbook

> "Grid" is a legacy code name for SMSLY; both still appear in older scripts and docs.

## Quick Reference

### Service Access
- **Dashboard**: `http://<YOUR_IP>/` (Caddy binds 80/443 directly; port 8090 is the **legacy** nginx bridge and is no longer used)
- **Admin**: `http://<YOUR_IP>/admin/`
- **API**: `http://<YOUR_IP>/api/v1/`

### Installation Details
- **Root Directory**: `/opt/smsly-hosting`
- **Config File**: `/opt/smsly-hosting/.env`
- **Compose File**: `/opt/smsly-hosting/docker-compose.prod.yml`

### Container Names
| Service | Container Service Name |
|---------|------------------------|
| Backend | `backend` |
| Frontend | `frontend` |
| Database | `db` |
| Redis | `redis` |
| Celery | `celery` |

---

## Common Operations

### View Logs

```bash
cd /opt/smsly-hosting

# All services
docker compose -f docker-compose.prod.yml logs -f

# Specific service (e.g., backend)
docker compose -f docker-compose.prod.yml logs -f backend
```

### Restart Services

```bash
cd /opt/smsly-hosting

# Restart all
docker compose -f docker-compose.prod.yml restart

# Restart specific service
docker compose -f docker-compose.prod.yml restart backend
```

### Apply Updates

To update the platform to the latest version:

```bash
cd /opt/smsly-hosting
git pull origin main

# Full rebuild (production compose only)
docker compose -f docker-compose.prod.yml down
docker compose -f docker-compose.prod.yml up -d --build --force-recreate

# Run migrations
docker compose -f docker-compose.prod.yml exec backend python manage.py migrate
```

> **Important**: Always use `down` + `up --force-recreate` instead of just `up --build` to ensure clean state.

### Database Backup

```bash
# Run the backup script
bash /opt/smsly-hosting/scripts/backup.sh
```

Backups are stored in `/opt/smsly-hosting/backups`.

### Restore Database

```bash
# Decompress and pipe to psql
gunzip -c /opt/smsly-hosting/backups/smsly_hosting_YYYYMMDD.sql.gz | \
  docker compose -f docker-compose.prod.yml exec -T db psql -U smsly_admin -d smsly_hosting
```

---

## Troubleshooting

### 502 Bad Gateway After Rebuild

**Symptoms**: Frontend loads fine but all `/api/` requests return 502. Backend container shows healthy in `docker ps`.

**Diagnosis**:
```bash
# Check backend logs — look for "Connection refused" or errors
docker compose -f docker-compose.prod.yml logs backend --tail 20 2>&1 | grep error
```

**Fix**:
```bash
# Recreate everything
docker compose -f docker-compose.prod.yml down
docker compose -f docker-compose.prod.yml up -d --build --force-recreate
```

### 429 Too Many Requests

**Symptoms**: Dashboard actions fail with "Too Many Requests" error.

**Root cause**: Two rate limiting layers — middleware (IP-based) and DRF throttle (user-based).

**Diagnosis**:
```bash
# Check which layer is throttling
docker logs smsly-hosting-backend-1 --tail 50 2>&1 | grep -i "rate limit"
```

**Current limits** (as of 2026-02-18):
- Middleware: 1000 req/60s per IP (anonymous only, skips authenticated users)
- DRF `user` throttle: 5000/hour (~83/min)
- DRF `deployments` throttle: 10/hour
- DRF `deployment_burst` throttle: 3/minute

**Fix**: If legitimate users are being throttled, bump rates in `backend/config/settings.py` → `DEFAULT_THROTTLE_RATES`.

### Dashboard Not Accessible on Host Port 80/443

1.  **Check Containers**:
    ```bash
    docker compose -f docker-compose.prod.yml ps
    ```
    Ensure `backend`, `frontend`, and `caddy` containers are all Up.

2.  **Check Firewall**:
    Ensure ports 80 and 443 are allowed on your VPS firewall (Security Group). Port 8090 is legacy and should **not** be open.
    ```bash
    ufw status
    # If active, allow 80 + 443
    ufw allow 80/tcp
    ufw allow 443/tcp
    ```

3.  **Check Logs**:
    ```bash
    docker compose -f docker-compose.prod.yml logs backend
    ```

### Database Connection Error

1.  **Check Backend Logs**:
    ```bash
    docker compose -f docker-compose.prod.yml logs backend
    ```
    Look for "connection refused" or "password authentication failed".

2.  **Verify .env**:
    Ensure `DATABASE_URL` matches the `POSTGRES_PASSWORD` in `.env`.

### Reset Admin Password

If you lose access to the `admin` account:

```bash
cd /opt/smsly-hosting
docker compose -f docker-compose.prod.yml exec backend python manage.py shell -c "from django.contrib.auth import get_user_model; User = get_user_model(); u = User.objects.get(username='admin'); u.set_password('new_password_here'); u.save()"
```

---

---

## Monitoring

- **Disk Space**: Monitor `df -h` to ensure Docker volumes have space.
- **Memory**: Monitor `docker stats` for high usage by `backend` or `celery`.
- **Health Check**: `curl http://localhost/health` (should return 200 OK with `"status": "healthy"`). Caddy listens on host :80 and proxies `/health` to `backend:8000`.

### Production Monitoring

```bash
# Enable Prometheus + Grafana
docker compose -f docker-compose.observability.yml up -d

# View metrics
open http://localhost:9090  # Prometheus
open http://localhost:3001  # Grafana (admin/admin)

# Prometheus targets
# - Backend metrics: http://backend:8000/metrics
# - Traefik metrics: http://traefik:8082/metrics
```

### Set Up Health Check Monitoring

Add to crontab (`crontab -e`):

```bash
*/5 * * * * curl -f https://cloud.smsly.cloud/health || echo "Grid health check failed" | mail -s "Alert: Grid Down" admin@smsly.cloud
```

---

## Disaster Recovery

### Automated Backups

The platform includes automated database backup:

```bash
# Enable daily backups (runs at 2 AM)
0 2 * * * /opt/smsly-hosting/scripts/backup.sh
```

Backups are stored in `/opt/smsly-hosting/backups/`.

### Manual Backup

```bash
cd /opt/smsly-hosting

# Database + volumes
docker compose -f docker-compose.prod.yml exec db pg_dump -U smsly_admin smsly_hosting | gzip > backups/manual_$(date +%Y%m%d_%H%M%S).sql.gz

# Backup registry data (user Docker images)
tar czf backups/registry_$(date +%Y%m%d).tar.gz -C /var/lib/docker/volumes smsly-hosting_registry_data
```

### Restore from Backup

```bash
# Stop dependent services
docker compose -f docker-compose.prod.yml stop backend celery celery-beat

# Restore database
gunzip -c backups/manual_20260206_140000.sql.gz | \
  docker compose -f docker-compose.prod.yml exec -T db psql -U smsly_admin -d smsly_hosting

# Restart services
docker compose -f docker-compose.prod.yml start backend celery celery-beat

# Verify
docker compose -f docker-compose.prod.yml logs -f backend
```

### Disaster Recovery Scenarios

#### Scenario 1: Database Corruption

1. Stop all services writing to DB:
   ```bash
   docker compose -f docker-compose.prod.yml stop backend celery celery-beat
   ```

2. Restore from latest backup (see above)

3. Verify data integrity:
   ```bash
   docker compose -f docker-compose.prod.yml exec backend python manage.py check
   ```

4. Restart services:
   ```bash
   docker compose -f docker-compose.prod.yml start backend celery celery-beat
   ```

#### Scenario 2: Full Server Failure

1. Provision new server (same specs)

2. Install Docker + Docker Compose

3. Restore from backups:
   ```bash
   # Copy backups to new server
   scp -r backups/ root@new-server:/opt/smsly-hosting/

   # Deploy services
   cd /opt/smsly-hosting
   docker compose -f docker-compose.prod.yml up -d
   
   # Restore database
   gunzip -c backups/latest.sql.gz | docker compose -f docker-compose.prod.yml exec -T db psql -U smsly_admin -d smsly_hosting
   ```

4. Update DNS to point to new server

5. Verify health: `curl https://cloud.smsly.cloud/health`

#### Scenario 3: Rollback After Failed Deployment

```bash
# View deployment history
docker compose -f docker-compose.prod.yml exec backend python manage.py showmigrations

# Rollback to previous git commit
git log --oneline -n 5
git checkout <previous-commit-hash>

# Rebuild and restart
docker compose -f docker-compose.prod.yml up -d --build

# Rollback migrations if needed
docker compose -f docker-compose.prod.yml exec backend python manage.py migrate <app_name> <migration_number>
```

### Recovery Time Objectives (RTO)

- **Database restore**: < 15 minutes (for databases < 10GB)
- **Full server rebuild**: < 1 hour
- **Service restart**: < 5 minutes

### Backup Retention

- **Daily backups**: Kept for 30 days
- **Weekly backups**: Kept for 90 days
- **Monthly backups**: Kept for 1 year

---

## Production Deployment Best Practices

### Pre-Deployment Checklist

- [ ] All tests passing (`cd backend && pytest`)
- [ ] Database migrations reviewed
- [ ] Environment variables validated
- [ ] SSL certificate valid and not expiring soon
- [ ] Disk space available (at least 20% free)

### Zero-Downtime Deployment

```bash
# 1. Pull latest code
git pull origin main

# 2. Rebuild services (health checks ensure zero downtime)
docker compose -f docker-compose.prod.yml up -d --build

# 3. Run migrations (backend remains available)
docker compose -f docker-compose.prod.yml exec backend python manage.py migrate

# 4. Verify health
curl https://cloud.smsly.cloud/health
```

### Rollback Procedure

If deployment fails:

```bash
# Quick rollback
git checkout <previous-working-commit>
docker compose -f docker-compose.prod.yml up -d --build
```

---

## Security Scanning

### Pre-Deployment Security Scan

```bash
# Scan Docker images for vulnerabilities
docker compose -f docker-compose.prod.yml build
docker scan smsly-hosting_backend:latest

# Check for known vulnerabilities in dependencies
docker compose -f docker-compose.prod.yml exec backend pip-audit
```
