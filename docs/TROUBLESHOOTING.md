# Troubleshooting Guide

This guide covers common production issues and resolutions when operating
SMSLY Hosting. Each scenario includes symptoms, diagnosis commands, and fixes.

## Table of Contents

1. [Container Won't Start](#1-container-wont-start)
2. [SSL Certificate Not Provisioning](#2-ssl-certificate-not-provisioning)
3. [Database Connection Refused](#3-database-connection-refused)
4. [Redis Connection Failed](#4-redis-connection-failed)
5. [Celery Task Stuck](#5-celery-task-stuck)
6. [WebSocket Connection Failing](#6-websocket-connection-failing)
7. [Build Fails with OOM](#7-build-fails-with-oom)
8. [Domain Not Resolving](#8-domain-not-resolving)
9. [Backup Failing](#9-backup-failing)
10. [Update Fails Mid-Way](#10-update-fails-mid-way)
11. [Container CPU/Memory Spike](#11-container-cpumemory-spike)
12. [Permission Denied on Docker Socket](#12-permission-denied-on-docker-socket)
13. [Migration Fails](#13-migration-fails)
14. [Rate Limiting Too Aggressive](#14-rate-limiting-too-aggressive)
15. [Addon Provisioning Fails](#15-addon-provisioning-fails)
16. [Nixpacks Build Crashes](#16-nixpacks-build-crashes)
17. [GitHub Webhook Failing](#17-github-webhook-failing-401-unauthorized)
18. [OAuth Social Login Fails](#18-oauth-social-login-fails)
19. [Caddy SSL Failing](#19-caddy-ssl-failing)

---

## 1. Container Won't Start

### Symptoms

- `docker compose ps` shows container in `Restarting`, `Exit`, or `Created` state
- Application is unreachable; health checks fail
- `docker logs <container>` shows crash loop or immediate exit

### Diagnosis

```bash
# Check container state and exit code
docker inspect --format='{{.State.Status}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}}' <container>

# View last 50 lines of logs
docker logs --tail=50 <container>

# Check if OOM-killed
docker inspect --format='{{.State.OOMKilled}}' <container>

# Verify resource limits vs actual usage
docker stats --no-stream <container>

# Check if the image exists
docker images | grep <image_name>

# Check dependency health (e.g. backend depends on pgcat, redis, socket-proxy)
docker inspect --format='{{range .State.Health.Log}}{{.ExitCode}} {{.Output}}{{end}}' <container>
```

### Fix

**OOM-killed:** Increase memory limit in `.env` or `docker-compose.prod.yml`:
```bash
# In .env
BACKEND_MEMORY_LIMIT=4G   # default 2G
CELERY_MEMORY_LIMIT=8G    # default 4G
```

**Image missing:** Rebuild:
```bash
docker compose -f docker-compose.prod.yml build --no-cache <service>
docker compose -f docker-compose.prod.yml up -d <service>
```

**Dependency not ready:** Ensure upstream services are healthy first:
```bash
docker compose -f docker-compose.prod.yml ps pgcat redis-primary socket-proxy
```

**Port conflict:** Another process is using the required port:
```bash
ss -tlnp | grep :<port>
# Kill the conflicting process or change the port binding in compose
```

**Volume mount broken:** Bind-mounted host path doesn't exist:
```bash
ls -la /opt/smsly-hosting/caddy-config   # must exist for caddy
ls -la /opt/smsly-hosting/builds          # must exist for backend
mkdir -p /opt/smsly-hosting/caddy-config /opt/smsly-hosting/builds
chown -R 1000:1000 /opt/smsly-hosting/caddy-config /opt/smsly-hosting/builds
```

---

## 2. SSL Certificate Not Provisioning

### Symptoms

- Browser shows `ERR_SSL_PROTOCOL_ERROR` or `NET::ERR_CERT_AUTHORITY_INVALID`
- Caddy logs show ACME challenge failures
- Traefik `acme.json` not being written

### Diagnosis

```bash
# Check if Caddy is running and healthy
docker compose -f docker-compose.prod.yml ps caddy

# Check Caddy logs for ACME errors
docker logs --tail=100 smsly-hosting-caddy-1 2>&1 | grep -i "acme\|certificate\|challenge"

# Verify Caddyfile syntax
docker compose -f docker-compose.prod.yml exec caddy caddy validate --config /etc/caddy/Caddyfile

# Check DNS resolution from inside the container
docker compose -f docker-compose.prod.yml exec caddy nslookup <your-domain>

# Check if Cloudflare token is set (if using Cloudflare DNS)
docker compose -f docker-compose.prod.yml exec caddy env | grep CLOUDFLARE

# Verify acme.json exists and is writable
docker compose -f docker-compose.prod.yml exec caddy ls -la /data/acme.json

# Test ACME challenge manually
curl -v http://<your-domain>/.well-known/acme-challenge/test
```

### Fix

**DNS not propagated:** Wait for DNS propagation (can take 5-30 min). Verify:
```bash
dig +short <your-domain>
# Should return your server IP
```

**Cloudflare token invalid:** Update `CLOUDFLARE_API_TOKEN` in `.env`:
```bash
# The token needs Zone:DNS:Edit and Zone:Zone:Read permissions
# Generate at https://dash.cloudflare.com/profile/api-tokens
```

**Caddyfile syntax error:** Fix the Caddyfile and reload:
```bash
docker compose -f docker-compose.prod.yml exec caddy caddy reload --config /etc/caddy/Caddyfile
```

**acme.json permissions:** Fix ownership:
```bash
docker compose -f docker-compose.prod.yml exec caddy chmod 600 /data/acme.json
```

**Port 80/443 blocked:** Ensure firewall allows HTTP/HTTPS:
```bash
ufw status | grep -E "80|443"
ufw allow 80/tcp
ufw allow 443/tcp
```

---

## 3. Database Connection Refused

### Symptoms

- Backend logs: `connection refused`, `could not connect to server`, `FATAL: no pg_hba.conf entry`
- `pg_isready` returns non-zero exit code
- PgCat healthcheck failing

### Diagnosis

```bash
# Check PostgreSQL container status
docker compose -f docker-compose.prod.yml ps postgres-primary postgres-replica pgcat

# Check PostgreSQL logs
docker logs --tail=50 smsly-postgres-primary 2>&1

# Test direct connection to PostgreSQL
docker exec smsly-postgres-primary pg_isready -U smsly_admin

# Test connection through PgCat
PGPASSWORD=<password> psql -h 127.0.0.1 -p 5432 -U smsly_admin -d smsly_hosting -c "SELECT 1;"

# Check max_connections usage
docker exec smsly-postgres-primary psql -U smsly_admin -d smsly_hosting -c \
  "SELECT count(*) as active, max_conn.setting as max FROM pg_stat_activity, pg_settings max_conn WHERE max_conn.name='max_connections' GROUP BY max_conn.setting;"

# Check PgCat pool status
PGPASSWORD=<pgcat_admin_password> psql -h 127.0.0.1 -p 5432 -U pgcat_admin -d pgbouncer -c "SHOW POOLS;"

# Verify .env credentials match what's running
docker exec smsly-postgres-primary env | grep POSTGRES_PASSWORD
grep POSTGRES_PASSWORD .env
```

### Fix

**Connection pool exhaustion (PgCat):**
```bash
# PgCat pool_size defaults to 100. If max_connections on PG is 200, and
# PgCat opens 100 connections, backend/celery can starve.
# Check PgCat pool stats:
PGPASSWORD=<pgcat_admin_password> psql -h 127.0.0.1 -p 5432 -U pgcat_admin -d pgbouncer -c "SHOW POOLS;"

# Increase PG max_connections (edit docker-compose.prod.yml command):
#   -c max_connections=400
# Or reduce PgCat pool_size in infrastructure/pgcat/pgcat.toml
```

**Password mismatch:** Restart after updating `.env`:
```bash
docker compose -f docker-compose.prod.yml down postgres-primary
docker compose -f docker-compose.prod.yml up -d postgres-primary
```

**pg_hba.conf too restrictive:** The init script sets up replication access. For direct connections, ensure the host is in the allowed range.

**Disk full:** PostgreSQL stops accepting connections when data directory is full:
```bash
df -h /opt/smsly-hosting
# Free space or expand volume
```

---

## 4. Redis Connection Failed

### Symptoms

- Backend/Celery logs: `ConnectionError`, `Could not connect to Redis`
- Session data lost; cache misses
- Sentinel healthcheck failing

### Diagnosis

```bash
# Check Redis containers
docker compose -f docker-compose.prod.yml ps redis-primary redis-replica redis-sentinel-*

# Test Redis directly
docker exec smsly-redis-primary redis-cli -a <password> ping
# Expected: PONG

# Check Redis replication lag
docker exec smsly-redis-primary redis-cli -a <password> info replication | grep -E "master_link|lag"

# Check Sentinel status
docker exec smsly-redis-sentinel-1 redis-cli -p 26379 sentinel master mymaster
docker exec smsly-redis-sentinel-1 redis-cli -p 26379 sentinel get-master-addr-by-name mymaster

# Check Redis memory usage
docker exec smsly-redis-primary redis-cli -a <password> info memory | grep used_memory_human

# Verify REDIS_HOST in .env
grep REDIS_HOST .env
# Should be: redis-primary (not localhost, not 127.0.0.1)
```

### Fix

**Sentinel failover in progress:** Wait 15s for failover (configured as `failover-timeout mymaster 15000`). Check new master:
```bash
docker exec smsly-redis-sentinel-1 redis-cli -p 26379 sentinel get-master-addr-by-name mymaster
```

**Password mismatch:** Ensure `REDIS_PASSWORD` is the same across all Redis and Sentinel containers. Update `.env` and restart:
```bash
docker compose -f docker-compose.prod.yml down redis-primary redis-replica redis-sentinel-1 redis-sentinel-2 redis-sentinel-3
docker compose -f docker-compose.prod.yml up -d redis-primary redis-replica redis-sentinel-1 redis-sentinel-2 redis-sentinel-3
```

**Network isolation:** Redis must be on `smsly-net`:
```bash
docker network inspect smsly-net | grep redis
# If missing, reconnect:
docker network connect smsly-net smsly-redis-primary
```

**Memory limit hit:** Redis evicts keys when `maxmemory` is reached. Increase:
```bash
# In .env
REDIS_MAXMEMORY=800mb   # default 400mb
```

---

## 5. Celery Task Stuck

### Symptoms

- Tasks show `PENDING` state indefinitely
- Deployments hang at "Building..."
- `celery inspect ping` returns no response
- Celery worker healthcheck failing

### Diagnosis

```bash
# Check Celery worker status
docker compose -f docker-compose.prod.yml ps celery celery-fast celery-deploy celery-beat

# Ping workers
docker compose -f docker-compose.prod.yml exec celery celery -A config inspect ping --timeout 10

# Check active tasks
docker compose -f docker-compose.prod.yml exec celery celery -A config inspect active --timeout 10

# Check reserved tasks (tasks being executed)
docker compose -f docker-compose.prod.yml exec celery celery -A config inspect reserved --timeout 10

# Check queue lengths (via RabbitMQ management)
curl -u smsly_user:<password> http://127.0.0.1:15672/api/queues | python3 -m json.tool

# Check Celery logs for errors
docker logs --tail=200 smsly-hosting-celery-1 2>&1 | grep -i "error\|exception\|timeout"

# Check if worker is actually running inside container
docker compose -f docker-compose.prod.yml exec celery ps aux | grep celery
```

### Fix

**Worker crashed:** Restart the specific worker:
```bash
docker compose -f docker-compose.prod.yml up -d --force-recreate celery
```

**Time limit exceeded:** Tasks have `soft_time_limit`. If a task exceeds it, the worker kills it. Check logs for `SoftTimeLimitExceeded` and optimize the task or increase the limit in the task decorator.

**Queue backlog:** Scale up workers or drain stuck messages:
```bash
# Purge all messages in the 'celery' queue (DANGER: drops queued tasks)
docker compose -f docker-compose.prod.yml exec celery celery -A config purge

# Or restart the worker to re-queue unacked messages
docker compose -f docker-compose.prod.yml restart celery
```

**RabbitMQ full:** RabbitMQ memory watermark hit:
```bash
docker exec smsly-hosting-rabbitmq-1 rabbitmq-diagnostics memory_used
docker exec smsly-hosting-rabbitmq-1 rabbitmq-diagnostics status | grep memory
# Increase RABBITMQ_MEMORY_WATERMARK in .env if needed
```

---

## 6. WebSocket Connection Failing

### Symptoms

- Real-time features (chat, notifications) don't connect
- Browser console: `WebSocket connection to 'wss://...' failed`
- Redis channel layer errors in backend logs

### Diagnosis

```bash
# Check WebSocket endpoint
curl -i -N -H "Connection: Upgrade" -H "Upgrade: websocket" -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" http://localhost/ws/

# Check if Redis is accessible (Django Channels uses Redis as channel layer)
docker exec smsly-hosting-backend-1 python -c "
from django.conf import settings
print(settings.CHANNEL_LAYERS)
"

# Check CORS configuration
docker exec smsly-hosting-backend-1 python -c "
from django.conf import settings
print(settings.CORS_ALLOWED_ORIGINS)
"

# Verify auth token expiry (if using token auth for WS)
docker logs --tail=50 smsly-hosting-backend-1 2>&1 | grep -i "websocket\|channel\|redis"
```

### Fix

**Redis channel layer unreachable:** Ensure `REDIS_HOST=redis-primary` in `.env` (not `localhost`). Check Redis is on `smsly-net`.

**CORS blocking WebSocket origin:** Add your domain to `CORS_ALLOWED_ORIGINS` in `.env`:
```bash
CORS_ALLOWED_ORIGINS=https://your-domain.com,https://www.your-domain.com
```

**Auth token expired:** WebSocket connections authenticate on connect. If the JWT token is expired, the connection is rejected. The client must reconnect with a fresh token.

**Traefik not forwarding WebSocket:** Ensure the backend Traefik labels include WebSocket support. The default config already supports it via the Uvicorn worker, but if you've added custom Traefik middleware, verify it doesn't strip `Upgrade` headers.

---

## 7. Build Fails with OOM

### Symptoms

- `docker compose build` fails with exit code 137
- `Killed` in build output
- Nixpacks/Node build process crashes mid-compilation

### Diagnosis

```bash
# Check available memory
free -h

# Check swap
swapon --show

# Check Docker build output for OOM
docker compose -f docker-compose.prod.yml build --progress=plain 2>&1 | tail -50

# Check if any other containers are consuming too much memory
docker stats --no-stream --format "table {{.Name}}\t{{.MemUsage}}"
```

### Fix

**Enable swap (primary fix):**
```bash
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
# Make persistent:
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

**Reduce concurrent builds:**
```bash
# Build one service at a time instead of all at once
docker compose -f docker-compose.prod.yml build backend
docker compose -f docker-compose.prod.yml build frontend
```

**Increase Docker build memory limit** (Docker Desktop / daemon config):
```json
// /etc/docker/daemon.json
{
  "build": {
    "memory": "8g"
  }
}
```

**Optimize Dockerfile:** Use multi-stage builds and minimize layer size. The existing Dockerfiles already use multi-stage — ensure `--no-cache` isn't forcing re-downloads.

---

## 8. Domain Not Resolving

### Symptoms

- `curl -v https://your-domain` times out or connects to wrong IP
- `nslookup your-domain` returns no records or wrong IP
- Site works on IP address but not on domain

### Diagnosis

```bash
# Check DNS from multiple resolvers
dig +short your-domain @8.8.8.8
dig +short your-domain @1.1.1.1
dig +short your-domain @$(hostname -I | awk '{print $1}')

# Check what IP Traefik is serving
docker exec smsly-hosting-traefik-1 wget -qO- http://127.0.0.1:8080/api/http/routers | python3 -m json.tool | grep -A5 "rule"

# Check Caddy config for the domain
docker compose -f docker-compose.prod.yml exec caddy cat /etc/caddy/Caddyfile | grep -A3 your-domain

# Check if the domain is set in the database
docker compose -f docker-compose.prod.yml exec backend python manage.py shell -c "
from apps.deployments.models import PlatformConfig
c = PlatformConfig.load()
print('domain:', c.domain)
"
```

### Fix

**DNS A record missing:** Add an A record at your DNS provider pointing to the server IP. Propagation takes 5-30 minutes.

**Wrong DNS provider:** If using Cloudflare, ensure the proxy (orange cloud) is off for initial setup, or on once SSL is working.

**Caddy/Traefik not configured for domain:** The Caddyfile is auto-generated from the database. Ensure `DOMAIN` is set in `.env` and the platform config:
```bash
# Update domain in the database
docker compose -f docker-compose.prod.yml exec backend python manage.py shell -c "
from apps.deployments.models import PlatformConfig
c = PlatformConfig.load()
c.domain = 'your-domain.com'
c.save()
"
# Reload Caddy to pick up the change
docker compose -f docker-compose.prod.yml exec caddy caddy reload --config /etc/caddy/Caddyfile
```

---

## 9. Backup Failing

### Symptoms

- `/opt/smsly-hosting/backups/` is empty or has 0-byte files
- Cron job `backup.sh` exits with error
- `BACKUP_PASS` not set

### Diagnosis

```bash
# Check if BACKUP_PASS is set
grep BACKUP_PASS .env

# Check if postgres container is running
docker ps | grep postgres-primary

# Run backup.sh manually with debug output
bash -x /opt/smsly-hosting/scripts/backup.sh 2>&1

# Check backup log
tail -20 /opt/smsly-hosting/backups/backup.log

# Check disk space
df -h /opt/smsly-hosting/backups

# Check if pg_dump works inside the container
docker exec smsly-postgres-primary pg_dump -U smsly_admin smsly_hosting | head -5
```

### Fix

**BACKUP_PASS not set:** Add to `.env`:
```bash
BACKUP_PASS="your-strong-backup-password"
```

**Disk full:** Clean old backups or expand disk:
```bash
ls -lah /opt/smsly-hosting/backups/
rm /opt/smsly-hosting/backups/smsly_hosting_20240101_*.sql.gz.enc  # remove old ones
```

**PostgreSQL user permissions:** The backup script tries `smsly_admin`, `smsly`, and `postgres` users. If none have `pg_dump` permission:
```bash
docker exec smsly-postgres-primary psql -U postgres -c \
  "ALTER USER smsly_admin WITH SUPERUSER;"
# Note: Superuser is only needed for backup. Remove after if desired.
```

**Encryption key issue:** The `openssl enc` command requires `BACKUP_PASS`. Verify it's exported:
```bash
export BACKUP_PASS="..."
bash /opt/smsly-hosting/scripts/backup.sh
```

---

## 10. Update Fails Mid-Way

### Symptoms

- `install.sh --update` exits with error
- Some containers are updated, others are on old versions
- Platform is partially functional

### Diagnosis

```bash
# Check update log
tail -100 /var/log/smsly-install.log

# Check safe-update snapshot
cat /opt/smsly-hosting/.update-safe-snapshot

# Check which containers are running vs expected
docker compose -f docker-compose.prod.yml ps

# Check if rollback is available
ls -la /opt/smsly-hosting/.update-backups/
```

### Fix

**Use the safe-update recovery mechanism:**
```bash
# The safe-update.sh script has built-in rollback.
# If the update failed, run:
bash /opt/smsly-hosting/scripts/safe-update.sh
# It will detect the failure and auto-rollback if post-verify fails.
```

**Manual rollback:**
```bash
# 1. Stop all app containers
docker compose -f docker-compose.prod.yml stop backend frontend celery celery-deploy celery-fast celery-beat

# 2. Revert to previous git commit
cd /opt/smsly-hosting
git log --oneline -5   # find the last good commit
git reset --hard <commit-hash>

# 3. Restore .env if changed
cp /opt/smsly-hosting/.update-backups/pre-update.env /opt/smsly-hosting/.env

# 4. Restore DB if migration was applied
docker exec -i smsly-postgres-primary psql -U smsly_admin smsly_hosting < /opt/smsly-hosting/.update-backups/pre-update-*.sql

# 5. Rebuild and restart with old images
docker compose -f docker-compose.prod.yml up -d --force-recreate backend frontend celery celery-deploy celery-fast celery-beat
```

**Clear stale lock file:** If another install is "running":
```bash
rm -f /tmp/smsly-install.lock
```

---

## 11. Container CPU/Memory Spike

### Symptoms

- Server becomes unresponsive or very slow
- `docker stats` shows container at 100% CPU or near memory limit
- OOM kills in `dmesg` or `journalctl -k`

### Diagnosis

```bash
# Real-time resource usage
docker stats --no-stream

# Check cAdvisor metrics (if observability stack is deployed)
curl -s http://localhost:8080/api/v1.3/docker | python3 -m json.tool | grep -E "cpu|memory" | head -20

# Check which container is using most resources
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}"

# Check for OOM kills
dmesg | grep -i "oom\|killed process"
journalctl -k | grep -i "oom\|killed process"

# Check Grafana dashboards (if deployed)
# http://localhost:3001 → Dashboards → Docker
```

### Fix

**Identify and kill the offending process:**
```bash
# If a Celery worker is spinning, restart it
docker compose -f docker-compose.prod.yml restart celery

# If backend is using too much memory, restart it
docker compose -f docker-compose.prod.yml restart backend
```

**Adjust resource limits:**
```bash
# In .env, increase limits for the offending service
CELERY_MEMORY_LIMIT=8G        # was 4G
BACKEND_MEMORY_LIMIT=4G       # was 2G
GUNICORN_WORKERS=2            # reduce from 4 if memory-bound
```

**Check for infinite loops or unbounded queries:** Review Celery task logs for tasks that run repeatedly or fetch unbounded result sets.

**Scale down workers:**
```bash
# Reduce autoscale max
CELERY_AUTOSCALE=6,2   # was 12,4
```

---

## 12. Permission Denied on Docker Socket

### Symptoms

- Backend/Celery logs: `permission denied while connecting to Docker daemon socket`
- Cannot create/manage containers from within the platform
- Socket-proxy healthcheck failing

### Diagnosis

```bash
# Check socket-proxy status
docker compose -f docker-compose.prod.yml ps socket-proxy

# Check Docker socket permissions
ls -la /var/run/docker.sock
# Should be: srw-rw---- root docker

# Check which user is inside the backend container
docker exec smsly-hosting-backend-1 id

# Check if socket-proxy can reach the socket
docker compose -f docker-compose.prod.yml exec socket-proxy wget -qO- http://127.0.0.1:2375/version

# Check the DOCKER_HOST env var in backend/celery
docker compose -f docker-compose.prod.yml exec backend env | grep DOCKER_HOST
# Should be: tcp://socket-proxy:2375
```

### Fix

**Socket-proxy not running:** Start it:
```bash
docker compose -f docker-compose.prod.yml up -d socket-proxy
```

**Docker socket permissions:** Fix socket permissions:
```bash
sudo chmod 666 /var/run/docker.sock   # Quick fix (less secure)
# Or add your user to the docker group:
sudo usermod -aG docker $USER
```

**Backend connecting directly to socket instead of socket-proxy:** Ensure `DOCKER_HOST=tcp://socket-proxy:2375` in `.env` (not `unix:///var/run/docker.sock`). The socket-proxy provides an authenticated, rate-limited interface.

**Socket-proxy network:** Ensure backend is on the `socket-proxy` network:
```bash
docker network connect socket-proxy smsly-hosting-backend-1
```

---

## 13. Migration Fails

### Symptoms

- `relation does not exist` errors
- `python manage.py migrate` hangs or times out
- Deploy stuck at migration step

### Diagnosis

```bash
# Check migration status
docker compose -f docker-compose.prod.yml exec backend python manage.py showmigrations

# Check for conflicting migrations
docker compose -f docker-compose.prod.yml exec backend python manage.py showmigrations --list | grep "\[ \]"

# Check for lock contention
docker exec smsly-postgres-primary psql -U smsly_admin smsly_hosting -c \
  "SELECT pid, usename, state, query FROM pg_stat_activity WHERE datname='smsly_hosting' AND state != 'idle';"

# Check if the migration container can reach the database
docker run --rm --network smsly-net smsly-hosting-backend:latest \
  python manage.py migrate --database=default --noinput --plan 2>&1 | head -20
```

### Fix

**Migration lock held by another process:**
```bash
# Kill the blocking process (check pid from pg_stat_activity)
docker exec smsly-postgres-primary psql -U smsly_admin smsly_hosting -c \
  "SELECT pg_terminate_backend(<pid>);"
# Then retry migration
```

**Conflicting migrations:** Squash or resolve conflicts:
```bash
docker compose -f docker-compose.prod.yml exec backend python manage.py migrate <app_name> zero
docker compose -f docker-compose.prod.yml exec backend python manage.py migrate <app_name>
```

**Data migration timeout:** Large tables can cause timeouts. Run in background:
```bash
docker compose -f docker-compose.prod.yml exec backend python manage.py migrate --noinput --timeout 3600
```

**Migration ordering issue:** If a migration depends on another that hasn't run:
```bash
docker compose -f docker-compose.prod.yml exec backend python manage.py showmigrations --plan
# Identify missing migrations and run them in order
docker compose -f docker-compose.prod.yml exec backend python manage.py migrate <app_name> <migration_name>
```

---

## 14. Rate Limiting Too Aggressive

### Symptoms

- Legitimate API requests return 429 Too Many Requests
- Users blocked after normal usage patterns
- Deploy webhook triggers blocked

### Diagnosis

```bash
# Check current rate limit configuration
docker compose -f docker-compose.prod.yml exec traefik wget -qO- http://127.0.0.1:8080/api/http/routers 2>/dev/null | python3 -m json.tool | grep ratelimit

# Check Traefik labels for rate limit settings
docker inspect smsly-hosting-backend-1 | python3 -c "
import json,sys
labels = json.load(sys.stdin)[0]['Config']['Labels']
for k,v in labels.items():
    if 'ratelimit' in k.lower():
        print(f'{k}: {v}')
"

# Check CrowdSec ban decisions (may be blocking legitimate traffic)
docker exec smsly-crowdsec cscli decisions list

# Check access logs for 429 responses
docker exec smsly-hosting-traefik-1 cat /var/log/traefik/access.log | grep '"status":429' | tail -5
```

### Fix

**Increase Traefik rate limit:** Edit the backend labels in `docker-compose.prod.yml`:
```yaml
# Current (default):
- traefik.http.middlewares.api-ratelimit.ratelimit.average=200
- traefik.http.middlewares.api-ratelimit.ratelimit.period=1s
- traefik.http.middlewares.api-ratelimit.ratelimit.burst=50

# Increase for high-traffic:
- traefik.http.middlewares.api-ratelimit.ratelimit.average=500
- traefik.http.middlewares.api-ratelimit.ratelimit.burst=100
```

**Add per-endpoint overrides:** For specific endpoints that need higher limits:
```yaml
# In docker-compose.prod.yml backend labels:
- traefik.http.routers.backend-highlimit.rule=PathPrefix(`/api/v1/webhooks/`)
- traefik.http.routers.backend-highlimit.middlewares=webhook-ratelimit
- traefik.http.middlewares.webhook-ratelimit.ratelimit.average=1000
- traefik.http.middlewares.webhook-ratelimit.ratelimit.period=1s
```

**CrowdSec false positive:** If CrowdSec is blocking legitimate IPs:
```bash
docker exec smsly-crowdsec cscli decisions delete --ip <false-positive-ip>
# Add IP to whitelist in infrastructure/crowdsec/whitelists.yaml
```

---

## 15. Addon Provisioning Fails

### Symptoms

- New addon stuck in "Provisioning..." state
- Container created but never starts
- Volume mount errors in container logs

### Diagnosis

```bash
# Check if the addon container exists
docker ps -a | grep <addon-name>

# Check container logs
docker logs --tail=50 <addon-container>

# Check Docker network connectivity
docker exec smsly-hosting-backend-1 ping <addon-container>

# Check volume mounts
docker inspect <addon-container> | python3 -c "
import json,sys
mounts = json.load(sys.stdin)[0]['Mounts']
for m in mounts:
    print(f'{m[\"Source\"]} -> {m[\"Destination\"]} ({m[\"Mode\"]})')
"

# Check environment variables
docker inspect <addon-container> | python3 -c "
import json,sys
env = json.load(sys.stdin)[0]['Config']['Env']
for e in env:
    print(e)
"
```

### Fix

**Network not connected:** Ensure the addon is on `smsly-net`:
```bash
docker network connect smsly-net <addon-container>
```

**Volume permissions:** The addon container may run as a different UID. Fix:
```bash
docker run --rm -v <volume-name>:/data alpine chown -R <uid>:<gid> /data
```

**Environment variables missing:** Check the addon's required env vars. The provisioning system injects them from `.env`. If a variable is missing, add it.

**Image pull failure:** If the addon image can't be pulled:
```bash
# Check Docker daemon DNS
docker run --rm alpine nslookup <registry-host>

# Check if insecure registry is configured for local registry
cat /etc/docker/daemon.json | grep insecure-registries
```

**Resource limits:** Addon container may be OOM-killed. Increase its memory limit in `docker-compose.prod.yml` or check for memory leaks.

---

## 16. Nixpacks Build Crashes

### Symptoms

- `Nixpacks build failed` or out of memory during docker build
- Build process killed with exit code 137

### Diagnosis

```bash
# Check available memory
free -h

# Check if swap is enabled
swapon --show

# Check Docker build output for OOM
docker compose -f docker-compose.prod.yml build --progress=plain 2>&1 | tail -50
```

### Fix

Enable swap memory on the server. The `install.sh` script does this automatically, but if you bypassed it, run:
```bash
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

---

## 17. GitHub Webhook Failing (401 Unauthorized)

### Symptoms

- Push to main doesn't trigger a build
- GitHub shows red 'X' on webhook deliveries

### Diagnosis

```bash
# Check webhook secret in .env
grep GITHUB_WEBHOOK_SECRET .env

# Test webhook endpoint manually
curl -X POST http://localhost/api/v1/webhooks/github/ \
  -H "Content-Type: application/json" \
  -H "X-Hub-Signature-256: sha256=..." \
  -d '{}'
```

### Fix

The `GITHUB_WEBHOOK_SECRET` in your `.env` must exactly match the secret configured in the GitHub repository webhook settings. Regenerate the secret in GitHub and update `.env`:
```bash
# Generate new secret
openssl rand -hex 32
# Add to .env
GITHUB_WEBHOOK_SECRET=<generated-secret>
# Restart backend to pick up the change
docker compose -f docker-compose.prod.yml restart backend
```

---

## 18. OAuth Social Login Fails

### Symptoms

- Clicking GitHub/Google login loops back to the login page or shows `400 Bad Request`
- OAuth callback URL returns error

### Diagnosis

```bash
# Check SITE_URL in .env
grep SITE_URL .env

# Check OAuth callback URLs registered in GitHub/Google
# Backend callback: https://your-domain.com/api/v1/auth/github/callback/
# Frontend redirect: https://your-domain.com/accounts/github/login/callback/

# Check backend logs for OAuth errors
docker logs --tail=50 smsly-hosting-backend-1 2>&1 | grep -i "oauth\|social\|callback"
```

### Fix

Ensure the `SITE_URL` in `.env` is set exactly to your domain including `https://` (e.g. `https://cloud.mycompany.com`). The OAuth callback URL registered in GitHub/Google must match `https://cloud.mycompany.com/api/v1/auth/github/callback/` or similar. Restart the backend after changing:
```bash
docker compose -f docker-compose.prod.yml restart backend
```

---

## 19. Caddy SSL Failing

### Symptoms

- Site works on IP but domain doesn't load or shows invalid certificate
- `ERR_SSL_PROTOCOL_ERROR` in browser

### Diagnosis

```bash
# Check Caddy logs
docker compose -f docker-compose.prod.yml logs caddy --tail=50

# Check if DNS A record points to server IP
dig +short your-domain @8.8.8.8

# Check if ports 80/443 are open
ss -tlnp | grep -E ":80|:443"

# Check Caddyfile validity
docker compose -f docker-compose.prod.yml exec caddy caddy validate --config /etc/caddy/Caddyfile
```

### Fix

Ensure your DNS A record points to the server IP and port 80/443 are open. Check logs:
```bash
docker compose -f docker-compose.prod.yml logs caddy --tail=100
```

If using Cloudflare, ensure the Cloudflare proxy is configured correctly and the API token has the required permissions. Reload Caddy after fixing:
```bash
docker compose -f docker-compose.prod.yml exec caddy caddy reload --config /etc/caddy/Caddyfile
```
