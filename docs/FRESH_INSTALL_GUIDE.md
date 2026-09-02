# Fresh VPS Installation Guide — Grid by SMSLY

## E2E Review Summary (2026-09-02)

All fixes from this session are in `master` (25 commits). The fresh
install flow was reviewed end-to-end and is sound. The 9-phase
installer is idempotent (checkpoint/resume), generates all secrets,
and self-verifies at the end.

---

## Prerequisites

| Requirement | Minimum | Recommended |
|---|---|---|
| OS | Ubuntu 22.04/24.04 LTS | Ubuntu 24.04 |
| RAM | 2 GB | 8 GB+ |
| Disk | 5 GB | 20 GB+ |
| Ports open | 22, 80, 443 | + 5001 (registry mirror, nodes only) |
| DNS | A record → VPS IP | Cloudflare zone recommended |
| Internet | required (git clone, apt, docker pull) | |

## One-Command Install

```bash
# SSH into the fresh VPS as root (or sudo -i)
curl -fsSL https://raw.githubusercontent.com/SMSLYCLOUD/smsly-hosting/master/install.sh -o install.sh
sudo bash install.sh
```

Or with pre-set domain (non-interactive):

```bash
sudo DOMAIN=grid.yourdomain.com NON_INTERACTIVE=true bash install.sh
```

## What the installer does (9 phases)

1. **[1/9] Pre-flight** — hardware check, internet, port conflicts,
   disk space (auto-prunes Docker cache if <3 GB), clones the repo.
2. **[2/9] Dependencies** — Docker Engine + Compose plugin + buildx,
   Python 3.11, Git, curl, openssl. Removes any stale SMSLY
   containers/volumes from previous runs.
3. **[3/9] Configuration** — interactive prompts for `DOMAIN` (or
   auto-detects public IP), `USE_SSL`, Cloudflare token (optional),
   `WILDCARD_SUBDOMAINS`. Generates ALL secrets: `SECRET_KEY`,
   `FIELD_ENCRYPTION_KEY`, `POSTGRES_PASSWORD`, `REDIS_PASSWORD`,
   `RABBITMQ_PASSWORD`, `GATEWAY_SECRET`, registry TLS cert/key pair
   (modulus-matched), `CADDY_ASK_SECRET`. Writes `/opt/smsly-hosting/.env`.
4. **[4/9] Deploy stack** — `docker compose -f docker-compose.prod.yml
   up -d --build` with `COMPOSE_PROFILES=local-ha` (Patroni HA Postgres,
   Redis Sentinel). Backend image includes the docker-compose-plugin
   (needed for mTLS deploy endpoint).
5. **[5/9] Database init** — runs `manage.py migrate`, creates the
   primary DB, PgCat pooler, Patroni cluster bootstrap.
6. **[6/9] Admin user** — creates `admin` user with a random password,
   saved to `/opt/smsly-hosting/.credentials`.
7. **[7/9] Caddy proxy** — generates the Caddyfile (single global
   block with `on_demand_tls ask`), validates, reloads.
8. **[8/9] Hardening** — UFW (80/443/SSH), DOCKER-USER iptables for
   infra ports (5000/5432/6379/5672 trusted-only), fail2ban, auditd,
   kernel params, optional gVisor/Kata/CrowdSec/Falco.
9. **[9/9] Verification** — checks backend health, Traefik, Lite
   Agent profile, compose services. Reports pass/fail score.

## Post-Install (5 minutes)

```bash
# 1. Read your admin credentials
cat /opt/smsly-hosting/.credentials

# 2. Verify the platform is up
curl -s http://localhost/health

# 3. Login to the dashboard
#    http://<your-domain-or-ip>  (or https:// if USE_SSL=true)

# 4. Configure your domain in Settings → Domains
#    (or it was already set if you used DOMAIN= env var)

# 5. (Optional) Enable Edge Shield — BGP/DDoS protection via Cloudflare
#    Settings → Domains → set Cloudflare API token (needs Zone DNS +
#    Zone Settings + Zone DNSSEC edit scopes), then:
sudo docker exec smsly-hosting-backend-1 python manage.py deploy_edge_shield

# 6. (Optional) Enable mTLS (SPIRE)
#    Settings → Security → toggle "Deploy Platform SPIRE"

# 7. (Optional) Connect GitHub for push-to-deploy
#    Settings → Integrations → "Create & Connect GitHub App" (one-click
#    manifest flow — no manual pasting of secrets)
```

## Key URLs after install

| URL | Purpose |
|---|---|
| `http://<domain>/` | Frontend dashboard |
| `http://<domain>/api/v1/` | REST API |
| `http://<domain>/health` | Health check (public) |
| `https://<domain>/api/v1/mtls/health/` | mTLS status (admin) |
| `http://<domain>/api/v1/services/check-domain/?domain=X&secret=Y` | Caddy on-demand TLS gate |

## Troubleshooting

| Symptom | Fix |
|---|---|
| Container crash-loop at boot | `docker logs smsly-hosting-backend-1` — check `.env` has all required secrets |
| 502/503 on the domain | Caddy not running: `docker ps | grep caddy` + `docker exec smsly-hosting-caddy-1 caddy validate --config /etc/caddy/Caddyfile` |
| Traefik "no available server" | Service container not on the project bridge: redeploy the service |
| TLS alert / no cert | Check Caddy on-demand: `docker logs smsly-hosting-caddy-1 | grep -i acme` |
| Redis Sentinel "No master" | `docker exec smsly-redis-sentinel-1 redis-cli -p 26379 SENTINEL get-master-addr-by-name mymaster` |
| Postgres HA not syncing | `docker exec smsly-postgres-primary psql -U smsly_admin -d postgres -tAc "SELECT * FROM pg_stat_replication"` |
| git pull fails on the VPS | Repo is private: use `git bundle` for updates, or add a deploy key |
| Port 80/443 blocked | `ufw status` + `iptables -L DOCKER-USER -n` — Edge Shield lockdown may need `--off` |

## What's included out of the box

- **Blue-green deploys** with auto-promotion and rollback
- **Dual-homed networking** (project bridge + platform bridge)
- **Egress isolation** per project bridge (iptables DOCKER-USER)
- **Env sanitization** (no leaked secrets in .env files)
- **Orphaned container cleanup** (beat task, every 30 min)
- **Edge Shield** (BGP-hijack defense via Cloudflare Anycast proxy)
- **DNSSEC** (Cloudflare zone signing, DS record surfaced)
- **Preview environment gating** (basic-auth on PR deployments)
- **mTLS** (SPIRE, platform + ecosystem scopes)
- **GitHub App** (one-click manifest flow, push-to-deploy webhooks)
- **Domain verification** (quorum DNS, rebinding-proof)
- **Caddyfile validation** (refuses to apply without the control-plane block)
