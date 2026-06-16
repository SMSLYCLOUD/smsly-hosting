# Refactor Plan — Split `views.py` and `tasks.py` God Files

> **Status:** Read-only analysis. No code has been moved yet.
> **Author:** Refactor sweep, 2026-06-16
> **Target files:**
> - `backend/apps/deployments/views.py` (5,827 lines, 248 KB, 18 classes, 58 `@action`s, 0 `@api_view`s)
> - `backend/apps/deployments/tasks.py` (5,483 lines, 226 KB, ~118 functions / `@shared_task`s)

---

## 1. Current state

`apps/deployments/` has been the dumping ground for every feature added in the
last 18 months. The two largest files — `views.py` and `tasks.py` — together
hold **~11,300 lines**, almost a third of the entire app.

Sibling files already exist for some domains, but the bulk of the code is
still in the parent:

| File | Lines | What it owns |
|------|------:|--------------|
| `views.py` (god) | 5,827 | Service + Deployment ViewSets, env-var/file-manager, backups, audit, system config, session token, route status/recheck, platform resources, remote trigger, Caddy ask permission, Zero-Trust HMAC auth, CleanupFileResponse |
| `tasks.py` (god) | 5,483 | Smart deploy, resume, AI-router env build, ecosystem link, runtime env derivation, route readiness, container deploy, self-heal, one-click template, addon provisioning, all backup tasks, server transfer, platform update/rollback, maintenance, node watchdog, managed-server health, remote SSH update, plus 60+ private helpers |
| `views_safedeploy.py` | 333 | PreviewEnvironment + DeploymentApproval ViewSets |
| `views_servers.py` | 1,430 | ManagedServer ViewSet, HMAC auth, remote proxy, run-command |
| `views_safedeploy.py` / `views_storage.py` / `views_topology.py` / `views_integrations.py` / `views_election.py` / `views_mesh.py` / `views_attestation.py` / `views_subdomains.py` | 200-600 each | Per-domain siblings (already extracted) |
| `views_addons.py` / `views_analysis.py` / `views_autoscale.py` / `views_blueprints.py` / `views_chat.py` / `views_cloud_storage.py` / `views_cron.py` / `views_metrics.py` / `views_node_exchange.py` / `views_oauth.py` / `views_project.py` / `views_replication.py` / `views_slow_query.py` / `views_templates.py` / `views_tokens.py` / `views_transfer.py` / `views_tunnels.py` / `views_updates.py` / `views_webhooks.py` / `views_github.py` / `views_gitlab.py` / `views_bitbucket.py` / `views_health_webhook.py` | varies | Per-domain siblings |
| `tasks_safedeploy.py` (571) / `tasks_alerts.py` (400) / `tasks_ai.py` / `tasks_autoscale.py` / `tasks_code_intelligence.py` / `tasks_cron.py` / `tasks_ecosystem.py` / `tasks_election.py` / `tasks_mesh.py` / `tasks_metrics.py` / `tasks_replication.py` | 50-600 each | Per-domain task siblings |

**Fragmentation pattern:** the previous refactors carved out a feature when it
first appeared, but the parent files were never trimmed. The two god files now
read as scrapbooks of every feature anyone bolted on. 248 of the 1,800+ sibling
files in the repo live in `apps/deployments/`, which is itself a smell.

---

## 2. `views.py` analysis

### 2.1 Class inventory (18 classes, 58 `@action`s, 0 `@api_view`s)

| Class | Line | End | Approx. lines | Domain | `@action`s |
|-------|-----:|----:|--------------:|--------|-----------:|
| `ZeroTrustHMACAuthentication` | 65 | ~215 | 150 | auth (cross-cutting) | – |
| `CleanupFileResponse` | 216 | ~354 | 138 | file (cross-cutting helper) | – |
| `EmptySerializer` | 355 | 358 | 4 | schema helper | – |
| `CaddySecretOrAdminPermission` | 359 | 386 | 27 | permission (cross-cutting) | – |
| `ServiceViewSet` | 663 | 3,642 | 2,979 | **service** (god-viewset) | 26 |
| `DeploymentViewSet` | 3,642 | 4,480 | 838 | **deployment** | 12 |
| `PlatformResourcesView` (1st) | 4,480 | 4,529 | 49 | system / dashboard | – |
| `AuditLogViewSet` | 4,529 | 4,558 | 29 | audit | – |
| `SessionTokenView` | 4,558 | 4,596 | 38 | auth | – |
| `SystemConfigView` | 4,596 | 4,824 | 228 | system / config | – |
| `DomainConfigView` | 4,824 | 5,063 | 239 | domain | – |
| `RouteRecheckView` | 5,063 | 5,173 | 110 | routing | – |
| `RouteStatusView` | 5,173 | 5,229 | 56 | routing | – |
| `ServiceBackupViewSet` | 5,229 | 5,468 | 239 | backup | 6 |
| `ServerBackupViewSet` | 5,468 | 5,666 | 198 | backup | 6 |
| `BackupScheduleViewSet` | 5,666 | 5,724 | 58 | backup | 1 |
| `PlatformResourcesView` (2nd — duplicate) | 5,724 | 5,772 | 48 | system / dashboard | – |
| `RemoteTriggerView` | 5,772 | 5,827 | 55 | deployment (remote) | – |

### 2.2 Top 20 largest classes/methods

The top 20 by span are dominated by `ServiceViewSet` (a single 2,979-line
class). The 20 largest *methods* in `ServiceViewSet` / `DeploymentViewSet`
account for ~1,600 lines of action bodies:

| Method | Line | Domain | Approx. lines |
|--------|-----:|--------|--------------:|
| `ServiceViewSet.deploy` | 1,219 | deploy | 123 |
| `ServiceViewSet.env_vars` | 1,887 | env | 199 |
| `ServiceViewSet.env_var_detail` | 2,088 | env | 45 |
| `ServiceViewSet.ai_router_config` | 2,133 | ai router | 49 |
| `ServiceViewSet.verify_domain` | 2,183 | domain | 139 |
| `ServiceViewSet.check_domain` | 2,355 | domain | 116 |
| `ServiceViewSet.dependencies` | 2,471 | service | 35 |
| `ServiceViewSet.bulk_action` | 2,506 | service | 50 |
| `ServiceViewSet.sidebar` | 2,557 | service | 104 |
| `ServiceViewSet.retry_domain` | 2,661 | domain | 18 |
| `ServiceViewSet.add_domain` | 2,679 | domain | 121 |
| `ServiceViewSet.delete_domain` | 2,800 | domain | 200 |
| `ServiceViewSet.file_browse` | 3,000 | file | 380 |
| `ServiceViewSet.file_download` | 3,284 | file | 48 |
| `ServiceViewSet.file_delete` | 3,332 | file | 44 |
| `ServiceViewSet.file_mkdir` | 3,376 | file | 44 |
| `ServiceViewSet.file_read` | 3,420 | file | 51 |
| `ServiceViewSet.file_write` | 3,471 | file | 65 |
| `ServiceViewSet.file_upload` | 3,536 | file | 100 |
| `ServiceViewSet._dispatch_file_operation` | 2,906 | file | 90 |

### 2.3 Imports in `views.py` (top of file, lines 4-62)

Domains referenced:

- **DRF core**: `viewsets, permissions, status, parsers, serializers, authentication, GenericAPIView, action, Response, Token`
- **Django core**: `timezone, FileResponse, HttpResponse, StreamingHttpResponse, Prefetch, settings, cache, ValidationError, transaction, Q, Count, Avg, F, ExpressionWrapper, DurationField, signing`
- **Celery**: `AsyncResult`
- **Apps**: `apps.cloud.models.CloudProvider`, `apps.cloud.docker_client.get_docker_client`, `apps.deployments.services.github_webhooks`, `apps.deployments.ai_router.*`, `apps.deployments.services.server_guard`
- **Local models**: `Service, Deployment, EnvironmentVariable, PlatformConfig, AuditLog, ServiceBackup, ServerBackup, BackupSchedule`
- **Local serializers**: `ServiceSerializer, DeploymentSerializer, EnvVarSerializer, DeploymentTimelineSerializer, InstantRollbackSerializer, AuditLogSerializer, ServiceBackupSerializer, ServerBackupSerializer, BackupScheduleSerializer, DeploymentTriggerSerializer, DeploymentApproveSerializer`
- **Local tasks** (only these 5 imported into views): `smart_deploy_task, resume_deploy_task, create_service_backup_task, create_server_backup_task, restore_service_backup_task, enqueue_smart_deploy_task`
- **Local utils**: `validate_and_sanitize_path, resolve_running_container, normalize_domain, BurstRateThrottle, DeploymentRateThrottle, ServerGuard`

This shows the cross-domain coupling: `ServiceViewSet` reaches into backup,
file, env, domain, deploy, ai-router, route, and ai-fix subsystems. The
extract must move helpers *with* the methods that use them.

### 2.4 Domain grouping of `views.py` classes

| Domain | Classes / methods | Suggested target file |
|--------|-------------------|----------------------|
| **auth** (cross-cutting) | `ZeroTrustHMACAuthentication`, `CaddySecretOrAdminPermission`, `SessionTokenView` | `views_auth.py` (new) |
| **file helpers** (cross-cutting) | `CleanupFileResponse`, file-stream/range/sign helpers (lines 244-354) | `views_file_helpers.py` or stay near consumer — see Phase 2 |
| **service** | `ServiceViewSet` *core* (`get_queryset`, `perform_create`, `perform_update`, `destroy`, `_destroy_remote_sync`, `perform_destroy`, `deployments`, `stop`, `restart`, `recheck_health`, `status`, `deploy`, `trigger_jules_fix`, `instant_rollback`, `timeline`, `stats`, `dependencies`, `bulk_action`, `sidebar`, `multi_deploy`, `retry_delete`, `hide_public_domain`, `unhide_public_domain`, `get_permissions`, `get_throttles`) | `views_service.py` (new) |
| **env-vars** | `ServiceViewSet.env_vars`, `.env_var_detail` + `_is_valid_env_key`, `_looks_masked_secret` | `views_envvars.py` (new) |
| **domain** | `ServiceViewSet.verify_domain`, `.check_domain`, `.retry_domain`, `.add_domain`, `.delete_domain` + `DomainConfigView`, `_normalize_request_domain`, `_rewrite_public_domain`, `_service_for_domain`, `_parse_bool` | `views_domains.py` (new) — note `views_subdomains.py` already exists |
| **file manager** | `ServiceViewSet.file_*` + `_dispatch_file_operation`, `_resolve_target_type`, `_resolve_remote_server`, `_k8s_*`, `_exec_file_*`, `_parse_ls_output`, `_local_file_*` | `views_files.py` (new) |
| **ai router** | `ServiceViewSet.ai_router_config` + `create_preview`, `list_previews`, `destroy_preview` (move to `views_safedeploy.py` is wrong — these are *service-level* previews, distinct from `PreviewEnvironment`) | `views_ai_router.py` (new) |
| **deployment** | `DeploymentViewSet` *core* | already in god, extract to `views_deployment.py` (new) |
| **deployment remote** | `RemoteTriggerView` | move to `views_deployment.py` |
| **backup** | `ServiceBackupViewSet`, `ServerBackupViewSet`, `BackupScheduleViewSet` | `views_backup.py` (new) |
| **audit** | `AuditLogViewSet` | `views_audit.py` (new) |
| **system / config** | `SystemConfigView`, `PlatformResourcesView` (both duplicates — keep one), `RouteRecheckView`, `RouteStatusView` | `views_system.py` (new) |

