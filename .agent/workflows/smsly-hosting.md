---
description: Exhaustive unified workflow for SMSLY Hosting — local dev, production deployment, updates, backups, troubleshooting, security, and disaster recovery
---

# SMSLY Hosting — Unified Operations Workflow

> **Architecture**: Django 5 backend + Next.js 14 frontend + Postgres 16 + Redis 7 + Celery + Nginx + Docker Socket Proxy  
> **Install Dir (Prod)**: `/opt/smsly-hosting`  
> **Compose Files**: `docker-compose.yml` (dev) · `docker-compose.prod.yml` (prod) · `docker-compose.traefik.yml` (SSL) · `docker-compose.observability.yml` (monitoring)

---

## Part 1 — Local Development

### 1.1 Prerequisites

- Docker Desktop running
- Python 3.10+
- Node.js 18+
- `.env.example` copied to `.env`

### 1.2 Generate Secrets

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# → Paste into FIELD_ENCRYPTION_KEY in .env
openssl rand -hex 32
# → Paste into SECRET_KEY in .env
```

### 1.3 Option A — Full Docker Stack (Recommended)

// turbo-all

```bash
docker compose up -d --build
```

```bash
docker compose logs -f backend
```

```bash
docker compose exec backend python manage.py migrate
```

```bash
docker compose exec backend python manage.py createsuperuser
```

**Access Points**:
- Frontend: http://localhost:3000
- Backend API: http://localhost:8000
- Admin: http://localhost:8000/admin/

### 1.4 Option B — Bare-Metal (Zero-Trust Verification)

Use this when debugging migration or schema issues outside Docker.

```powershell
cd backend
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

```bash
cp ../.env.example ../.env
# Set DATABASE_URL=sqlite:///db.sqlite3 for isolation
python manage.py check --traceback
python manage.py makemigrations
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

```bash
# Frontend (separate terminal)
cd frontend
npm install
npm run dev
```

### 1.5 Run Celery Workers (Background Tasks)

```bash
cd backend
celery -A config worker -l INFO
celery -A config beat -l INFO --pidfile=/tmp/celerybeat.pid --schedule=/tmp/celerybeat-schedule
```

### 1.6 Common Local Issues

| Symptom | Fix |
|---|---|
| Port 3000/8000 in use | `netstat -ano \| findstr ":3000"` → `taskkill /F /PID <PID>` |
| CORS errors | Set `CORS_ALLOWED_ORIGINS=http://localhost:3000` in `.env` |
| Frontend can't reach backend | Set `NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1` |
| UUID to BigInt cast error | See Part 8.1 below |
| `FIELD_ENCRYPTION_KEY` error | Regenerate valid Fernet key (see 1.2) |

---

## Part 2 — Pre-Deployment Checks

// turbo-all

### 2.1 Backend Lint

```bash
cd backend && pip install flake8 && flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
```

### 2.2 Django Deploy Check

```bash
cd backend && python manage.py check --deploy
```

### 2.3 Migration State

```bash
cd backend && python manage.py makemigrations --check --dry-run
```

### 2.4 Frontend Build

```bash
cd frontend && npm run build
```

### 2.5 Frontend Lint

```bash
cd frontend && npm run lint
```

### 2.6 Docker Image Build Test

```bash
docker build -t smsly-backend-test ./backend
docker build -t smsly-frontend-test ./frontend
```

### 2.7 Full Stack Smoke Test

```bash
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d
sleep 30
curl -f http://localhost:8090/health && echo "✅ Stack healthy"
docker compose -f docker-compose.prod.yml down
```

**Gate**: ALL checks must pass before production deployment.

---

## Part 3 — Production Deployment (Fresh VPS)

### 3.1 One-Line Installer (IP Mode or SSL Mode)

```bash
ssh root@<VPS_IP>
curl -fsSL https://raw.githubusercontent.com/SMSLYCLOUD/smsly-hosting/main/install.sh | sudo bash
```

