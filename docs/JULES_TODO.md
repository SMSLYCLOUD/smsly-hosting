# Jules TODO — Deferred Refactors & Hardening

> **Audience:** Google Jules (or any future autonomous refactor agent).
> **Why this doc exists:** A 2026-06 deep-sweep produced a list of half-applied or
> not-yet-applied refactors and hardening items. Every entry below was either
> (a) explicitly rejected by the operator for not slowing prod down, or
> (b) left half-done because the agent that started the work ran out of
> context. Items are sequenced roughly lowest-risk → highest-risk. Each item
> is scoped to non-overlapping files so multiple agents can work in parallel.
>
> **Pre-flight before any item:**
> ```bash
> cd smsly-hosting
> python --version            # expect 3.11
> node --version              # expect >= 20
> docker --version            # expect >= 24
> git status --short          # expect only "?? K8native/" and "?? frontend/playwright-report/"
> ```
>
> **Hard rules — never violate:**
> 1. Do NOT commit. Every change is uncommitted for human review.
> 2. Do NOT run `docker compose up`, `manage.py migrate`, or any side-effecting
>    command against prod. Use `--dry-run` flags where available.
> 3. Preserve every public API: `from x import y` must keep working; URL
>    patterns and Celery task names are part of the contract.
> 4. The Django `Service.function_code` encryption (item B-1) must use the
>    platform's existing `FIELD_ENCRYPTION_KEY` — do NOT introduce a new key.
> 5. The docker-socket-proxy split (item D-1) must keep `EXEC=0` on the
>    runtime proxy — this is the security-critical change.
> 6. Every image pin (item D-2) must be `<tag>@sha256:<digest>` where the
>    digest is the multi-arch manifest digest fetched live from
>    `https://hub.docker.com/v2/repositories/<repo>/tags/<tag>`. Keep the tag.

---

## A. God-File Inventory (the big ones — context for everything below)

These are NOT to be touched by Jules in one pass. They are listed so subsequent
work (items B-F) understands the context and so contributors can choose which
slice to tackle first.

| File | Lines | Domain | Suggested split |
|------|------:|--------|-----------------|
| `backend/apps/deployments/views.py` | 5,033 | API | 18 classes; `ServiceViewSet` alone is 2,979 lines / 26 `@action`s. Per-domain sibling pattern already exists (24 `views_*.py` files). |
| `backend/apps/deployments/tasks.py` | 4,952 | Celery | ~118 funcs. Per-domain siblings exist (`tasks_*.py`). |
| `backend/apps/deployments/models/ecosystem.py` | 2,078 | Ecosystem orchestrator | No siblings yet. Suggested: `ecosystem/{graph,deploy,plan,persist,ai,env}.py`. |
| `backend/apps/deployments/services/caddy_manager.py` | 1,158 | Caddy config | Suggested: `caddy/{config,cert,upstream,watcher,monolith}.py`. |
| `backend/apps/addons/services/addon_provisioner.py` | 789 | Addon lifecycle | Acceptable size but tightly coupled — consider splitting after E-1. |
| `backend/apps/deployments/services/pipeline.py` | ~2,500 | Deploy pipeline | Suggested: `pipeline/{stages,runner,promote,rollback}.py`. |
| `backend/apps/deployments/services/backup_service.py` | ~2,600 | Backup + restore + retention | Suggested: `backup/{create,restore,retention,encrypt}.py`. |
| `backend/apps/deployments/services/transfer_service.py` | ~1,900 | Server transfer | Suggested: `transfer/{plan,execute,verify,cleanup}.py`. |
| `backend/apps/deployments/services/remote_orchestrator.py` | ~1,500 | Remote API proxy | Suggested: `remote/{client,executor,attest}.py`. |
| `backend/apps/deployments/services/provisioner.py` | ~1,500 | Node provisioning | Suggested: `provisioner/{ssh,docker,bootstrap,verify}.py`. |
| `backend/apps/intelligence/providers.py` | ~1,400 | 10 AI providers | Suggested: `providers/{openai,anthropic,gemini,grok,claude,jules,freemodel,opencode,mistral,nvidia,cloudflare}/__init__.py`. |
| `backend/config/settings.py` | 1,150 | Django settings | See item F-1. |
| `backend/apps/deployments/models_core.py` | 1,077 | Core models | Acceptable size but mixes concerns; consider per-model split later. |
| `frontend/src/app/settings/page.tsx` | 1,568 | Platform settings UI | See item C-1. |
| `frontend/src/app/ecosystem/page.tsx` | 1,173 | Ecosystem graph UI | Suggested split: `<EcosystemGraph/>`, `<EcosemetryPanel/>`, `<EcosystemActions/>`. |
| `frontend/src/app/servers/page.tsx` | 910 | Server list | Suggested split: `<ServerTable/>`, `<ServerFilters/>`, `<ServerActions/>`. |
| `frontend/src/app/new/page.tsx` | 1,206 | New-service wizard | Suggested split: per-step components. |
| `frontend/src/components/settings/PlatformTab.tsx` | 1,051 | (orphan — see C-1) | Delete or wire in. |

