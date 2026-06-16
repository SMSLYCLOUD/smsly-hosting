# rust_twin CI

A CI workflow (`.github/workflows/rust-ci.yml`) was added back on 2026-06-16
as part of the rust_twin revival (B1-B6). It runs:

- `cargo fmt --check` (continue-on-error: true — code is not yet fmt-clean)
- `cargo clippy -- -D warnings` (continue-on-error: true — code has warnings)
- `cargo check --workspace` (NOT continue-on-error — we want compile failures to fail)
- `cargo test --workspace --exclude frontend` (continue-on-error: true)

The workflow triggers on:
- Push to `main` that changes `archive/rust_twin-2026-06/rust_twin/**`
- PR that changes that path
- Manual `workflow_dispatch`

## What you'll see initially

The first time the workflow runs, expect:
- `cargo check` may fail (compile errors not yet fixed; B1 audit will tell you which)
- `cargo fmt` and `cargo clippy` will fail (code is not yet clean)
- `cargo test` will fail (most tests don't exist; B2-B5 added some)

Once the code is stable, remove the `continue-on-error: true` flags to make
these blocking.
