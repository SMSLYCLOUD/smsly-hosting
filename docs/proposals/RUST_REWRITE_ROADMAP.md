# Grid Rust Rewrite Roadmap

## Objective
Rewrite the entire Grid platform (Python/Django Backend, Celery Workers, Next.js Frontend) into a single, high-performance, unified Rust workspace. This roadmap is designed for AI agents to incrementally and safely migrate the system.

## Architectural Mapping

| Existing Component | Rust Replacement | Purpose |
| :--- | :--- | :--- |
| **Backend Framework** (Django) | `axum` | High-performance, async HTTP routing and middleware. |
| **Database ORM** (Django ORM) | `sea-orm` | Async, macro-driven ORM supporting PostgreSQL. |
| **Background Workers** (Celery) | `faktory-rs` or custom Redis+`tokio` | Handling long-running tasks (builds, deployments, SSH transfers). |
| **Database Migrations** | `sea-orm-cli` | Managing schema changes outside of Python. |
| **Frontend** (Next.js/React) | `leptos` or `dioxus` | Full-stack Rust WASM framework for the UI. |
| **Management CLI** (manage.py) | `clap` | Custom CLI binary for administrative tasks (e.g., `setup_social_apps`, `createsuperuser`). |
| **Process Manager** (Gunicorn/Celery) | `tokio` | Native async runtime, compiling to single binaries. |

---

## Progress Tracker

- [x] **Phase 1: Foundation & Infrastructure Setup** (Workspace, `.env` config, Postgres pooling, Tracing)
- [x] **Phase 2: Database Schema & Core Models** (SeaORM `User`, `Project`, `APIKey`, `Service`, `Deployment`)
- [x] **Phase 3: The API Layer** (Axum routing, `GET/POST /projects` endpoints)
- [x] **Phase 4: Background Workers & Orchestration** (Redis `brpop` polling, Tokio concurrent tasks)
- [x] **Phase 5: The Frontend UI** (Leptos WASM CSR, Client-side routing, `reqwest` integration)
- [x] **Phase 6: CLI Tool** (Clap-based management binary, `createsuperuser` with Argon2)
- [x] **Phase 7: Infrastructure Integrations** (Bollard Docker API wrapper, Tokio async Nixpacks builder)
- [x] **Phase 8: Docker Orchestration** (`cargo-chef` multi-stage builds, Trunk compilation, `docker-compose.yml`)
- [x] **Phase 9: Network & Proxy Parity** (Internal Nginx routing, External Caddy SSL termination, `deploy.sh`)
- [x] **Phase 10: Authentication & Security** (JWT generation/validation, Axum `AuthUser` extractor, Leptos Login UI)
- [x] **Phase 11: Deployment Pipeline** (`POST /deploy` endpoint, Redis queuing, Worker Docker execution)

---

## Next Steps for Future Agents

*   **Phase 12: Billing Integration** (Migrate Stripe/Cryptomus logic, update `PlatformLicense` models)
*   **Phase 13: Auto-Remediation & Intelligence** (Port the Z-Score statistical anomaly detection to Rust, integrate Gemini/OpenAI API clients)
*   **Phase 14: SSH Transfer Engine** (Implement `thrussh` or `ssh2` for the zero-downtime ServerTransferService)

---

## Workspace Structure
The new project is housed in a Cargo workspace (`rust_twin/`) sharing models and logic across the stack:

```
rust_twin/
├── Cargo.toml                  # Workspace definition
├── crates/
│   ├── core/                   # Shared models, DB connections, SeaORM entities, constants
│   ├── api/                    # Axum web server (replaces backend/apps)
│   ├── worker/                 # Background task runner (replaces Celery)
│   ├── cli/                    # Management commands (replaces manage.py)
│   ├── infrastructure/         # External integrations (Docker API, Nixpacks, Caddy/Nginx)
│   └── frontend/               # Leptos/Dioxus WASM UI (replaces frontend/src)
```

---

## Agent Execution Phases

**Instructions for Agents:** Do not attempt to rewrite everything at once. Execute one phase, verify compilation (`cargo check`), write tests, and commit before moving to the next phase.

### Phase 1: Foundation & Infrastructure Setup
**Goal:** Establish the Rust workspace, configure database connections, and replicate the initial `.env` configuration logic.

1.  **Initialize Workspace:** Create the `rust_twin/` directory and structure the crates defined above.
2.  **Configuration Management:** Create a robust config loader in `crates/core/src/config.rs` using the `config` crate. Map all existing `.env` variables (`SECRET_KEY`, `POSTGRES_PASSWORD`, `REDIS_URL`, etc.).
3.  **Database Connection Pool:** Set up a `sqlx::PgPool` or `sea_orm::DatabaseConnection` in `crates/core/src/db.rs` using the connection strings.
4.  **Logging & Tracing:** Integrate `tracing` and `tracing-subscriber` for structured logging, replacing Python's `logging` module.

### Phase 2: Database Schema & Core Models (The `core` crate)
**Goal:** Migrate the PostgreSQL schema from Django to SeaORM.

