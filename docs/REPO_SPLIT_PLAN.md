# Repository Split Plan (No-Delete, Zero-Downtime)

This plan separates apps into clean repos while keeping the platform stable and avoiding destructive moves.

## Guardrails

- Do not delete existing repos or branches.
- Do not rewrite git history.
- Keep existing deployment paths working until cutover is confirmed.
- Migrate by copy + verify + switch traffic, not by remove/replace.

## Target Repo Layout

1. `smsly-hosting-platform`
- Cloud control plane (current `smsly-hosting` backend/frontend).
- Domain, SSL, deploy orchestration, health checks, addons, topology/canvas UI.

2. `buyforfront-web`
- Next.js frontend only.
- Marketing + app routes, no infrastructure orchestration logic.

3. `buyforfront-api`
- Django/FastAPI backend only.
- Auth, catalog, checkout, order, webhook flows.

4. `ucoin-web`
- Next.js landing + web app frontend.

5. `ucoin-api`
- Backend API and data layer for Ucoin.

6. `shared-devops`
- Reusable CI/CD templates, Docker build actions, release scripts, IaC modules.

## Runtime Model

For each product app (`buyforfront`, `ucoin`):

1. `web` container
2. `api` container
3. data addons as needed (`postgres`, `redis`, `elasticsearch`)

Routing rules:

1. `/` -> web container
2. `/api` -> api container
3. websocket paths (`/ws`) -> api container

This removes redirect loops caused by mixed web/api root handling.

## Migration Sequence

1. Baseline freeze
- Tag current working revisions in monorepo.
- Export env variables and secrets inventory.

2. Create new repos
- Initialize each target repo.
- Copy source (do not move) from monorepo paths.
- Add README, env example, and CI entrypoint.

3. Wire CI/CD
- Build on PR.
- Build + push image on main.
- Add smoke test per repo (`/health`, `/`, `/api/health`).

4. Add platform service templates
- In `smsly-hosting-platform`, create templates for:
  - `buyforfront-web`
  - `buyforfront-api`
  - `ucoin-web`
  - `ucoin-api`
- Each template must set route prefix and health endpoint explicitly.

5. Dual-run rollout
- Deploy new split services on subdomains first.
- Validate auth, cookies, CORS, websocket, and API routing.
- Keep old deployment live during validation.

6. Cutover
- Point production domains to new services.
- Verify SSL issuance and renew logs.
- Keep rollback target for at least 7 days.

## Required Checks Before Cutover

1. `web` root returns 200/301 expected response (not 404).
2. `/api/health` returns 200.
3. No redirect loops on `/`, `/en`, `/api`, and login callback paths.
4. Websocket terminal path upgrades successfully (101).
5. Custom domain appears in service `custom_domains` and Caddy config.
6. Certificate status is valid for apex and `www`.

## Buyforfront-Specific Notes

1. Ensure frontend API base URL never points to its own `/api` unless reverse proxy explicitly routes to backend.
2. Keep `web` and `api` as separate deployable services.
3. Define cookie domain and secure flags per environment.

## Ucoin-Specific Notes

1. Keep landing pages in `ucoin-web` and API logic in `ucoin-api`.
2. Add explicit fallback route for landing (`/`) to avoid blank or missing homepage.
3. Validate product/cart API calls against the API host, not frontend host-relative looped routes.

## Rollback Strategy

1. Do not remove old service definitions.
2. Keep old domains and routes disabled but ready.
3. Rollback = restore previous route mapping + restart proxy, no image rebuild required.
