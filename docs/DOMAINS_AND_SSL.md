# Custom Domains & SSL

Trulay Grid binds public traffic to services through Caddy, which auto-issues Let's Encrypt certificates on demand. Services can attach custom domains at two scopes: per-service (the common case) and global (rare, for apex + wildcard). SSL issuance is automatic for public hostnames; wildcard certs require a Cloudflare DNS-01 challenge.

## Overview

A Trulay Grid install has two layers of domain configuration:

1. **Per-service** — `Service.custom_domains` (JSONField). A list of hostnames that the service should respond to. Each entry is verified independently.
2. **Global** — `apps.domains.models.Domain`. A row that the platform manages as a singleton for the install. Used for the apex + wildcard cert when a wildcard is required.

SSL certificates are managed by Caddy, not by the platform. The platform's role is to:

- Tell Caddy which hostnames to serve.
- Verify that the user controls the hostname (CNAME chain or A-record match).
- Issue Let's Encrypt certificates via Caddy's `on_demand_tls` for HTTP-01 challenges.
- Issue wildcard certs via Cloudflare DNS-01 (when the user has configured a `CLOUDFLARE_TOKEN`).

The verification, certificate issuance, and Caddy reload are wired into a small state machine in `apps/domains/services/caddy_manager.py`.

## Adding a Custom Domain

There are two paths: per-service via `Service.custom_domains`, or global via the Domain model. Most operators use the per-service path.

### Per-Service (Recommended)

Each `Service` has a `custom_domains` JSONField. The list is edited through the service detail page or the API. The field accepts an array of fully-qualified hostnames:

```json
["api.example.com", "www.example.com"]
```

The platform validates each entry on save:

- Must be a syntactically valid hostname (RFC 1123).
- Must not be a loopback / link-local / private address.
- Must not already be in use by another service (the uniqueness check is on `(hostname, status=VERIFIED)`).

On save, the platform enqueues a verification job for each new entry.

### Global (Apex + Wildcard)

For installs that want a single wildcard cert to cover `*.example.com`, the operator creates a `Domain` row in `apps.domains.models.Domain`. The fields are:

| Field | Type | Notes |
| --- | --- | --- |
| `apex` | string | The apex domain (`example.com`). |
| `wildcard` | bool | Whether to issue a wildcard cert. |
| `cloudflare_token` | FK | Optional. A `CloudflareToken` row; required for wildcard certs. |
| `verification_status` | enum | `PENDING`, `VERIFIED`, `FAILED`. |

The global path is rarely used. It exists for installs that want to expose dozens of subdomains on a single cert and do not want to manage the per-service list. Per-service certs are issued per hostname by default; the wildcard is an optimization.

## Verification Flow

The platform verifies that the user controls each hostname before issuing a cert. The flow is:

1. **CNAME chain** — The platform resolves the hostname. If the answer is a CNAME, the chain is followed to the final A record. The chain must terminate on a platform-owned IP (the platform's public IP, or a `ManagedServer`'s IP).
2. **IP match** — The final A record must match an IP that the platform controls. For a single-node install, this is `PlatformConfig.public_ip`. For a multi-node install, this is the union of all `ManagedServer.public_ip` rows that have `is_reachable=True`.

There is **no DNS-01 challenge for per-service certs** — that is a future-work item. The current model assumes that anyone who can set an A record or CNAME for `api.example.com` controls the domain.

### Verification Failure

A verification failure puts the domain in `FAILED` status. The platform emits a `DOMAIN_VERIFY` `AuditLog` row with the failure reason:

- `CNAME_CHAIN_BROKEN` — the CNAME chain does not terminate on a platform IP.
- `IP_MISMATCH` — the A record points to a non-platform IP.
- `RESOLUTION_TIMEOUT` — DNS resolution took longer than 5 seconds.
- `MULTIPLE_ANSWERS` — the A record has multiple answers and not all of them are platform IPs.

The domain is retried on the next save (the platform does not auto-retry on a timer). Operators can re-trigger verification via `POST /api/v1/services/{id}/check-domain/`.

## Caddy `ask` Endpoint

