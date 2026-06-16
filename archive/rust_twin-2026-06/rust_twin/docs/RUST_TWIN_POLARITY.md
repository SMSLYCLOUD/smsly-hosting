# rust_twin vs Django: polarity analysis

**Generated:** 2026-06-16
**Verdict:** Not polarity-compatible. Not a port. Parallel implementation.

## What "polarity" means here

Two systems have polarity if they are interchangeable: same wire protocol,
same data model, same auth, same deployment model. You could run them
side-by-side or cut over from one to the other without losing data or
breaking clients.

The rust_twin does not have polarity with the Django backend.

## Detailed gap analysis

### 1. Wire protocol: HTTP routes

The rust_twin implements 8 routes. The Django backend implements 100+.

| Path | Django | rust_twin | Body match? |
|---|---|---|---|
| `GET /health` | yes | yes | no (Django returns JSON, rust_twin returns "OK") |
| `POST /api/v1/auth/login` | yes (DRF token in cookie) | yes (JWT in body) | no |
| `GET /api/v1/projects` | yes | yes | ~ (different field shapes) |
| `GET /api/v1/billing/license` | yes | yes | ~ (different field names) |
| `GET /api/v1/teams` | yes | yes | ~ |
| `POST /api/v1/billing/upgrade` | yes (Cryptomus + Stripe) | yes (mocked) | no (real payment missing) |

**97% of Django's routes are not implemented in rust_twin.**

### 2. Data model

Django models: ~150 across 12 apps. 92+ migrations.
rust_twin sea-orm entities: 13. 12 migrations (added in B3).

| Domain | Django | rust_twin | Polarity |
|---|---|---|---|
| User | `auth.User` + `teams.User` | `user` | ~ (similar fields) |
| Project | `deployments.Project` | `project` | ~ |
| Service | `deployments.Service` | `service` | ~ |
| Deployment | `deployments.Deployment` (with status enum) | `deployment` (with String status) | no |
| Addon | `models_addons.Addon` | `addon` | ~ |
| Team | `teams.Team` | `team` | ~ |
| TeamMember | `teams.TeamMember` | `team_member` | ~ |
| Cron | `models_cron.Cron` | `cron` | ~ |
| EnvVar | `deployments.EnvironmentVariable` | `environment_variable` | ~ |
| License | `licensing.PlatformLicense` | `platform_license` | ~ |
| APIKey | `core.ApiKey` | `api_key` | ~ |
| Usage | `billing.Usage` | `usage` | ~ |
| **Missing from rust_twin** | Region, ComplianceProfile, Backup, CloudStorage, Election, Mesh, Metrics, Replica, Safedeploy, Server, Storage, Template, Transfer, Tunnel, Update, Webhook, etc. (~30+ models) | | no |

**~90% of Django's data model is missing from rust_twin.**

### 3. Auth

| | Django | rust_twin |
|---|---|---|
| Token format | DRF token (40 char hex) + HttpOnly cookie | JWT (HS256, 24h) |
| Password hash | Argon2, PBKDF2, bcrypt, PBKDF2-SHA1 | Argon2 (B2 adds Django hash compat) |
| Cookie attributes | `__Host-`, Secure, SameSite=Strict, HttpOnly | (no cookies) |
| WebSocket auth | subprotocol-based | (no WebSocket) |
| Throttling | per-action, IP-aware | (none) |
| MFA | (not implemented) | (not implemented) |

**No polarity.** A Django user cannot use their token in the rust_twin and
vice versa. B2 fixes password hash compat for login, but token format
remains divergent.

### 4. Task queue

Django uses Celery 5.6 with 3 queues (`celery`, `deploy`, `fast`).
rust_twin uses a single Redis list `grid:tasks:default`.

B5 (this PR set) adds a Celery message-format parser, but the worker
still uses its own sea-orm entities. The schema-entity mismatch is
unsolved.

### 5. Frontend

Django serves the Next.js 15 frontend (100+ routes, App Router).
rust_twin's Leptos frontend has 4 routes. No code reuse, no design system
alignment.

To make the rust_twin usable, the Next.js frontend would need to either:
- (a) Be ported to Leptos (3-6 months)
- (b) Be retained and pointed at the Rust API (1-2 weeks of API shape
  alignment, plus auth migration)

### 6. Deployment

Django backend:
- Docker Compose (3 variants: dev, prod, e2e)
- Helm chart (12 templates, securityContext, NetworkPolicy, PDB)
- Installer (7,232 lines, hardened by Batch S5)
- CI (6 GitHub workflows, pinned actions, SCA, secret scanning)

rust_twin:
- No Docker Compose
- No Helm chart
- No installer
- No CI

### 7. Operational concerns

| Concern | Django | rust_twin |
|---|---|---|
| Logging | python-json-logger, Sentry | tracing (text logs) |
| Metrics | Prometheus | none |
| Distributed tracing | none | none |
| Health checks | /health (DB + cache), /health/live, /health/ready | /health (returns "OK") |
| Feature flags | tier gates (`SMSLY_DISABLE_TIER_GATES`) | none |
| Audit log | per-action with redaction | none |

## What would change my mind about reviving this

- A specific business need for a Rust-based control plane (latency,
  memory footprint, single-binary deploy) that Django can't meet
- A 4-6 month investment with a clear deliverable
- Willingness to maintain two implementations in lockstep (the
  maintenance burden is 2x, not 1x)
- Migration tooling to move all users, all encrypted data, all
  in-flight tasks from one to the other

Without these, the rust_twin should remain archived.
