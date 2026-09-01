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

### 15. docker-py 7.x Silently Drops networking_config

**What happened:** Deploys passed `containers.create(networking_config=<NetworkingConfig>)`
with no `network` kwarg. docker-py 7.x's `_create_container_args` only honors
`networking_config` **when `network` is also present**, and its
`network not in networking_config` sanity check requires a **plain dict**
(`{net: EndpointConfig}`) — a `NetworkingConfig` wrapper has only one top-level
key (`"EndpointsConfig"`), so the check fails, the config is silently discarded,
and the container lands on the default `bridge` (172.17.x.x) with **no aliases**.

**Why it's bad:** Traefik labels pointed at the project-scoped bridge while the
container was on the default bridge — every redeploy returned "no available
server" and operators had to re-attach networks by hand from the project page.

**The correct pattern** (verified live):
```python
networks = {
    project_bridge: client.api.create_endpoint_config(aliases=[...]),
    platform_bridge: client.api.create_endpoint_config(aliases=[...]),
}
client.containers.create(..., network=project_bridge, networking_config=networks)
```
`network` = the PRIMARY bridge (must be a key of the dict), `networking_config`
= the plain dict with ALL bridges.

**How to catch:** After any container-create change, immediately inspect the
running container: `docker inspect <c> --format '{{json .NetworkSettings.Networks}}'`.
If the container is on `bridge` only, this bug is live.

---

### 16. docker compose --remove-orphans With the Wrong Project Name Kills the Whole Stack

**What happened:** The mTLS deploy ran
`docker compose -f docker-compose.spire.yml up -d ... --remove-orphans` with
`cwd=/opt/smsly-hosting`. Compose resolved the project name from that directory —
the MAIN stack's project — so `--remove-orphans` classified every main-stack
service (backend, celery, caddy, registry...) as orphans of the spire project
and **SIGTERM'd them all. Full platform outage** (exit 143 across the board),
including the DB connection of the request that triggered it.

**Lesson:** When compose-ing a secondary stack that shares a directory (or
networks) with the main stack, ALWAYS pass an explicit isolated project name
(`-p smsly-spire`) and NEVER use `--remove-orphans` on it.

**How to catch:** Any `docker compose` call in backend code must be reviewed
for: explicit `-p`? no `--remove-orphans`? wrong `cwd`?

---

### 17. Egress Wildcards Must Match the Host's Real NIC Names

**What happened:** The egress isolation rules RETURNed internet traffic via
`-o wl+ / enp+ / eth+` only. The OVH host's NIC is **ens3** — matched by none
of them — so every packet fell through to the catch-all DROP. The platform's
infrastructure bridge (celery workers, backend) lost ALL internet: GitHub
clones timed out, AI providers "Network is unreachable", deploys stuck QUEUED.

**Lesson:** When writing iptables interface wildcards, cover every naming
scheme (`wl+, enp+, ens+, eth+, eno+`) — or better, enumerate the host's
actual default-route NICs. `scripts/verify_platform_integrity.sh` now guards
this on a schedule.

**How to catch:** After applying egress rules, from inside a worker:
`docker exec smsly-hosting-celery-deploy-1 python -c "import socket; socket.create_connection(('api.github.com',443),timeout=6)"`.

---

### 18. Single-Use Secrets Can't Live in Compose Files

**What happened:** The spire-agent compose service crashed with
`nodeattestor(join_token): join token was not provided`. Join tokens are
single-use, minted per boot — a compose `environment:` entry can't carry them.

**Lesson:** For SPIRE agents (or anything with one-shot bootstrap secrets):
compose-up only the server, then mint the token
(`spire-server token generate`) and `docker run ... -joinToken <token>` the
agent. Store the token nowhere. The mtls deploy endpoint
(`apps/mtls/views.py::_start_agent_with_token`) implements this.

---

### 19. Traefik Healthcheck Labels Need a Path That Actually Answers

**What happened:** A Next.js app served `/api/health` but not `/health`.
The Docker healthcheck passed (it probes a fallback path list), but the
Traefik service healthcheck label carried the single primary path `/health`
→ Traefik marked the backend DOWN → the domain returned "no available server".

**Lesson:** Before promoting, probe the running container for the first
candidate path that answers within the acceptable status range (default
200–399, matching Traefik's own rule — 401/403/404 are "wrong path", 5xx
means keep the primary so the instance is pulled). Direct starts get a
one-time invisible recreate with corrected labels.

---

### 20. Serializers: get_<field> Must Live on the Class That Declares the Field

**What happened:** `effective_registry` (SerializerMethodField) was declared on
`ServiceSerializer` (detail) but `get_effective_registry` was added to
`ServiceListSerializer`. DRF resolves the method on the declaring class only →
`AttributeError` → **every service detail page 500'd** until hot-fixed.

**How to catch:** When adding a SerializerMethodField, grep the class you
actually edited for `def get_<field_name>` — not a same-named method on a
different class in the same file.

---

### 21. Parent Poll Re-seeds Forms and Fights the User's Keyboard

**What happened:** The service detail page polls the service every 3s and passes
a fresh object reference to tabs. Health/Resources tabs had
`useEffect([serviceId, initialService])` that re-seeded form state each tick —
the user's in-progress edits were reverted mid-keystroke ("stubborn inputs").

**Lesson:** With a polled prop, seed form state once (keyed by id), keep a
dirty-guard while editing, and re-seed only on explicit Save/Refresh. Depend on
`initialService?.id`, never the object reference.

**How to catch:** Any form component receiving a frequently-refreshed prop must
use the `dirtyRef`/`seededRef` pattern (see HealthTab.tsx / ResourcesTab.tsx).

---

### 22. Crash Logs Belong in Their Own Field

**What happened:** Runtime crash output was embedded into `build_logs` with
`--- Runtime Crash Logs ---` markers; the Runtime tab regex-scraped it back out.
The Build tab therefore showed runtime errors and the Runtime tab showed build
pipeline text.

**Lesson:** `Deployment.runtime_logs` (migration 0192) is the canonical home for
runtime/crash output. Writers append there; readers prefer the field and only
marker-scrape legacy rows. Never append runtime output to `build_logs`.

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
16. **After any container-create networking change:** Deploy one service and
    run `docker inspect <container> --format '{{json .NetworkSettings.Networks}}'`.
    The container must be on the project bridge + `smsly-platform-net`, never
    default `bridge` only (AGENTS.md #15).
17. **After any `docker compose` call in backend code:** Verify it passes an
    explicit `-p` project name and NO `--remove-orphans` (AGENTS.md #16).
18. **After adding a SerializerMethodField:** Grep the DECLARING class for
    `def get_<field_name>` (AGENTS.md #20).
19. **After writing iptables egress rules:** Verify the interface wildcards
    include the host's actual NICs (`wl+, enp+, ens+, eth+, eno+`), then
    test internet from a worker container (AGENTS.md #17).
20. **After any form component receives a polled prop:** Verify it uses the
    `dirtyRef`/`seededRef` pattern and depends on `prop?.id`, not the object
    reference (AGENTS.md #21).

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
