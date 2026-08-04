# Grid — Installation & Operations Guide

> **Grid by SMSLY** — The Self-Healing, Multi-Cloud PaaS.

---

## Table of Contents

1. [System Requirements](#system-requirements)
2. [Fresh Installation](#fresh-installation)
3. [Deployment Modes](#deployment-modes)
4. [Updating Grid](#updating-Grid)
5. [Managing Services](#managing-services)
6. [Database Operations](#database-operations)
7. [SSL & Custom Domains](#ssl--custom-domains)
8. [Troubleshooting](#troubleshooting)
9. [Security Hardening](#security-hardening)
10. [Uninstallation](#uninstallation)

---

## System Requirements

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| **OS** | Ubuntu 20.04 LTS | Ubuntu 22.04 / 24.04 LTS |
| **CPU** | 2 vCPUs | 4 vCPUs |
| **RAM** | 2 GB | 4 GB |
| **Disk** | 20 GB | 40 GB+ SSD |
| **Ports** | 80, 443 | 80, 443 |
| **Network** | Public IPv4 | Static IP preferred |

**Software dependencies** (installed automatically): Docker, Docker Compose, Python 3, Caddy, Git.

---

## Fresh Installation

### One-Command Install

SSH into your server as root and run:

```bash
curl -fsSL https://raw.githubusercontent.com/SMSLYCLOUD/smsly-hosting/main/backend/install.sh -o /tmp/install.sh
sudo bash /tmp/install.sh
```

> **Important:** Do NOT pipe directly from `curl` (`curl ... | bash`). The installer requires interactive input unless you pre-seed SSL env vars.

### Install the Next-Gen Rust Architecture (Beta)
If you want to skip the legacy Python/Django monolith and immediately install the new high-performance Rust ecosystem (API, Worker, Frontend WASM), use the `--rust` flag:

```bash
sudo bash /tmp/install.sh --rust
```

### Non-Interactive SSL Install (Optional)

If you're automating installation (CI or one-line SSH), you can run SSL mode non-interactively:

```bash
curl -fsSL https://raw.githubusercontent.com/SMSLYCLOUD/smsly-hosting/main/backend/install.sh -o /tmp/install.sh && \
sudo USE_SSL=true DOMAIN=your-domain.com ACME_EMAIL=admin@your-domain.com SKIP_SCREEN=1 bash /tmp/install.sh
```

### What Happens During Installation

The installer runs 8 automated steps:

| Step | What It Does |
|------|-------------|
| **1. Pre-flight** | Checks OS, root access, available resources |
| **2. Dependencies** | Installs Docker, Python, system packages. Stops conflicting services (nginx, apache2) |
| **3. Configuration** | Generates all secrets: Django `SECRET_KEY`, Fernet encryption key, DB password, Redis password, HMAC gateway secret |
| **4. Deployment** | Builds and starts all Docker containers (backend, frontend, celery, DB, Redis) |
| **5. Database** | Waits for PostgreSQL, syncs passwords, runs Django migrations |
| **6. Admin User** | Creates admin superuser (credentials saved to `/opt/smsly-hosting/.credentials`) |
| **7. Reverse Proxy** | Installs and configures Caddy for HTTP or HTTPS with auto-SSL |
| **8. Verification** | Runs health checks, prints container status |

### After Installation

You'll see a summary with:

```
URL:         http://YOUR_IP  (or https://your-domain.com)
Admin:       /admin
Credentials: /opt/smsly-hosting/.credentials
```

**First steps:**
1. Open the URL in your browser
2. Log in with `admin` and the password in `/opt/smsly-hosting/.credentials`
3. (Recommended) Change the admin password (Settings → Security)
4. Configure your cloud providers (Settings → Cloud)

---

## Deployment Modes

### IP Mode (Quick Start)

Best for testing and development. No domain needed.

- **Access:** `http://YOUR_IP`
- **Caddy:** Binds port 80 directly; routes to `backend:8000` (and `frontend:3000`). No nginx bridge.
- **SSL:** None

Select option `1` during installation.

### SSL Mode (Production)

Best for production deployments. Requires a domain with DNS A record pointing to your server IP.

- **Access:** `https://your-domain.com`
- **Caddy:** Auto-obtains Let's Encrypt certificate
- **SSL:** Automatic renewal

Select option `2` during installation, then provide:
- Your domain name (e.g., `app.example.com`)
- An email for SSL certificate notifications

**DNS Prerequisite:** Before running, create an A record in your DNS provider:
```
Type: A
Name: app (or @ for root)
Value: YOUR_SERVER_IP
TTL: 300
```

---

## Updating Grid

### From the Terminal (SSH)

Navigate to the install directory and run the update command:

```bash
cd /opt/smsly-hosting
```

#### Full Update (Frontend + Backend)

Pulls latest code, rebuilds all containers, runs migrations:

```bash
sudo bash install.sh --update
```

#### Migrate to Rust Twin

If you are running the legacy Python monolith and want to safely switch to the new Rust ecosystem using the same PostgreSQL and Redis databases:

```bash
sudo bash install.sh --update --rust
```

#### Frontend Only

Rebuilds only the Next.js frontend container. Fast (1-2 minutes). No downtime for backend:

```bash
sudo bash install.sh --update-frontend
```

#### Backend Only

Rebuilds Django backend, runs migrations, restarts Celery workers:

```bash
sudo bash install.sh --update-backend
```

### What the Update Does

1. **Stashes** any local changes (restored on failure)
2. **Pulls** latest code from GitHub (`git pull origin main`)
3. **Validates** required files exist (Dockerfiles, compose file)
4. **Rebuilds** targeted containers with `--no-cache`
5. **Runs migrations** (backend/full update only)
6. **Verifies** health check passes
7. **Reports** container status

### From the Dashboard (Admin UI)

Admins can trigger updates from: **Settings → System → Update Software**

This triggers the same `install.sh --update` pipeline via the backend API.

### Rollback on Failure

If an update fails, the installer automatically:

1. Stops new containers
2. Restores the previous `.env` backup
3. Pops the git stash (rolls back code)

Manual rollback:

```bash
cd /opt/smsly-hosting
git log --oneline -n 5           # Find the previous commit
git checkout <commit-hash>       # Roll back
docker compose -f docker-compose.prod.yml up -d --build  # Rebuild
```

---

## Managing Services

### View Container Status

```bash
cd /opt/smsly-hosting
docker compose -f docker-compose.prod.yml ps
```

### View Logs

```bash
# All services
docker compose -f docker-compose.prod.yml logs -f

# Specific service
docker compose -f docker-compose.prod.yml logs -f backend
docker compose -f docker-compose.prod.yml logs -f frontend
docker compose -f docker-compose.prod.yml logs -f celery
```

### Restart Services

```bash
# Restart everything
docker compose -f docker-compose.prod.yml restart

# Restart specific service
docker compose -f docker-compose.prod.yml restart backend
```

### Health Check

```bash
curl http://localhost/health
# Returns: {"status": "healthy", ...}
```

### Container Map

| Service | Internal Port | Purpose |
|---------|--------------|---------|
| `backend` | 8000 | Django API (Gunicorn) |
| `frontend` | 3000 | Next.js SSR |
| `db` | 5432 | PostgreSQL 16 |
| `redis` | 6379 | Cache + Celery broker |
| `celery` | — | Background task worker |
| `celery-beat` | — | Periodic task scheduler |

---

## Database Operations

### Backup

```bash
cd /opt/smsly-hosting

# Quick backup
docker compose -f docker-compose.prod.yml exec db \
  pg_dump -U smsly_admin smsly_hosting | gzip > backups/backup_$(date +%Y%m%d).sql.gz
```

### Automated Backups (Cron)

Add to root crontab (`crontab -e`):

```bash
# Daily at 2 AM
0 2 * * * cd /opt/smsly-hosting && docker compose -f docker-compose.prod.yml exec -T db pg_dump -U smsly_admin smsly_hosting | gzip > backups/daily_$(date +\%Y\%m\%d).sql.gz
```

### Restore from Backup

```bash
# Stop services that write to DB
docker compose -f docker-compose.prod.yml stop backend celery celery-beat

# Restore
gunzip -c backups/backup_20260211.sql.gz | \
  docker compose -f docker-compose.prod.yml exec -T db psql -U smsly_admin -d smsly_hosting

# Restart
docker compose -f docker-compose.prod.yml start backend celery celery-beat
```

### Reset Admin Password

```bash
docker compose -f docker-compose.prod.yml exec backend python manage.py shell -c \
  "from django.contrib.auth import get_user_model; User = get_user_model(); u = User.objects.get(username='admin'); u.set_password('your_new_password'); u.save(); print('Password reset.')"
```

---

## SSL & Custom Domains

### Switch from IP Mode to SSL Mode

1. Set up your DNS A record pointing to the server IP
2. Edit the Caddyfile:

```bash
nano /etc/caddy/Caddyfile
```

Replace with:

```
your-domain.com {
    reverse_proxy localhost:8000
    encode gzip
}
```

3. Update `.env`:

```bash
cd /opt/smsly-hosting
nano .env

# Change:
DOMAIN=your-domain.com
USE_SSL=true
ALLOWED_HOSTS=your-domain.com,YOUR_IP,localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=https://your-domain.com,http://localhost
CORS_ALLOWED_ORIGINS=https://your-domain.com
```

4. Restart services:

```bash
systemctl restart caddy
docker compose -f docker-compose.prod.yml restart backend
```

### Renew SSL Certificate

Caddy handles automatic renewal. To force renew:

```bash
caddy reload --config /etc/caddy/Caddyfile
```

---

## Troubleshooting

### Dashboard Not Loading

1. Check containers: `docker compose -f docker-compose.prod.yml ps`
2. Check that backend and frontend containers are healthy
3. Check Caddy: `systemctl status caddy`
4. Check firewall: `ufw status` — ports 80 and 443 should be allowed (8090 is the legacy nginx bridge and is **not** required)

### Database Connection Error

1. Check backend logs: `docker compose -f docker-compose.prod.yml logs backend`
2. Verify `.env` has matching `POSTGRES_PASSWORD` and `DATABASE_URL`
3. Re-sync password: run the installer with `--update`

### Build Fails During Update

1. Check disk space: `df -h`
2. Clean Docker cache: `docker system prune -f`
3. Retry: `sudo bash install.sh --update`

### Caddy SSL Error

1. Verify DNS resolves: `host your-domain.com`
2. Check Caddy logs: `journalctl -u caddy --no-pager -n 30`
3. Ensure ports 80 and 443 are open

### Container Keeps Restarting

```bash
# Check which container is failing
docker compose -f docker-compose.prod.yml ps

# View its logs
docker compose -f docker-compose.prod.yml logs --tail=100 <service_name>
```

---

## Security Hardening

### Post-Install Checklist

- [ ] Change the admin password (recommended)
- [ ] Set `DEBUG=False` in `.env` (default)
- [ ] Configure `ALLOWED_HOSTS` to only your domain
- [ ] Enable SSL mode for production
- [ ] Set up firewall: only ports 80, 443 exposed

### Firewall Setup (UFW)

```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw allow 80/tcp
ufw allow 443/tcp
ufw enable
```

### Credential Locations

| File | Purpose | Permissions |
|------|---------|-------------|
| `/opt/smsly-hosting/.env` | All secrets & config | `chmod 600` |
| `/opt/smsly-hosting/.credentials` | Admin login info | `chmod 600` |

---

## Uninstallation

### Complete Removal

```bash
cd /opt/smsly-hosting
docker compose -f docker-compose.prod.yml down -v  # Stops all containers, removes volumes
systemctl stop caddy
systemctl disable caddy
rm -rf /opt/smsly-hosting
```

> **Warning:** This permanently deletes all data including the database. Back up first!

### Keep Data, Remove Services

```bash
cd /opt/smsly-hosting
docker compose -f docker-compose.prod.yml down  # Keeps volumes
systemctl stop caddy
```

Data persists in Docker volumes and can be restored with `docker compose up -d`.

---

<p align="center">
  <strong>Grid</strong> by <a href="https://github.com/SMSLYCLOUD">SMSLY</a><br />
  <em>Deploy anything. Own everything.</em>
</p>
