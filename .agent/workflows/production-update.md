---
description: Update production server with latest code from GitHub
---

# Production Update Workflow

## Prerequisites

- Production server already running
- SSH access to server

## Steps

### 1. SSH into production server

```bash
ssh root@your-server-ip
cd /opt/smsly-hosting
```

### 2. Pull latest code

// turbo

```bash
git pull origin main
```

### 3. Rebuild and restart services

```bash
docker compose -f docker-compose.prod.yml build --no-cache
docker compose -f docker-compose.prod.yml up -d
```

### 4. Run new migrations (if any)

// turbo

```bash
docker compose -f docker-compose.prod.yml exec backend python manage.py migrate --no-input
```

### 5. Collect static files

// turbo

```bash
docker compose -f docker-compose.prod.yml exec backend python manage.py collectstatic --no-input
```

### 6. Verify deployment

// turbo

```bash
docker compose -f docker-compose.prod.yml ps
curl -f http://localhost/api/health/ && echo "✅ Update successful"
```

## Rollback (if needed)

```bash
# Find previous commit
git log --oneline -5

# Rollback to specific commit
git checkout <commit-hash>

# Rebuild
docker compose -f docker-compose.prod.yml build --no-cache
docker compose -f docker-compose.prod.yml up -d
```

## Zero-Downtime Update (advanced)

```bash
# Scale up new containers
docker compose -f docker-compose.prod.yml up -d --scale backend=2

# Wait for new container to be healthy
sleep 30

# Scale down old container
docker compose -f docker-compose.prod.yml up -d --scale backend=1
```