Duplicate-imports audit (smell from the June refactor):
- `backend/apps/deployments/tasks_deploy.py` lines 46-72 imports 12 symbols
  each twice in a single import block. Real sign that the file was concat-
  assembled. De-duplicate when this file is next touched.

---

## B. Backend Hardening (independent items, run in parallel)

### B-1. Encrypt `Service.function_code` at rest

**Files:** `backend/apps/deployments/models_core.py`,
`backend/apps/cloud/services/function_provisioner.py`,
`backend/apps/deployments/tasks_build.py`.

**Goal:** Replace `models.TextField` with `EncryptedTextField` so a DB dump
does not leak serverless source code.

**Steps:**
1. Change `Service.function_code` in `models_core.py:284` from
   `models.TextField(blank=True, help_text=...)` to
   `EncryptedTextField(blank=True, default="", help_text="...")`. The library
   is already imported at line 12 of the same file.
2. Run `cd backend && python manage.py makemigrations deployments` to generate
   the schema migration. Django may detect no column-level change (both types
   report `get_internal_type() == "TextField"`); in that case the migration
   will be empty and you can `makemigrations --empty` instead.
3. If any rows have plaintext `function_code`, add a `RunPython` data
   migration that bulk-re-encrypts them in batches of 500 via
   `Service.objects.bulk_update(rows, fields=["function_code"])`. Detect
   plaintext by `not value.startswith("gAAAAA")`.
4. Verify the call sites read the attribute (not raw SQL):
   - `apps/cloud/services/function_provisioner.py` line ~19
   - `apps/deployments/tasks_build.py:_build_function` line ~60
   Both should keep working — `EncryptedTextField` decrypts on attribute read.
5. Add test `backend/apps/deployments/tests/test_function_code_encryption.py`:
   - Create a Service with `function_code='print("hi")'`.
   - Read raw via `Service._default_manager.using('default').raw('SELECT function_code FROM ...')[0]`.
   - Assert raw value starts with `gAAAAA`.
   - Assert `service.function_code == 'print("hi")'`.

**Verify:**
```bash
cd backend && TESTING=1 pytest apps/deployments/tests/test_function_code_encryption.py -v
cd backend && python manage.py makemigrations --check --dry-run
```

**Risk:** low — column type is unchanged. Worst case is rows that were
written in plaintext get re-encrypted on next deploy.

---

### B-2. Encrypt `EnvironmentVariable.value` migration re-check

**Files:** `backend/apps/deployments/models_core.py` (already `EncryptedCharField`),
`backend/apps/deployments/migrations/`.

**Verify** that `EnvironmentVariable.value` is consistently `EncryptedCharField`
across all migrations. Earlier refactors added re-encrypt migrations; ensure
no migration sequence can leave a row readable as plaintext.

**Verify:**
```bash
cd backend && python manage.py makemigrations --check --dry-run
```

---

## C. Frontend Decomposition (independent items, run in parallel)

### C-1. Finish `frontend/src/app/settings/page.tsx` decomposition

**Files:** `frontend/src/app/settings/page.tsx` (currently 1,568 lines),
new: `frontend/src/components/settings/*.tsx`.

**Goal:** Reduce page.tsx to a composition root of ~100 lines by extracting
each tab into its own component.

**Existing extracted components in the tree** (already used by
`services/[id]/page.tsx` and `servers/[id]/page.tsx`):
`BackupsTab.tsx`, `DeploymentsTab.tsx`, `EnvVarsTab.tsx`, `DomainsTab.tsx`,
`BackupKeysTab.tsx`, `OAuthTab.tsx`, `FilesTab.tsx`, `AiRouterTab.tsx`,
`ResourcesTab.tsx`, `HealthTab.tsx`, `AIInsightsTab.tsx`, `ScalingTab.tsx`,
`CloudStorageTab.tsx`, `BuildTab.tsx`, `AdvancedTab.tsx`,
`GitHubIntegrationCard.tsx`, `GitLabIntegrationCard.tsx`,
`BitbucketIntegrationCard.tsx`.