Caddy does not know about Trulay Grid's domain database. When Caddy sees a request for an unknown hostname, it calls the platform's `ask` endpoint to ask "is this hostname yours?". The active endpoint is `GET /api/v1/services/check-domain/?domain=<hostname>`.

### Authentication: `CaddySecretOrAdminPermission`

The endpoint accepts two auth modes:

1. **`X-Caddy-Secret`** — A shared secret set in Caddy's config and in the platform's `CADDY_ASK_SECRET` env var. Used in production.
2. **Session / token** — An admin user with `is_staff=True`. Used in dev / debugging.

The auth is implemented as a DRF permission class, `CaddySecretOrAdminPermission`. The shared secret path is preferred for production because the Caddy process does not have a user account.

### Throttling: 60/min + Per-Apex Daily Cap

The endpoint is throttled to **60 calls/minute per source IP**. The throttle is a DRF `ScopedRateThrottle` with `scope='caddy_ask'`. The 60/min cap is enough for a fleet of 200 services with one Caddy reload per minute; if you exceed it, Caddy's request will fail and the platform returns 429. Caddy retries internally.

A second, stricter throttle is applied **per apex domain**: at most 20 `ask` calls per apex per day. This is enforced by a Redis-backed counter (`caddy_ask:apex:<apex>:<date>`, TTL 86400). The cap exists to prevent a misconfigured Caddy from hammering the platform with repeated `ask` calls for the same hostname — the cap means a misbehaving Caddy will see a 429 within minutes and the operator can intervene.

## SSL Issuance

The platform does not issue SSL certs directly. Caddy does, via its `on_demand_tls` module.

### Let's Encrypt via `on_demand_tls`

For HTTP-01 challenges, Caddy issues a Let's Encrypt cert the first time it sees a request for a new hostname. The cert is cached and renewed automatically. The platform's role is to **trust Caddy to do this** — it does not pre-issue certs, and it does not pre-register hostnames with Let's Encrypt.

The trade-off: the first request to a new hostname pays a ~5–10 second cert-issuance delay. Subsequent requests are sub-millisecond. For high-traffic services, operators can `curl -fsS https://<new-hostname>/` once after adding the domain to "warm" the cert.

### Cloudflare DNS-01 for Wildcards

Wildcard certs (`*.example.com`) cannot be issued via HTTP-01 — there is no way to serve a challenge from a hostname that does not yet exist. The platform uses Cloudflare's DNS-01 challenge via the Cloudflare API.

#### Requirements

- A `CloudflareToken` row in the platform's database with a token that has `Zone:DNS:Edit` permission for the apex zone.
- The `CloudflareToken.token` is `EncryptedCharField` (Fernet at rest). The token is never returned in API responses.
- The token is read into the platform's environment at boot and used by the Caddy-side wildcard issuer. Caddy caches the token for 30 days; see [Token Cache](#token-cache).

#### Issuance Flow

1. The operator saves a `Domain` row with `wildcard=True`.
2. The platform calls the Cloudflare API to create a TXT record `_acme-challenge.example.com` with the Let's Encrypt challenge value.
3. Let's Encrypt verifies the TXT record.
4. The platform deletes the TXT record.
5. Caddy stores the wildcard cert in its cert store.

The flow runs in a Celery task with a 5-minute timeout. The TXT record cleanup is **always** attempted, even on verification failure — a stale `_acme-challenge` record will block future issuances.

### Self-Signed IP Cert

For installs that need HTTPS on a raw IP (e.g. a staging install on a public IP with no DNS), the platform can issue a self-signed cert. See `caddy_manager.py:88-125`.

The self-signed path:

- Generates a 2048-bit RSA key and a self-signed cert with the platform's public IP as the SAN.
- Stores the cert in the platform's `certs/` directory.
- Configures Caddy to serve it for the IP-only hostname.

The cert is **not trusted by browsers** — they will show a warning. This is intentional. The self-signed path is for internal use (Loki, Prometheus, admin dashboards) where the operator can install the cert in the trust store manually.

## Cloudflare Token Cache