---

## 3. `tasks.py` analysis

### 3.1 Function/task inventory (~118 top-level defs)

`@shared_task` / `@app.task` count: **18** (verified via `grep -E '^@shared_task'`).
Total top-level functions: **118**.

### 3.2 Top 20 largest functions by line span

| Function | Line | Domain | Approx. lines |
|----------|-----:|--------|--------------:|
| `smart_deploy_task` | 1,167 | deploy | 143 |
| `_handle_remote_deployment` | 1,499 | deploy / remote | 85 |
| `_poll_remote_deployment` | 1,664 | deploy / remote | 202 |
| `_wait_for_local_container_healthy` | 2,121 | deploy / local | 103 |
| `_wait_for_local_route_ready` | 2,224 | deploy / local | 181 |
| `_deploy_container` | 2,405 | deploy | 279 |
| `_do_promote` | 2,684 | deploy | 84 |
| `_post_deploy_monitor` | 2,768 | deploy | 190 |
| `_escalate_to_ai` | 2,958 | ai router | 64 |
| `_handle_failure` | 3,022 | deploy | 131 |
| `self_heal_remote_deployment` | 3,153 | deploy / remote | 129 |
| `_ensure_shared_ollama_cpp` | 3,326 | ai router | 94 |
| `one_click_deploy_template_task` | 3,507 | template | 515 |
| `update_remote_server_task` | 5,030 | server / remote | 320 |
| `node_watchdog_task` | 5,351 | server / remote | 121 |
| `refresh_managed_server_health` | 5,472 | server / health | (rest of file) |
| `_build_runtime_env` | 658 | env / ai router | 182 |
| `_link_ecosystem` | 954 | env / ecosystem | 126 |
| `delete_service_task` | 4,682 | service lifecycle | 100 |
| `delete_addon_task` | 4,784 | addon | 34 |
| `recover_stalled_queued_deployments` | 213 | deploy | 68 |
| `create_service_backup_task` + family | 4,136-4,257 | backup | ~120 |
| `execute_server_transfer_task` + `rollback_transfer_task` | 4,323-4,408 | transfer | ~86 |
| `platform_update_task` + `platform_rollback_task` | 4,409-4,435 | platform update | ~26 |
| `run_maintenance_task` | 4,577 | maintenance | 105 |

### 3.3 Imports in `tasks.py` (top of file, lines 1-66)

Same shape as `views.py`:

- **stdlib**: `logging, random, re, shlex, shutil, tempfile, subprocess, os, json, time, zipfile, secrets, threading, contextmanager, urllib.parse`
- **third-party**: `docker, requests, celery.shared_task, services.addon_provisioner`
- **Django**: `settings, cache, timezone, Sum, close_old_connections`
- **Apps.cloud**: `CloudProvider, NixpacksBuilder, ComputeService, FunctionProvisioner`
- **Apps.deployments models**: `Service, Deployment, EnvironmentVariable, PlatformConfig, Addon, Backup, BackupSchedule, ServiceBackup, Volume, ServerTransfer`
- **Apps.deployments services**: `BackupService, PipelineManager, PipelineError, RemoteOrchestrator, should_verify, ServerTransferService`
- **Apps.deployments utils**: `append_log, broadcast_status, build_local_source_bundle, update_stage, is_deployment_local`
- **Apps.deployments.ai_router**: `DEFAULT_AI_ROUTER_API_BASE, DEFAULT_AI_ROUTER_UI_BASE, DEFAULT_BRAID_ALIAS, generate_ai_router_proxy_config, get_ollama_model_name, is_ai_router_service, is_ollama_service`
- **Apps.intelligence**: `AIProviderSettings` (try/except)

### 3.4 Domain grouping of `tasks.py`