**New platform-level tabs to extract** (these do NOT exist yet — previous
attempt created orphan files that were reverted):
- `GeneralTab.tsx` — profile + API keys + team settings (~280 lines)
- `PlatformTab.tsx` — cloud providers, AI config, autoscale, domain/SSL,
  Redis/Celery/Registry/RateLimit/DB/RouteRecheck, maintenance
  (~1050 lines — the largest extraction)
- `SecurityTab.tsx` — password change, 2FA setup (~80 lines)
- `NotificationsTab.tsx` — notification preferences (~50 lines)
- `IntegrationsTab.tsx` — OAuth + GitHub/GitLab/Bitbucket/Cloud delegate
  cards (~20 lines; delegates to existing per-provider cards)
- `BillingTab.tsx` — link to `/settings/billing` route (~30 lines)
- `PlatformDeploymentsTab.tsx` — link to per-service deployments page
  (~30 lines; rename to avoid clobbering existing `DeploymentsTab.tsx`)

**Steps:**
1. Read `frontend/src/app/settings/page.tsx` end-to-end. Identify each
   `<TabsContent value="...">` block by its `value` prop and surrounding
   JSX. There should be one block per tab above.
2. For each tab, create the new component as a `'use client'` file under
   `frontend/src/components/settings/`. Receive the props it needs from
   page.tsx via the parent — do NOT use global state.
3. Move the inline type definitions (`CloudProvider`, `MaintenanceAction`,
   `MAINTENANCE_COPY`, `INITIAL_MAINTENANCE_STATE`, `DomainFormState`)
   into `frontend/src/components/settings/_types.ts` with `export` keywords.
4. **Important: page.tsx currently defines these inline. If `_types.ts`
   exports the same names and page.tsx still defines them inline, you will
   get TS2300 "Duplicate identifier". To avoid this, the new tab files
   must NOT import from `_types.ts` until page.tsx is fully trimmed.**
   Either trim page.tsx FIRST (then add _types imports), or have the new
   tabs redeclare the types locally for the transition period.
5. Replace the inline JSX in page.tsx with `<GeneralTab {...props}/>`,
   `<PlatformTab {...props}/>`, etc. Trim page.tsx to ~100 lines.
6. Wire `page.tsx` to import each tab component and pass props.

**Verify:**
```bash
cd frontend && npm run typecheck   # must pass
cd frontend && npm run lint        # must pass
cd frontend && npm run build       # /settings bundle should be ~25-30 kB
```

**Risk:** medium — moving state between parent and child components
can change re-render behavior. Watch for prop-drilling loops.

---

### C-2. Decompose `frontend/src/app/ecosystem/page.tsx` (1,173 lines)

**Files:** `frontend/src/app/ecosystem/page.tsx`, new under
`frontend/src/components/ecosystem/`.

**Goal:** Split into `<EcosystemGraph/>` (3D canvas), `<EcosystemPanel/>`
(sidebar), `<EcosystemActions/>` (deploy/rollback buttons).

**Steps:** Mirror the page.tsx pattern from C-1.

**Verify:** same as C-1.

---

### C-3. Decompose `frontend/src/app/servers/page.tsx` (910 lines)

**Files:** `frontend/src/app/servers/page.tsx`, new under
`frontend/src/components/servers/`.

**Goal:** Split into `<ServerTable/>`, `<ServerFilters/>`, `<ServerActions/>`.

---

### C-4. Decompose `frontend/src/app/new/page.tsx` (1,206 lines)

**Files:** `frontend/src/app/new/page.tsx`, new under
`frontend/src/components/new-service/`.

**Goal:** Split into per-step components of the new-service wizard.

---

## D. Docker / Compose Hardening (single item — keep atomic)

### D-1 + D-2. Image pins + socket-proxy split + PgCat loopback bind

**Files:** `docker-compose.prod.yml` ONLY.

**Step D-1: socket-proxy split.** Replace the single `socket-proxy` service
with two:
- `socket-proxy-build`: env `BUILD=1, COMMIT=1, POST=1` (everything else 0,
  especially `EXEC=0`).
