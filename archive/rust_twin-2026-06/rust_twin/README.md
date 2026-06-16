# rust_twin

> **STATUS (2026-06-16): PROTOTYPE / PROOF OF FEASIBILITY**
>
> This is a parallel Rust implementation of the SMSLY PaaS control plane
> that was being prototyped in 2024-2025. It is **not production-ready**
> and **not polarity-compatible** with the existing Django deployment.
>
> The codebase has been **archived** (moved to `archive/rust_twin-2026-06/`)
> as of 2026-06. See `docs/CLI_UNIFICATION_DECISION.md` in the parent
> project for the rationale.

## What's here

A Cargo workspace with 7 crates that together implement ~10-15% of the
functionality of the Django backend:

| Crate | Lines | Purpose | State |
|---|---|---|---|
| `crates/core` | ~3,500 | Domain entities (sea-orm), config, db pool, telemetry, auth, migration | Scaffolded entities; migration set added in B3 |
| `crates/api` | ~3,000 | Axum HTTP server with auth, projects, billing, teams handlers | Functional for the 8 endpoints it implements |
| `crates/worker` | ~2,000 | Redis-queue worker that processes SmartDeploy, ProvisionAddon, CollectUsage | Functional; Celery bridge added in B5 |
| `crates/cli` | ~120 | `smsly` CLI: create-superuser, migrate, setup-social-apps | Functional but admin defaults to `admin123` |
| `crates/infrastructure` | ~900 | Docker (bollard), Nixpacks builder, SSH/SCP transfer (ssh2) | Functional but not wired to the worker tasks |
| `crates/frontend` | ~260 | Leptos (Rust WASM) frontend with login + projects | 4 routes; not the main UI |
| `crates/intelligence` | ~100 | Z-score anomaly detection | Standalone; no LLM provider integration |

**Total:** 42 .rs files, ~84 KB of code.

## What it can do (verified by reading the code)

- Bootstrap an Axum server with config, DB pool, Redis connection, telemetry
- Handle 8 HTTP routes: `/health`, `/api/v1/auth/login`, `/api/v1/auth/register`,
  `/api/v1/projects`, `/api/v1/projects/:id/deploy`, `/api/v1/billing/license`,
  `/api/v1/billing/upgrade`, `/api/v1/teams`