| Domain | Functions | Suggested target file |
|--------|-----------|----------------------|
| **deploy core** | `smart_deploy_task`, `resume_deploy_task`, `enqueue_smart_deploy_task`, `_resolve_provider_for_service`, `_deployment_effective_server`, `_is_local_deployment_server`, `recover_stalled_queued_deployments`, `fleet_build_lock`, `_run_managed_image_post_deploy_hooks`, `_do_promote`, `_deploy_container`, `_post_deploy_monitor`, `_handle_failure`, `delete_service_task`, `_IN_PROGRESS_DEPLOYMENT_STATUSES`, etc. | `tasks_deploy.py` (new) |
| **deploy local** | `_docker_safe_segment`, `_detect_exposed_port`, `_coerce_int`, `_is_legacy_default_healthcheck`, `_build_platform_healthcheck`, `_build_runtime_env`, `_smart_derive_database_vars`, `_smart_derive_redis_vars`, `_infer_database_name`, `_ensure_database_exists`, `_is_low_resource_service`, `_local_route_timeout_seconds`, `_local_container_timeout_seconds`, `_wait_for_local_container_healthy`, `_wait_for_local_route_ready` | `tasks_deploy_local.py` (new) |
| **deploy remote** | `_handle_remote_deployment_legacy`, `_remote_failure_message`, `_stop_local_service_container`, `_remote_deploy_failed`, `_handle_remote_deployment`, `_resume_remote_deployment`, `_copy_remote_deployment_fields`, `_poll_remote_deployment`, `_is_traefik_not_ready`, `_route_misroute_reason`, `self_heal_remote_deployment` | `tasks_deploy_remote.py` (new) |
| **build** | `_build_function`, `_build_uploaded_source`, `_resolve_upload_zip_path`, `_safe_extract_zip` | `tasks_build.py` (new) |
| **ai router** | `_escalate_to_ai`, `_detect_safe_ollama_ram_mb`, `_detect_safe_ollama_cpu`, `_ensure_shared_ollama_cpp`, `_pull_ollama_models_into_shared`, `_cleanup_shared_ollama_if_unused` | `tasks_ai_router.py` (new) |
| **template** | `one_click_deploy_template_task` (515 lines) | `tasks_templates.py` (new) |
| **addon** | `provision_addon_task`, `deprovision_addon_task`, `backup_addon_task`, `restore_addon_task` | `tasks_addons.py` (new) |
| **backup** | `create_service_backup_task`, `create_server_backup_task`, `restore_service_backup_task`, `restore_server_backup_task`, `purge_user_backups_task`, `cleanup_old_backups_task`, `run_scheduled_backups_task` | `tasks_backup.py` (new) |
| **transfer** | `execute_server_transfer_task`, `rollback_transfer_task` | `tasks_transfer.py` (new) |
| **platform update** | `platform_update_task`, `platform_rollback_task`, `_clear_directory_contents` | `tasks_platform_update.py` (new) |
| **maintenance** | `_extract_addon_id_from_name`, `_is_stale_maintenance_container`, `_clear_orphaned_runtime_resources`, `run_maintenance_task`, `ThrottledLogAppender` | `tasks_maintenance.py` (new) |
| **server / remote update** | `_redact_remote_update_log`, `_append_remote_update_log`, `_remote_update_preflight_script`, `_remote_update_postflight_script`, `_run_ssh_command`, `update_remote_server_task` | `tasks_server_update.py` (new) |
| **server / health** | `auto_authenticate_nodes_task`, `check_managed_servers_health_task`, `node_watchdog_task`, `refresh_managed_server_health` | `tasks_health.py` (new — does not yet exist) |
| **caddy / routing** | `_regenerate_caddyfile` | move to `tasks_caddy.py` (new) or `tasks_routing.py` |
| **env helpers** | `_env_bool`, `_env_int` (two copies), `should_skip_review_for_commit_message`, `_current_agent_node_queue` | stay in `tasks.py` until last extract, or move to `tasks_utils.py` |

---

## 4. Risk assessment

### 4.1 What can break

| Surface | What is wired to `views.py` / `tasks.py` | Risk |
|---------|------------------------------------------|------|
| **URL routing** | `backend/apps/deployments/urls.py:5-9` imports `DeploymentViewSet, ServiceViewSet, SessionTokenView, SystemConfigView, AuditLogViewSet, DomainConfigView, RouteRecheckView, ServiceBackupViewSet, ServerBackupViewSet, BackupScheduleViewSet, PlatformResourcesView, RemoteTriggerView` from `views` | **High** — every move needs an import update. *Mitigation:* keep `views.py` as a re-export shim in Phase 1; switch to direct imports in Phase 4. |
| **Celery autodiscover** | `backend/config/celery.py:33` calls `app.autodiscover_tasks()` (picks up `tasks.py` in each app); the explicit `register_extra_tasks` block (lines 38-54) imports `apps.deployments.tasks` and `apps.deployments.tasks_alerts` etc. for periodic-task registration; `app.conf.task_routes` (lines 75-96) references task names like `apps.deployments.tasks.smart_deploy_task` | **High** — every `@shared_task` move must update the dotted task name. *Mitigation:* leave a thin `tasks.py` shim that re-exports the moved tasks with their original names until the very last phase. |
| **Test imports** | 20 test files import from `views.py` and 14 test files import from `tasks.py` (e.g. `test_finding94_caddyfile_preview_redaction.py:21` → `from apps.deployments.views import _redact_caddyfile_preview`; `test_backup_system.py:16` → `from apps.deployments.tasks import cleanup_old_backups_task`; `test_audit_log_domain_backup.py:141` → `from apps.deployments.tasks import restore_service_backup_task`; `test_runtime_env_domains.py:9` → `from apps.deployments.tasks import _build_runtime_env`) | **High** — need a `tests/` test-by-test audit; many tests can be left untouched if shim re-exports are used. |
| **Cross-file references inside `views.py`** | `ServiceViewSet.deploy` (line 1,219) calls into file-browse, env-var, route, ai-router helpers in the same class. `DeploymentViewSet` (line 3,642) calls into `enqueue_smart_deploy_task`, `smart_deploy_task`. | **Medium** — methods must move *with* the helpers they call, or helpers must be re-imported. |
| **Cross-file references inside `tasks.py`** | `smart_deploy_task` calls `_deploy_container`, `_do_promote`, `_post_deploy_monitor`, `_handle_failure`, `_route_misroute_reason`, `_wait_for_local_container_healthy`, `_wait_for_local_route_ready` — 8 helpers, all in the same file. `one_click_deploy_template_task` calls ~20 helpers. | **High** — every shared_task must move *with* its private helpers, or helpers must move to a shared `tasks_deploy_helpers.py` module. |
| **Cross-app references** | `views.py` imports `apps.cloud.models.CloudProvider`, `apps.cloud.docker_client.get_docker_client`, `apps.deployments.services.server_guard`, `apps.deployments.services.github_webhooks`. `tasks.py` imports `apps.cloud.models`, `apps.cloud.services.builder`, `apps.cloud.services.compute`, `apps.cloud.services.function_provisioner`, `apps.deployments.services.backup_service`, `apps.deployments.services.pipeline`, `apps.deployments.services.remote_orchestrator`, `apps.deployments.services.tls_verify`, `apps.deployments.services.transfer_service`, `services.addon_provisioner`. | **Low** — these imports are cross-package and unaffected by the split. |
| **DRF `basename` and router URL reversal** | `urls.py` uses `basename='service'` for `ServiceViewSet` and `basename='deployment'` for `DeploymentViewSet`. As long as the ViewSet is registered under the same basename, the URL name and `reverse()` lookup stay stable. | **Low** — keeps working. |
| **DRF `@action` URL path collision** | `ServiceViewSet` has 26 `@action` methods. They all live under `/api/v1/services/<pk>/<action>/` because of the router. Splitting the ViewSet into multiple ViewSets (e.g. `ServiceCoreViewSet` + `ServiceFileViewSet`) is **not** needed; we keep one ViewSet and *move* methods. | **Low** — keep `ServiceViewSet` as the URL-facing class but extract mixin classes (`ServiceFileActionsMixin`, `ServiceDomainActionsMixin`, `ServiceEnvVarsActionsMixin`) into the new sibling files. |
| **`pylint disable=too-many-lines`** | Already present at the top of both files — a code smell we've tolerated. Removing the disable on the empty post-extraction file would surface the cleanup. | **Low** — but useful as a regression check. |
| **Pinned task names** | `app.conf.task_routes` and `app.conf.beat_schedule` in `config/celery.py` reference tasks by dotted name. If we move a task, we must update the dotted name. We can also keep the original name by re-exporting in a shim. | **Medium** — see Mitigation above. |

