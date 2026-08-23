# Secret rotation runbook

## When to use this
- Quarterly rotation (recommended)
- After any suspected exposure (`.env` was emailed, committed to a fork, etc.)
- After employee offboarding
- After any of these deep-sweep findings are confirmed:
  - Batch S1: `certs/registry.key` was tracked in git
  - Batch S5: `.secrets.tmp` was written to disk
  - Working-copy `.env` was on disk

## The 13 secrets to rotate

| Variable | Length / format | Upstream action needed? |
|---|---|---|
| `SECRET_KEY` | 32-byte hex (64 chars) | No |
| `FIELD_ENCRYPTION_KEY` | 32-byte Fernet key (url-safe base64, 44 chars) | YES — re-encrypt all encrypted model fields |
| `GATEWAY_SECRET` | 32-byte hex | No |
| `GITHUB_WEBHOOK_SECRET` | 32-byte hex | YES — update GitHub webhook secret |
| `POSTGRES_PASSWORD` | 32-byte hex | YES — re-issue in infrastructure/docker/postgres/init-primary.sh, restart |
| `RABBITMQ_PASSWORD` | 32-byte hex | YES — update all broker URLs |
| `REDIS_PASSWORD` | 32-byte hex | YES — update all cache URLs |
| `REGISTRY_PASSWORD` | 32-byte hex | YES — restart registry, re-login all agents |
| `GRAFANA_PASSWORD` | 32-byte hex | YES — notify all dashboard users |
| `PGCAT_ADMIN_PASSWORD` | 32-byte hex | YES — re-issue in pgcat.toml |
| `AUTOSCALER_API_TOKEN` | urlsafe token (32 bytes) | YES — update lit agents |
| `FRP_AUTH_TOKEN` | urlsafe token (32 bytes) | YES — update all FRP clients |
| `JWT_SIGNING_KEY` (if separate) | urlsafe token | YES — all JWTs invalidated |

## Procedure (estimated: 60-90 min for a production rotate)

### 1. Generate new values
```bash
./scripts/rotate_secrets.sh .env
# Writes: .env.rotated.<timestamp>
```

### 2. Review the diff
```bash
diff .env .env.rotated.<timestamp>
# Verify: no real secrets in the output
# Verify: 13 new values, all 64 chars (or 44 for Fernet/tokens)
```

### 3. Backup the current `.env`
```bash
cp .env .env.bak.$(date -u +%Y%m%dT%H%M%SZ)
```

### 4. Apply the rotation
```bash
mv .env.rotated.<timestamp> .env
chmod 600 .env
```

### 5. Restart services (in this order)
```bash
docker compose -f docker-compose.prod.yml restart db        # DB password
docker compose -f docker-compose.prod.yml restart redis     # Redis password
docker compose -f docker-compose.prod.yml restart rabbitmq  # RabbitMQ password
docker compose -f docker-compose.prod.yml restart pgcat     # PgCat (uses DB password)
docker compose -f docker-compose.prod.yml restart backend celery celery-beat celery-fast celery-deploy
docker compose -f docker-compose.prod.yml restart registry
docker compose -f docker-compose.prod.yml restart grafana
docker compose -f docker-compose.prod.yml restart caddy      # Reloads env for ACME
```

### 6. Update the upstream providers
- **GitHub webhook secret**: settings → webhooks → edit → update secret
- **Registry users**: re-create `.htpasswd` with `htpasswd -Bbn user $REGISTRY_PASSWORD`
- **PostgreSQL replication**: update `REPLICATION_PASSWORD` in init-primary.sh
- **PgCat admin**: update `pgcat.toml`
- **Lite agents**: re-issue `AUTOSCALER_API_TOKEN` on each agent node
- **FRP clients**: re-issue `FRP_AUTH_TOKEN` on each client

### 7. Verify
```bash
# Backend
curl https://$DOMAIN/health
curl https://$DOMAIN/api/v1/auth/login/ -d 'username=admin&password=...' -c /tmp/cookies

# Registry
docker login registry.smsly.cloud

# Database
docker exec -it $(docker ps -qf name=db) psql -U smsly_admin -c '\du'

# Redis
docker exec -it $(docker ps -qf name=redis) redis-cli -a "$REDIS_PASSWORD" ping
```

### 8. Field encryption migration (one-time only, per rotation)
If `FIELD_ENCRYPTION_KEY` changed, you MUST re-encrypt all `EncryptedField` values in the database. There is no automated migration for this in the current codebase. Options:
- (a) Re-save every model that has an `EncryptedCharField` / `EncryptedTextField` (script needed)
- (b) Accept that existing encrypted values become unreadable; users must re-enter them
- (c) Roll back the rotation of `FIELD_ENCRYPTION_KEY` only

**Recommendation:** rotate `FIELD_ENCRYPTION_KEY` rarely (annually or on confirmed compromise), not quarterly.

## Post-rotation

- [ ] Update backup of `.env` in your password manager / secret store
- [ ] Verify Grafana dashboards still load (anonymous access was disabled in W2)
- [ ] Verify webhook deliveries from GitHub (check Delivery → Recent deliveries)
- [ ] Schedule the next rotation in 90 days
