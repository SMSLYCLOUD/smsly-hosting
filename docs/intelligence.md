# Intelligence (Runtime)

The intelligence layer is Grid's always-on watchdog. It runs periodic scans over every service, watches for anomaly patterns in build logs, and proposes (or applies) remediations. The full AI / chat surface lives in [docs/ai.md](ai.md); this page covers the **runtime** observability and self-healing tasks.

## Overview

The intelligence subsystem is a small set of Celery periodic tasks and a remediation engine. Unlike the chat / Senate subsystem, it does not require an LLM to be configured — the engine falls back to rule-only mode when no provider is available.

The boundary between the two layers is:

- **AI** (see [ai.md](ai.md)) — interactive, user-driven, LLM-billable. Throttled by `AIChatRateThrottle` and `AIAnalysisRateThrottle`. Always requires an authenticated user.
- **Intelligence** (this page) — automated, schedule-driven, runs in the background. Does not require user input and does not hit the per-user LLM cap.

The two layers share the `apps.intelligence` Django app and the same `provider` configuration, but they are not coupled: you can disable the AI chat endpoints and still have the periodic scans run.

## Periodic Tasks

There are three Celery beat schedules that power the runtime layer. They are registered when the `apps.intelligence` app boots.

### `detect_anomalies_task` — every 3 minutes

Runs `LogAnalyzer.analyze_logs()` over the last 20000 chars of each service's latest deployment logs, plus a health-status fallback. Detected patterns (CRASH_LOOP, OOM_KILLED, DB_CONNECTION_TIMEOUT, etc.) are passed to the `RemediationEngine.apply_fix()` for auto-remediation.

The scan walks services in a paginated batch of 100. Each service is processed in its own `try` / `except` so a single broken service does not abort the entire scan. The summary is logged at INFO and returned to the Celery result backend:

```json
{
  "checked": 247,
  "issues_detected": 3,
  "auto_fixed": 2,
  "errors": 1
}
```

### `proactive_health_scan_task` — every 5 minutes

Walks every service with `health_status='unhealthy'` and calls `RemediationEngine.apply_fix('HEALTH_CHECK_FAIL', service_id)`. The remediation action is `RESTART_OR_ROLLBACK` — it first attempts a container restart, then rolls back to the previous `ACTIVE` deployment if the restart does not bring the service back to healthy.

This is intentionally conservative: it only operates on services that are **already** marked unhealthy. It does not speculatively restart healthy services.

### `daily_intelligence_report_task` — 06:00 UTC

Generates a daily summary of the last 24 hours. The report covers:

- Total deployments.
- Failed deployments.
- Success rate.
- Number of anomalies detected (from `AuditLog` rows with `actor in ['AI_REMEDIATOR', 'AI_REVIEWER']`).

The report is stored as an `AuditLog` row with `actor='AI_REPORTER'`, `action='DAILY_REPORT'`, and `target='SYSTEM'`. The full payload is in `metadata`:

```json
{
  "date": "2026-06-12",
  "total_deployments": 47,
  "failed_deployments": 3,
  "success_rate": "93.6%",
  "anomalies_detected": 11,
  "generated_at": "2026-06-12T06:00:00+00:00"
}
```

Reports are immutable (as with all `AuditLog` rows) so they form a permanent, hash-chained daily ledger.

## AI Codemap

The `LogAnalyzer` class is the platform's primary log-pattern recognizer. It maintains a small library of regex / heuristic patterns and a confidence score per pattern. When the configured LLM is available, ambiguous patterns are sent to the model for confirmation; the response is folded into the confidence score.

| Issue | Pattern | Confidence (rule-only) | LLM-confirmed |
| --- | --- | --- | --- |
| `OOM_KILLED` | exit code 137 / `Out of memory: Killed process` | 0.95 | 0.99 |
| `CRASH_LOOP` | container restarted >3× in 5min | 0.85 | 0.92 |
| `DB_CONNECTION_TIMEOUT` | `psycopg2.OperationalError: timeout expired` | 0.80 | 0.90 |
| `BUILD_FAILURE` | `npm ERR!`, `pip: command not found` in build logs | 0.90 | 0.95 |
| `HEALTH_CHECK_FAIL` | `GET /health` returning 5xx | 0.90 | 0.95 |
| `SSL_CERT_EXPIRED` | `x509: certificate has expired` | 0.95 | 0.99 |
| `DISK_FULL` | `No space left on device` | 0.99 | 0.99 |
| `PORT_CONFLICT` | `bind: address already in use` | 0.95 | 0.97 |
| `DNS_FAILURE` | `no such host` | 0.85 | 0.92 |
| `DEPENDENCY_MISSING` | `ModuleNotFoundError`, `Cannot find module` | 0.90 | 0.95 |
| `TIMEOUT` | `context deadline exceeded` | 0.70 | 0.85 |

Patterns with confidence below 0.9 are surfaced to the dashboard but **not** auto-remediated — they wait for a human `approve` action via the AI assistant.

## Anomaly Detection

Anomalies are detected in two ways:

1. **Pattern-based** (see above) — the LogAnalyzer runs over the latest deployment's `build_logs` plus a health-status fallback (if a service is `health_status='unhealthy'` for any reason and no log pattern matches, the analyzer synthesizes a `CRASH_LOOP` issue with confidence 0.9).
2. **AI-enhanced** (when an LLM is configured) — ambiguous patterns are sent to the model with the last 20000 chars of logs. The model's response is parsed for `type`, `confidence`, and a free-text `fix` recommendation.

The `detect_anomalies_task` walks services in batches of 100. For each service, it:

1. Fetches the latest deployment's `build_logs` (or `None`).
2. Falls back to the service's `health_status` if there are no logs.
3. Runs `LogAnalyzer.analyze_logs()`.
4. For each issue with `confidence >= 0.9`, calls `RemediationEngine.apply_fix(issue_type, service_id)`.
5. Logs the result to `AuditLog`.

The remediation actions are read-only from the perspective of the rest of the platform: the engine either mutates a `Service` row, enqueues a deployment, or runs a maintenance command. Each action is gated by a per-action precondition (see [Self-Healing](#self-healing)).

## Self-Healing

The `RemediationEngine` knows about six remediation actions. Each is a `(action, resource, message, [amount])` tuple. The actions are pre-conditions for the side-effect they trigger.

| Issue | Action | Resource | Side effect |
| --- | --- | --- | --- |
| `OOM_KILLED` | `SCALE_UP` | `MEMORY` (+256MB) | Increments `Service.memory_mb` by 256 (capped at `MAX_MEMORY_LIMIT = 2048MB`), then triggers a re-deploy. |
| `DB_CONNECTION_TIMEOUT` | `SCALE_UP_POOL` | `DB_POOL` (+20) | Emits an `AuditLog` with the recommended fix; does not auto-apply (the platform does not own the database connection pool). |
| `CRASH_LOOP` | `ROLLBACK` | `DEPLOYMENT` | Calls `RemediationEngine._handle_rollback()`, which finds the most recent `ACTIVE` deployment and triggers an instant-rollback. |
| `SSL_CERT_EXPIRED` | `NOTIFY_ADMIN` | `SSL` | Emits an admin notification; the platform's Caddy-managed certs auto-renew, so this is only emitted for self-managed certs. |
| `DISK_FULL` | `CLEANUP` | `DISK` | Runs `docker system prune -f`. **Gated by `explicit_admin=True`.** |
| `PORT_CONFLICT` | `RESTART` | `CONTAINER` | Issues a Docker restart on the running container. |
| `DNS_FAILURE` | `NOTIFY_ADMIN` | `DNS` | Emits an admin notification. |
| `DEPENDENCY_MISSING` | `REBUILD` | `BUILD` | Triggers a fresh deploy (with cache invalidation). |
| `BUILD_FAILURE` | `NOTIFY_AND_DIAGNOSE` | `BUILD` | Generates an AI diagnosis and writes it to `Deployment.ai_diagnosis`. |
| `TIMEOUT` | `SCALE_UP` | `REPLICAS` (+1) | Increments `Service.min_replicas` by 1, then triggers a re-deploy. |
| `HEALTH_CHECK_FAIL` | `RESTART_OR_ROLLBACK` | `CONTAINER` | First attempts a Docker restart; if the service is still unhealthy on the next scan, triggers a rollback. |

The engine is **cooldown-aware** for auto-deploys. After a remediation triggers a re-deploy, no further re-deploy is triggered for the same service within `AUTO_DEPLOY_COOLDOWN_MINUTES = 10` minutes. This prevents oscillation when the same issue repeats in consecutive scans.

The `Service.last_scale_at` field (see [docs/autoscaling.md](autoscaling.md)) is also respected: any scaling action updates `last_scale_at`, and the autoscaler's 1-minute cooldown is applied across all scale changes.

### The `explicit_admin` Gate

Two of the side effects — `CLEANUP` (the `docker system prune` call) and certain ad-hoc notifications — are destructive or external. The engine refuses to run them unless `explicit_admin=True` is passed to `apply_fix()`. The proactive scan (`proactive_health_scan_task`) and the anomaly scan (`detect_anomalies_task`) **never** pass `explicit_admin=True`. Only the admin's manual "fix this now" action in the UI (or a direct API call) can trigger these actions.

This is enforced at the engine level: the `CLEANUP` action returns `False` immediately if `explicit_admin` is falsy, and logs a warning. The platform's `docker system prune` cache key (`docker_prune:<server_id>`) is set after a successful prune and prevents re-running the command within 24 hours (`DOCKER_PRUNE_COOLDOWN_SECONDS`).

### Service-Locking

The engine uses `SELECT … FOR UPDATE` on the `Service` row before applying any fix. This is necessary because the anomaly scan and the proactive scan can run concurrently on the same service — without a row lock, two scans could observe the same issue and each trigger a re-deploy, doubling the remediation work. The lock is held for the duration of the `apply_fix()` call.

## Security

### Docker Pruning Is Gated

`docker system prune -f` removes all stopped containers, dangling images, and unused networks. The action is destructive: any in-flight `BUILDING` deployment that depends on a removed image will fail.

The engine only runs the prune when `explicit_admin=True` AND no prune has been issued for the same `server_id` in the last 24 hours. The 24-hour cooldown is enforced via `cache.set("docker_prune:<server_id>", now, DOCKER_PRUNE_COOLDOWN_SECONDS)`.

The prune is run via `subprocess.run(['docker', 'system', 'prune', '-f'], timeout=30, check=True)`. The 30-second timeout prevents a stuck prune from blocking the Celery worker. The `check=True` ensures a non-zero exit is raised as an exception, which the engine catches and turns into a `False` return.

### Audit Trail

Every remediation action writes an `AuditLog` row. The chain is:

1. `actor = "AI_REMEDIATOR"`.
2. `action` is the action name (`SCALE_UP`, `CLEANUP`, `REBUILD`, `RESTART`, `NOTIFY_ADMIN`, …).
3. `target` is the service name (or `"SYSTEM"` for platform-wide actions like `CLEANUP`).
4. `metadata` includes the old / new values, the reason, and any side-effect details (e.g. PR URLs from Jules).

Because the chain is hash-linked (see `AuditLog.save()` in `models_audit.py`), the audit trail cannot be tampered with retroactively.

## API Reference

The intelligence layer exposes a small set of read-only API endpoints. There are no write endpoints for periodic tasks (they run on Celery beat); the only user actions are "view" and "trigger scan".

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/api/v1/ai/report/` | `GET` | Returns the most recent `DAILY_REPORT` audit log row. |
| `/api/v1/ai/anomalies/` | `GET` | Returns the last 100 `AuditLog` rows with `actor in ['AI_REMEDIATOR', 'AI_REVIEWER']`. |
| `/api/v1/ai/cost-estimate/` | `POST` | Estimates the LLM cost of a proposed prompt (no actual call). |
| `/api/v1/ai/analyze/` | `POST` | Run a one-shot log analysis on a deployment. Same as `ai/analyze_logs/` (see [ai.md](ai.md)). |
| `/api/v1/jules/history/{service_id}/` | `GET` | Returns the auto-fix history for a service. |

These endpoints are throttled by `AIAnalysisRateThrottle` (10/minute per user). They are not admin-gated; any authenticated user can read the report and anomaly history.

## Troubleshooting

### "Daily intelligence report did not generate at 06:00 UTC"

Celery beat is not running, or the platform's timezone is misconfigured. The task is registered with a fixed `06:00 UTC` cron; if the platform's `TIME_ZONE` is not UTC, the report will still be generated at 06:00 UTC and stored under the UTC date. Verify with:

```bash
docker exec smsly-hosting-backend-1 python manage.py shell \
  -c "from apps.intelligence.tasks import daily_intelligence_report_task; print(daily_intelligence_report_task)"
```

### "Anomaly scan returned 0 issues_detected across 247 services"

This can be normal if the platform has been running quietly. If you expect issues, check the platform's log shipping — Loki / Promtail must be running, and the `prometheus_loki` integration must be configured for the LogAnalyzer to receive runtime log streams.

### "RemediationEngine refused to run docker system prune"

`explicit_admin` was not passed. The scan path never passes it. Trigger the prune manually from the UI (Settings → Storage → Cleanup) or call `RemediationEngine().apply_fix('DISK_FULL', service_id, explicit_admin=True)` from a Django shell.

### "Remediation triggered a re-deploy that is still running. The next scan skipped the service."

This is the auto-deploy cooldown. After a remediation triggers a re-deploy, the same service will not be re-deployed by the engine for 10 minutes. Wait for the cooldown, or for the active deploy to reach a terminal state.

### "I disabled AI in Settings but the periodic scans still run"

That is expected. The intelligence scans do not require an LLM — they fall back to rule-only mode when no provider is configured. To disable the scans entirely, set `INTELLIGENCE_DISABLED=True` in `.env` and restart the backend and beat scheduler.

## Limitations

- **No model retraining.** The platform does not learn from past remediations. Each scan is independent.
- **No cross-service correlation.** A spike in `DB_CONNECTION_TIMEOUT` across many services is treated as N independent issues, not as one database problem.
- **Remediation is best-effort.** A fix that fails to apply returns `False` and the next scan re-evaluates. There is no permanent "this issue cannot be fixed automatically" state.
- **The anomaly scan reads only the last 20k chars of build logs.** A long-running deploy that has been streaming logs for an hour may have its head truncated when the scan reads.
- **Reports are stored forever.** The daily report ledger is append-only and never compacted. For a multi-year install, the `AuditLog` table will grow proportionally.
- **No per-service cooldown configuration.** `AUTO_DEPLOY_COOLDOWN_MINUTES` and `DOCKER_PRUNE_COOLDOWN_SECONDS` are platform-wide constants.
- **The 3-minute anomaly scan is fixed.** It cannot be re-tuned per install.