- `socket-proxy-runtime`: env `CONTAINERS=1, IMAGES=1, NETWORKS=1,
  VOLUMES=1, INFO=1, VERSION=1, EVENTS=1` (no `EXEC`, no `POST`,
  no `BUILD`/`COMMIT`).

Rewire `DOCKER_HOST` per service:
- `backend`, `celery-deploy` → `tcp://socket-proxy-build:2375`
- `celery`, `celery-fast`, `traefik` → `tcp://socket-proxy-runtime:2375`
- Rewire `depends_on:` accordingly.

Rewire Traefik's `--providers.docker.endpoint` to
`tcp://socket-proxy-runtime:2375`.

**Step D-2: image digests.** Replace floating tags with
`<image>:<tag>@sha256:<digest>`. The `<digest>` is the multi-arch manifest
digest from
`https://hub.docker.com/v2/repositories/<repo>/tags/<tag>` (look at the
top-level `digest` field in the response). Keep the tag for readability.

Images to pin:
| Service | Image |
|---|---|
| `registry` | `registry:2.8.3` |
| `docker-mirror` | `registry:2.8.3` |
| `apt-cacher` | `sameersbn/apt-cacher-ng:latest` |
| `verdaccio` | `verdaccio/verdaccio:latest` |
| `traefik` | `traefik:v3.6` |
| `buildkitd` | `moby/buildkit:latest` |
| `socket-proxy-build` & `socket-proxy-runtime` | `tecnativa/docker-socket-proxy:0.1.2` |

Keep the `# TODO: pin to digest via dependabot/renovate (Renovate: digest-update).`
comment on each pinned line for Renovate to discover.

**Step D-3: PgCat loopback bind.** Change line ~54 from
`ports: - "10.100.0.1:5432:5432"` to `ports: - "127.0.0.1:5432:5432"`.

**Step D-4: Linux capabilities.** Add `cap_drop: [ALL]` to backend,
celery, celery-fast, celery-deploy, frontend, registry. Add
`cap_drop: [ALL]` + `cap_add: [NET_BIND_SERVICE]` to caddy.

**Verify:**
```bash
python -c "import yaml; yaml.safe_load(open('docker-compose.prod.yml', encoding='utf-8'))"
docker compose -f docker-compose.prod.yml config --quiet   # if docker available
```

**Risk:** medium — wrong socket-proxy wiring breaks deployments. Test on a
staging compose stack first.

---

## E. CI / Repo Hardening (independent items, run in parallel)

### E-1. SHA-pin GitHub Actions + `permissions: contents: read`

**Files:** `.github/workflows/*.yml`.

**Steps:** For every `uses: owner/repo@vN` or `@main` or `@master`, replace
with `uses: owner/repo@<full-sha>  # vN`. Look up SHAs with
`git ls-remote https://github.com/owner/repo refs/tags/vN` (or webfetch
github.com/owner/repo.git/info/refs?service=git-upload-pack for the SHA).

Add a top-level `permissions:` block to each workflow with at minimum
`contents: read`. Override per-job with `permissions:` if a job needs more.

**Verify:**
```bash
grep -rE "uses:.*@(v[0-9]+|main|master)" .github/workflows/   # expect empty
```

### E-2. Verify security-scanning jobs are present

**Files:** `.github/workflows/ci.yml` (or similar).

Verify these jobs exist (they may already — confirm by reading the file):
- `pip-audit --strict --requirement backend/requirements.txt`
- `bandit -r backend/ -ll -i`
- `gitleaks/gitleaks-action` (SARIF upload)
- `npm audit --omit=dev --audit-level=high`
- `safety check --file backend/requirements.txt`

If any are missing, add them under a new `sca:` job.

### E-3. CODEOWNERS + Dependabot

**Files:** `.github/CODEOWNERS`, `.github/dependabot.yml`.

If absent:
```bash
cat > .github/CODEOWNERS <<EOF
*                       @smsly/maintainers
/backend/               @smsly/backend
/frontend/              @smsly/frontend
/infrastructure/        @smsly/platform
/charts/                @smsly/platform
/.github/               @smsly/maintainers
SECURITY.md             @smsly/maintainers
EOF

cat > .github/dependabot.yml <<EOF
version: 2
updates:
  - package-ecosystem: "pip"
    directory: "/backend"
    schedule: { interval: "weekly" }
  - package-ecosystem: "npm"
    directory: "/frontend"
    schedule: { interval: "weekly" }
  - package-ecosystem: "docker"
    directory: "/"
    schedule: { interval: "weekly" }
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule: { interval: "weekly" }
EOF
```

