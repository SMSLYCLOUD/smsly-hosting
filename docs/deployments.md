# Deployments

Grid's deployment pipeline takes source code (from a Git repository, a Docker image, a tarball upload, a one-click template, or an inline serverless function), produces a runnable container image, and promotes it to a live service. Every step is observable, audit-logged, and rollback-safe.

## Overview

A **deployment** is one attempt to promote a new revision of a service. Each deployment has a single status that advances through a fixed set of states. Deployments are asynchronous: the API returns the new record immediately and a Celery worker drives it through the pipeline.

Use deployments when you need to:

- Ship a new commit to a running service.
- Roll back a broken release to the last `ACTIVE` revision.
- Wire a Git provider to deploy on every push.
- Promote a tagged release to production.
- Re-run the pipeline (after a settings change, env var update, or build-config tweak).

Deployments always run in the context of a `Service`. A service has a `deploy_type` (`GIT`, `DOCKER`, `UPLOAD`, `TEMPLATE`, or `FUNCTION`) that determines how the pipeline is wired.

## Deployment Types

| `deploy_type` | Source of truth | When to use |
| --- | --- | --- |
| `GIT` | A Git repository (GitHub, GitLab, Bitbucket) reachable from the build agent. The pipeline clones the repo at a chosen commit, runs the buildpack, and produces an image. | The common case: your application lives in a Git repo. |
| `DOCKER` | A pre-built image reference (`docker.io/library/nginx:latest`, `ghcr.io/org/app:abc1234`). The pipeline pulls the image and runs it. | You build images elsewhere (CI, local Docker) and want Grid to host them. |
| `UPLOAD` | A source tarball uploaded through the API. The pipeline unpacks it and runs the buildpack. | One-off deploys, prototypes, environments without a Git provider. |
| `TEMPLATE` | A one-click template from the Grid catalog. The pipeline materializes the template, applies user-provided config, and runs the buildpack. | Spinning up Postgres + Redis + app stacks with a few clicks. |
| `FUNCTION` | Inline source code stored on the `Service` row (`function_code`, `function_runtime`). The pipeline wraps the code in a hand-rolled HTTP container. | See [docs/functions.md](functions.md) for the serverless workflow. |

## Build Phases

A `GIT` deployment passes through seven observable phases. The phase name is the `pipeline_stages` entry, and the deployment's `status` reflects the dominant phase.

```
QUEUED  →  REVIEW  →  BUILDING  →  PUSH  →  DEPLOYING  →  HEALTH_CHECK  →  ACTIVE
                          │            │           │              │
                          └─ BUILD_FAILED   PUSH_FAILED DEPLOY_FAILED HEALTH_FAILED → FAILED
```

1. **Clone** — the build agent fetches the repository (shallow clone, `git fetch --depth=1` to the commit hash). The clone is checked out into a temp dir keyed by `build_<deployment_id>_*`.
2. **Analyze** — `PipelineManager.run_analysis_only()` reads `package.json`, `pyproject.toml`, `requirements.txt`, `Dockerfile`, `nixpacks.toml`, and the language toolchain. The output is `Deployment.review_summary` (recommended env vars, resource sizes, ports, buildpack hints). Fresh `GIT` deploys pause at `REVIEW` so the user can confirm the analysis before a build runs.
3. **Build** — the chosen buildpack (`NIXPACKS`, `DOCKER`, or `STATIC`) produces a container image. Nixpacks auto-detects the language and emits a multi-stage Dockerfile. The `DOCKER` buildpack uses the user-supplied `Dockerfile`. `STATIC` serves the directory as-is.
4. **Push** — for multi-node fleets the image is pushed to the local insecure registry on `MASTER_MESH_IP:5000` (only when the target node is a Lite Agent or when the deployment was delegated to a remote). On a single-node install the image is loaded directly into the local Docker daemon and no push is performed.
5. **Deploy** — a new container is started. The strategy (`ROLLING`, `BLUE_GREEN`, or `CANARY`) is set on the service. `BLUE_GREEN` keeps the old container alive until the new one passes health checks. `CANARY` routes a percentage (`canary_percentage`, default 10%) of traffic to the new container.
6. **Health check** — Grid's reverse proxy (Traefik) sends a `GET <health_check_path>` (default `/health`) at `health_check_interval` (default 30s). A container is marked healthy after `health_check_retries` consecutive 200s. If `auto_restart` is true (default), the platform restarts unhealthy containers.
7. **Active** — the new container is now serving traffic. All other `ACTIVE` deployments for the same service are demoted to `INACTIVE`. The previous `ACTIVE` row is retained so the user can roll back.