### 4.2 What cannot break

- The test runner uses the `django.test` API; as long as the ViewSet / task
  symbol is importable, the tests run.
- DRF's `DefaultRouter` resolves class → URL mapping at startup.
- Celery's `app.autodiscover_tasks()` looks for `tasks.py` per app, but the
  periodic-task registration in `config/celery.py` is explicit; we control it.

---

## 5. Refactor phases

### Phase 1 — Low risk, immediate (the "easy wins")

Extract **3-5 self-contained ViewSets / functions** that have no
cross-dependencies. Each extract = new sibling file + import in `urls.py` (no
behaviour change).

| # | Item | Source line | New file | Why it's safe |
|---|------|-------------|----------|---------------|
| 1.1 | `AuditLogViewSet` | views.py:4,529-4,557 | `views_audit.py` | Single self-contained ViewSet, no `@action`s, no helpers, no cross-refs |
| 1.2 | `RouteStatusView` | views.py:5,173-5,228 | `views_routes.py` (or merge into `views_topology.py`) | No `@action`, only `get()` |
| 1.3 | `RouteRecheckView` | views.py:5,063-5,172 | `views_routes.py` | No `@action` |
| 1.4 | `PlatformResourcesView` (de-dup) | views.py:4,480-4,528 **and** 5,724-5,771 | `views_platform.py` (keep one, drop the duplicate) | Standalone `GenericAPIView`, 1 duplicate to remove |
| 1.5 | `SessionTokenView` | views.py:4,558-4,595 | `views_auth.py` (new) | 38 lines, no cross-refs |
| 1.6 | `recover_stalled_queued_deployments` (function, not task) | tasks.py:213-280 | `tasks_deploy_utils.py` (new) | Pure helper, called from Celery beat but not a `@shared_task` itself |

**Mechanical steps per extract:**

1. `git mv` the class into the new sibling file (this preserves blame).
2. Re-import in `views.py` from the new location (keeps `urls.py` working
   unchanged — `from .views_audit import AuditLogViewSet` in `views/__init__.py`
   or directly in `urls.py`).
3. Re-import in `tasks.py` for helpers (e.g. `from .tasks_deploy_utils import
   recover_stalled_queued_deployments`).
4. Run `python manage.py check`, `python manage.py makemigrations --check`,
   the targeted test files, and `python -m pyflakes` on the touched files.

**Effort:** ~0.5 day (5 small moves).

**Risk:** Negligible. Each item moves 30-250 lines and the symbols are
referenced in only one or two files.

### Phase 2 — Medium risk: extract the rest of `views.py` by domain

The big workhorse is `ServiceViewSet` (2,979 lines, 26 `@action`s). The
extraction strategy here is **mixin-based**, not "split the ViewSet":

```
ServiceViewSet (views_service.py)  ← keeps ModelViewSet + get_queryset + CRUD
 ├─ ServiceFileActionsMixin       (views_files.py)
 ├─ ServiceEnvVarActionsMixin      (views_envvars.py)
 ├─ ServiceDomainActionsMixin      (views_domains.py)
 ├─ ServiceDeployActionsMixin      (views_service_deploy.py)
 └─ ServiceAIRouterActionsMixin    (views_ai_router.py)
```

This keeps one URL-facing class (no router changes) and one `@action` URL
namespace (`/api/v1/services/<pk>/<action>/`).

