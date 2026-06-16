# Deploy runbook — Batch S20 (commit `5424f91f`) to `vps3385097`

This runbook ships the 9 live-log fixes from commit `5424f91f` to the production
VPS at `vps3385097:/opt/smsly-hosting`. The live VPS is still showing the old
behaviour (WebSocket `/ws/service-status/` reconnect spam every 5s, `docker
stats` ENOENT in autoscaler logs), which confirms the container has **not** yet
been rebuilt against the new code.

**Image-vs-restart gotcha.** `docker compose up -d` alone does **not** rebuild;
it just restarts the existing image. You MUST `build` first, then `up -d`,
otherwise the new code never reaches the container.

**Scope of this deploy.** The fixes touch three image layers:

| Layer       | Image (compose service)                                          | Triggered by                      |
|-------------|------------------------------------------------------------------|-----------------------------------|
| Backend     | `backend`, `celery`, `celery-beat`, `celery-fast`, `celery-deploy` | U1, U3, U5, U6, U8, U9, U10a, U10b |
| Frontend    | `frontend`                                                       | U4 (terminal WebSocket subprotocol) |
| Env-only    | (all of the above + `caddy`)                                     | U7 (`CADDY_ASK_SECRET` in `.env`) |

All five backend-flavoured services share `./backend/Dockerfile`, so a single
`docker compose build` pass produces one image and all five services pick it up
on `up -d`.

---

## Table of contents

