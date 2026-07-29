# Grid Helm Chart Strategy

Once your k3s infrastructure is provisioned via Terraform, the next step is deploying Grid itself (the control plane) into the cluster. The industry standard for packaging complex multi-container K8s applications is **Helm**.

This document outlines how to structure a Helm chart (`charts/grid`) to replicate and enhance the legacy `docker-compose.prod.yml` setup.

## 1. Directory Structure

A standard Helm chart structure for Grid should look like this:

```text
charts/grid/
├── Chart.yaml             # Chart metadata (name, version, appVersion)
├── values.yaml            # Default configuration values (replicas, env vars, image tags)
├── values.prod.yaml       # Environment-specific overrides (Production)
├── templates/             # Kubernetes YAML templates using Go templating
│   ├── _helpers.tpl       # Reusable template functions (labels, names)
│   ├── deployment-backend.yaml   # Django API & Gunicorn
│   ├── deployment-frontend.yaml  # Next.js Server
│   ├── deployment-celery.yaml    # Celery Workers & Beat
│   ├── statefulset-postgres.yaml # Self-managed Postgres (or use CloudNativePG subchart)
│   ├── statefulset-redis.yaml    # Self-managed Redis (or use bitnami/redis subchart)
│   ├── service-backend.yaml      # ClusterIP for Backend
│   ├── service-frontend.yaml     # ClusterIP for Frontend
│   ├── ingress.yaml              # Traefik IngressRoute / standard Ingress mapping domains
│   ├── secret-env.yaml           # Opaque Secret for .env vars (DB passwords, Fernet keys)
│   ├── configmap-nginx.yaml      # Nginx routing config (if still needed, though Traefik can replace it)
│   └── cronjob-backups.yaml      # Automated DB/Volume backups
└── charts/                # Subcharts (e.g., bitnami/postgresql, bitnami/redis)
```

## 2. Key Differences from Docker Compose

When migrating from `docker-compose.prod.yml` to Helm, several architectural shifts must occur:

### A. Statelessness & Volumes
- **Compose:** Used local bind mounts (`./:/platform-src`, `/opt/smsly-cache`).
- **Kubernetes:** Pods are ephemeral and can be scheduled on *any* node. You cannot use host-path bind mounts if you expect High Availability.
  - **Solution:** Use **PersistentVolumeClaims (PVCs)** backed by AWS EBS (`gp3` storage class) for persistent data like PostgreSQL (`/var/lib/postgresql/data`) and Redis.
  - **Source Code:** The code MUST be baked into the Docker image (`COPY . /app` in the Dockerfile). Do not mount `./:/platform-src` at runtime in K8s.

### B. The Docker Socket Proxy (Crucial Change)
- **Compose:** Grid used a `socket-proxy` container to talk to the host's Docker daemon to spin up user apps.
- **Kubernetes:** **You must remove the Docker socket proxy.** K3s uses `containerd`, not Docker, and giving a pod root access to the host container runtime is a severe security risk.
  - **Solution:** The Grid Backend (`pipeline.py`) must be refactored. Instead of using `docker.from_env()`, it must use the **Kubernetes Python Client** (`pip install kubernetes`). Grid will generate standard K8s `Deployment` and `Service` YAMLs for user apps and submit them to the k3s API server.
  - **Building Images:** To build user images (Nixpacks/Dockerfiles), Grid should spawn a K8s **Job** using a tool like [Kaniko](https://github.com/GoogleContainerTools/kaniko) which securely builds container images inside a pod without needing a Docker daemon.

### C. Ingress Routing (Nginx -> Traefik)
- **Compose:** Caddy directly routes `/api/*`, `/ws/*`, `/health`, `/admin`, `/static/*`, `/media/*` to the backend, and `/*` (catch-all) to the frontend, with SSL termination handled by Caddy on demand.
- **Kubernetes:** Drop Caddy and Nginx. Use the native **Traefik Ingress Controller** (which comes pre-installed in k3s).
  - **Solution:** Create an `Ingress` resource in Helm that defines the routing rules directly:
    ```yaml
    # Example ingress.yaml snippet
    apiVersion: networking.k8s.io/v1
    kind: Ingress
    metadata:
      name: {{ include "grid.fullname" . }}
      annotations:
        cert-manager.io/cluster-issuer: "letsencrypt-prod"
    spec:
      rules:
      - host: "app.your-paas.com"
        http:
          paths:
          - path: /api
            pathType: Prefix
            backend:
              service:
                name: {{ include "grid.fullname" . }}-backend
                port:
                  number: 8000
          - path: /
            pathType: Prefix
            backend:
              service:
                name: {{ include "grid.fullname" . }}-frontend
                port:
                  number: 3000
    ```

## 3. Recommended Subcharts

For stateful backing services (PostgreSQL and Redis), it is highly recommended *not* to write your own `StatefulSet` templates. Instead, use hardened community Helm charts as dependencies in your `Chart.yaml`:

```yaml
# Chart.yaml
dependencies:
  - name: postgresql
    version: "15.x.x"
    repository: "https://charts.bitnami.com/bitnami"
  - name: redis
    version: "18.x.x"
    repository: "https://charts.bitnami.com/bitnami"
```

You can then configure these dependencies directly in your `values.prod.yaml` (e.g., setting passwords, storage sizes, and enabling High Availability/Replication).

## 4. Deployment Workflow

1.  **Build Images:** Ensure your GitHub Actions build the Backend and Frontend Docker images and push them to a registry (e.g., AWS ECR or Docker Hub).
2.  **Configure Values:** Create a `values.prod.yaml` containing your production secrets (Fernet keys, Django Secret Key, etc.). Use tools like **Helm Secrets** (with SOPS and AWS KMS) to encrypt this file in Git.
3.  **Install/Upgrade:**
    ```bash
    helm upgrade --install grid ./charts/grid \
      --namespace grid-system \
      --create-namespace \
      -f values.prod.yaml
    ```