Deployments also support these secondary phases (visible in `pipeline_stages`):

- **Backup running** — for services with `production_requires_backup=True`, the platform runs a `ServiceBackup` of the previous `ACTIVE` deployment before promoting the new one.
- **Migration planning** / **Migration running** — for services with `safe_deploy_enabled=True`, a migration analysis runs before and during the deploy. The migration plan is gated by the service's `migration_auto_approval_policy`.

## Status Reference

Every deployment carries a single `status` value. The list below covers all defined statuses; the most common ones are bolded.

| Status | Phase | What it means | Terminal? |
| --- | --- | --- | --- |
| `QUEUED` | (initial) | The deployment row has been created and is waiting for a Celery worker. | No |
| `REVIEW` | analyze | Fresh `GIT` deploys pause here after analysis. The user must `POST /api/v1/deployments/{id}/approve/` to continue. | No |
| `BUILDING` | build | Nixpacks / Dockerfile is producing the image. | No |
| `BUILD_FAILED` | build | The buildpack exited non-zero. Logs are in `Deployment.build_logs`. | Yes |
| `AWAITING_APPROVAL` | review | A safe-deploy gate is open. The user must explicitly approve the migration plan. | No |
| `BACKUP_RUNNING` | pre-deploy | The platform is taking a pre-deploy backup of the current `ACTIVE` revision. | No |
| `BACKUP_FAILED` | pre-deploy | The backup step failed; the deploy is paused. The user can either cancel or skip the backup. | No |
| `MIGRATION_PLANNING` | pre-deploy | Migration analyzer is running. | No |
| `MIGRATION_RUNNING` | pre-deploy | A migration is being applied to the new container. | No |
| `MIGRATION_FAILED` | pre-deploy | Migration failed. The deploy is paused. | No |
| `DEPLOYING` | deploy | The new container is starting. | No |
| `HEALTH_CHECK` | health | The container is running; the platform is waiting for the health probe to return 200. | No |
| `HEALTH_CHECK_FAILED` | health | The health check failed after retries. May trigger auto-rollback. | Yes |
| `ACTIVE` | (success) | The deployment is the live revision. Other `ACTIVE` rows for the same service have been demoted. | Yes (lifecycle) |
| `INACTIVE` | (post-success) | A previously `ACTIVE` deployment that has been demoted by a newer promotion. Retained for rollback. | Yes (lifecycle) |
| `FAILED` | (any) | A non-recoverable error occurred. The previous `ACTIVE` deployment is preserved. | Yes |
| `CANCELLED` | (any) | The user cancelled the deployment. The new container is removed. | Yes |
| `ROLLING_BACK` | (any) | A rollback is in progress. The deployment is being replaced by a previous revision. | No |
| `ROLLED_BACK` | (terminal) | The rollback completed. The service is back to the previous revision. | Yes |

`BUILDING`, `DEPLOYING`, `HEALTH_CHECK`, `BACKUP_RUNNING`, `MIGRATION_RUNNING`, and `ROLLING_BACK` are the **active** statuses. A service can only have one active deployment at a time; creating a second one returns HTTP 409 with the existing deployment in the response body.

## API Reference

All endpoints are mounted under `/api/v1/`. Authentication is session- or token-based for user endpoints and HMAC-signed for node-to-node traffic.

### `POST /api/v1/deployments/trigger/`

Trigger a new deployment for a service the caller owns. The deployment enters the pipeline as `QUEUED` and is dispatched to a Celery worker.

**Request body:**

