# Autoscaling

Grid ships three autoscaler implementations that work together. The classic CPU-based engine handles day-to-day scale up / scale down with predictable hysteresis. The AI-enhanced engine adds Prometheus + Loki metrics, anomaly detection, and a paginated batch driver. The K8s / Docker admin surface provides manual replica control for operators.

## Overview

There are three distinct autoscaler code paths:

| Path | Module | Trigger | Scope |
| --- | --- | --- | --- |
| Classic CPU | `apps/autoscaler/services/legacy_autoscaler.py` | Celery beat, every 30 seconds | Every service, CPU threshold |
| AI-enhanced | `apps/autoscaler/services/tasks_autoscale.py` + `scaling_ai.py` | Celery beat, every 180s | Every service, Prometheus + Loki + AI |
| K8s / Docker admin | `apps/autoscaler/views/dashboard.py` | Manual (HTTP) | One service, per-call |

The classic engine is the **default** and is what the platform runs out of the box. The AI-enhanced engine is opt-in via `AUTOSCALER_AI_ENABLED=True` and requires the `prometheus_loki` integration. The admin surface is always available but requires `IsAdminUser`.

All three share the same `Service` fields (`min_replicas`, `max_replicas`, `autoscale_cpu_target`, `last_scale_at`) and the same `MAX_REPLICAS` global guard. They coordinate via a single row-level lock (see [Race Conditions](#race-conditions)).

## Unified Engine (`apps.autoscaler.engine`)

The two per-`Service` engines (Classic + AI-Enhanced) have been refactored onto a single pipeline so the three code paths cannot diverge and the periodic Celery tasks cannot double-spawn replicas for the same service.

| Module | Role |
| --- | --- |
| `apps/autoscaler/engine/metrics.py` | `MetricsCollector` with fallback chain `db → prometheus → docker`. The two periodic tasks prefer `db` (fast, no network); the on-demand REST `analyze` endpoint prefers `prometheus` (fresher). |
| `apps/autoscaler/engine/decision.py` | Pure `DecisionEngine` — converts a `MetricsSnapshot` + cooldown + replica state into a `Recommendation` (`scale_up` / `scale_down` / `none`). No I/O. |
| `apps/autoscaler/engine/reconciler.py` | `Reconciler` applies a `Recommendation` via `SpawningService` (local first, then `NodeScorer` for remote). Holds a **per-service `threading.Lock`** to serialize concurrent invocations. |
| `apps/autoscaler/engine/pipeline.py` | `analyze_and_apply(service)` and `analyze_only(service)` are the public entry points. All three Celery tasks and the legacy REST endpoint go through these. |
| `apps/autoscaler/engine/container_metrics.py` | Container-level primitives (K8s metrics API, `docker stats`, unit parsing) shared with the platform-level autoscaler in `apps/autoscaler/views/dashboard.py`. |

The K8s / Docker admin surface in `apps/autoscaler/views/dashboard.py` is **not** on this pipeline: it scales *platform* containers (celery, gunicorn, customer apps via Swarm/K8s deployments) using a `demand_score` on raw container metrics, not `Service.autoscale_cpu_target`. The two engines are intentionally separate.

### Race Condition Prevention

The three periodic tasks (`check-autoscale-every-30s`, `auto-scaling-analyze-every-3m`, `autoscaler-collect-stats-every-60s`) used to be able to spawn replicas for the same service in parallel. The `Reconciler` now holds an in-process `threading.Lock` keyed by `service.id` so the second invocation sees the updated `last_scale_at` and the cooldown logic short-circuits. The lock is per-service — work for service A never blocks work for service B. This is verified by `test_autoscaler_engine.py::ReconcilerRaceConditionTests`.

## Classic Engine (`services/autoscaler.py`)

The classic engine is a CPU-based, two-threshold controller with asymmetric cooldowns. It runs on Celery beat every 30 seconds.

### How It Works

For each service with `min_replicas > 0` (or `autoscale_cpu_target > 0`):

1. Read the **current** CPU average over the last minute (sourced from `docker stats` on the local node, or from a `ManagedServer` proxy call on a remote node).
2. Compare to `autoscale_cpu_target` (default 80).
3. **Scale up** if `cpu > target` AND the service is not in cooldown.
4. **Scale down** if `cpu < target * 0.5` (wider hysteresis on the way down, to absorb brief lulls) AND the service is not in cooldown.
5. Update `Service.last_scale_at` and exit.

The asymmetric cooldown is the key invariant: **scale-up cooldown is 3 minutes, scale-down cooldown is 10 minutes**. This is configurable via `SCALE_COOLDOWN_MIN` and `SCALE_COOLDOWN_DOWN_MIN` environment variables.

### The `last_scale_at` Field (NOT `updated_at`)

The cooldown is computed from `Service.last_scale_at`, **not** from `Service.updated_at`. The `updated_at` field is touched by any model save (env var edit, manual replica change, settings update) — using it for cooldown would let a side effect reset the autoscaler's clock. The `last_scale_at` field is only written by the autoscaler itself, on a real scale event. The same field is also written by the [AI-enhanced engine](#ai-enhanced-engine-tasks_autoscalepy--scaling_aipy) so the two engines cannot oscillate against each other on the same service.

### API Reference

The classic engine has no user-facing API. It writes to `Service` directly. Operators can read the current state via `GET /api/v1/services/{id}/`.

## AI-Enhanced Engine (`tasks_autoscale.py` + `scaling_ai.py`)

The AI-enhanced engine is a superset of the classic one. It uses Prometheus for CPU / memory metrics, Loki for runtime log volume, and (when configured) the Senate Committee for capacity recommendations. It runs on a 180-second beat.

### Prometheus + Loki Integration

Metrics are scraped from the platform's Prometheus instance. The engine queries:

- `sum(rate(container_cpu_usage_seconds_total{service=~"<name>"}[1m]))` — CPU rate
- `sum(container_memory_usage_bytes{service=~"<name>"})` — memory footprint
- `sum(rate(loki_log_entries_total{service=~"<name>"}[1m]))` — log volume rate

If the platform's Loki is not running, the engine falls back to the classic `docker stats` path. The integration is detected at runtime via the `PROMETHEUS_LIVE` and `LOKI_LIVE` flags on `PlatformConfig`.

### Paginated Batch via `id__gt` Cursor

The engine walks all services in batches of 20 using a keyset cursor on the primary key:

```python
qs = Service.objects.filter(id__gt=cursor).order_by("id")[:20]
```

This avoids the `OFFSET` performance cliff on large fleets. The cursor is held in `cache.set("autoscale:cursor", last_id, 600)` so a worker crash resumes from the same point. The walk is incremental: each 60-second tick advances the cursor by 20 services. A fleet of 10 000 services takes 500 ticks (~500 minutes) to complete a full sweep. The cursor is reset to 0 at the end of a sweep.

### AI Recommendations

When `AUTOSCALER_AI_ENABLED=True` and an LLM is configured, the engine consults the Senate Committee on scale-up decisions that exceed `max_replicas * 0.8` (i.e. the engine is about to hit the ceiling). The model is asked: "given the last 24 hours of CPU, memory, and request volume, should we raise `max_replicas` or hold it?" The response is logged to `AuditLog` with `actor='AI_SCALER'` and is **advisory only** — the engine does not auto-raise `max_replicas` based on the model output. An operator must approve the change in the UI or via API.

## K8s / Docker Admin (`apps/autoscaler/views.py`)

The admin surface exposes a manual replica controller. It requires `IsAdminUser` (staff status) and is gated by `ADMIN_AUTOSCALER_ENABLED` (env, default `True`).

### Endpoints

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/api/v1/scaling/{service_pk}/analyze/` | `POST` | Run a one-shot analysis on a service. Returns the current CPU, memory, replica count, and a recommended `desired_replicas` (with reasoning). |
| `/api/v1/scaling/{service_pk}/spawn/` | `POST` | Force-spawn a replica. Bypasses cooldowns. Audit-logged. |
| `/api/v1/scaling/replicas/` | `GET` | List current replica state for a service. Use query param `?service=<UUID>`. |
| `/api/v1/scaling/destroy_replica/` | `DELETE` | Force-destroy a specific replica. Use query param `?id=<replica_uuid>`. Bypasses cooldowns. |
| `/api/v1/scaling/{service_pk}/alert_config/` | `GET/PUT` | Update `Service.alert_config` (CPU / memory / disk thresholds, channels). See [Alert Config](#alert-config). |

### Alert Config

`Service.alert_config` is a JSONField added in Batch C. It holds the per-service alert thresholds and notification settings. The schema is:

```json
{
  "cpu_warning": 70,
  "cpu_critical": 90,
  "memory_warning": 75,
  "memory_critical": 90,
  "disk_warning": 80,
  "disk_critical": 95,
  "notify_email": true,
  "notify_webhook": false,
  "webhook_url": ""
}
```

`PUT /api/v1/scaling/{service_pk}/alert_config/` accepts a partial body. The `webhook_url` is `EncryptedCharField` on a related row (not in the JSON) and is never echoed back in responses.

When the engine observes a breach, it writes an `AuditLog` row and emits the configured channels. The `cooldown_minutes` field prevents the same alert from firing more than once per window per channel.

## Security

### MAX_REPLICAS Guard

A global `MAX_REPLICAS` env var (default 5) caps the replica count on a single service. The classic engine, the AI-enhanced engine, and the admin surface all respect this cap. The check is enforced **before** the spawn — a request to set `desired_replicas=64` is rejected with HTTP 400, not silently capped. Configurable via `SCALE_MAX_REPLICAS` environment variable.

### Race Conditions (Now Fixed)

A long-standing bug was that two concurrent scale events (e.g. a manual `spawn/` and the AI-enhanced engine's tick) could both observe `current_replicas=2`, both decide to add one, and end up with `replicas=4` instead of the intended `3`.

The fix: every scale event acquires a `SELECT … FOR UPDATE` row lock on the `Service` row for the duration of the read-decide-write cycle. The lock is held inside a `transaction.atomic()` block. The classic engine and the AI-enhanced engine both use the same pattern; the admin surface uses it too. Concurrent calls serialize on the lock and only one observes the up-to-date `current_replicas`.

A residual race that **cannot** be fixed at the row level: a `min_replicas` change and a deploy starting at the same time. The deploy's `queued_min_replicas` snapshot (see [docs/deployments.md](deployments.md#autoscaler-interaction)) covers this case — the deploy uses the snapshot, not the live field, so the container plan is consistent.

### Audit Log

Every scale event writes an `AuditLog` row with:

- `actor` — the engine or admin user that triggered the event.
- `action` — `SCALE_UP`, `SCALE_DOWN`, `SPAWN`, `DESTROY_REPLICA`, `ALERT_FIRED`.
- `target` — the service name.
- `metadata` — old / new replica count, the reason, and (for the AI engine) the model output that drove the decision.

The audit log is hash-linked — see `models_audit.py`. Manual `spawn/` and `destroy_replica/` calls log the calling admin's user ID.

## API Reference

All endpoints are mounted under `/api/v1/scaling/`. Admin endpoints require `IsAdminUser`. Service-level reads (e.g. `replicas/`) require the service owner. Detail actions use `{service_pk}` in the URL path.

### `POST /api/v1/scaling/{service_pk}/analyze/`

Run a one-shot analysis on a service. Returns the current observed state and a recommendation.

**Example request:**

```bash
curl -sS -X POST http://localhost:8000/api/v1/scaling/9c8b4b1a-7d1c-4a2b-9a55-2e8c3d4f9b21/analyze/ \
  -H "Authorization: Token $SMSLY_TOKEN"
```

**Example response:**

```json
{
  "service_id": "9c8b4b1a-7d1c-4a2b-9a55-2e8c3d4f9b21",
  "current_replicas": 3,
  "cpu_avg_1m": 78.2,
  "cpu_avg_5m": 71.5,
  "memory_avg_1m": 412.0,
  "desired_replicas": 4,
  "reasoning": "CPU sustained > target (80) for 3 minutes; recommend +1.",
  "would_scale_at": "2026-06-12T15:25:11Z",
  "blocked_by_cooldown": false
}
```

The endpoint is **non-mutating** — it does not actually scale the service. Use the `spawn/` endpoint to act on the recommendation.

### `POST /api/v1/scaling/{service_pk}/spawn/`

Force-spawn a replica. Bypasses cooldowns. Admin only.

**Request body:**

| Field | Type | Notes |
| --- | --- | --- |
| `count` | int | Optional. Default 1. The resulting `replica_count` is capped at `MAX_REPLICAS` and at `Service.max_replicas`. |

```bash
curl -sS -X POST http://localhost:8000/api/v1/scaling/spawn/ \
  -H "Authorization: Token $SMSLY_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"service_id": "9c8b4b1a-7d1c-4a2b-9a55-2e8c3d4f9b21", "count": 1}'
```

**Error responses:**

| Status | Cause |
| --- | --- |
| 400 | `count` would exceed `MAX_REPLICAS` or `Service.max_replicas`. |
| 403 | Caller is not an admin. |
| 404 | Service not found. |

### `GET /api/v1/scaling/replicas/?service_id=…`

List current replica state.

**Example response:**

```json
{
  "service_id": "9c8b4b1a-7d1c-4a2b-9a55-2e8c3d4f9b21",
  "current_replicas": 3,
  "min_replicas": 2,
  "max_replicas": 8,
  "last_scale_at": "2026-06-12T15:20:11Z",
  "last_scale_action": "SCALE_UP",
  "replicas": [
    {"container_id": "abc123", "state": "running", "started_at": "2026-06-12T15:20:13Z"},
    {"container_id": "def456", "state": "running", "started_at": "2026-06-12T15:18:01Z"},
    {"container_id": "ghi789", "state": "starting", "started_at": "2026-06-12T15:24:55Z"}
  ]
}
```

### `POST /api/v1/scaling/destroy_replica/`

Force-destroy a specific replica. Admin only.

**Request body:**

| Field | Type | Notes |
| --- | --- | --- |
| `service_id` | UUID | Required. |
| `container_id` | string | Required. Must be an active replica of the service. |

```bash
curl -sS -X POST http://localhost:8000/api/v1/scaling/destroy_replica/ \
  -H "Authorization: Token $SMSLY_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"service_id": "9c8b4b1a-7d1c-4a2b-9a55-2e8c3d4f9b21", "container_id": "abc123"}'
```

The endpoint refuses to destroy the last replica if `min_replicas >= 1`. To take a service to zero, set `min_replicas=0` first.

### `PUT /api/v1/scaling/alert_config/`

Update `Service.alert_config`. The service owner (not just admins) can call this.

**Request body:**

| Field | Type | Notes |
| --- | --- | --- |
| `service_id` | UUID | Required. |
| `cpu_threshold` | int | Optional. 0-100. Default 85. |
| `memory_threshold` | int | Optional. 0-100. Default 90. |
| `error_rate_threshold` | float | Optional. 0.0-1.0. Default 0.05. |
| `channels` | array | Optional. Subset of `["email", "slack", "webhook"]`. |
| `slack_webhook_url` | string | Optional. Encrypted at rest. |
| `cooldown_minutes` | int | Optional. Default 15. |

```bash
curl -sS -X PUT http://localhost:8000/api/v1/scaling/alert_config/ \
  -H "Authorization: Token $SMSLY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "service_id": "9c8b4b1a-7d1c-4a2b-9a55-2e8c3d4f9b21",
    "cpu_threshold": 75,
    "channels": ["email", "slack"],
    "slack_webhook_url": "https://hooks.slack.com/services/..."
  }'
```

**Example response:**

```json
{"status": "ok", "alert_config": {"cpu_threshold": 75, "channels": ["email", "slack"], "memory_threshold": 90, "error_rate_threshold": 0.05, "cooldown_minutes": 15}}
```

Note that `slack_webhook_url` is not in the response — it is `EncryptedCharField` and is never echoed back.

## Troubleshooting

### "Service is at min_replicas but CPU is 100%"

Either the CPU is a transient spike and the cooldown will trigger a scale-up, or the engine is throttled. The classic engine scales up at 1-minute intervals; if CPU is at 100% for a full minute, the next tick will scale it up to `min_replicas + 1`. To force an immediate scale-up, use the `spawn/` endpoint.

### "AI-enhanced engine is not running"

Check `AUTOSCALER_AI_ENABLED=True` in `.env`. Then check `PlatformConfig.prometheus_loki_live` — both Prometheus and Loki must be reachable. The engine logs a warning and falls back to the classic path if either is down.

### "Replica count is stuck at MAX_REPLICAS"

`MAX_REPLICAS` is a global cap. To raise it, edit `.env` and restart the backend. The new value is read at boot; there is no hot reload.

### "Autoscaler is oscillating"

Check the cooldowns: 3 minutes up, 10 minutes down. If your workload has high variance on the order of minutes, the asymmetric cooldown will still produce flapping. Lower `autoscale_cpu_target` so the engine is less aggressive, or set `min_replicas` to the average demand and let the engine only handle spikes.

### "alert_config was reset to defaults after a deploy"

The default values are emitted on every service create, and the engine backfills defaults for older services when they are first scaled by the AI engine. To permanently override, save the values via `PUT /api/v1/scaling/alert_config/`.

### "Manual destroy_replica fails with 'cannot destroy last replica'"

`Service.min_replicas >= 1` and there is only one running replica. Set `min_replicas=0` first, then destroy the replica.

## Limitations

- **No scale-to-zero by default.** `min_replicas=0` is allowed but cold starts are heavy (full container boot). Pair with a function surface (see [docs/functions.md](functions.md)) if you need zero-idle endpoints.
- **Cooldowns are platform-wide.** The 3/10-minute values cannot be tuned per service (only via `SCALE_COOLDOWN_MIN` and `SCALE_COOLDOWN_DOWN_MIN` env vars).
- **No multi-region awareness.** The engine treats all replicas as a single pool. Geographic routing and per-region targets are not supported.
- **No predictive scaling.** The AI-enhanced engine uses last-24h metrics for advisory only; it does not pre-warm replicas for known traffic patterns.
- **No custom metrics out of the box.** CPU and memory are the only signals. To scale on request rate, queue depth, or business KPIs, instrument your service to write to Prometheus and add a custom query in `scaling_ai.py`.
- **The cursor is single-worker.** A multi-worker Celery deployment may have two workers advancing the cursor concurrently. The cursor is only used for AI-enhanced engine batches, which are advisory; the worst case is duplicate work, not over-scaling.
- **Admin surface requires staff status.** The `IsAdminUser` gate is not granular — a staff user has full access. Combine with audit log review to detect misuse.
