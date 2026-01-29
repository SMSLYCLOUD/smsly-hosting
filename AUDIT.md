# SMSly Hosting Platform - Codebase Audit

## 1. Executive Summary

SMSly Hosting is a Platform-as-a-Service (PaaS) solution designed to simplify application deployment using Docker and Kubernetes (K3s). The architecture consists of a Django-based control plane (Backend) and a Next.js-based dashboard (Frontend).

Overall, the project structure is solid and follows modern practices (DRF for API, Next.js for UI, Celery for async tasks). However, there are critical functional bugs in the frontend authentication flow and some architectural artifacts (references to "Coolify") that suggest legacy code or incomplete refactoring.

## 2. Architecture Overview

- **Backend**: Django 4.x + Django Rest Framework.
    - Uses `celery` for asynchronous tasks (deployments, alerts).
    - Uses `channels` (Daphne) likely for real-time logs (implied by `consumers.py` existence).
    - **Orchestrator Pattern**: `services/orchestrator.py` cleanly separates the deployment lifecycle (Build -> Cluster).
    - **Simulation Mode**: A clever fallback in `ClusterManager` allows the backend to run without a real K8s cluster, mocking deployments.

- **Frontend**: Next.js 14 (App Router).
    - Uses `reactflow` for a visual topology view.
    - Authentication state is managed via Cookies (middleware) and LocalStorage (API).

- **Infrastructure**:
    - Docker Compose for local dev.
    - K3s (Kubernetes) for production workloads.
    - Nginx (implied) for ingress.

## 3. Critical Findings (Bugs & Issues)

### 3.1 Frontend Authentication Broken
**Severity:** Critical 🔴
The login page successfully acquires a token and stores it in `localStorage` and `document.cookie`. However, the API client (`frontend/src/lib/api.ts`) **never attaches this token** to outgoing requests.
*   **Impact:** Users cannot perform any actions after logging in. All API calls will return 401 Unauthorized.
*   **Fix:** Add an Axios interceptor to inject the `Authorization: Token ...` header.

### 3.2 "Coolify" Artifacts
**Severity:** Low 🟡
The `Service` and `Addon` models contain fields like `coolify_uuid`.
*   **Observation:** This suggests the project might have been forked from, inspired by, or migrated from Coolify (another open-source PaaS).
*   **Recommendation:** If this is a standalone project, rename these fields to `external_id` or `container_id` to avoid confusion.

### 3.3 Security Configuration
**Severity:** Medium 🟠
- **Secret Key:** `settings.py` provides a default `SECRET_KEY` (`django-insecure-...`). While standard for dev, the production check relies solely on `DEBUG=False`. It is safer to strictly crash if `SECRET_KEY` is missing in production.
- **CORS:** `CORS_ALLOW_ALL_ORIGINS` defaults to `False` (Good), but `CORS_ALLOWED_ORIGINS` defaults to localhost. Ensure this is updated in production.

### 3.4 K8s "Simulation Mode"
**Severity:** Info 🔵
The `ClusterManager` silently falls back to simulation if K8s config is missing.
*   **Risk:** In a misconfigured production environment, the system might "pretend" to deploy successfully without actually doing anything.
*   **Recommendation:** Add a strict flag (e.g., `REQUIRE_K8S=True`) for production to prevent accidental simulation.

## 4. Code Quality & Patterns

- **Models**: `TimeStampedModel` is used consistently. Encryption is used for sensitive env vars (`EncryptedCharField`).
- **Tasks**: Celery tasks are granular (`run_deployment_task`, `provision_addon_task`).
- **Frontend**: deeply uses `use client` which is necessary for the visual canvas features. API calls are centralized in `lib/api.ts`.

## 5. Next Steps

1.  **Immediate Fix**: Patch `frontend/src/lib/api.ts` to restore functionality.
2.  **Hardening**: Update `settings.py` to enforce secure secrets in non-debug modes.
3.  **Cleanup**: Rename `coolify_uuid` in a future migration.