The script auto-handles:
- System update + Docker install
- UFW firewall (22, 80, 443, 8090)
- Repository clone into `/opt/smsly-hosting`
- Secret generation (Fernet key, Django secret, Postgres password)
- IP vs SSL mode selection
- `docker compose build` + `up -d`
- `pg_isready` polling → `migrate` → `collectstatic`
- Health check verification

### 3.2 Manual Production Deploy (With SSL via Traefik)

```bash
# 1. Clone & configure
cd /opt/smsly-hosting
cp .env.example .env
nano .env  # Fill all required vars (see Part 3.3)

# 2. Create external network
docker network create smsly-proxy

# 3. Deploy Traefik for SSL
docker compose -f docker-compose.traefik.yml up -d

# 4. Deploy app stack with Traefik adapter
docker compose -f docker-compose.prod.yml -f docker-compose.traefik-adapter.yml up -d --build

# 5. Initialize database
docker compose -f docker-compose.prod.yml exec backend python manage.py migrate
docker compose -f docker-compose.prod.yml exec backend python manage.py collectstatic --noinput
docker compose -f docker-compose.prod.yml exec backend python manage.py createsuperuser
```

### 3.3 Required Environment Variables

| Variable | Description | Example |
|---|---|---|
| `SECRET_KEY` | Django secret (no default) | `openssl rand -hex 32` |
| `FIELD_ENCRYPTION_KEY` | Valid Fernet key | `python -c "..."` |
| `POSTGRES_PASSWORD` | DB password | `openssl rand -hex 16` |
| `ALLOWED_HOSTS` | Comma-separated hosts | `hosting.smsly.cloud` |
| `CSRF_TRUSTED_ORIGINS` | Trusted origins | `https://hosting.smsly.cloud` |
| `CORS_ALLOWED_ORIGINS` | CORS origins | `https://hosting.smsly.cloud` |
| `DATABASE_URL` | Postgres connection | `postgresql://smsly_admin:<PW>@db:5432/smsly_hosting` |
| `DEBUG` | Must be `False` in prod | `False` |
| `DOMAIN` | Server domain or IP | `hosting.smsly.cloud` |
| `ACME_EMAIL` | SSL cert email | `admin@smsly.cloud` |

### 3.4 Post-Deploy Checklist

- [ ] `docker compose -f docker-compose.prod.yml ps` — all containers `Up`
- [ ] `curl http://localhost:8090/health` — returns `{"status": "ok"}`
- [ ] Admin panel loads at `/admin/`
- [ ] Frontend loads at `/`
- [ ] `.env` has `chmod 600`
- [ ] Socket-proxy running (no direct Docker socket mount)
- [ ] Firewall configured (only 22, 80, 443, 8090)

---

## Part 4 — Production Updates

### 4.1 Standard Update (Zero-Downtime)

```bash
cd /opt/smsly-hosting
git pull origin main
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml exec backend python manage.py migrate --no-input
docker compose -f docker-compose.prod.yml exec backend python manage.py collectstatic --no-input
curl -f http://localhost:8090/health && echo "✅ Update successful"
```

### 4.2 Clean Redeploy (Cache Bust)

```bash
cd /opt/smsly-hosting
git pull origin main
docker compose -f docker-compose.prod.yml down
docker compose -f docker-compose.prod.yml build --no-cache
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml exec backend python manage.py migrate --no-input
```

### 4.3 Rollback

```bash
git log --oneline -5
git checkout <previous-commit-hash>
docker compose -f docker-compose.prod.yml up -d --build
```

---

## Part 5 — Database Backup & Restore

### 5.1 Manual Backup

// turbo-all

```bash
mkdir -p /opt/backups
```

```bash
docker compose -f docker-compose.prod.yml exec -T db pg_dump -U smsly_admin smsly_hosting | gzip > /opt/backups/smsly_$(date +%Y%m%d_%H%M%S).sql.gz
```

```bash
ls -la /opt/backups/
```

### 5.2 Restore from Backup

