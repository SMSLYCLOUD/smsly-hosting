# grid Rust Twin Architecture

This document exhaustively details the complete architectural mapping between the legacy Python/Django stack and the new Rust workspace ecosystem (`rust_twin`).

## 1. System Topology & Crates

The legacy monolith has been decomposed into a highly modular Cargo Workspace, designed for concurrency, type safety, and minimal memory footprints.

| Legacy Component (Python) | Rust Crate (`rust_twin/crates/`) | Technology Stack |
| :--- | :--- | :--- |
| **Django Backend & DRF** | `api` | Axum, Tokio, Reqwest |
| **Django ORM & Models** | `core` | SeaORM (PostgreSQL/SQLite), `uuid`, `chrono` |
| **Celery Worker Engine** | `worker` | Tokio (spawn), `redis-rs` (BRPOP), `rand` |
| **Celery Beat (Cron)** | `worker` | Tokio (`interval` loop) |
| **Next.js React Frontend** | `frontend` | Leptos (CSR), `trunk`, TailwindCSS, WebAssembly |
| **manage.py** | `cli` | Clap, Argon2 |
| **TransferEngine (paramiko)** | `infrastructure/ssh.rs` | `ssh2`, TCP Streams |
| **Docker SDK (bollard)** | `infrastructure/docker.rs` | `bollard`, Unix Sockets, `StreamExt` |
| **AI Root Cause & Z-Score** | `intelligence` | Gemini REST API, custom statistical engine |

## 2. Request & Orchestration Flow

### A. Client Traffic Ingress
1. **External Traffic (Internet)** hits the `Caddy` reverse proxy (`Caddyfile`). Caddy handles automated Let's Encrypt SSL termination and Gzip/Zstd compression.
2. Caddy proxies the traffic internally to `localhost:8090` where the `Nginx` router listens.
3. **Nginx Routing:**
   - Traffic to `/api/*` is forwarded to the `api` (Axum) container on port `8000`.
   - All other traffic `/` is forwarded to the `frontend` (Nginx static + Leptos WASM) container on port `80`.

### B. Deployment Pipeline (The "Smart Deploy")
1. A user authenticates via the Leptos frontend (`POST /api/v1/auth/login`). The backend issues an Argon2-secured JWT.
2. The user clicks "Deploy" in the UI. The frontend sends a `POST /api/v1/projects/:id/deploy` request with the JWT Bearer token.
3. The `api` crate's `AuthUser` extractor validates the JWT. The handler queries SeaORM to verify ownership, creates a `PENDING` Deployment record, and pushes a JSON `SmartDeploy` task to the Redis `grid:tasks:default` list.
4. The `worker` crate's polling loop (`BRPOP`) pops the task and spawns a green thread (`tokio::spawn`).
5. The task updates the DB to `BUILDING`. It leverages the `infrastructure` crate to spawn the `nixpacks` CLI asynchronously, streaming the stdout/stderr build logs directly to `tracing`.
6. Once the image is built, the worker uses `bollard` to connect to the Docker socket, ensures the `smsly-net` bridge exists, and provisions the container (`UNLESS_STOPPED`).
7. The DB is updated to `RUNNING`, completing the zero-downtime deployment loop.

## 3. Background Ecosystem (Addons & Metrics)

### The Addon Provisioner
Instead of heavy Python subprocess calls, Addons (PostgreSQL, Redis) are provisioned via `Task::ProvisionAddon`. The worker generates cryptographically secure passwords locally (`uuid::Uuid::new_v4`), injects them as Docker Environment Variables (`Vec<&str>`) via Bollard, and saves the connection URI back into the database for the user.

### Telemetry & Billing (Celery Beat Replacement)
The legacy `celery-beat` system is replaced by a concurrent `tokio::try_join!` loop in `worker/src/main.rs`.
- **The Scheduler** ticks every 60 seconds, globally pushing `CollectUsage` tasks to Redis.
- **The Worker Threads** pop these tasks, fetch raw container telemetry (CPU, Memory) via the Docker socket, and append `ResourceUsage` records to the SeaORM database.
- **Billing API**: The `api/src/handlers/billing.rs` simulates external webhook events (like Stripe), upgrading the `PlatformLicense` Singleton to PRO or ENTERPRISE, securing feature access via the DB layer.

## 4. Parity Testing Framework
To prove the 1:1 functional exactness and performance gains of the new Rust architecture against the legacy Python monolith, a dedicated side-by-side parity testing suite is included.

### Running the Parity Suite
1. From the `rust_twin` directory, boot the unified test orchestration:
   ```bash
   docker compose -f docker-compose.parity.yml up -d --build
   ```
   *This starts the PostgreSQL DB, Redis broker, the old Python backend (`:8001`), and the new Rust backend (`:8002`) simultaneously.*

2. Run the concurrent tester script:
   ```bash
   python3 test_parity.py
   ```
   *This script fires asynchronous `requests` to both backends at the exact same millisecond, validating that both return identical HTTP status codes, and generates a `PARITY_REPORT.md` documenting the latency differences.*

## 5. Testing & CI/CD
The Rust twin enforces strict code quality and continuous integration:
- **Local Testing:** Integration tests bypass Docker entirely by leveraging `sqlx-sqlite` in-memory databases (`sqlite::memory:`). The `axum-test` crate verifies HTTP status codes and JWT middlewares without needing to bind to physical ports.
- **GitHub Actions (`rust-ci.yml`):**
  - **Caching:** Uses `Swatinem/rust-cache` to speed up compilations.
  - **Toolchain:** Installs `wasm32-unknown-unknown` to verify Leptos compilations.
  - **Linting:** Enforces strict `cargo fmt` and `cargo clippy -- -D warnings`.
  - **Testing:** Runs `cargo test` across all workspace members (excluding the WASM frontend, which requires browser emulation).