| # | Item | New file | Approx. lines moved |
|---|------|----------|--------------------:|
| 2.1 | `ServiceViewSet` core (CRUD, deploy, stop, restart, status, timeline, stats, sidebar, dependencies, bulk_action, recheck_health, retry_delete, hide/unhide_public_domain, multi_deploy, instant_rollback, trigger_jules_fix) + `DeploymentViewSet` + `RemoteTriggerView` | `views_service.py`, `views_deployment.py` | ~1,800 |
| 2.2 | File manager (file_browse, file_download, file_delete, file_mkdir, file_read, file_write, file_upload, _dispatch_file_operation, _resolve_target_type, _resolve_remote_server, _k8s_*, _exec_file_*, _parse_ls_output, _local_file_*) + `CleanupFileResponse` + the file-stream/range/sign helpers | `views_files.py` | ~700 |
| 2.3 | Env-var actions (env_vars, env_var_detail) + `_is_valid_env_key`, `_looks_masked_secret` | `views_envvars.py` | ~250 |
| 2.4 | Domain actions (verify_domain, check_domain, retry_domain, add_domain, delete_domain) + `DomainConfigView` + `_normalize_request_domain`, `_rewrite_public_domain`, `_service_for_domain`, `_parse_bool` | `views_domains.py` | ~700 |
| 2.5 | AI router config (ai_router_config) + create_preview / list_previews / destroy_preview (move to `views_safedeploy.py` extension OR a new `views_service_previews.py`) | `views_ai_router.py` or `views_service_previews.py` | ~600 |
| 2.6 | `ServiceBackupViewSet`, `ServerBackupViewSet`, `BackupScheduleViewSet` | `views_backup.py` | ~500 |
| 2.7 | `SystemConfigView` + final `PlatformResourcesView` | `views_system.py` | ~280 |
| 2.8 | `ZeroTrustHMACAuthentication`, `CaddySecretOrAdminPermission` | `views_auth.py` (already started in Phase 1.5) | ~180 |

**Mechanical steps per extract (mixin pattern):**

1. Create the new sibling file with the mixin class plus its private helpers.
2. `from .views_files import ServiceFileActionsMixin` etc. at the top of the
   new `views_service.py`.
3. `class ServiceViewSet(ServiceFileActionsMixin, ServiceEnvVarActionsMixin,
   ServiceDomainActionsMixin, ..., viewsets.ModelViewSet):` — MRO order:
   mixins first, then `ModelViewSet`.
4. Delete the moved code from `views.py`.
5. `views.py` becomes ~600 lines: just `ServiceViewSet`, `DeploymentViewSet`,
   the auth/permission classes that aren't easy to split, and re-export
   shims (`from .views_audit import AuditLogViewSet as _AuditLogViewSet; ...
   # re-exports for backwards-compat with `urls.py` and tests`).
6. Run the test suite. Update `urls.py` if any new modules were added.
7. Update the 20 test files that import from `views` to use the new locations.

**Effort:** ~2 days.

**Risk:** Medium. The mixin pattern is well-understood but touches 6+ new
files, 1 shim, 20 test files, and `urls.py`. Each extract should be one
PR/branch with the test suite green.

### Phase 3 — High risk: extract `tasks.py`

Tasks are harder than views because:

- Celery resolves task names by dotted path; the names `apps.deployments.tasks.smart_deploy_task`
  are pinned in `config/celery.py:75-96` (task_routes) and `:99-219`
  (beat_schedule).
- Tests call private helpers with leading underscore (`from apps.deployments.tasks import
  _build_runtime_env`, `from apps.deployments.tasks import _route_misroute_reason`).
- Cross-task helper sharing is high: `smart_deploy_task` needs ~15 helpers,
  `one_click_deploy_template_task` needs ~20.

**Strategy: keep `tasks.py` as a shim until the very end.**

| # | Item | New file | Approx. lines |
|---|------|----------|--------------:|
| 3.1 | `smart_deploy_task`, `resume_deploy_task`, `enqueue_smart_deploy_task`, `recover_stalled_queued_deployments`, `_resolve_provider_for_service`, `_deployment_effective_server`, `_is_local_deployment_server`, `fleet_build_lock`, `_run_managed_image_post_deploy_hooks`, `_do_promote`, `_deploy_container`, `_post_deploy_monitor`, `_handle_failure`, `delete_service_task` | `tasks_deploy.py` | ~1,400 |
| 3.2 | Local-container helpers + `_build_runtime_env`, `_smart_derive_database_vars`, `_smart_derive_redis_vars`, `_link_ecosystem`, `_infer_database_name`, `_ensure_database_exists`, `_is_legacy_default_healthcheck`, `_is_low_resource_service`, `_local_route_timeout_seconds`, `_local_container_timeout_seconds`, `_wait_for_local_container_healthy`, `_wait_for_local_route_ready` | `tasks_deploy_local.py` | ~700 |
| 3.3 | Remote deploy helpers + `_handle_remote_deployment_legacy`, `_handle_remote_deployment`, `_resume_remote_deployment`, `_copy_remote_deployment_fields`, `_poll_remote_deployment`, `_is_traefik_not_ready`, `_route_misroute_reason`, `_remote_failure_message`, `_stop_local_service_container`, `_remote_deploy_failed`, `self_heal_remote_deployment` | `tasks_deploy_remote.py` | ~700 |
| 3.4 | Build helpers + `_build_function`, `_build_uploaded_source`, `_resolve_upload_zip_path`, `_safe_extract_zip` | `tasks_build.py` | ~180 |
| 3.5 | AI-router helpers + `_escalate_to_ai`, `_detect_safe_ollama_ram_mb`, `_detect_safe_ollama_cpu`, `_ensure_shared_ollama_cpp`, `_pull_ollama_models_into_shared`, `_cleanup_shared_ollama_if_unused` | `tasks_ai_router.py` | ~700 |
| 3.6 | `one_click_deploy_template_task` (515 lines, mostly self-contained) | `tasks_templates.py` | ~520 |
| 3.7 | `provision_addon_task`, `deprovision_addon_task`, `backup_addon_task`, `restore_addon_task` | `tasks_addons.py` | ~150 |
| 3.8 | `create_service_backup_task`, `create_server_backup_task`, `restore_service_backup_task`, `restore_server_backup_task`, `purge_user_backups_task`, `cleanup_old_backups_task`, `run_scheduled_backups_task` | `tasks_backup.py` | ~250 |
| 3.9 | `execute_server_transfer_task`, `rollback_transfer_task` | `tasks_transfer.py` | ~90 |
| 3.10 | `platform_update_task`, `platform_rollback_task`, `_clear_directory_contents` | `tasks_platform_update.py` | ~70 |
| 3.11 | `run_maintenance_task`, `_extract_addon_id_from_name`, `_is_stale_maintenance_container`, `_clear_orphaned_runtime_resources`, `ThrottledLogAppender` | `tasks_maintenance.py` | ~250 |
| 3.12 | `update_remote_server_task`, `_redact_remote_update_log`, `_append_remote_update_log`, `_remote_update_preflight_script`, `_remote_update_postflight_script`, `_run_ssh_command` | `tasks_server_update.py` | ~520 |
| 3.13 | `auto_authenticate_nodes_task`, `check_managed_servers_health_task`, `node_watchdog_task`, `refresh_managed_server_health` | `tasks_health.py` | ~250 |
| 3.14 | `_regenerate_caddyfile` (caddy/routing) | `tasks_caddy.py` | ~20 |