```bash
# 1. Stop writers
docker compose -f docker-compose.prod.yml stop backend celery celery-beat

# 2. Restore
gunzip -c /opt/backups/smsly_YYYYMMDD_HHMMSS.sql.gz | \
  docker compose -f docker-compose.prod.yml exec -T db psql -U smsly_admin -d smsly_hosting

# 3. Restart
docker compose -f docker-compose.prod.yml start backend celery celery-beat

# 4. Verify
docker compose -f docker-compose.prod.yml exec backend python manage.py check
```

### 5.3 Automated Daily Backup (Cron)

```cron
# Daily at 2 AM
0 2 * * * cd /opt/smsly-hosting && docker compose -f docker-compose.prod.yml exec -T db pg_dump -U smsly_admin smsly_hosting | gzip > /opt/backups/smsly_$(date +\%Y\%m\%d).sql.gz

# Cleanup > 7 days
0 3 * * * find /opt/backups -name "smsly_*.sql.gz" -mtime +7 -delete
```

### 5.4 Registry Backup

```bash
tar czf /opt/backups/registry_$(date +%Y%m%d).tar.gz -C /var/lib/docker/volumes smsly-hosting_registry_data
```

### 5.5 Offsite Copy

```bash
rsync -avz /opt/backups/ user@backup-server:/backups/smsly/
```

---

## Part 6 — Monitoring & Observability

### 6.1 Enable Full Observability Stack

```bash
docker compose -f docker-compose.observability.yml up -d
```

- **Prometheus**: http://localhost:9090
- **Grafana**: http://localhost:3001 (admin/admin)
- **Loki**: http://localhost:3100

### 6.2 Health Check Commands

// turbo-all

```bash
curl -sf http://localhost:8090/health && echo "✅ API OK" || echo "❌ API DOWN"
```

```bash
docker compose -f docker-compose.prod.yml exec db pg_isready -U smsly_admin && echo "✅ DB OK"
```

```bash
docker compose -f docker-compose.prod.yml exec redis redis-cli ping && echo "✅ Redis OK"
```

```bash
docker compose -f docker-compose.prod.yml ps
```

```bash
docker stats --no-stream
```

### 6.3 Log Monitoring

```bash
# All services
docker compose -f docker-compose.prod.yml logs -f

# Per-service
docker compose -f docker-compose.prod.yml logs -f backend
docker compose -f docker-compose.prod.yml logs -f frontend
docker compose -f docker-compose.prod.yml logs -f celery
docker compose -f docker-compose.prod.yml logs -f celery-beat
docker compose -f docker-compose.prod.yml logs -f nginx
```

### 6.4 Cron Health Monitor

```cron
*/5 * * * * curl -f http://localhost:8090/health || echo "SMSLY Hosting DOWN" | mail -s "Alert" admin@smsly.cloud
```

---

## Part 7 — Troubleshooting

### 7.1 Quick Diagnostics

// turbo-all

```bash
docker compose -f docker-compose.prod.yml ps -a
```

```bash
docker compose -f docker-compose.prod.yml logs --tail=50
```

```bash
df -h && docker system df
```

```bash
free -m
```

### 7.2 Issue Matrix

| Issue | Diagnostic | Fix |
|---|---|---|
| Backend not responding | `docker compose logs backend --tail=100` | `docker compose restart backend` |
| Frontend stale | See logs | `docker compose build frontend --no-cache && docker compose up -d frontend` |
| DB connection refused | `docker compose exec db pg_isready` | `docker compose restart db; sleep 10; docker compose restart backend celery` |
| Redis down | `docker compose exec redis redis-cli ping` | `docker compose restart redis` |
| Disk full | `docker system df` | `docker system prune -af --volumes && docker builder prune -af` |
| SSL cert expired | `certbot certificates` | `certbot renew && nginx -s reload` |
| Port 8090 blocked | `ufw status` | `ufw allow 8090/tcp` |

### 7.3 Reset Admin Password

```bash
docker compose -f docker-compose.prod.yml exec backend python manage.py shell -c \
  "from django.contrib.auth import get_user_model; u = get_user_model().objects.get(username='admin'); u.set_password('NewPass123!'); u.save()"
```