| Field | Type | Notes |
| --- | --- | --- |
| `service_id` | UUID | Required. Must be owned by the caller. |
| `provider_id` | UUID | Required. The `CloudProvider` that will be used. |
| `commit_hash` | string | Optional. Defaults to `latest` (HEAD of the service's branch). |

**Example request:**

```bash
curl -sS -X POST http://localhost:8000/api/v1/deployments/trigger/ \
  -H "Authorization: Token $SMSLY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "service_id": "9c8b4b1a-7d1c-4a2b-9a55-2e8c3d4f9b21",
    "provider_id": "f1c2b0c1-1234-5678-9abc-def012345678",
    "commit_hash": "abc1234"
  }'
```

**Example response (HTTP 201):**

```json
{
  "message": "Deployment triggered successfully",
  "deployment_id": "2d3e4f5a-6b7c-8d9e-0f1a-2b3c4d5e6f7a",
  "status": "QUEUED"
}
```

**Error responses:**

| Status | Cause |
| --- | --- |
| 400 | `service_id` or `provider_id` missing; service is on a control-plane node; deployment already in progress (returns `existing_deployment`). |
| 403 | `skip_review` is reserved for trusted internal paths. |
| 404 | `service_id` or `provider_id` not found / not owned by caller. |
| 409 | An active deployment for this service already exists. |
| 429 | Throttled. See [Security](#security). |

### `GET /api/v1/deployments/`

List deployments for services the caller can access. The list endpoint uses a lightweight serializer that omits the heavy fields (`build_logs`, `review_summary`, `vulnerability_report`, `pipeline_stages`, `runtime_logs_url`, `green_container_id`, `container_id`). Call `GET /api/v1/deployments/{id}/` for the full record.

**Query parameters:**

| Param | Type | Notes |
| --- | --- | --- |
| `project_id` | UUID | Optional. Filter to a project. |
| `status` | string | Optional. Filter by status. |
| `service` | UUID | Optional. Filter to a single service. |

**Example request:**

```bash
curl -sS http://localhost:8000/api/v1/deployments/?service=9c8b4b1a-7d1c-4a2b-9a55-2e8c3d4f9b21 \
  -H "Authorization: Token $SMSLY_TOKEN"
```

### `GET /api/v1/deployments/{id}/`

Return a single deployment. Includes `build_logs`, `review_summary`, `pipeline_stages`, `vulnerability_report`, and live runtime metadata. The record is mutated by the Celery worker as the deploy progresses, so poll this endpoint to track status.

**Example response (abridged):**

```json
{
  "id": "2d3e4f5a-6b7c-8d9e-0f1a-2b3c4d5e6f7a",
  "service": "9c8b4b1a-7d1c-4a2b-9a55-2e8c3d4f9b21",
  "status": "HEALTH_CHECK",
  "commit_hash": "abc1234",
  "commit_message": "Add /api/v2/users endpoint",
  "branch": "main",
  "pipeline_stages": [
    {"name": "clone", "status": "completed", "duration_ms": 1234},
    {"name": "analyze", "status": "completed", "duration_ms": 890},
    {"name": "build", "status": "completed", "duration_ms": 12045},
    {"name": "push", "status": "completed", "duration_ms": 532},
    {"name": "deploy", "status": "completed", "duration_ms": 2104},
    {"name": "health_check", "status": "in_progress", "duration_ms": 30000}
  ],
  "is_rollback": false,
  "ai_diagnosis": "",
  "started_at": "2026-06-12T15:23:11Z",
  "finished_at": null,
  "created_at": "2026-06-12T15:23:11Z"
}
```

### `POST /api/v1/deployments/{id}/cancel/`

Cancel a deployment that has not yet reached a terminal state. Allowed only when the deployment is in `QUEUED`, `REVIEW`, `BUILDING`, or `AWAITING_APPROVAL`. Once accepted, the deployment is moved to `CANCELLED` and the new container (if any) is force-removed.

**Example request:**

```bash
curl -sS -X POST http://localhost:8000/api/v1/deployments/2d3e4f5a-6b7c-8d9e-0f1a-2b3c4d5e6f7a/cancel/ \
  -H "Authorization: Token $SMSLY_TOKEN"
```

**Error responses:**

| Status | Cause |
| --- | --- |
| 409 | Deployment is in `DEPLOYING`, `HEALTH_CHECK`, `ACTIVE`, `FAILED`, `CANCELLED`, `INACTIVE`, `ROLLED_BACK`, or `ROLLING_BACK`. |
| 404 | Deployment does not exist or is not owned by the caller. |

### `POST /api/v1/deployments/{id}/approve/`

Approve a deployment that is paused at `REVIEW` or `AWAITING_APPROVAL`. Once approved, the pipeline resumes at the next phase (build or migration).

**Example request:**

```bash
curl -sS -X POST http://localhost:8000/api/v1/deployments/2d3e4f5a-6b7c-8d9e-0f1a-2b3c4d5e6f7a/approve/ \
  -H "Authorization: Token $SMSLY_TOKEN"
```

**Error responses:**

| Status | Cause |
| --- | --- |
| 409 | Deployment is not in `REVIEW` or `AWAITING_APPROVAL`. |
| 404 | Deployment not found. |

### `POST /api/v1/deployments/{id}/rollback/`

Roll back to a specific deployment's `commit_hash` / image. The endpoint creates a **new** deployment row with `is_rollback=True` and `rollback_from` set to the target. The new row enters the pipeline as `QUEUED` and runs through the same build phases.

**Request body:**

| Field | Type | Notes |
| --- | --- | --- |
| `confirm` | string | **Required.** Must be the literal string `"true"`. The confirmation gate prevents accidental rollbacks. |

**Example request:**

```bash
curl -sS -X POST http://localhost:8000/api/v1/deployments/2d3e4f5a-6b7c-8d9e-0f1a-2b3c4d5e6f7a/rollback/ \
  -H "Authorization: Token $SMSLY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"confirm": "true"}'
```

**Example response (HTTP 201):**

```json
{
  "id": "3e4f5a6b-7c8d-9e0f-1a2b-3c4d5e6f7a8b",
  "service": "9c8b4b1a-7d1c-4a2b-9a55-2e8c3d4f9b21",
  "status": "QUEUED",
  "commit_hash": "abc1234",
  "is_rollback": true,
  "rollback_from": "2d3e4f5a-6b7c-8d9e-0f1a-2b3c4d5e6f7a",
  "rollback_state": "rollback_pending",
  "rollback_target": "2d3e4f5a-6b7c-8d9e-0f1a-2b3c4d5e6f7a"
}
```

**Error responses:**

| Status | Cause |
| --- | --- |
| 400 | `confirm` was not `"true"`, or the target deployment has no `commit_hash`, or the target deployment is not in `ACTIVE` / `INACTIVE`. |
| 404 | Deployment not found. |

### `POST /api/v1/services/{id}/instant-rollback/`

One-click rollback. The endpoint looks up the most recent `ACTIVE` deployment for the service and triggers a rollback to it. The caller does not need to know the deployment ID.

**Request body:**

| Field | Type | Notes |
| --- | --- | --- |
| `confirm` | string/bool | **Required.** Must be `"true"`. Without it, returns HTTP 400. |
| `message` | string | Optional. Reason for the rollback; stored on the new deployment's `commit_message`. |

**Example request:**

```bash
curl -sS -X POST http://localhost:8000/api/v1/services/9c8b4b1a-7d1c-4a2b-9a55-2e8c3d4f9b21/instant-rollback/ \
  -H "Authorization: Token $SMSLY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"confirm": true, "message": "5xx spike after deploy"}'
```

**Example response (HTTP 201):**

```json
{
  "deployment": {
    "id": "3e4f5a6b-7c8d-9e0f-1a2b-3c4d5e6f7a8b",
    "status": "QUEUED",
    "commit_hash": "abc1234",
    "commit_message": "INSTANT ROLLBACK to abc1234 — 5xx spike after deploy",
    "is_rollback": true
  },
  "rolled_back_to": {"id": "...", "commit_hash": "abc1234", "status": "ACTIVE"},
  "message": "Rollback initiated to abc1234"
}
```

**Error responses:**

| Status | Cause |
| --- | --- |
| 400 | Service is on a control-plane node. |
| 404 | No previous `ACTIVE` deployment to roll back to. |

### `POST /api/v1/services/{id}/deploy/`

Multi-server and single-server deploy entry points are unified under the `deploy/` and `multi-deploy/` actions on the `Service` viewset. The body shape is identical to `/api/v1/deployments/trigger/`, with the service ID in the path. The `multi-deploy` action additionally accepts `target_server_id` to pin the deployment to a specific `ManagedServer` and a `target_is_local: true` override for "force run on the controller" semantics.

## Webhook Setup

Grid accepts webhooks from GitHub, GitLab, and Bitbucket. Each delivery creates a deployment for the matching service, and the webhook handler is idempotent: a `WebhookDelivery` row is keyed on the provider's `delivery_id` (e.g. GitHub's `X-GitHub-Delivery` UUID), so duplicate deliveries are dropped.

### GitHub

1. In your repo, go to **Settings → Webhooks → Add webhook**.
2. Set **Payload URL** to `https://<your-grid-host>/api/v1/webhooks/github/`.
3. Set **Content type** to `application/json`.
4. Set **Secret** to the same value as `GITHUB_WEBHOOK_SECRET` in the Grid `.env` (recommended).
5. Choose **Let me select individual events** and enable `Push` and `Pull request`.
6. Save. Push to the configured branch to fire a deployment.

Grid matches the incoming repo against the user's `Service.repository_url`. If multiple services match (preview environments), the most recent service on the pushed branch wins.

### GitLab

1. **Settings → Webhooks** in the project.
2. URL: `https://<your-grid-host>/api/v1/webhooks/gitlab/`.
3. Trigger: **Push events** and **Merge request events**.
4. Set the **Secret token** to `GITLAB_WEBHOOK_SECRET`.
5. Save and test with a push.

### Bitbucket

1. **Repository settings → Webhooks → Add webhook**.
2. URL: `https://<your-grid-host>/api/v1/webhooks/bitbucket/`.
3. Triggers: **Repo: push** and **Pull request: created / updated**.
4. Save.

The handler for all three providers is identical in shape: it parses the JSON, derives `(branch, commit_hash, commit_message)`, and either reuses the most recent matching `Service` or creates a preview environment for `pull_request` events.

## Buildpacks

A service's `buildpack` field selects the build strategy. The default is `NIXPACKS`.

| Buildpack | Behavior |
| --- | --- |
| `NIXPACKS` | Detects the language from the repo and emits a multi-stage Dockerfile. Supports Node, Python, Go, Ruby, Rust, Java, PHP, Elixir, Deno, and Bun. |
| `DOCKER` | Uses the `Dockerfile` at the service's `root_directory` (default `/`). |
| `STATIC` | Serves the directory as a static site. Traefik routes `/` to a small nginx container. |

The build phase runs as a Celery task. The output image is tagged `smsly/<service-slug>:<deployment-id>`.

## Environment Variables

`Service.env_vars` is a list of `(key, value, is_secret, is_locked, source)` rows. The values are stored as `EncryptedCharField` and decrypted at deploy time.

### Precedence

The final env on the new container is the union of these sources, in this order (later overrides earlier):

1. **Platform defaults** — `PORT` (set to `internal_port`), `SMSLY_API_KEY`, `SMSLY_PUBLIC_DOMAIN`.
2. **Addon auto-injection** — `source=ADDON`. PostgreSQL addons inject `DATABASE_URL`, Redis addons inject `REDIS_URL`, etc.
3. **Shortcode resolution** — `source=SHORTCODE`. Resolved at deploy time. Example: `{{pg.MAIN.DATABASE_URL}}`.
4. **System auto-injection** — `source=SYSTEM`. Includes `DEPLOYMENT_ID`, `COMMIT_HASH`, `BRANCH`, `SERVICE_NAME`.
5. **User-defined** — `source=USER`. Highest precedence.

If a user-defined row is marked `is_locked=True`, it cannot be overridden by any auto-injection step. This is the only way to guarantee that an auto-injected `PORT` or `DATABASE_URL` will not clobber a deliberate override.

### Masking

The `EnvironmentVariable.value` field is `EncryptedCharField` (Fernet, at rest in the database). The serializer masks values whose `is_secret=True`: they appear as `••••••••` in any API response and never in `Deployment.build_logs`. The full value is only returned in two places: the `env_vars` endpoint on the service (for the service owner) and the `runtime-logs` endpoint for the active deployment.

### Ciphertext Detection

If a row's `value` decrypts to a string longer than 8192 bytes, or contains non-printable bytes, the serializer substitutes the row's `key` followed by `(invalid)` and emits a warning in the `AuditLog`. This catches paste-of-encrypted-ciphertext mistakes where the user accidentally pastes a Fernet-encrypted blob from another tool into an env var.

## Health Checks and Auto-Restart

Each service has its own health check config:

- `health_check_path` (default `/health`)
- `health_check_port` (blank = auto-detect from `PORT` env)
- `health_check_interval` (default 30s)
- `health_check_timeout` (default 300s)
- `health_check_retries` (default 90)
- `auto_restart` (default `True`)
- `restart_policy` (`always`, `unless-stopped`, `on-failure`, `no`)

The health monitor polls the configured path on the active container. A container is marked `unhealthy` after `health_check_retries` consecutive non-2xx responses. When `auto_restart=True`, the platform issues a Docker restart; the deploy is only failed if the container is still unhealthy after the restart policy kicks in.

Containers can also push their own health status via the **Service Health Webhook**:

```bash
curl -X POST https://<your-grid-host>/api/v1/services/<service-id>/health/webhook/ \
  -H "X-Health-Webhook-Token: <service.health_webhook_token>" \
  -H "Content-Type: application/json" \
  -d '{"status": "healthy", "details": {"db": "ok", "cache": "ok"}}'
```

Accepted `status` values: `healthy`, `unhealthy`, `starting`, `needs_manual_intervention`. The token is generated on service creation and is visible only to the service owner.

## Autoscaler Interaction

The autoscaler can mutate `Service.min_replicas` while a deploy is in flight. To prevent the deploy's container plan from drifting, the platform snapshots `min_replicas` onto the deployment row at queue time as `Deployment.queued_min_replicas`. The deploy executor uses this snapshot to decide how many containers to bring up at deploy time, not the live `min_replicas` field.

This means:

- If a user triggers a deploy and the autoscaler is concurrently scaling up, the new deploy starts with the smaller count and the autoscaler brings the extra replicas online a few seconds later.
- If the autoscaler is concurrently scaling down, the new deploy starts with the larger count and the autoscaler schedules a scale-down after its cooldown elapses.
- The snapshot is for the **deploy**; the post-deploy autoscaler behavior is governed by the service's `autoscale_cpu_target` and `min_replicas`/`max_replicas` as normal.

## Security

### Deployment Throttles

The `DeploymentViewSet` is gated by two DRF throttles:

- `BurstRateThrottle` — `3/minute` per user. Prevents rapid-fire re-triggers.
- `DeploymentRateThrottle` — `10/hour` per user. Prevents resource exhaustion from excessive builds.

Both return HTTP 429 with a `Retry-After` header.

### Audit Log

Every state change on a deployment writes an `AuditLog` row. The chain is hash-linked — see the `AuditLog.calculate_hash()` and `AuditLog.save()` overrides in `models_audit.py`. Logs are immutable (modification raises `ValidationError`; deletion is forbidden).

Common audit events emitted by the pipeline:

- `DEPLOYMENT_TRIGGER` — user triggered a new deployment.
- `DEPLOYMENT_ROLLBACK` — user requested a specific rollback.
- `DEPLOYMENT_ROLLBACK_INSTANT` — user clicked instant-rollback.
- `DEPLOYMENT_APPROVE` — user approved a `REVIEW` / `AWAITING_APPROVAL` deployment.
- `DEPLOYMENT_CANCEL` — user cancelled a deployment.

### SSRF Protection

The deploy pipeline clones repositories over `https://` or `git://`. URLs are validated against `_validate_registry_url()` (see `apps/deployments/tests/test_registry_url_validation.py`) which:

- Rejects loopback, link-local, multicast, reserved, and unspecified ranges.
- Accepts private RFC 1918 ranges only when the host resolves to a registered `CloudProvider` (so a misconfigured cloud registry does not cause a silent clone from the internal network).
- Rejects non-HTTPS URLs unless the host is in the platform's `localhost` / Docker service list.

## Troubleshooting

### "Deployment already in progress (status: BUILDING)"

There is an active deployment for this service. Either wait for it to finish or `POST /api/v1/deployments/{id}/cancel/`. Creating a second active deployment returns HTTP 409 with the existing deployment in `existing_deployment`.

### "Cannot cancel deployment in HEALTH_CHECK status"

`HEALTH_CHECK` is past the cancel boundary. Wait for the deployment to reach `ACTIVE` or `FAILED`, then trigger a rollback if needed.

### Build hangs in `BUILDING`

The buildpack has stalled — usually a network failure (npm registry down, `apt-get update` timing out) or a runaway `npm install` cycle. Inspect `GET /api/v1/deployments/{id}/build-logs/` for the live log tail. If the build is genuinely stuck, `POST /api/v1/deployments/{id}/cancel/` and re-trigger.

### "BUILD_FAILED: exit 137"

OOM-killed during build. The build sandbox ran out of memory. Reduce the build's memory pressure (move large assets out of the build, use `.dockerignore`, switch from a single-page Webpack build to Vite, etc.) or raise the platform's per-task memory limit (see `docker-compose.prod.yml`).

### "RESTORE_FAILED: …" in deploy logs

The pre-deploy backup step failed. The deployment is paused at `BACKUP_FAILED`. Inspect the backup target. The most common cause is a service volume that is too large to back up within the platform's IO budget.

### "ENCRYPTION_KEY_MISMATCH" at restore time

A `BACKUP_ENCRYPTION_KEY` was rotated without restarting the backend, or the encrypted backup was made on a different installation. Set `BACKUP_ENCRYPTION_KEY` to the value used at backup time, restart the backend, and re-run the deploy.

### Health checks pass on the dashboard but the public domain returns 502

The platform considers the container healthy, but the Traefik route is stale. Either the domain was reconfigured or the deployment hit a `BLUE_GREEN` swap that left the route pointing at the old container. Force a route re-check: `POST /api/v1/services/{id}/recheck-health/` and then `POST /api/v1/system/route-recheck/`.

### Webhook deliveries do not trigger deployments

Inspect the `WebhookDelivery` table — duplicate deliveries are recorded with `status=ignored` and a hash of the prior delivery. The most common cause is a webhook signed with a secret that does not match the service owner's `CloudProvider` config, in which case the handler emits `status=failed` with `metadata.error="signature_mismatch"`. Recreate the webhook with the same secret as `GITHUB_WEBHOOK_SECRET` in the platform `.env`.

### "vulnerability_report is empty after build"

The Trivy scan was skipped. This happens when the image is on a registry that Trivy cannot reach (private registry with no credentials). Configure `TRIVY_REGISTRY_USERNAME` / `TRIVY_REGISTRY_PASSWORD` in the platform `.env` and re-trigger.

## Limitations

- **No native cron / scheduled deploys.** Use a CI pipeline (GitHub Actions, GitLab CI) to call `/api/v1/deployments/trigger/` on a schedule.
- **One active deployment per service.** Sequential deploys are fine; parallel deploys to the same service are blocked with HTTP 409.
- **Build is local-only.** The build agent always runs on the local controller. The push step forwards the image to a remote node for delegated deploys, but the build itself is not distributed.
- **Encrypted env vars are decrypted at deploy time.** Anyone with read access to the running container can `cat /proc/1/environ` to read them. Use `is_secret=True` only for tokens you would otherwise put in a vault.
- **No pause-and-resume.** Once a deployment is past `REVIEW`, the only way to stop it is to cancel it.
- **Build logs are truncated to the last 20000 characters** before being persisted. The full log stream is in `Deployment.build_logs` but the field is excluded from the list endpoint; fetch the detail endpoint for the full record.
