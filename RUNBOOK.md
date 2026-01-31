# SMSLY Hosting Operations Runbook

## Quick Reference

### Service URLs

- **Dashboard**: `https://${DOMAIN}/`
- **Admin**: `https://${DOMAIN}/admin/`
- **API**: `https://${DOMAIN}/api/v1/`
- **Health**: `https://${DOMAIN}/health/`
- **Grafana**: `https://${DOMAIN}:3001/`

### Container Names

| Service | Container | Port |
|---------|-----------|------|
| Backend | smsly-backend | 8000 |
| Frontend | smsly-frontend | 3000 |
| Database | smsly-postgres | 5432 |
| Redis | smsly-redis | 6379 |
| Nginx | smsly-nginx | 80/443 |
| Worker | smsly-celery-worker | - |
| Beat | smsly-celery-beat | - |

---

## Common Operations

### View Logs

```bash
# All services
docker-compose -f docker-compose.prod.yml logs -f

# Specific service
docker logs -f smsly-backend --tail 100

# Filter errors
docker logs smsly-backend 2>&1 | grep -i error
```

### Restart Services

```bash
# Single service
docker restart smsly-backend

# All services
docker-compose -f docker-compose.prod.yml restart

# Full rebuild
docker-compose -f docker-compose.prod.yml up -d --build
```

### Database Operations

```bash
# Access PostgreSQL
docker exec -it smsly-postgres psql -U smsly_admin -d smsly_hosting

# Run migrations
docker exec -it smsly-backend python manage.py migrate

# Create backup
/opt/smsly-hosting/scripts/backup.sh

# Restore backup
gunzip -c /opt/smsly-hosting/backups/smsly_hosting_YYYYMMDD.sql.gz | \
  docker exec -i smsly-postgres psql -U smsly_admin -d smsly_hosting
```

### Django Shell

```bash
docker exec -it smsly-backend python manage.py shell
```

---

## Troubleshooting

### Service Won't Start

1. Check logs: `docker logs smsly-backend`
2. Verify .env file exists
3. Check disk space: `df -h`
4. Check memory: `free -m`

### Database Connection Failed

1. Verify PostgreSQL is running: `docker ps | grep postgres`
2. Check connectivity: `docker exec smsly-backend pg_isready -h smsly-postgres`
3. Review DATABASE_URL in .env

### High Memory Usage

1. Check container stats: `docker stats`
2. Restart memory-heavy containers
3. Review Celery worker concurrency

### SSL Certificate Expired

```bash
certbot renew --nginx
docker restart smsly-nginx
```

### Build Failures

1. Check Nixpacks logs in deployment detail
2. Verify source code access
3. Check registry availability
4. Review Trivy scan results

---

## Monitoring Alerts

### Critical (Immediate Action)

- Database disconnected
- Redis unavailable
- Health check failing
- SSL cert < 7 days

### Warning (Investigate)

- Memory > 80%
- Disk > 85%
- Error rate > 1%
- Response time > 2s

### Info (Monitor)

- Deployment completed
- User created
- Service scaled

---

## Emergency Procedures

### Full System Restore

```bash
cd /opt/smsly-hosting
docker-compose -f docker-compose.prod.yml down
# Restore latest backup
gunzip -c /opt/smsly-hosting/backups/latest.sql.gz | \
  docker exec -i smsly-postgres psql -U smsly_admin -d smsly_hosting
docker-compose -f docker-compose.prod.yml up -d
```

### Rollback Deployment

The AI remediator handles this automatically for crash loops.
Manual rollback:

```bash
docker exec -it smsly-backend python manage.py shell <<EOF
from apps.deployments.models import Deployment
d = Deployment.objects.filter(service__name='SERVICE_NAME', status='ACTIVE').first()
# Trigger redeploy with last good commit
EOF
```

---

## Contact

- **Status Page**: status.smsly.cloud
- **Incident Response**: <ops@smsly.cloud>