### 7.4 Emergency Full Reset (⚠️ DATA LOSS)

```bash
docker compose -f docker-compose.prod.yml down -v
docker system prune -af
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml exec backend python manage.py migrate
docker compose -f docker-compose.prod.yml exec backend python manage.py createsuperuser
```

---

## Part 8 — Django Migration Pitfalls

### 8.1 UUID to BigInt Cast Error

If `auth.0007` fails with "column id cannot be cast automatically to type bigint":

```bash
# Option A — Fake it
python manage.py migrate auth 0007 --fake

# Option B — Full regeneration (clean local DB)
# 1. Delete migration files (keep __init__.py)
Get-ChildItem -Path "apps" -Recurse -Filter "0*.py" -File | Remove-Item -Force

# 2. Wipe Postgres volume
docker compose down -v

# 3. Regenerate
python manage.py makemigrations
python manage.py migrate
```

### 8.2 Ghost Migrations

```powershell
# Deep clean
Get-ChildItem -Path "apps" -Recurse -Filter "0*.py" -File | Remove-Item -Force
Get-ChildItem -Path "apps" -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
docker compose down -v
python manage.py makemigrations
python manage.py migrate
```

---

## Part 9 — Security Hardening Checklist

- [ ] `.env` has `chmod 600` (no world-readable secrets)
- [ ] `DEBUG=False` in production
- [ ] `SECRET_KEY` is unique, not default
- [ ] `FIELD_ENCRYPTION_KEY` is valid Fernet
- [ ] `ALLOWED_HOSTS` does not contain `*`
- [ ] `CORS_ALLOW_ALL=False`
- [ ] Docker socket accessed via `tecnativa/docker-socket-proxy` only
- [ ] Socket proxy denies `SERVICES`, `SWARM`, `NODES`, `SECRETS`, `CONFIGS`
- [ ] Registry bound to `127.0.0.1:5000` only
- [ ] GitHub webhooks validated with `HMAC-SHA256`
- [ ] UFW enabled: only 22, 80, 443, 8090
- [ ] No hardcoded credentials in codebase
- [ ] Audit logs use hash-linked immutable structure

---

## Part 10 — Disaster Recovery

| Scenario | Action | RTO |
|---|---|---|
| DB corruption | Stop writers → `pg_restore` from latest backup → restart | 15 min |
| Full server failure | New VPS → `install.sh` → restore DB + registry volumes | 60 min |
| Failed deployment | `git checkout <hash>` → `docker compose up -d --build` | 5 min |

### Backup Retention

| Period | Retention |
|---|---|
| Daily | 30 days |
| Weekly | 90 days |
| Monthly | 365 days |

---

## Part 11 — Architecture Quick Reference

### Prod Stack (8 Services)

```
┌─────────────┐     ┌──────────┐     ┌──────────┐
│   Nginx     │────▶│ Backend  │────▶│ Postgres │
│   :8090     │     │ Gunicorn │     │    16    │
└─────────────┘     └──────────┘     └──────────┘
      │                   │
      ▼                   ▼
┌──────────┐        ┌──────────┐     ┌──────────┐
│ Frontend │        │  Celery  │────▶│  Redis 7 │
│ Next.js  │        │  Worker  │     └──────────┘
└──────────┘        └──────────┘
                          │
                    ┌──────────┐     ┌──────────┐
                    │  Celery  │     │ Socket   │
                    │   Beat   │     │  Proxy   │
                    └──────────┘     └──────────┘
                                          │
                                    ┌──────────┐
                                    │ Registry │
                                    │  :5000   │
                                    └──────────┘
```

### Networks

- `smsly-net` — Internal service mesh (dev/prod)
- `smsly-proxy` — External edge proxy (Traefik → Nginx)
- `socket-proxy` — Isolated Docker API network (dev)

### Volumes

- `postgres_data` — Database persistence
- `redis_data` — Cache/queue persistence
- `registry_data` — Built Docker images
- `static_volume` — Django static files
- `media_volume` — User uploads