- Connect to a Postgres + Redis backend
- Hash passwords with Argon2 (added in B2: also verify Django's PBKDF2/bcrypt hashes)
- Parse Celery task messages from Redis and dispatch to the Rust task handler
- Build Docker images via Nixpacks
- Transfer files via SSH/SCP
- Run a 60-second scheduler that emits `CollectUsage` tasks

## What it CANNOT do (gaps vs Django)

- **No deployment lifecycle states** (only BUILDING/RUNNING/FAILED; Django has
  AWAITING_APPROVAL, DEPLOYING, ROLLING_OUT, ROLLED_BACK, etc.)
- **No addon marketplace / templates** (only hardcoded POSTGRES and REDIS)
- **No tunnel system** (`apps/deployments/services/tunnels/` is entirely missing)
- **No webhooks** (no SSRF guard, no HMAC, no delivery tracking)
- **No domain management** (no Let's Encrypt, no DNS validation)
- **No team RBAC** (entities exist; enforcement is minimal)
- **No intelligence providers** (no LLM adapters, no Jules, no scanners)
- **No observability** (no Prometheus, no Loki, no Grafana)
- **No security middleware** (no HMAC V2, no CSRF, no rate limiting)
- **No mesh / election / WireGuard** (`apps.deployments/tasks_election.py` etc.
  is missing)
- **No replication** (no `apps/deployments/services/replication_service.py` equivalent)
- **No backups** (no `apps/deployments/services/backup_service.py` equivalent)
- **No transfers** (no `apps/deployments/services/transfer_service.py` equivalent)
- **No safedeploy** (no approval workflow, no rollback)
- **No Celery task bridge to Django ORM** (B5 bridges the message format only;
  the worker still uses its own sea-orm entities, not Django's)

## Polarity with Django: not compatible

| Concern | Django | rust_twin | Polarity |
|---|---|---|---|
| Schema | 92+ migrations, ~150 models | 12 sea-orm migrations, 13 entities | **~ intentionally divergent** |
| Auth | DRF token, HttpOnly cookie, HMAC middleware | JWT (HS256, 24h) | **different wire format** |
| Password hash | Argon2 primary, PBKDF2, bcrypt, PBKDF2-SHA1 | Argon2 (B2: also verifies Django hashes) | **OK after B2** |
| Task queue | Celery 5.6 with 3 queues | Redis list `grid:tasks:default` (B5: also Celery-format) | **~ message format only** |
| Frontend | Next.js 15 + React 19 (100+ routes) | Leptos WASM (4 routes) | **different UI** |
| Web framework | Django 5.0 + DRF + Channels | Axum 0.7 | **different** |
| ORM | Django ORM | SeaORM 1.0 | **different entities** |
| Deployment | Helm + Caddy + Docker | (not deployed) | **n/a** |
| CI | 6 GitHub workflows | (none) | **n/a** |

The rust_twin is **not a port**. It is a parallel implementation. To use it
as a port, you would need to:

1. **Schema bridge** (1-2 weeks): Either write sea-orm migrations matching
   Django's 92, or use Django's managed schema and read-only via sea-orm.
2. **Auth bridge** (1 week): Translate between DRF tokens and JWTs, or
   migrate all users to JWTs.
3. **UI bridge** (3-6 months): Either port the Next.js UI to Leptos, or
   keep the Next.js UI and have it talk to the Rust API.
4. **Deployment pipeline bridge** (2-3 weeks): AWAITING_APPROVAL,
   canary, rollback, mesh, WireGuard, election, replication, tunnels,
   webhooks, domains, billing, intelligence.

Realistic estimate: **4-6 engineer-months** to get from "84 KB of real Rust"
to "production parity". The deliverable would be two implementations of the
same thing — the maintenance burden would double.

## How to build

Requires Rust 1.75+ and a Postgres + Redis reachable via env vars.

```bash
cd archive/rust_twin-2026-06/rust_twin
cargo build --release
./target/release/api       # starts the API on $PORT (default 8080)
./target/release/worker    # starts the worker polling Redis
./target/release/cli migrate  # runs the 12 sea-orm migrations
./target/release/cli create-superuser --username admin --email [email protected] --password <pw>
```

## How to test

```bash
cargo test --workspace
python3 test_parity.py --django-url http://localhost:8000 --rust-url http://localhost:8080
```

## Honest assessment of the previous version

The previous `PARITY_REPORT.md` showed identical `10.00ms` for every
endpoint, which were not real measurements. The `test_parity.py` was a
script that just printed the literal string. The 2026-06-16 audit
replaced both with a real measurement harness.

The previous audit also said the rust_twin was "0-9 line scaffolding",
which was wrong. The actual codebase is 84 KB of real, well-structured
Rust with proper async, error handling, tracing, and entity modeling.
The previous audit was just looking at the wrong files (only the
smallest lib.rs files, not the handlers and infrastructure).

## Decision

The 2026-06 deep sweep concluded that:
- Continuing to maintain the rust_twin is not justified (10-15% coverage
  of Django, no path to polarity without 4-6 months of work)
- The codebase is preserved in `archive/rust_twin-2026-06/` for
  historical reference
- Anyone wanting to revive a Rust rewrite should start fresh with the
  lessons learned (sea-orm + axum + bollard + ssh2 is a sensible stack)

## See also

- `PARITY_REPORT.md` — actual parity status
- `ARCHITECTURE.md` — the original design document (unchanged)
- `BUILD_STATUS.md` — whether the code compiles as of 2026-06-16
- `docs/RUST_TWIN_POLARITY.md` (in this archive) — full gap analysis