**Celery wiring update for each move:**

In `config/celery.py`:

- Update the `app.conf.task_routes` (lines 74-96) — change
  `apps.deployments.tasks.smart_deploy_task` →
  `apps.deployments.tasks_deploy.smart_deploy_task`, etc.
- Update the `app.conf.beat_schedule` (lines 98-219) — same dotted-name change
  for every schedule that references a moved task.
- Add the new module to `register_extra_tasks` (lines 38-54) so it gets
  imported once Django apps are ready.

**Test impact (14 files, sample):**

| Test file | Import | New home |
|-----------|--------|----------|
| `tests/test_backup_system.py:16` | `cleanup_old_backups_task` | `tasks_backup.py` |
| `tests/test_recover_stalled.py:17` | `recover_stalled_queued_deployments` | `tasks_deploy.py` |
| `tests/test_backup_gdpr_cleanup.py:16` | `purge_user_backups_task` | `tasks_backup.py` |
| `tests/test_audit_log_domain_backup.py:141` | `restore_service_backup_task` | `tasks_backup.py` |
| `tests/test_templates_unit.py:86` | `one_click_deploy_template_task` | `tasks_templates.py` |
| `tests/test_system_maintenance.py:13` | `_clear_orphaned_runtime_resources` | `tasks_maintenance.py` |
| `tests/test_runtime_env_domains.py:9` | `_build_runtime_env` | `tasks_deploy_local.py` |
| `tests/test_route_ready_guard.py:6` | `_route_misroute_reason` | `tasks_deploy_remote.py` |
| `tests/test_node_mode_topology.py:14` | `provision_addon_task` | `tasks_addons.py` |
| `tests/test_env_vars.py:251` | `_build_runtime_env` | `tasks_deploy_local.py` |
| `tests/test_deletion_orchestrator.py:7` | `delete_service_task` | `tasks_deploy.py` |
| `tests/test_ai_router_docker_hooks.py:8` | `_run_managed_image_post_deploy_hooks` | `tasks_deploy.py` |
| `tests/test_ai_router_refactor.py:4` | `one_click_deploy_template_task` | `tasks_templates.py` |
| `tests/test_route_readiness.py:6` | `_is_traefik_not_ready` | `tasks_deploy_remote.py` |

**The shim strategy keeps all 14 tests working without modification** —
`tasks.py` becomes a re-export module:

```python
# tasks.py — re-export shim, do not add new code here
from .tasks_deploy import (smart_deploy_task, resume_deploy_task, ...)
from .tasks_deploy_local import (_build_runtime_env, ...)
# etc.

# Preserve dotted task names for in-flight Celery messages:
# `apps.deployments.tasks.smart_deploy_task` is now an alias for
# `apps.deployments.tasks_deploy.smart_deploy_task`. Celery will resolve the
# name once and route to the same class — no migration needed for queued
# tasks. (If pinning the old name is required, add explicit
# `app.task(name='apps.deployments.tasks.smart_deploy_task', bind=True)(smart_deploy_task)`
# shims — see Phase 4.)
```

**Effort:** ~2 days.

**Risk:** High. The shim helps, but periodic tasks, test imports, and the
`register_extra_tasks` list all need explicit updates. Run the entire test
suite after every move. **No moves on a Friday afternoon.**

### Phase 4 — Cleanup

1. **Delete the now-thin `views.py` and `tasks.py`** (they become shims only
   with ~50 lines of re-exports). If the shim is no longer needed (no external
   importer), delete the file entirely.
2. **Remove `# pylint: disable=too-many-lines`** from the top of both files.
3. **Add a CI lint rule** to fail when any `.py` file in `apps/deployments/`
   exceeds 500 lines. Configurations to consider:
   - **ruff** (`tool.ruff.lint.mccabe.max-module-lines = 500` or
     `args = ["--max-module-lines=500"]`).
   - **pylint** (add `max-module-lines=500` to `.pylintrc`).
   - **pre-commit hook** wrapping `radon cc -s -n C` or `lizard`.
4. **Audit `apps/deployments/` for other oversized files.** The deep sweep
   flagged 2 god files; a follow-up sweep should check for any file > 800
   lines (`models_*.py` is a likely next target — there are 19 of them).
5. **Document the pattern** in `docs/DEVELOPER_GUIDE.md` under a "Where does
   new code go?" section.

**Effort:** 0.5 day.

---

## 6. Estimated effort

| Phase | Effort | Risk | Lines moved |
|-------|-------:|------|------------:|
| Phase 1 (low-risk extracts) | 0.5 day | Low | ~400 |
| Phase 2 (views split) | 2 days | Medium | ~5,400 |
| Phase 3 (tasks split) | 2 days | High | ~5,400 |
| Phase 4 (cleanup + lint rule) | 0.5 day | Low | – |
| **Total** | **3-5 days** | | **~11,200** |

