---
description: Deploy SMSLY Hosting to production VPS via Docker Compose
---

# Production Deployment Workflow

## Prerequisites

- Ubuntu 22.04+ VPS with root/sudo access
- Domain pointed to VPS IP (A record)
- Minimum: 4GB RAM, 2 vCPUs, 40GB SSD

## Deployment Steps

### 1. SSH into your VPS

```bash
ssh root@your-server-ip
```

### 2. Set environment variables

```bash
export DOMAIN="hosting.yourdomain.com"
export ADMIN_EMAIL="admin@yourdomain.com"
export ADMIN_PASSWORD="your-secure-password-min-8-chars"
```

### 3. Run the install script

// turbo

```bash
curl -fsSL https://raw.githubusercontent.com/SMSLYCLOUD/smsly-hosting/main/install-v2.sh | sudo bash
```

### 4. Verify services are running

// turbo

```bash
docker compose -f docker-compose.prod.yml ps
```

### 5. Check backend-init completed

// turbo

```bash
docker compose -f docker-compose.prod.yml logs backend-init
```

### 6. Test the API health

// turbo

```bash
curl http://localhost:8000/api/health/ || echo "Backend starting..."
```

### 7. Get SSL certificate (if needed)

```bash
sudo certbot --nginx -d $DOMAIN --non-interactive --agree-tos -m $ADMIN_EMAIL
```

## Verification Checklist

- [ ] All containers showing "Up" status
- [ ] `backend-init` completed successfully (exited 0)
- [ ] API responds at `/api/health/`
- [ ] Frontend loads at `/`
- [ ] Admin panel accessible at `/admin/`
- [ ] SSL certificate valid (if using HTTPS)

## Troubleshooting

### View logs

```bash
docker compose -f docker-compose.prod.yml logs -f backend
docker compose -f docker-compose.prod.yml logs -f frontend
```

### Restart services

```bash
docker compose -f docker-compose.prod.yml restart
```

### Rebuild and redeploy

```bash
docker compose -f docker-compose.prod.yml down
docker compose -f docker-compose.prod.yml up -d --build
```

### Reset database (DESTRUCTIVE)

```bash
docker compose -f docker-compose.prod.yml down -v
docker compose -f docker-compose.prod.yml up -d --build
```
