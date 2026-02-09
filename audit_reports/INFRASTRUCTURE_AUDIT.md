# Infrastructure Audit Report

**Generated:** $(date)
**Focus:** `smsly-hosting` Deployment Engine

## 1. Architecture Review

### Deployment Engine (`apps/deployments`)
- **Webhooks (`github.py`):**
  - **Security:** Verified `HMAC-SHA256` signature validation using `GITHUB_WEBHOOK_SECRET`.
  - **Fail-Safe:** Properly fails closed (returns `False`) if the secret is missing or signature is invalid.
  - **Logic:** Extracts branch and commit info to match against `Service` models. Handles `repository_url` containment checks.
- **Audit Logs (`models_audit.py`):**
  - **Integrity:** Implements a hash-linked ledger. Each `AuditLog` entry calculates its hash based on content + `previous_hash`.
  - **Immutability:** `previous_hash` and `hash` fields are `editable=False`.
  - **Indexing:** Indexes on `hash` and `actor` for performance.

### Local Adapter (`apps/cloud/adapters/local.py`)
- **Docker Integration:**
  - Uses `docker-py` to manage containers.
  - Creates/uses a dedicated bridge network `smsly-net`.
  - **Traefik Support:** Automatically assigns labels for `Host`, `entrypoints` (websecure), and Let's Encrypt resolver (`myresolver`).
- **Kubernetes Integration:**
  - Auto-detects in-cluster vs local kubeconfig.
  - Deploys standard `Deployment` and `Service` (ClusterIP).
  - Handles resource limits (requests/limits).
- **Database Provisioning:**
  - Spawns Postgres containers on-demand.
  - **Security:** Generates cryptographically secure passwords (`secrets.token_urlsafe(24)`).

## 2. Security Findings
- **Positive:**
  - Webhook verification is enforced.
  - Database passwords are auto-generated and strong.
  - Containers are isolated on a specific network.
- **Recommendations:**
  - **Network Policy:** In K8s mode, explicitly define `NetworkPolicy` to restrict pod-to-pod communication by default.
  - **Secrets:** Move `POSTGRES_PASSWORD` env var injection to K8s/Docker Secrets instead of plaintext env vars where possible.

## 3. Scalability
- **Local:** Limited by single-host Docker resources.
- **Kubernetes:** Scalable via `replicas` in `_deploy_k8s`, but current logic hardcodes `replicas=1`.
  - *Recommendation:* Expose `replicas` as a configurable parameter in the `Service` model.

## 4. Next Steps
- Verify integration tests.
- Improve K8s deployment logic to support scaling and rolling updates.
