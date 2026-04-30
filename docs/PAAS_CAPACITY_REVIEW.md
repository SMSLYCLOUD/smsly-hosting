# Cloud SMSLY PaaS Capacity Review (Codebase Audit)

Date: 2026-04-30  
Scope: `frontend/`, root docs/config, and repository structure for deployment/runtime signals.

## CONFIRMED_WORKING

| Feature | Evidence | Backend/task evidence | Risk | Landing-safe wording |
|---|---|---|---|---|
| Public + protected routing split | `frontend/src/middleware.ts` protects dashboard/service paths while leaving `/` public | Auth cookie checks and redirects implemented | Low | "Public landing + authenticated dashboard routes are separated." |
| Dashboard/service surfaces exist | Routes: `frontend/src/app/dashboard/page.tsx`, `frontend/src/app/services/page.tsx`, `frontend/src/app/services/[id]/page.tsx`, `frontend/src/app/deployments/page.tsx` | UI is wired to authenticated app shell and API client patterns | Medium | "Dashboard workflows for services and deployments are available." |
| Settings tabs for domains, env vars, files, logs, scaling, backups | `frontend/src/components/settings/*Tab.tsx`, `frontend/src/components/logs/LogsTab.tsx` | Tab-level components and pages exist | Medium | "Service operations are managed from settings and logs panels." |
| Addons + storage surfaces | `frontend/src/components/addons/AddonsTab.tsx`, `frontend/src/components/storage/StorageTab.tsx` | UI components and pages present | Medium | "Addons and storage controls are available in the dashboard." |
| Docker-compose self-hosted install paths | Root `README.md` install + manual compose commands; `docker-compose.socket-proxy.yml` hardening option | Installer + compose workflows documented | Medium | "Self-host with Docker on your VPS using install script or compose." |

## PARTIAL_OR_EXPERIMENTAL

| Feature | Evidence | Backend/task evidence | Risk | Landing-safe wording |
|---|---|---|---|---|
| Autoscaler | `frontend/src/app/autoscaler/page.tsx`, `frontend/src/components/settings/ScalingTab.tsx`, `docs/AUTOSCALING.md` | Feature appears in UI/docs but production tuning varies by workload | High | "Autoscaling controls are available with workload-specific tuning." |
| Topology and visual infrastructure maps | `frontend/src/app/topology/page.tsx`, topology components | Strong UI layer; runtime fidelity depends on data sources | Medium | "Topology views help visualize service relationships." |
| Replication / mesh / tunnel workflows | Routes: `/replication`, `/tunnels`, `/network`; docs `docs/VPN_MESH.md`, `docs/TOPOLOGY.md` | Surfaces exist; deployment-level reliability depends on environment | High | "Advanced network and replication workflows are in active evolution." |
| Billing and reseller views | `frontend/src/app/billing/page.tsx`, `frontend/src/app/reseller/page.tsx` | UI present; provider integration varies by configuration | High | "Billing and partner surfaces are available where configured." |

## UI_ONLY_OR_PLACEHOLDER

| Feature | Evidence | Backend/task evidence | Risk | Landing-safe wording |
|---|---|---|---|---|
| Marketing claims in prior landing (SLA/50K deployments/etc.) | Previous `frontend/src/app/page.tsx` static arrays | No direct repository verification for those numbers | High | "Do not use unverifiable metrics on the landing page." |

## BROKEN_OR_NEEDS_FIX

| Feature | Evidence | Backend/task evidence | Risk | Landing-safe wording |
|---|---|---|---|---|
| Public landing visual identity leaked from dashboard | Root layout globally mounted `SpaceOpsBackground` in `frontend/src/app/layout.tsx` | Applied starfield to all routes including `/` | Medium | "Landing now uses dedicated storm background; dashboard visual layer remains intact." |
| Missing explicit frontend GitHub URL config | `frontend/.env.example` lacked `NEXT_PUBLIC_GITHUB_URL` | CTA configurability absent | Low | "GitHub CTA is now environment-configurable." |
| Missing dedicated `/download` route | No `frontend/src/app/download/page.tsx` before this change | Install CTA not route-backed | Medium | "Download/install route now exists with verified commands." |

## SHOULD_NOT_BE_CLAIMED_ON_LANDING_PAGE

- Guaranteed SLA, build-time, and deployment-volume numbers without telemetry proof.
- Absolute "zero downtime" guarantees for every deployment path.
- Full production-readiness claims for all autoscaling/mesh/replication modes.
- "One-click everything" messaging for workflows that require infra prerequisites.

## Notes for landing copy safety

- Safe core statement: **free, open-source, self-hosted PaaS on your own VPS using Docker workflows**.
- Safe capability statement: **service deploy/manage, domain/env/log/files/settings surfaces exist in dashboard**.
- Transparency statement: **advanced autoscaling/network/replication capabilities are evolving and environment-dependent**.