### E-4. `.env` permissions + tracked-secret guard script

**Files:** new `scripts/check_env_perms.sh`.

The script:
```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

# .env must not exist with mode > 600
if [ -f .env ]; then
    mode=$(stat -c '%a' .env 2>/dev/null || stat -f '%Lp' .env)
    if [ "$mode" -gt 600 ]; then
        echo "FAIL: .env mode is $mode (must be <= 600)"
        exit 1
    fi
fi

# No tracked secrets
if git ls-files | grep -E '(^secrets/|\.key$|htpasswd)' | grep -v '^certs/registry\.crt$'; then
    echo "FAIL: tracked secret file found"
    exit 1
fi

# .env.production must match HEAD
if ! git diff --quiet HEAD -- .env.production; then
    echo "FAIL: .env.production differs from committed baseline"
    exit 1
fi

echo "OK"
```

Wire into `.pre-commit-config.yaml` as a `local` hook.

---

## F. Django Settings Refactor

### F-1. Split `backend/config/settings.py` (1,150 lines) into a package

**Files:** `backend/config/settings.py`, new under
`backend/config/settings/`, `backend/apps/deployments/apps.py`,
new `backend/apps/deployments/runtime_config_sync.py`.

**Goal:** Each settings submodule ≤200 lines; settings import must NOT
trigger a DB connection.

**Sub-package layout:**
```
backend/config/settings/
  __init__.py        # imports all submodules in dependency order
  secrets.py         # SECRET_KEY, FIELD_ENCRYPTION_KEY, GATEWAY_SECRET, ...
  domain.py          # DOMAIN, DEBUG, IS_TESTING, _env_bool, ALLOWED_HOSTS, ...
  paths.py           # BASE_DIR, STATIC_URL/ROOT, MEDIA_URL/ROOT, ...
  databases.py       # DATABASES, _resolve_db_url, PgCat bypass, ...
  redis_cache.py     # REDIS_*, CACHES, CHANNEL_LAYERS, ...
  celery_queues.py   # CELERY_*, RABBITMQ_*, ...
  security.py        # SECURE_HSTS_*, SECURE_SSL_REDIRECT, ...
  cors.py            # CORS_*, CSRF_TRUSTED_ORIGINS, ...
  oauth.py           # SOCIALACCOUNT_*, AUTHENTICATION_BACKENDS, ...
  apps.py            # INSTALLED_APPS, MIDDLEWARE, ROOT_URLCONF, ...
  registry.py        # CONTAINER_REGISTRY_URL SSRF guard, ...
  throttles.py       # REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'], ...
  logging.py         # LOGGING dict
```

**Each submodule** mutates `from django.conf import settings` directly —
they're not classes, they're side-effecting modules that set attributes on
the settings module.

**Step F-1.0 (do FIRST): remove eager DB connection.** Move the
`try: ... psycopg2.connect ...` block in `settings.py:424-471` into
`backend/apps/deployments/runtime_config_sync.py` as
`def sync_platform_config_to_django() -> None:`. Wire it into
`apps/deployments/apps.py:DeploymentsConfig.ready()` wrapped in
`if os.environ.get('SMSLY_DISABLE_STARTUP_TASKS') != 'true':`. Replace the
removed block in settings.py with a comment pointing to the new module.

**Step F-1.1:** Move `settings.py` content into the package submodules per
the table above. Delete `settings.py` after — Python will prefer the
package over the module. `__init__.py` imports all submodules in order:
secrets → domain → paths → databases → redis_cache → celery_queues →
security → cors → oauth → apps → registry → throttles → logging.

**Verify:**
```bash
cd backend
TESTING=1 SECRET_KEY="$(python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())')" \
  FIELD_ENCRYPTION_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
  python -c "import config.settings; print('IMPORT OK')"
# Expected output: IMPORT OK (with one warning about PlatformConfig DB sync)
```

**Risk:** medium-high. Django's settings import is fragile. Order matters.
Test in a fresh venv before committing.

---

## G. Backend Coverage Gate

### G-1. `.coveragerc` fail_under + test scaffolds for under-tested apps

