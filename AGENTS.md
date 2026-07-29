# AGENTS.md — Critical Review Notes

## Expensive Mistakes Found During Code Review

These are real bugs that shipped into the codebase and were caught only after deep
review. Each one caused or would cause production failures. Do not repeat them.

---

### 1. Removing Code Without Verifying All Callers

**What happened:** `lib/docker.sh` `configure_docker_mirror` was rewritten to remove
the pull-through mirror. The rewrite also silently removed the master node
`insecure-registries` config (`127.0.0.1:5000`, `registry:5000`, `${my_ip}:5000`).

**Why it's bad:** Master nodes would fail to pull images from their own local
registry (`registry:5000`) because Docker would reject the self-signed cert. This
breaks every deployment on master nodes.

**Lesson:** When rewriting a function, diff the OLD and NEW versions line by line.
Every branch in the old code must have a corresponding path in the new code — unless
you can prove the branch is dead. Do not assume "I removed the mirror, so the rest
is the same." The master insecure-registry config had nothing to do with the mirror.

**How to catch:** Run `git diff` on the file. For every `-` line, ask: "Is this
logic still present somewhere in the `+` lines?" If not, it's a deletion bug.

---

### 2. Reverting a File and Losing All Changes

**What happened:** `tasks.py` got corrupted from partial edits. The fix was
`git checkout -- tasks.py`, which reverted ALL changes including the new
`recover_stalled_deletions` task, the `delete_service_task` retry logic, and the
`SoftTimeLimitExceeded` import. The celery.py route and beat schedule still
referenced the now-missing task.

**Why it's bad:** Celery beat dispatches `recover_stalled_deletions` every 5 minutes.
The worker doesn't have the task registered. Every 5 minutes: "Received unregistered
task" error, log noise, stuck deletions never recover.

**Lesson:** After `git checkout` or `git stash`, immediately verify that all
references to changed code are still valid. If celery.py references a task, the task
must exist. If a route points to a module, the module must export the symbol.

**How to catch:** After reverting, grep for any new function/class names you added
in OTHER files. `grep -r "recover_stalled_deletions" backend/` would have caught
this instantly.

---

### 3. Adding Code to Celery Without Registering the Module

**What happened:** 6 task modules (`tasks_ai`, `tasks_addons`, `tasks_autoscale`,
`tasks_bundles`, `tasks_server_update`, `tasks_transfer`) defined `@shared_task`
functions but were never imported in `celery.py`'s `register_extra_tasks`. Celery
never saw their tasks.

**Why it's bad:** The tasks exist in code but are dead. beat schedules that reference
them fail silently. Manual calls via `task_name.delay()` raise
`NotRegisteredError`. The `node_watchdog_task` had a different Celery name than the
beat schedule expected, so it also never ran.

**Lesson:** When adding a new `tasks_*.py` file, you MUST add it to
`register_extra_tasks` in `backend/config/celery.py`. When adding a beat schedule
entry, verify the task name matches the `@shared_task(name=...)` or the default
Celery naming convention exactly.

**How to catch:** After adding a task module, run:
```bash
grep -r "tasks_.*\.py" backend/config/celery.py
```
Every file must appear. After adding a beat schedule, grep for the task name in
the task module to confirm it exists.

---

### 4. Unconditional Docker Restart on Every Update

**What happened:** `configure_docker_mirror` called `systemctl restart docker`
unconditionally (not just when daemon.json changed). Every `install.sh --update`
restarts Docker, killing all running containers mid-update.

**Why it's bad:** The update flow calls `refresh_runtime_services` which calls
`configure_docker_mirror`. If Docker restarts here, all app containers, Traefik,
and celery workers die. The update script continues assuming they're running.

**Lesson:** Never put unconditional service restarts in helper functions that are
called during deployment flows. Always diff the config before and after, and only
restart if it changed:
```bash
prev="$(cat /etc/docker/daemon.json 2>/dev/null || echo '')"
_merge_daemon_json "$new_config"
new="$(cat /etc/docker/daemon.json 2>/dev/null || echo '')"
if [ "$prev" != "$new" ]; then
    systemctl restart docker
fi
```

