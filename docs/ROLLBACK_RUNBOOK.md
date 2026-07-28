# Rollback Runbook

## When to Rollback
- Deployment causes 5xx errors
- Database migration breaks queries
- Service health checks failing after update
- Performance degradation post-update
- Celery workers crash-looping after deploy
- Redis connection errors after config change
- Memory leaks or OOM kills observed

## Automated Rollback (safe-update.sh)
The update script creates automatic rollback points:
- Git commit hash (stored in `.rollback-point`)
- Database dump (`backups/smsly_*.dump`)
- Redis RDB snapshot (`backups/redis_*.rdb`)
- Docker image tags (`rollback-safe`)

### Quick Rollback
```bash
cd /opt/smsly-hosting
bash scripts/safe-update.sh --rollback
```

This will:
1. Stop all application services
2. Restore the database from the pre-update dump
3. Checkout the previous git commit
4. Rebuild and restart containers from the old image
5. Verify health checks pass

## Manual Rollback Steps

### 1. Code Rollback
```bash
cd /opt/smsly-hosting

# Find last known good commit
git log --oneline -10

# Check what changed in the bad commit
git diff <bad-commit> <good-commit> --stat

# Revert to the good commit
git checkout <good-commit>

# If you need to create a rollback branch
git checkout -b rollback/<date>
```

### 2. Database Rollback
```bash
# List available backups (sorted by date, newest first)
ls -lhtr /opt/smsly-hosting/backups/smsly_*.dump | tail -5

# Restore database (deletes existing data first)
pg_restore -U smsly_admin -d smsly -c --if-exists \
  /opt/smsly-hosting/backups/smsly_YYYYMMDD.dump

# Verify restore succeeded
psql -U smsly_admin -d smsly -c "SELECT count(*) FROM django_migrations;"
```

### 3. Environment Rollback
```bash
# Check if a backup exists
ls -la /opt/smsly-hosting/.env.backup

# Restore environment file
cp /opt/smsly-hosting/.env.backup /opt/smsly-hosting/.env

# If no backup, diff current vs production
diff /opt/smsly-hosting/.env /opt/smsly-hosting/.env.production
```

### 4. Docker Image Rollback
```bash
# List tagged images
docker images --format "{{.Repository}}:{{.Tag}}\t{{.CreatedAt}}" | grep smsly

# Revert to previous image
docker tag smsly-backend:rollback-safe smsly-backend:latest
docker tag smsly-celery:rollback-safe smsly-celery:latest

# If rollback-safe tag is missing, find the last working image
docker images --format "{{.Repository}}:{{.Tag}}\t{{.ID}}" | grep smsly-backend
docker tag smsly-backend:<image-id> smsly-backend:latest
```

### 5. Restart Services
```bash
cd /opt/smsly-hosting

# Restart in dependency order
docker compose up -d --force-recreate backend
sleep 10
docker compose up -d --force-recreate celery-worker
docker compose up -d --force-recreate celery-beat
```

## Verification
```bash
# Check all services are running
docker compose ps

# Check backend health
curl -f http://localhost:8089/health/
curl -f http://localhost:8089/api/v1/status/

# Check for errors in logs (last 50 lines)
docker compose logs --tail=50 backend
docker compose logs --tail=50 celery-worker
docker compose logs --tail=50 celery-beat

# Check database connectivity
docker compose exec backend python manage.py dbshell -c "SELECT 1;"

# Check Redis connectivity
docker compose exec redis redis-cli ping
```

## Partial Rollback

### Database Only (code is fine)
1. Stop celery beat: `docker compose stop celery-beat`
2. Stop celery worker: `docker compose stop celery-worker`
3. Restore database from backup
4. Run migrations: `docker compose exec backend python manage.py migrate`
5. Restart celery worker: `docker compose start celery-worker`
6. Restart celery beat: `docker compose start celery-beat`

### Code Only (database is fine)
1. Checkout the good commit: `git checkout <good-commit>`
2. Rebuild images: `docker compose build backend celery-worker`
3. Restart services: `docker compose up -d --force-recreate backend celery-worker celery-beat`
4. Verify: `docker compose ps`

### Config Only (.env change)
1. Restore `.env` from backup
2. Restart affected services: `docker compose up -d --force-recreate backend celery-worker`
3. No database changes needed

## Rollback Failure Scenarios

### "database is locked" during restore
```bash
# Stop all services first
docker compose stop backend celery-worker celery-beat
# Then restore
pg_restore -U smsly_admin -d smsly -c --if-exists backups/smsly_YYYYMMDD.dump
# Restart
docker compose up -d
```

### Docker build fails after checkout
```bash
# Clear build cache
docker compose build --no-cache backend
# Or use a known-good image tag
docker pull smsly-backend:<known-good-tag>
docker tag smsly-backend:<known-good-tag> smsly-backend:latest
```

### Celery tasks stuck after rollback
```bash
# Purge all queued tasks
docker compose exec celery-worker celery -A config purge -f

# Restart workers
docker compose restart celery-worker celery-beat
```

### Rollback-safe tag missing
```bash
# Find the image from before the update
docker images --format "{{.Repository}}:{{.Tag}}\t{{.CreatedAt}}" | grep smsly
# Tag the most recent one as latest
docker tag smsly-backend:<previous-tag> smsly-backend:latest
```

## Contact
If rollback fails, check:
- `/opt/smsly-hosting/backups/` for available backups
- `/var/log/syslog` for system-level errors
- Docker logs for container-level errors
- `/opt/smsly-hosting/.rollback-point` for the last automated rollback point
- Database connection: `psql -U smsly_admin -d smsly -c "SELECT 1;"`
- Disk space: `df -h /opt/smsly-hosting`