**Files:** `.coveragerc`, `backend/apps/{billing,autoscaler,licensing,notifications}/tests/test_smoke.py`.

**Goal:** 80% branch coverage gate + smoke test stubs for the four
under-tested apps (each currently has 2-19 test files vs. deployments' 282).

**Step G-1.1:** Update `.coveragerc`:
```ini
[run]
source = backend/apps
omit =
    */migrations/*
    */tests/*
    */venv/*
    */__pycache__/*
branch = True

[report]
show_missing = True
fail_under = 80
exclude_lines =
    pragma: no cover
    raise NotImplementedError
    if __name__ == .__main__.:
    if TYPE_CHECKING:
```

**Step G-1.2:** Create smoke test scaffolds (each marked
`@pytest.mark.smoke`):
- `backend/apps/billing/tests/test_smoke.py` — model imports,
  mocked `StripeService.ensure_customer` returning `cus_test_*`.
- `backend/apps/autoscaler/tests/test_smoke.py` — engine + registry
  imports, `DecisionEngine.decide()` default + scale-up + scale-down.
- `backend/apps/licensing/tests/test_smoke.py` — `TierLimitsMiddleware`
  instantiation, `require_tier` decorator, `PlatformLicense.load()`
  community defaults.
- `backend/apps/notifications/tests/test_smoke.py` — model defaults,
  `validate_ssrf()`, `resource-alerts-list` returns 401 for anon.

**Verify:**
```bash
cd backend && pytest --collect-only apps/billing apps/autoscaler apps/licensing apps/notifications
```

---

## H. Repo Hygiene

### H-1. Fix corrupted `.gitignore` K8native/ entry

**File:** `.gitignore` line 136.

**Issue:** The K8native/ entry is corrupted as `K 8 n a t i v e / ` (spaces
between every character + trailing NUL byte). It does NOT actually ignore
the K8native/ directory, which is why `git status` shows `?? K8native/`.

**Fix:** Replace line 136 with `K8native/`. Verify with
`git check-ignore -v K8native/` — expect non-zero exit and the
`.gitignore:<lineno>:K8native/ K8native` output.

### H-2. Decide K8native/ disposition

**Directory:** `K8native/` (~857 MB, 51,797 files; near-full clone of the
main repo including its own nested `.git/`, `node_modules/`, `venv/`).

**Verified safe to delete:** zero references in main repo
(grep across `*.py`, `*.yml`, `*.yaml`, `*.sh`, `*.json`, `*.ts`, `*.tsx`,
`*.md`, `*.go`, `*.rs`, `*.toml`, `*.cfg`, `*.ini`, `*.txt` returns no hits).

**Fix:** After H-1, `git clean -fd K8native/` (or just `rm -rf K8native/`).

---

## I. Order of Operations (recommended)

Run items in this order to minimize blast radius:

1. **H-1, H-2** (repo hygiene — frees ~857 MB and prevents confusion)
2. **E-1, E-2, E-3, E-4** (CI hardening — no runtime impact, just gates)
3. **D-1+D-2, D-3, D-4** (docker compose hardening — staging-test before prod)
4. **B-1** (function_code encryption — needs migration)
5. **G-1** (coverage gate — needs existing tests to pass first)
6. **F-1** (settings.py split — highest risk, do after everything else)
7. **C-1** (frontend settings/page.tsx — biggest UI surface, do first among C)
8. **C-2, C-3, C-4** (other page.tsx monoliths)
9. **A** (backend god-files — long-running; tackle one at a time)

---

## J. Done State Checklist

When ALL items above are landed, the working tree should:

- [ ] Pass `npm run typecheck && npm run build && npm run lint`
- [ ] Pass `cd backend && pytest --cov=apps --cov-fail-under=80 -m "not slow and not integration and not e2e"`
- [ ] Have `docker-compose.prod.yml` parse with `python -c "import yaml; yaml.safe_load(open(...))"`
- [ ] Have all 18 docker images pinned with `@sha256:` digests
- [ ] Have `grep -rE "uses:.*@(v[0-9]+|main|master)" .github/workflows/` return empty
- [ ] Have `git check-ignore -v K8native/` return a real match
- [ ] Have `git status --short` show only `??` items (no `M` or `D`)
- [ ] Have `from django.conf import settings; settings.SECRET_KEY` work without
      opening a database connection (verified via `lsof -p $$` during a
      `python -c "import config.settings"` invocation).
