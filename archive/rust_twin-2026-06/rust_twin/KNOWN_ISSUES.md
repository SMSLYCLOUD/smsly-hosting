# rust_twin P-batch — known issues

## Status after P1-P10 + integration (2026-06-17)

### What works
- `core` crate: `cargo check -p core` → clean, 0 errors, 0 warnings
- `core` crate: `cargo test -p core --lib` → 12 passed, 0 failed
- `worker` crate: `cargo test -p worker` → 4 passed, 0 failed
- `core` has 22 new entities (plan, subscription, invoice, cluster, mesh_node, node_election, heartbeat_log, safedeploy_approval, transfer_log, backup_record, tunnel, webhook, domain, social_account, social_app, social_token, addon_template) and 18 new migrations, all registered and compiling.

### What doesn't (yet)
- `api` crate: 14 errors, all in the P6 safedeploy service:
  1. `safedeploy_state` module path resolution — the P6 agent wrote the dispatcher as if the state machine were a sibling at `services/safedeploy/safedeploy_state.rs`; it's actually at `services/safedeploy_state.rs`
  2. `deployment.requester_id` field doesn't exist on the entity — would need to add it
  3. `ActiveModel::insert` / `into_active_model` API usage is wrong (sea-orm 1.1 changed the API)
  4. `.into_response()` trait bound issues on tuple return types
  5. The audit event log has an unused `body.reason` move

- The P9 middleware is not wired into the Axum router (would require a `Router::layer(...)` call which is an existing-file modification, deferred to a follow-up PR)

- The P7 webhooks dispatcher references `cn_core::entities::webhook` but `webhook` isn't yet registered in `core/entities/mod.rs` (added during integration, but the dispatcher code uses a stale path)

### Resolution plan

The 14 api errors are all in 1 file (`approval_dispatcher.rs`) plus 1 file (`routes.rs`). Fixing them is a 2-4 hour refactor that touches the deployment entity (adding a field) and the dispatcher (rewriting the sea-orm calls).

If we want this batch to land cleanly, the path is:
1. Add `requester_id: i32` to `deployment::Model` in `core/src/entities/deployment.rs` and add a migration
2. Rewrite `approval_dispatcher.rs` using the correct sea-orm 1.1 API (`ActiveModelTrait::insert`, `EntityTrait::update`)
3. Re-export `AuthUser` from `middleware/mod.rs` (done)
4. Add a `services/safedeploy_state` module declaration in `services/mod.rs` (done)

### What's documented but not implemented

- The P7 webhook dispatcher is functional code but the routes are not wired into the main router
- The P8 SSO providers have the trait + 3 implementations but the callback handler returns 501 NOT_IMPLEMENTED
- The P9 middleware functions exist but are not `.layer()`'d on the router

These can be fixed in a follow-up PR without further design work.

## Next steps

1. Decide whether to invest the 2-4 hours to fix the api crate compilation
2. If yes, dispatch a single agent with a focused 5-item punch list
3. If no, leave the api crate broken in this state and document the broken state in the README