**How to catch:** Search for `systemctl restart` in any lib/*.sh file. Every one
should be inside a conditional (changed-check, service-down-check, or explicit
user request).

---

### 5. Force-Recreating Edge Services on Every Update

**What happened:** `restart_edge_stack` and `refresh_runtime_services` used
`--force-recreate` for Traefik, socket-proxy, and route-fallback on every update.

**Why it's bad:** Force-recreating Traefik kills all in-flight HTTP requests and
drops active WebSocket connections. Every user hitting the platform during an update
gets a 502/504.

**Lesson:** Edge services (reverse proxy, API gateway) should never be
force-recreated during routine updates. Use check-then-start:
```bash
if docker compose ps "$svc" | grep -q "Up"; then
    echo "already running"
else
    docker compose up -d --no-deps "$svc"
fi
```

**How to catch:** `grep -n "force-recreate" lib/common.sh`. Any hit for
`traefik`, `socket-proxy`, or `route-fallback` in a restart/refresh function is a
bug.

---

### 6. No Timeout on Long-Running Commands

**What happened:** `pg_dump`, `sync_platform_domain_state`,
`safe_refresh_runtime_services`, `ensure_celery_workers_running`, and the stalled
queue check (`docker exec ... python manage.py shell`) had no timeout wrappers.

**Why it's bad:** If any of these hang (network issue, Docker stuck, database
lock), the entire update script hangs forever. No error, no rollback, no user
feedback.

**Lesson:** Every potentially long-running external command in shell scripts needs
a timeout:
- Database dumps: `timeout 300`
- Docker exec commands: `timeout 30`
- Service refresh: `timeout 600`
- Worker restart: `timeout 300`
- Python/Django management commands: `timeout 120`

**How to catch:** `grep -n "docker exec\|pg_dump\|docker compose exec" lib/*.sh
scripts/*.sh`. Every hit should have a `timeout` prefix.

---

### 7. Broken Celery Route Pointing to Wrong Module

**What happened:** `task_routes` in celery.py had
`apps.deployments.tasks_server_update.update_remote_server_task` but the task's
actual `name=` is `apps.deployments.tasks.update_remote_server_task`.

**Why it's bad:** The route doesn't match the task name, so the task runs on the
default `celery` queue instead of the `deploy` queue. This can cause priority
inversion — deploy tasks compete with general tasks.

**Lesson:** Celery task routes are matched by the task's `name=` attribute, not
the Python module path. When you see `tasks_server_update.update_remote_server_task`
as a route key, it will never match because the task's `name=` is
`tasks.update_remote_server_task`. Always use the `name=` value as the route key.

**How to catch:** For every entry in `task_routes`, verify the key matches a
`@shared_task(name="...")` or the default Celery naming convention. Run:
```bash
grep -rn "@shared_task" backend/apps/deployments/tasks_*.py | grep "name="
```
and compare against celery.py route keys.

---

### 8. Duplicate Task Definitions Across Files

**What happened:** 20 tasks were defined in both `tasks.py` (auto-discovered) and
specialized modules like `tasks_deploy.py` (manually imported). The `tasks.py`
version was stale — missing retry logic, time limits, and commit status posting.

**Why it's bad:** Celery registers both. The one imported last wins. Behavior
depends on import order, which is fragile. The stale version may execute instead of
the improved one.

**Lesson:** When a task is moved to a specialized module, the old definition in
tasks.py must be replaced with a re-export:
```python
# tasks.py
from .tasks_deploy import my_task  # re-export, not duplicate
```
Or the old definition must be deleted. Never leave two definitions of the same
task name.

**How to catch:** After adding a task to a specialized module, grep for the function
name across all tasks_*.py files. If it appears in more than one file, one is a
duplicate.

---

### 9. Importing from the Wrong Module After Refactor

**What happened:** `tasks_ai_router.py` and `tasks_safedeploy.py` imported
`delete_service_task` from `tasks_deploy.py` after it was changed to a re-export.

**Why it's bad:** The re-export works, but it creates a fragile chain. If
tasks_deploy.py's re-export is removed, both files break silently at import time.

**Lesson:** After refactoring, update all callers to import from the canonical
location. `from .tasks import delete_service_task` is always correct.
`from .tasks_deploy import delete_service_task` only works if the re-export exists.

**How to catch:** After changing where a function lives, grep for all imports of
that function name:
```bash
grep -rn "from.*import.*delete_service_task" backend/
```
Each import path should point to the canonical module.

---

### 10. Missing Time Limits on Celery Tasks

**What happened:** `delete_service_task` had `max_retries=3` but no
`soft_time_limit` or `time_limit`. It also never called `self.retry()`.

**Why it's bad:** If a Docker operation hangs (container stop waits forever, volume
removal blocked), the task blocks a worker thread indefinitely. With
`acks_late=True`, the task can't be acked, so the queue fills up. With
`max_retries=3` but no `self.retry()`, the retry counter is meaningless — one
failure = permanent death.

**Lesson:** Every Celery task that does I/O needs:
```python
@shared_task(bind=True, soft_time_limit=300, time_limit=330)
def my_task(self, ...):
    try:
        ...
    except SoftTimeLimitExceeded:
        # mark as failed, don't crash
    except self.MaxRetriesExceededError:
        # mark as failed, don't crash
    except Exception as exc:
        raise self.retry(exc=exc, countdown=30)
```

**How to catch:** `grep -rn "@shared_task" backend/apps/deployments/tasks*.py`.
Any task without `soft_time_limit` that does Docker/network/DB operations is a
hanging risk.

---

### 11. Running Migrations Twice

**What happened:** The update flow runs `run_backend_migrations` during the compose
rebuild phase (line 846), then `grid-handshake.sh` ran `migrate --noinput` again
at the end (line 1714).

**Why it's bad:** Double-migration wastes 30-60 seconds and risks race conditions
if another process is writing to the database. Not a crash, but unnecessary risk.

**Lesson:** When a script is called from a pipeline that already did the work,
pass a flag to skip redundant steps:
```bash
SMSLY_MIGRATIONS_DONE=1 bash scripts/grid-handshake.sh
```

**How to catch:** After any pipeline change, trace the full call chain and check
if any management command (`migrate`, `collectstatic`, `fix_sequences`) is called
more than once. `grep -rn "migrate\|collectstatic" lib/*.sh scripts/*.sh`.

---

### 12. No Recovery for Stuck Deletion Tasks

**What happened:** Services stuck in `DELETION_PENDING` were only re-queued during
manual `install.sh --update`. If the re-queue itself failed (Celery down, broker
overloaded), the service was stuck forever.

**Why it's bad:** Users see "Deleting..." forever with no way to retry. The only
fix was another full update or manual DB cleanup.

**Lesson:** Any state machine that can get stuck needs a periodic recovery task:
```python
@shared_task(name="apps.deployments.tasks.recover_stalled_deletions")
def recover_stalled_deletions():
    threshold = timezone.now() - timedelta(minutes=10)
    services = Service.objects.filter(
        status=Service.Status.DELETION_PENDING,
        updated_at__lt=threshold,
    )
    for s in services:
        delete_service_task.delay(str(s.id))
```
Register it in celery.py beat_schedule. Always.

**How to catch:** `grep -rn "DELETION_PENDING" backend/`. Any state that can
get stuck needs either a beat schedule entry or an update-flow recovery pass.
If the only recovery is in `install.sh --update`, it's not enough.

---

### 13. Dead Celery Route for tasks_health Module

**What happened:** `task_routes` in celery.py had
`apps.deployments.tasks_health.node_watchdog_task` as a route entry. But the task
defines `name="apps.deployments.tasks.node_watchdog_task"` (not
`tasks_health.node_watchdog_task`). The route key doesn't match the task name.

**Why it's bad:** The route is dead — it will never match any task. Not a crash,
but noisy and misleading. If someone adds a task with the `tasks_health.` prefix,
it would silently land on the wrong queue.

**Lesson:** A route key must exactly match the task's `name=` attribute. If a
task is defined in `tasks_health.py` but has `name="apps.deployments.tasks.node_watchdog_task"`,
the route key must be `apps.deployments.tasks.node_watchdog_task`, not
`apps.deployments.tasks_health.node_watchdog_task`. The `name=` is the task's
identity — the Python module path is irrelevant to Celery routing.

**How to catch:** For every entry in `task_routes`, verify the key matches a
`@shared_task(name="...")` exactly. The `name=` is the task's identity, not the
Python module path.

---

### 14. Duplicate Task Definitions Across Files (Pre-Existing)

**What happened:** Two tasks are defined in both `tasks.py` and specialized modules
with the same Celery `name=`:

- `node_watchdog_task`: `tasks.py:5378` (no `name=` override, auto-resolves to
  `apps.deployments.tasks.node_watchdog_task`) and `tasks_health.py:132` (explicit
  `name="apps.deployments.tasks.node_watchdog_task"`).
- `update_remote_server_task`: `tasks.py:5054` and `tasks_server_update.py:22`
  (both with explicit `name="apps.deployments.tasks.update_remote_server_task"`).

**Why it's bad:** Celery registers both. The one imported last wins. Behavior
depends on import order. The stale version may execute instead of the improved one.
If `tasks.py` is the stale version, retry logic, time limits, and error handling
from the specialized module are silently lost.

**Lesson:** When a task is moved to a specialized module, the old definition in
tasks.py must be replaced with a re-export:
```python
# tasks.py
from .tasks_health import node_watchdog_task  # re-export, not duplicate
```
Or the old definition must be deleted. Never leave two definitions of the same
task name.

**How to catch:** After adding a task to a specialized module, grep for the function
name across all tasks_*.py files. If it appears in more than one file, one is a
duplicate.

---

## Pre-Commit Checklist

Before committing changes to this codebase:

1. **`git diff` every changed file.** For every `-` line, verify the logic exists
   in the `+` lines.
2. **`python3 -m py_compile` every changed .py file.** Syntax errors are free to
   catch.
3. **`bash -n` every changed .sh file.** Same.
4. **`grep -r "function_name" backend/` after moving any function.** All callers
   must be updated.
5. **After adding a task module:** Add it to `register_extra_tasks` in celery.py.
6. **After adding a beat schedule entry:** Verify the task `name=` matches the
   schedule key.
7. **After adding a task route:** Verify the route key matches the task `name=`.
8. **After rewriting a shell helper:** Search for `systemctl restart` — every one
   must be conditional.
9. **After touching deletion logic:** Verify `delete_service_task` has
   `soft_time_limit`, `time_limit`, `self.retry()`, and exception handlers.
10. **After any refactoring:** `grep -rn "from.*import.*OLD_NAME" backend/` to
    find stale imports.
11. **After moving a task to a specialized module:** Grep for the function name
    across all `tasks_*.py` files. If it appears in more than one file, one is a
    duplicate — replace the old one with a re-export.
12. **After rewriting a restart/refresh function:** Search for `force-recreate` —
    edge services (`traefik`, `socket-proxy`, `route-fallback`) must never be
    force-recreated during routine updates.
13. **Before committing shell scripts:** Verify all potentially long-running
    commands (`pg_dump`, `docker exec`, `docker compose exec`) have `timeout`
    prefixes.
14. **After touching migration logic:** Verify migrations are not run redundantly
    across scripts in the same pipeline. `grep -rn "migrate\|collectstatic"
    lib/*.sh scripts/*.sh`.
15. **After adding magic numbers to task decorators:** Verify they reference
    constants from `apps/deployments/constants.py`. Run:
    ```bash
    grep -rn "soft_time_limit=[0-9]\|time_limit=[0-9]\|default_retry_delay=[0-9]" \
      backend/apps/deployments/tasks/ backend/apps/deployments/services/ \
      --include="*.py" | grep -v __pycache__ | grep -v "constants"
    ```
    Any remaining literal values should be replaced with named constants
    (TASK_TIME_LIMIT_*, RETRY_DELAY_*).

---

## Rules of Engagement

### Do Not Touch Unrelated Code

**Rule:** When assigned a task, change ONLY what the task requires. Do not modify
unrelated functions, files, or code blocks "while you're at it."

**Why:** Every extra change is a potential regression. A one-line fix becomes a
500-line diff. The reviewer has to verify every `-` line. Unrelated changes make it
impossible to tell what broke if something goes wrong.

**Examples of violations:**
- Rewriting `configure_docker_mirror` to remove the mirror AND silently removing
  the master insecure-registry config (AGENTS.md #1). The master config had
  nothing to do with the mirror.
- Adding retry logic to `delete_service_task` AND removing unrelated functions from
  the same file during a partial revert.
- Fixing a Celery route AND modifying unrelated imports in other modules.

**What to do instead:**
- Make the smallest possible change that fixes the issue.
- If you notice a separate problem, file it as a separate task. Do not bundle it.
- If a rewrite is necessary, diff the OLD and NEW versions line by line before
  committing. Every `-` line must have a corresponding `+` line unless you can
  prove the branch is dead.
