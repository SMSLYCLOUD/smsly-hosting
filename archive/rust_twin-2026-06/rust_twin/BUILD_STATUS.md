# rust_twin build status (2026-06-16)

## Cargo toolchain

| Tool   | Status  | Version                            |
|--------|---------|------------------------------------|
| cargo  | present | `cargo 1.89.0 (c24e10642 2025-06-23)` |
| rustc  | present | `rustc 1.89.0 (29483883e 2025-08-04)` |

## Build attempt

Two clean-state runs were performed after `cargo clean` (removed 4682 files, 1.3 GiB).

| Command                                      | Exit | Errors | Warnings | Wall time |
|----------------------------------------------|------|--------|----------|-----------|
| `cargo check --workspace`                    | 0    | 0      | 0        | 2 m 28 s  |
| `cargo check --workspace --all-targets`      | 0    | 0      | 0        | 7 m 49 s  |

`--all-targets` adds `--tests`, `--benches`, `--examples`, `--bins`, so this also
exercises the `axum-test` integration test in `crates/api/tests/api_tests.rs`
and every `#[cfg(test)]` module.

Final cargo line in both cases:
```
Finished `dev` profile [unoptimized + debuginfo] target(s) in <time>
```

### Note on incremental-cache flakiness

Earlier, before `cargo clean`, the same `cargo check --workspace` command
reported 12 × E0583 ("file not found for module") errors against
`crates/core/src/migration/migrator.rs:3-14` and 1 × E0277 trait-bound error
against `crates/core/src/auth.rs:201`. After `cargo clean` and a full
recompile these errors disappear, so they are **stale incremental cache
artifacts**, not real compile errors. The actual source code is consistent
and compiles. If anyone hits these errors again, run `cargo clean` first.

## Per-crate dependency audit

Imports below are the **external crate roots** (`use foo::...`) used in each
crate's source. Local re-exports (`use crate::...`, `use super::...`) are
omitted. `std` is implicit.

### crates/core/Cargo.toml
- **Declared deps**: `tokio`, `serde`, `serde_json`, `anyhow`, `tracing`,
  `tracing-subscriber`, `sea-orm`, `dotenvy`, `envy`, `jsonwebtoken`,
  `argon2`, `chrono`, `sea-orm-migration`, `async-trait`, `pbkdf2`,
  `password-hash`, `sha2`, `sha1`, `base64`, `hex`, `bcrypt`
- **Imports used**: `anyhow`, `argon2`, `base64`, `bcrypt` (call site
  `bcrypt::verify`), `chrono` (call site `chrono::Utc`), `jsonwebtoken`,
  `pbkdf2`, `sea_orm`, `sea_orm_migration`, `serde`, `sha1`, `sha2`,
  `tracing`, `tracing_subscriber`, `async_trait` (attribute macro in
  `migration/migrator.rs:18`)
- **Missing**: none
- **Declared but unused (low confidence)**: `serde_json`, `dotenvy`,
  `envy`, `tokio`, `password-hash`, `hex` — these may be intentional
  re-exports for downstream crates or stub future use. Not a build blocker.

### crates/api/Cargo.toml
- **Declared deps**: `tokio`, `serde`, `serde_json`, `anyhow`, `tracing`,
  `axum`, `sea-orm`, `uuid`, `chrono`, `redis`, `cn_core`
- **Dev-deps**: `tower`, `axum-test`
- **Imports used**: `axum`, `sea_orm`, `serde`, `uuid`, `redis`, `cn_core`,
  `tracing`, `anyhow`, `tokio`
- **Missing**: none

### crates/worker/Cargo.toml
- **Declared deps**: `tokio`, `serde`, `serde_json`, `anyhow`, `tracing`,
  `sea-orm`, `uuid`, `chrono`, `redis`, `rand`, `cn_core`, `infrastructure`
- **Imports used**: `cn_core`, `infrastructure`, `sea_orm`, `redis`, `rand`,
  `anyhow`, `serde`, `tracing`
- **Missing**: none (the prompt expected `rand` to be undeclared — it IS
  declared at line 18)

### crates/cli/Cargo.toml
- **Declared deps**: `tokio`, `serde`, `anyhow`, `tracing`, `sea-orm`,
  `sea-orm-migration`, `dotenvy`, `clap`, `argon2`, `chrono`,
  `tracing-subscriber`, `cn_core`
- **Imports used**: `anyhow`, `argon2`, `clap`, `cn_core`, `sea_orm`, `tracing`
- **Missing**: none

### crates/infrastructure/Cargo.toml
- **Declared deps**: `tokio` (with `process`, `rt`, `macros` features),
  `serde`, `serde_json`, `anyhow`, `tracing`, `bollard`, `futures-util`,
  `ssh2`
- **Imports used**: `anyhow`, `ssh2`, `bollard`, `futures_util`, `tokio`,
  `tracing`
- **Missing**: none

### crates/frontend/Cargo.toml
- **Declared deps**: `leptos` (csr), `leptos_router` (csr), `serde`,
  `serde_json`, `reqwest`, `console_error_panic_hook`, `tracing`,
  `tracing-wasm`
- **Imports used**: `leptos`, `leptos_router`, `reqwest`, `serde`
- **Missing**: none

### crates/intelligence/Cargo.toml
- **Declared deps**: `tokio`, `serde`, `serde_json`, `anyhow`, `tracing`,
  `reqwest`
- **Imports used**: `anyhow`, `reqwest`, `serde_json`, `tracing`, `serde`
- **Missing**: none

### Summary table

| Crate          | Imports | Declared | Missing |
|----------------|--------:|---------:|--------:|
| core           | 14      | 21       | 0       |
| api            | 9       | 11 + 2 dev | 0     |
| worker         | 8       | 12       | 0       |
| cli            | 6       | 12       | 0       |
| infrastructure | 6       | 8        | 0       |
| frontend       | 4       | 8        | 0       |
| intelligence   | 5       | 6        | 0       |

## Compile errors found (by code review)

None. Every crate's `[dependencies]` block covers every external crate
its source files import. The expected `bollard`, `ssh2`, `argon2`,
`jsonwebtoken`, `clap`, `leptos`, `redis`, `reqwest`, `dotenvy`, `uuid`,
`chrono` are all declared in the crate(s) that need them.

## What needs to happen before this can run

### Blocking issues (compile errors)
- None.

### Non-blocking but required for the stated purpose
- Workspace compiles but the audit only proves it **type-checks**; it has
  not been linked (`cargo build`) nor exercised (`cargo test`).
- The `frontend` crate is configured for `leptos` CSR (browser). Running
  it requires `wasm32-unknown-unknown` target + `trunk` or `wasm-bindgen-cli`,
  none of which are part of the workspace tooling.
- Integration test `crates/api/tests/api_tests.rs` uses `axum-test` with a
  `sea_orm::Database` — it will need a real DB URL or sqlite-in-memory at
  runtime; this report did not run it.
- Possible dead deps in `core/Cargo.toml` (`serde_json`, `dotenvy`, `envy`,
  `tokio`, `password-hash`, `hex`) — worth a cleanup pass but not blocking.

## Verdict

**Compiles.** `cargo check --workspace` and `cargo check --workspace --all-targets`
both succeed with **0 errors and 0 warnings** from a clean target directory on
rustc 1.89.0 / cargo 1.89.0. The prior "0-9 line scaffolding" characterization
was wrong; this is 42 real source files, ~84 KB of Rust, organized as a
7-crate workspace, and it type-checks end-to-end.