1.  **Reverse Engineer DB:** Use `sea-orm-cli generate entity` against a running instance of the existing PostgreSQL database to generate initial Rust structs in `crates/core/src/entities/`.
2.  **Model Polish:** Refine the generated entities. Implement custom types (like the Fernet encrypted fields, which will require a Rust equivalent like `aead` or `fernet`).
3.  **Migrations:** Set up `sea-orm-migration` to mirror the Django migrations (`backend/apps/*/migrations/`). Recreate the initial schema creation scripts.
4.  **Verify:** Write integration tests in `crates/core/tests/` that connect to a test database and perform basic CRUD operations on core models (Users, Projects, Deployments).

### Phase 3: The API Layer (The `api` crate)
**Goal:** Replicate the Django REST Framework endpoints using Axum.

1.  **Routing Scaffolding:** Create the initial Axum router in `crates/api/src/main.rs`. Set up CORS to match the existing Django configuration.
2.  **Authentication Middleware:** Replicate `TierLimitsMiddleware` and the HMAC/OAuth flows. Implement JWT or session-based auth using `axum-login` or custom extractors.
3.  **Endpoint Migration Strategy (Iterative):**
    *   *Agent Task 3.1:* Migrate Health Checks (`/health`) and basic public endpoints.
    *   *Agent Task 3.2:* Migrate Authentication/OAuth logic (`/api/v1/auth/`).
    *   *Agent Task 3.3:* Migrate Project & Deployment APIs (`/api/v1/projects/`, `/api/v1/deployments/`).
    *   *Agent Task 3.4:* Migrate complex domains (Billing, Intelligence, Addons).
4.  **Validation:** Replace Django serializers with `serde` and `validator` crate structs for robust input validation.

### Phase 4: Background Workers & Orchestration (The `worker` crate)
**Goal:** Replace Celery with a high-performance Rust daemon for long-running infrastructure tasks.

1.  **Queue Selection:** Implement a Redis-backed queue system. If using a raw Redis stream approach, use `redis-rs` + `tokio::spawn`.
2.  **Docker/Nixpacks Integration:** Migrate `backend/apps/cloud/services/builder.py`. Use the `bollard` crate (Rust Docker API) to manage containers, networks, and volumes instead of the Python Docker SDK.
3.  **Transfer Engine:** Rewrite the `ServerTransferService` (paramiko SSH logic). Use `thrussh` or `ssh2` in Rust to handle secure file transfers and zero-downtime migrations.
4.  **Task Migration:** Move the logic for `smart_deploy_task`, `collect_usage_task`, and platform update scripts into async Rust functions. Ensure strong error handling (`Result<T, E>`) to prevent silent failures.

### Phase 5: The Frontend UI (The `frontend` crate)
**Goal:** Replace the Next.js/React application with a Rust-native WASM framework.

1.  **Framework Setup:** Initialize a Leptos or Dioxus project within `crates/frontend/`.
2.  **Routing & Layouts:** Replicate the Next.js App Router structure. Create the base `DashboardLayout` and authentication guards.
3.  **State Management:** Replace React Context/Zustand with Leptos signals or Dioxus hooks.
4.  **Component Migration (Iterative):**
    *   *Agent Task 5.1:* Migrate UI Primitives (Buttons, Cards, Modals - replicating Shadcn).
    *   *Agent Task 5.2:* Migrate the Authentication Pages (Login, OAuth callbacks).
    *   *Agent Task 5.3:* Migrate the core Dashboard (Projects List, Topology Canvas).
    *   *Agent Task 5.4:* Migrate complex settings pages (Environment Variables, Domains).
5.  **API Integration:** Use `reqwest` or the framework's native `Server Functions` (if using Leptos SSR) to communicate with the `api` crate.

### Phase 6: CLI & Deployment Parity (The `cli` and `infrastructure` crates)
**Goal:** Ensure the Rust twin can be deployed exactly like the original.

1.  **CLI Tools:** Replicate `manage.py createsuperuser` and `setup_social_apps` using `clap` in the `cli` crate.
2.  **Reverse Proxy Sync:** Ensure the Caddy and Nginx logic in the existing `install.sh` and `docker-compose.yml` routes traffic correctly to the compiled Rust binaries (e.g., routing `:8000` to the Axum binary instead of Gunicorn).
3.  **Dockerization:** Write multi-stage `Dockerfile`s for the `api`, `worker`, and `frontend` crates. Ensure the final images are minimal (e.g., using `alpine` or `distroless`).
4.  **Integration Testing:** Stand up the entire new stack (DB, Redis, Axum API, Rust Worker, WASM Frontend) via `docker-compose.rust.yml` and run end-to-end API tests.

---

## Agent Guidelines & Warnings

*   **Borrow Checker:** Python is highly mutable; Rust is not. Expect to redesign complex data flows (especially around the Deployment Pipeline and `PipelineManager`) to satisfy the borrow checker. Favor passing by value or using `Arc<Mutex<T>>` sparingly for shared state.
*   **Error Handling:** Do not use `.unwrap()` in production code. Use `anyhow` for application-level errors or `thiserror` for library-level errors to provide deep context.
*   **Type Safety vs Zod:** The frontend currently lacks Zod validation. In Rust, `serde` and the `validator` crate will strictly enforce payload structures. This means the API surface must be perfectly typed.
*   **Transactions:** The existing billing and auto-remediation logic relies heavily on `transaction.atomic` with `select_for_update`. Ensure SeaORM transactions replicate this behavior perfectly to avoid race conditions.