The `CLOUDFLARE_TOKEN` (read from the platform's `CloudflareToken` row) is cached in Caddy's in-memory token store with a **30-day TTL**. The cache is keyed on the token's UUID; a token rotation invalidates the cache entry on the next access.

The 30-day TTL is a balance:

- Too short: Caddy thrashes the Cloudflare API on every cert renewal.
- Too long: a token rotation takes 30 days to propagate to Caddy.

For most operators, a 30-day cache is invisible — token rotations are rare. Operators who rotate frequently can set `CLOUDFLARE_TOKEN_CACHE_TTL_SECONDS` (env, default 2592000 = 30 days) to a shorter value.

## API Reference

### `POST /api/v1/services/{id}/add-domain/`

Add a hostname to a service's `custom_domains` list and trigger verification.

**Request body:**

| Field | Type | Notes |
| --- | --- | --- |
| `domain` | string | Required. A fully-qualified hostname. |

```bash
curl -sS -X POST http://localhost:8000/api/v1/services/9c8b4b1a-7d1c-4a2b-9a55-2e8c3d4f9b21/add-domain/ \
  -H "Authorization: Token $SMSLY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"domain": "api.example.com"}'
```

**Example response:**

```json
{
  "domain": "api.example.com",
  "status": "PENDING",
  "verification_token": "grid-verify-9c8b4b1a",
  "expected_cname": "9c8b4b1a.grid.example.com"
}
```

**Error responses:**

| Status | Cause |
| --- | --- |
| 400 | Invalid hostname; hostname is a loopback / private IP; hostname is already in use. |
| 403 | Caller is not the service owner. |
| 404 | Service not found. |

### `POST /api/v1/services/check-domain/`

Re-verify a hostname. Use this when DNS has been updated and the cached verification is stale.

**Request body:**

| Field | Type | Notes |
| --- | --- | --- |
| `domain` | string | Required. |

```bash
curl -sS -X POST http://localhost:8000/api/v1/services/check-domain/ \
  -H "Authorization: Token $SMSLY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"domain": "api.example.com"}'
```

**Example response (success):**

```json
{"domain": "api.example.com", "status": "VERIFIED", "resolved_ip": "203.0.113.10", "caddy_reloaded": true}
```

**Example response (failure):**

```json
{"domain": "api.example.com", "status": "FAILED", "reason": "IP_MISMATCH", "expected_ip": "203.0.113.10", "actual_ip": "198.51.100.5"}
```

### `GET /api/v1/system/domain-config/`

Read the global `Domain` row.

**Example response:**

```json
{
  "apex": "example.com",
  "wildcard": true,
  "cloudflare_token_configured": true,
  "verification_status": "VERIFIED",
  "last_issued_at": "2026-05-30T04:00:00Z",
  "next_renewal_at": "2026-07-29T04:00:00Z"
}
```

### `GET /api/v1/domains/`

List all `Domain` rows (admin only). Per-service hostnames are visible via the service detail endpoint.

## Security

### SSRF Protection

The verification flow resolves the hostname and follows the CNAME chain. Every IP in the chain is checked against `is_safe_ip()`:

- Loopback, link-local, multicast, reserved, unspecified: always rejected.
- RFC 1918 private: rejected **unless** `SSRF_ALLOW_PRIVATE=True` in the platform `.env` (the default for multi-node installs where services may be on private LANs).

The check uses the resolved IP at verification time. DNS-rebinding-style attacks (where the hostname resolves to a public IP at verify time and a private IP at request time) are mitigated by re-resolving at every Caddy `ask` call.

### SSRF `allow_private` Override

Multi-node installs frequently have services on private LANs. For these, the operator sets `SSRF_ALLOW_PRIVATE=True` in `.env` and the verification flow accepts private IPs that are in the `ManagedServer.private_ip_cidrs` list. The override is logged on every use.

### Audit Log

The platform writes `AuditLog` rows for the following events:

| Event | When |
| --- | --- |
| `DOMAIN_ADD` | A new hostname is added to a service. |
| `DOMAIN_DELETE` | A hostname is removed. |
| `DOMAIN_VERIFY` | Verification is run (success or failure). |
| `CADDY_RELOAD` | Caddy's config is reloaded. |
| `WILDCARD_ISSUE` | A wildcard cert is issued. |
| `WILDCARD_RENEW` | A wildcard cert is renewed. |
| `SELF_SIGNED_ISSUE` | A self-signed IP cert is issued. |

The audit chain is hash-linked — see `models_audit.py`. A misconfigured Caddy that emits many `CADDY_RELOAD` events is detectable by an audit log review.

## Frontend: the `/domains` Page

The `/domains` frontend page is a per-service listing of all hostnames, their verification status, and a "Check" button to re-run verification. The page was rewritten in Batch B to fix a routing bug (the previous version 404'd on direct load). The current version:

- Loads `GET /api/v1/services/?with_custom_domains=true` on mount.
- Groups by service.
- Shows the per-hostname `status` with a colored badge.
- "Check" button calls `POST /api/v1/services/check-domain/`.
- "Remove" button calls `DELETE /api/v1/services/{id}/add-domain/` (the `add-domain` endpoint handles removal via `DELETE`).

The page is reachable from the service detail page's "Domains" tab, or directly via `/domains?service_id=…`.

## Troubleshooting

### "Domain stuck in PENDING after CNAME set"

DNS propagation. The platform uses a 5-second timeout per resolution; some DNS providers cache the new CNAME for longer. Wait 5 minutes, then click "Check". If the issue persists, run `dig +trace api.example.com` to see the chain from the root.

### "Caddy ask endpoint returns 429"

Either the per-IP throttle (60/min) or the per-apex daily cap (20/day) has been hit. The per-IP throttle is usually a misconfigured Caddy loop — check Caddy's logs for repeated `ask` calls. The per-apex cap is usually a fresh install where a wildcard cert is being retried; the cap resets at UTC midnight.

### "Wildcard cert issuance fails with 'invalid Cloudflare token'"

The `CLOUDFLARE_TOKEN` does not have `Zone:DNS:Edit` permission. Re-issue the token in the Cloudflare dashboard with the correct scope, update the `CloudflareToken` row, and re-run.

### "Self-signed cert is rejected by curl with 'unable to get local issuer'"

Expected. The cert is self-signed and is not in the system trust store. Use `curl -k` to skip verification, or `curl --cacert /etc/grid/certs/ip.pem` to add the cert to the verification chain.

### "Let's Encrypt rate limit hit"

Let's Encrypt has a per-domain rate limit (50 certs per week per domain). If you have been issuing certs aggressively (e.g. adding and removing a hostname repeatedly), you may hit it. Wait for the rate limit to reset (Monday 00:00 UTC) and reduce the churn.

### "Verification says IP_MISMATCH but my A record is correct"

The platform's `PlatformConfig.public_ip` is stale. Update it via `POST /api/v1/system/update-config/` with the correct IP, then re-run verification.

### "Caddy is not picking up the new hostname"

The Caddy reload is async. The platform sets a `caddy_reload_pending=True` flag on the service after `add-domain/`, and Caddy picks it up on its next sync (typically within 5 seconds). If Caddy is down, the flag stays set and the reload happens on next boot.

## Limitations

- **No DNS-01 challenge for per-service certs.** Wildcards are the only DNS-01 path. Per-service certs use HTTP-01.
- **No ACME account pre-registration.** The platform trusts Caddy to handle the ACME account. If Caddy is replaced, the new server must re-register.
- **Caddy is the only reverse proxy.** Operators who want to use Traefik or nginx must configure them manually and disable the platform's Caddy integration.
- **The verification flow does not check CAA records.** Let's Encrypt will fail issuance if a CAA record on the apex forbids LE. The platform does not pre-check this; the failure surfaces as a `CADDY_RELOAD` audit log entry with `metadata.error="caa_record_forbids_le"`.
- **The 30-day Cloudflare token cache is a fixed default.** Operators who rotate tokens frequently must lower the TTL via env var.
- **No support for `.onion` or other non-ICANN TLDs.** Hostnames must be valid ICANN names.
- **Wildcard issuance is slow.** The first wildcard cert takes 30–60 seconds (DNS-01 round trip). Subsequent renewals are faster (cached TXT).
- **The frontend `/domains` page is per-service only.** A global domain listing (across all services) is not yet implemented.
- **No multi-region certs.** A single cert covers all the platform's public IPs. Operators who need region-specific certs (e.g. for compliance) must use a CDN with its own cert.