Assumes an experienced developer, good test coverage, and a working dev
environment with Celery + Redis.

---

## 7. Test strategy

For every extract, run the following in order. Stop on the first failure.

1. **Static checks** (under 5 seconds)
   - `python -c "import ast; ast.parse(open('<file>').read())"` — both god
     files and every new sibling.
   - `python -m pyflakes backend/apps/deployments/` — catches undefined names
     and unused imports.
   - `python -c "from apps.deployments import views, tasks"` — confirms the
     package still imports.

2. **Django checks** (under 10 seconds)
   - `python manage.py check` — full system check.
   - `python manage.py makemigrations --check --dry-run` — no model drift.
   - `python manage.py show_urls` (or grep `urls.py` + `router.urls`) — confirm
     the URL map still has every endpoint.

3. **Targeted unit tests** (under 1 minute)
   - `pytest apps/deployments/tests/test_<domain>.py -x` for the domain being
     moved.
   - Examples:
     - `pytest apps/deployments/tests/test_audit_log_domain_backup.py`
     - `pytest apps/deployments/tests/test_recover_stalled.py`
     - `pytest apps/deployments/tests/test_route_readiness.py`

4. **Full test suite** (1-5 minutes)
   - `pytest backend/ -x` — every test, fail fast on first error.

5. **Celery wiring** (manual, 30 seconds)
   - `celery -A config inspect registered` — confirms the new task names are
     registered.
   - `python -c "from config.celery import app; print([t for t in app.tasks if 'deploy' in t])"`
     — eyeball the dotted names.
   - `python manage.py shell` →
     `from apps.deployments.tasks import smart_deploy_task; smart_deploy_task.name`
     — confirms the re-export shim.

6. **Smoke test the running app** (5 minutes)
   - `docker compose up -d` (or local equivalent).
   - `curl http://localhost:8000/api/v1/services/` — list endpoint.
   - `curl http://localhost:8000/api/v1/deployments/` — list endpoint.
   - Hit one `@action` per moved ViewSet (e.g. `POST /api/v1/services/<pk>/env_vars/`).
   - Trigger one periodic task manually
     (`run_scheduled_backups_task.delay()` in a Django shell).

---

## 8. Migration checklist (per file)

Use this checklist for every file moved. **Roll back the entire branch on
the first red light.**

### Pre-flight
- [ ] Open the source file. Read the entire class/function to be moved.
- [ ] Grep the whole `backend/` tree for every public name in the slice.
- [ ] Confirm no circular import: the new file must not import from
      `views.py` / `tasks.py` (it can import from siblings and from
      `models`, `serializers`, `utils`, `services`, `tasks_safedeploy`,
      `tasks_alerts`, etc., but not from the god file we're shrinking).
- [ ] Branch: `git checkout -b refactor/extract-<domain>`.

### Move
- [ ] Create the new sibling file with the moved code (preserve formatting,
      comments, and `# pylint:` disables).
- [ ] Re-export in `views.py` / `tasks.py` (or in `urls.py` for
      router-registered ViewSets) so existing imports still resolve.
- [ ] For tasks: update `config/celery.py` `task_routes` and `beat_schedule`
      if you don't want to keep the old dotted name.
- [ ] Delete the moved code from the god file.
- [ ] Re-run the grep from pre-flight; all hits should now be in the new
      location.

### Validate
- [ ] `python -c "import ast; ast.parse(open('backend/apps/deployments/views.py').read())"`
- [ ] `python -c "import ast; ast.parse(open('backend/apps/deployments/tasks.py').read())"`
- [ ] `python -c "import ast; ast.parse(open('backend/apps/deployments/<new_file>.py').read())"`
- [ ] `python manage.py check`
- [ ] `python manage.py makemigrations --check`
- [ ] `pytest backend/apps/deployments/tests/ -x`
- [ ] `wc -l backend/apps/deployments/views.py backend/apps/deployments/tasks.py`
      — both numbers should drop.
- [ ] `celery -A config inspect registered` (or a `python -c` equivalent).

### Roll back
- [ ] `git revert <merge-sha>` (or `git reset --hard` on the branch).
- [ ] Re-run the test suite to confirm the rollback is clean.
- [ ] Open a follow-up issue with the failure mode (import cycle? missing
      dotted name? test import path?).

### Post-merge
- [ ] Update the banner comment in `views.py` / `tasks.py` with the new
      line count.
- [ ] Update this plan's "Lines moved" column.

---

## 9. Cross-cutting concerns

- **Naming consistency.** The existing siblings use `views_<domain>.py` and
  `tasks_<domain>.py`. New files should follow the same convention
  (`views_audit.py`, `views_files.py`, `tasks_deploy.py`, `tasks_health.py`).
- **Module-level `logger`.** Every new file needs `logger = logging.getLogger(__name__)`.
- **`# pylint: disable=too-many-lines`.** Each new file should be small
  enough that this disable is no longer needed; remove it from the top of
  any new file.
- **No new behaviour in this refactor.** The whole point is moving code
  without changing what it does. Any drive-by fix is a separate PR.
- **CI must stay green at every commit.** If a test fails, revert; do not
  "fix it forward" inside the refactor.

---

## 10. Summary

- 2 god files (11,310 lines combined) → 22-30 sibling files averaging 400-600
  lines each.
- Phase 1 is safe to start today (0.5 day).
- Phase 2 (views split) needs the mixin pattern to keep the URL surface stable.
- Phase 3 (tasks split) needs `tasks.py` to stay as a re-export shim until
  the last move.
- Phase 4 adds a lint rule so this never happens again.
- Total: **3-5 days** of focused work, gated on a green test suite after
  every move.