1. [Pre-flight](#1-pre-flight)
2. [Push to VPS](#2-push-to-vps)
3. [Pull on VPS](#3-pull-on-vps)
4. [Generate env vars (CADDY_ASK_SECRET)](#4-generate-env-vars-caddy_ask_secret)
5. [Rebuild containers](#5-rebuild-containers)
6. [Restart with new image](#6-restart-with-new-image)
7. [Verify each fix](#7-verify-each-fix)
8. [Rollback](#8-rollback)

---

## 1. Pre-flight

### 1a. On your Windows machine (PowerShell)

```powershell
# Confirm you're on the smsly-hosting checkout
cd C:\Users\osaretin\Documents\SMSLY\SMSLY_CORE\smsly-hosting

# Confirm HEAD is the Batch S20 commit
git log -1 --oneline
#  Expect: 5424f91f Batch S20: live log fixes - 9 user-visible backend bugs from production logs

# Confirm the working tree is clean (no uncommitted local edits to push)
git status

# Confirm the remote you'll push to
git remote -v
#  Expect: origin  https://github.com/SMSLYCLOUD/smsly-hosting.git (push)
```

### 1b. On the VPS (`vps3385097`, Linux/bash)

Open an SSH session — either:

- **From PowerShell:**     `ssh root@vps3385097`     (if your SSH key is loaded), OR
- **From the VPS provider's web console** (Contabo/Hetzner/etc. dashboard → "Console") if you don't have key-based SSH from this machine.

Then on the VPS:

```bash
cd /opt/smsly-hosting

# 1. Confirm the VPS is on the OLD code (anything older than 5424f91f).
#    Expect: 7d595235 Batch S19 ... or an even older commit. If you see
#    5424f91f here, the deploy was already done — stop and audit.
git log -1 --oneline

# 2. Confirm current container status (which image hash each service runs).
#    Note the IMAGE column — after the rebuild it should change for the
#    backend/celery/celery-beat/celery-fast/celery-deploy/frontend rows.
docker compose -f docker-compose.prod.yml ps --format 'table {{.Service}}\t{{.Image}}\t{{.Status}}'

# 3. Capture a snapshot of the bugs to confirm the fixes worked later.
#    a) WebSocket spam — count how many service-status connection lines hit
#       the log in the last 60s. Old behaviour: ~12 (one every 5s).
docker compose -f docker-compose.prod.yml logs backend --since=60s 2>&1 | grep -c "service-status"

#    b) docker stats ENOENT — should be NON-ZERO before, ZERO after.
docker compose -f docker-compose.prod.yml logs celery --since=10m 2>&1 | grep -c "docker stats"

#    c) Ephemeral CADDY_ASK_SECRET warning — should be PRESENT before, ABSENT after.
docker compose -f docker-compose.prod.yml logs backend --since=24h 2>&1 | grep -c "CADDY_ASK_SECRET is not set"

# 4. Confirm a writable .env exists and you can edit it (needed for U7).
ls -l /opt/smsly-hosting/.env
#  Expect: -rw------- 1 root root  ...  /opt/smsly-hosting/.env
#  If perms are wrong: chmod 600 /opt/smsly-hosting/.env
```

If any of step 3 returns the expected "old" values (>0 spam lines, >0 ENOENT
lines, >0 ephemeral warnings) you are clear to proceed.

---

## 2. Push to VPS

The VPS pulls from GitHub, so you need to push commit `5424f91f` to
`origin/main` first.

### 2a. From your Windows machine (PowerShell)

```powershell
cd C:\Users\osaretin\Documents\SMSLY\SMSLY_CORE\smsly-hosting

# Push the local main to origin. This is the only command needed if you
# already have credentials cached (HTTPS PAT or SSH key).
git push origin main

# If the push is rejected (non-fast-forward), someone else pushed first.
# Resolve with: git pull --rebase origin main; then re-push.
```

### 2b. Alternative — no GitHub push permission

If you can't push to GitHub from this workstation, ship a bundle:

```powershell
# Create a self-contained git bundle of the new commit (and its parents
# back to whatever the VPS already has — main covers everything).
git bundle create batch_s20.bundle origin/main..HEAD
#  Or, simpler — bundle the full main branch:
git bundle create batch_s20.bundle main

# Copy it to the VPS (any path the VPS can read).
scp .\batch_s20.bundle root@vps3385097:/tmp/batch_s20.bundle
```

Then on the VPS:

```bash
cd /opt/smsly-hosting
git fetch /tmp/batch_s20.bundle main:main-incoming  # imports as a temp ref
git merge --ff-only main-incoming                   # fast-forward main
git branch -D main-incoming                         # cleanup
rm /tmp/batch_s20.bundle
# Skip section 3 — you already pulled.
```

---

## 3. Pull on VPS

(Skip if you used the bundle path in 2b.)

```bash
cd /opt/smsly-hosting

# Make sure no local edits would block the fast-forward.
git status
#  Expect: working tree clean. If not, stash or commit before pulling.

# Pull the new commits. --ff-only refuses merges, which is what we want
# on a production checkout.
git pull --ff-only origin main

# Confirm HEAD is now 5424f91f.
git log -1 --oneline
#  Expect: 5424f91f Batch S20: live log fixes ...

# Fix ownership in case git wrote files as a different user (the compose
# build context reads from /opt/smsly-hosting; permission errors here
# show up as "no such file or directory" during docker build).
chown -R root:root /opt/smsly-hosting/backend /opt/smsly-hosting/frontend
find /opt/smsly-hosting/backend -type f -name "*.sh" -exec chmod +x {} \;
```

---

## 4. Generate env vars (`CADDY_ASK_SECRET`)

Without `CADDY_ASK_SECRET`, `backend/config/settings.py:563-570` generates a
random hex secret on every restart, logs a warning, and Caddy's `ask` endpoint
calls fail authentication after each restart. We want a persistent value.

```bash
cd /opt/smsly-hosting

# Show whether the key is present and empty / present and set / missing.
grep -E '^CADDY_ASK_SECRET=' .env || echo "(missing)"

# Idempotent set: only writes a new value if the var is missing or empty.
# 32 bytes hex = 64 chars, same format as GATEWAY_SECRET / GITHUB_WEBHOOK_SECRET.
if ! grep -qE '^CADDY_ASK_SECRET=.+' .env; then
  NEW_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  # Strip any empty line first, then append.
  sed -i '/^CADDY_ASK_SECRET=$/d' .env
  printf 'CADDY_ASK_SECRET=%s\n' "$NEW_SECRET" >> .env
  echo "CADDY_ASK_SECRET added (length: ${#NEW_SECRET})."
else
  echo "CADDY_ASK_SECRET already set — leaving as is."
fi

# Re-tighten perms (sed -i can recreate the file with default umask).
chmod 600 .env

# Confirm the new line is in place (shows the key only, not the value).
grep -E '^CADDY_ASK_SECRET=' .env | sed 's/=.*/=<REDACTED>/'
#  Expect: CADDY_ASK_SECRET=<REDACTED>
```

> **Note.** The current `caddy-config/Caddyfile` does not yet send the
> `X-Caddy-Secret` header itself, so restarting `caddy` is not required by
> this deploy. The backend simply needs the value to be present so it stops
> emitting the ephemeral-fallback warning and so the `check-domain/` endpoint
> has a stable secret to validate against once Caddy starts using it.

---

## 5. Rebuild containers

This builds the new images but does **not** swap traffic over yet. Doing
build and restart as separate steps lets you abort if the build fails.

```bash
cd /opt/smsly-hosting

# Build the backend image (all 5 backend-flavoured services share it).
# --pull refreshes the python base image so we don't ship known CVEs.
docker compose -f docker-compose.prod.yml build --pull backend celery celery-beat celery-fast celery-deploy

# Build the frontend image (U4 — XtermConsole subprotocol fix lives here).
docker compose -f docker-compose.prod.yml build --pull frontend

# Sanity-check that the new images were created in the last few minutes.
docker images --format 'table {{.Repository}}\t{{.Tag}}\t{{.CreatedSince}}\t{{.Size}}' \
  | grep -E "smsly|backend|celery|frontend" | head -20
```

If a build fails, fix the underlying issue and re-run. Containers keep
running on the old image — no production impact until step 6.

---

## 6. Restart with new image

We restart in order: workers first (drain the queue), then `backend` (last,
because `update_config.order: start-first` in compose.prod.yml runs a new
backend alongside the old one and only kills the old one after the new one
passes its healthcheck).

```bash
cd /opt/smsly-hosting

# Workers first — they will finish in-flight tasks before exit.
docker compose -f docker-compose.prod.yml up -d \
  --no-deps celery celery-beat celery-fast celery-deploy

# Frontend next — quick swap, no draining needed.
docker compose -f docker-compose.prod.yml up -d --no-deps frontend

# Backend last — start-first ensures zero-downtime swap.
docker compose -f docker-compose.prod.yml up -d --no-deps backend

# Watch the backend come up. Wait for "Listening on" / gunicorn boot.
docker compose -f docker-compose.prod.yml logs -f backend --since=2m
#  Ctrl+C once you see the gunicorn worker startup lines and no tracebacks.

# Confirm all targeted services are now running on a fresh image.
docker compose -f docker-compose.prod.yml ps --format 'table {{.Service}}\t{{.Image}}\t{{.Status}}' \
  | grep -E "backend|celery|frontend"
#  Status column should be "Up X seconds (healthy)" — wait for healthy.
```

---

## 7. Verify each fix

Run these one at a time. Replace `https://grid.smsly.cloud` with the actual
public hostname if different.

### U1 — `/api/v1/ecosystem/cached-scan/` no longer 500

```bash
# Unauthenticated request — pre-fix returned 500 (AttributeError in the
# alias view), post-fix returns 401/403 (auth required, view loaded cleanly).
curl -s -o /dev/null -w "%{http_code}\n" \
  https://grid.smsly.cloud/api/v1/ecosystem/cached-scan/
#  Expect: 401 or 403 (NOT 500)
```

### U3 — `/api/v1/observability/...` no longer 404

```bash
# Loki proxy
curl -s -o /dev/null -w "loki=%{http_code}\n" \
  https://grid.smsly.cloud/api/v1/observability/loki/query/

# Grafana embed (slug "overview" is fine; any value triggers the route).
curl -s -o /dev/null -w "grafana=%{http_code}\n" \
  https://grid.smsly.cloud/api/v1/observability/grafana/embed/overview/

#  Expect: loki=401 (or 200), grafana=401 (or 200/302) — NOT 404.
```

### U4 — WebSocket `/ws/terminal/` no longer 403

The terminal needs a real session token (lazy-fetched from
`POST /auth/session-token`). Easiest manual check: open a service detail
page in the browser, click the Console tab, and confirm a prompt appears
within 1-2s. Programmatic check via the VPS:

```bash
# Install wscat if missing (one-time).
docker run --rm node:20-alpine npm i -g wscat >/dev/null 2>&1 || true

# Without a token, the connection MUST close immediately (this is correct
# behaviour — proves the auth path is being enforced, not crashing).
docker run --rm --network smsly-net node:20-alpine sh -c \
  "npm i -g wscat >/dev/null && wscat -c wss://grid.smsly.cloud/ws/terminal/svc-1/ --no-color" \
  || echo "Closed as expected (no auth)."

# Positive check via the backend logs — after a real browser session opens
# the terminal, you should see:
docker compose -f docker-compose.prod.yml logs backend --since=5m 2>&1 \
  | grep -E "ws/terminal.*(connected|accepted)"
#  Expect: at least one "connected"/"accepted" line per browser session.
```

### U5 — `/ws/service-status/` reconnect spam stops

```bash
# Wait 60s after the restart for things to settle, then count again.
sleep 60
docker compose -f docker-compose.prod.yml logs backend --since=60s 2>&1 \
  | grep -c "service-status"
#  Pre-fix: ~12 (one connection attempt every 5s per open browser tab).
#  Post-fix: 0-2 (the cookie middleware now authenticates the WS scope,
#  so the connection stays open instead of looping).

# Belt and braces — actually open a long WS connection and confirm it
# stays alive for >10s (pre-fix it was hard-closed by the auth check).
docker run --rm --network smsly-net node:20-alpine sh -c "
  npm i -g wscat >/dev/null
  timeout 15 wscat -c wss://grid.smsly.cloud/ws/service-status/ --no-color 2>&1 \
    | tee /tmp/wsout
  grep -q 'Disconnected' /tmp/wsout && echo 'FAIL: closed early' || echo 'PASS: stayed open ≥15s'
" || true
```

### U6 — `docker stats` ENOENT gone from autoscaler

```bash
# Old behaviour: every metric poll (~30s) logged "[Errno 2] No such file
# or directory: 'docker'" because docker CLI isn't in the celery image.
# New code calls the daemon over HTTP via apps.cloud.docker_client +
# DOCKER_HOST=tcp://socket-proxy:2375, so no binary is required.

# Wait at least one poll interval after restart.
sleep 60

# Look for the legacy error string in the celery worker that owns the
# autoscaler (container_metrics.py).
docker compose -f docker-compose.prod.yml logs celery celery-deploy --since=2m 2>&1 \
  | grep -iE "docker stats|ENOENT.*docker|No such file.*docker" \
  | head -5
#  Expect: NO output. (Empty grep = pass.)

# Positive confirmation — autoscaler should now log a numeric snapshot.
docker compose -f docker-compose.prod.yml logs celery --since=2m 2>&1 \
  | grep -iE "container.metrics|autoscale.*cpu|stats collected" \
  | head -5
#  Expect: at least one line with a numeric value.
```

### U7 — `CADDY_ASK_SECRET` persists across restarts

```bash
# 1. Inside the running container, the var should be the value you wrote
#    to .env in step 4 — not a random UUID.
docker compose -f docker-compose.prod.yml exec backend \
  sh -c 'echo "len=$(printf %s "$CADDY_ASK_SECRET" | wc -c)"'
#  Expect: len=64

# 2. The ephemeral-fallback warning must NOT appear in the post-restart logs.
docker compose -f docker-compose.prod.yml logs backend --since=5m 2>&1 \
  | grep -c "CADDY_ASK_SECRET is not set"
#  Expect: 0

# 3. Restart-survival check (most important):
#    Restart backend once more and confirm the var hash is unchanged.
HASH_BEFORE=$(docker compose -f docker-compose.prod.yml exec -T backend \
  sh -c 'printf %s "$CADDY_ASK_SECRET" | sha256sum | cut -d" " -f1')
docker compose -f docker-compose.prod.yml restart backend
sleep 15
HASH_AFTER=$(docker compose -f docker-compose.prod.yml exec -T backend \
  sh -c 'printf %s "$CADDY_ASK_SECRET" | sha256sum | cut -d" " -f1')
[ "$HASH_BEFORE" = "$HASH_AFTER" ] && echo "PASS: secret stable" || echo "FAIL: secret changed"
```

### U8 — `/api/v1/backup-download/` and `/api/v1/backups/<uuid>/download/`

```bash
# The fix was in the URL builder — confirm the API now emits a correctly
# routed download URL for each backup.
curl -s https://grid.smsly.cloud/api/v1/backups/ \
  -H "Cookie: __Host-auth_token=$BROWSER_AUTH_TOKEN" \
  | python3 -m json.tool | grep -E '"download_url"|"url"' | head -5
#  Expect: URLs that end in /api/v1/backups/<uuid>/download/, NOT a path
#  that has the literal string "download" jammed in via concat.

# Then hit one to confirm it doesn't 404.
BACKUP_ID=$(curl -s https://grid.smsly.cloud/api/v1/backups/ \
  -H "Cookie: __Host-auth_token=$BROWSER_AUTH_TOKEN" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["results"][0]["id"])')
curl -s -o /dev/null -w "%{http_code}\n" \
  -H "Cookie: __Host-auth_token=$BROWSER_AUTH_TOKEN" \
  "https://grid.smsly.cloud/api/v1/backups/$BACKUP_ID/download/"
#  Expect: 200 (file downloads) or 302 (signed-URL redirect). NOT 404.
```

### U9 — No RuntimeWarning on DB access during app init

```bash
# AppConfig.ready() no longer queries Service.objects.filter — work moved
# to a connection_created signal handler that disconnects after first fire.
docker compose -f docker-compose.prod.yml logs backend --since=5m 2>&1 \
  | grep -iE "RuntimeWarning.*(apps populated|database access)" \
  | head -5
#  Expect: NO output.
```

### U10a — No `UnorderedObjectListWarning`

```bash
# Volume + CronJob list endpoints now add .order_by("id") in get_queryset.
docker compose -f docker-compose.prod.yml logs backend --since=5m 2>&1 \
  | grep -c "UnorderedObjectListWarning"
#  Expect: 0

# Optional positive check — list endpoints should still return paginated data.
curl -s -o /dev/null -w "volumes=%{http_code}\n" \
  https://grid.smsly.cloud/api/v1/volumes/
curl -s -o /dev/null -w "cronjobs=%{http_code}\n" \
  https://grid.smsly.cloud/api/v1/cronjobs/
#  Expect: both 200 or 401 (auth-required is fine — proves the view ran).
```

### U10b — `[patch]` startup logs quiet

```bash
# Only the "Added ... to ALLOWED_HOSTS" line should remain at INFO level;
# everything else was downgraded to DEBUG.
docker compose -f docker-compose.prod.yml logs backend --since=5m 2>&1 \
  | grep -E "\[patch\]" \
  | head -10
#  Expect: at most one or two lines, NOT the 5-10 verbose ones from before.
```

---

## 8. Rollback

If anything above fails and you need to revert quickly:

```bash
cd /opt/smsly-hosting

# 1. Move HEAD back to the previous good commit (Batch S19).
git log -2 --oneline
#  Expect: 5424f91f (HEAD) and 7d595235 (HEAD~1)
git reset --hard 7d595235

# 2. Optional: roll the .env entry back IF the new CADDY_ASK_SECRET caused
#    a downstream break (unlikely, since adding a value can't break anything
#    that wasn't already broken). Leave it set under all other circumstances.
#    sed -i '/^CADDY_ASK_SECRET=/d' .env

# 3. Rebuild the OLD code (same images, but built from the reverted tree).
docker compose -f docker-compose.prod.yml build --pull \
  backend celery celery-beat celery-fast celery-deploy frontend

# 4. Bring the OLD images back into rotation.
docker compose -f docker-compose.prod.yml up -d --no-deps \
  celery celery-beat celery-fast celery-deploy frontend backend

# 5. Confirm services are healthy on the old commit.
docker compose -f docker-compose.prod.yml ps --format 'table {{.Service}}\t{{.Status}}'
git log -1 --oneline
#  Expect: 7d595235 Batch S19 ...

# 6. Open a ticket capturing the failure mode + the verify-step that
#    regressed so the fix can be re-cut on the next deploy.
```

### When NOT to roll back

- **A single curl returning 401 instead of 200** is not a regression — the
  pre-fix behaviour was 500/404, and 401 means "auth required" which is the
  correct response for an unauthenticated request. Re-run the same curl with
  a session cookie before deciding to roll back.
- **One celery worker still on the old image after `up -d`** — give it 60s
  to drain in-flight tasks before re-running `up -d` on that service alone.

---

## Appendix — single-shot deploy (for the next batch)

Once you've walked this runbook end-to-end and trust the pattern, the
typical deploy reduces to:

```bash
cd /opt/smsly-hosting && \
git pull --ff-only origin main && \
docker compose -f docker-compose.prod.yml build --pull \
  backend celery celery-beat celery-fast celery-deploy frontend && \
docker compose -f docker-compose.prod.yml up -d \
  celery celery-beat celery-fast celery-deploy frontend backend && \
docker compose -f docker-compose.prod.yml ps
```

But always run the verify section for any batch that touches the bug surface
in the table at the top of this document.
