---
description: Diagnose and fix common production issues
---

# Production Troubleshooting Workflow

## Quick Diagnostics

### Check all container status

// turbo

```bash
docker compose -f docker-compose.prod.yml ps -a
```

### View recent logs (all services)

// turbo

```bash
docker compose -f docker-compose.prod.yml logs --tail=50
```

### Check disk space

// turbo

```bash
df -h
docker system df
```

### Check memory usage

// turbo

```bash
free -m
docker stats --no-stream
```

---

## Common Issues

### Issue: Backend not responding

**Check logs:**

```bash
docker compose -f docker-compose.prod.yml logs backend --tail=100
```

**Restart backend:**

```bash
docker compose -f docker-compose.prod.yml restart backend
```

**Check database connection:**

```bash
docker compose -f docker-compose.prod.yml exec backend python manage.py dbshell
```

---

### Issue: Frontend showing old version

**Force rebuild:**

```bash
docker compose -f docker-compose.prod.yml build frontend --no-cache
docker compose -f docker-compose.prod.yml up -d frontend
```

**Clear browser cache or hard refresh:** `Ctrl+Shift+R`

---

### Issue: Database connection refused

**Check PostgreSQL container:**

```bash
docker compose -f docker-compose.prod.yml logs db
docker compose -f docker-compose.prod.yml exec db pg_isready -U smsly_admin
```

**Restart database:**

```bash
docker compose -f docker-compose.prod.yml restart db
sleep 10
docker compose -f docker-compose.prod.yml restart backend celery
```

---

### Issue: Redis connection issues

**Check Redis container:**

```bash
docker compose -f docker-compose.prod.yml logs redis
docker compose -f docker-compose.prod.yml exec redis redis-cli ping
```

---

### Issue: Disk full

**Clean Docker resources:**

```bash
docker system prune -af --volumes
docker builder prune -af
```

**Remove old images:**

```bash
docker image prune -af
```

---

### Issue: SSL certificate expired

**Renew certificate:**

```bash
sudo certbot renew
sudo nginx -s reload
```

---

## Health Check Commands

```bash
# API health
curl -sf http://localhost:8000/api/health/ && echo "✅ API OK" || echo "❌ API DOWN"

# Database
docker compose -f docker-compose.prod.yml exec db pg_isready && echo "✅ DB OK"

# Redis
docker compose -f docker-compose.prod.yml exec redis redis-cli ping && echo "✅ Redis OK"

# Full status
docker compose -f docker-compose.prod.yml ps
```

## Emergency: Full Reset

⚠️ **WARNING: This will delete all data!**

```bash
docker compose -f docker-compose.prod.yml down -v
docker system prune -af
docker compose -f docker-compose.prod.yml up -d --build
```
