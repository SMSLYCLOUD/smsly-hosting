# Kubernetes Deployment Guide

> [!NOTE]
> **Version**: 1.2.0
> **Last Updated**: 2026-11-25
> **Changelog**:
> - v1.2.0: Added StatefulSets for PostgreSQL, Redis, and RabbitMQ; `collectstatic` initContainer; configurable `persistence.accessMode`; writable `/home/smsly` & `/app/backups` `emptyDir`s; `Recreate` strategy for `celery-beat`; worker `terminationGracePeriodSeconds: 360`; tightened `NetworkPolicy` (scoped ingress to backend/frontend, RFC1918+CGNAT egress exceptions); hardened Prometheus probes/resources; fixed `existingSecret` & `redis.auth.enabled=false` chart validators.
> - v1.1.0: Documented the 2026-06 chart security additions (`securityContext`, `NetworkPolicy`, `PodDisruptionBudget`, per-component `ServiceAccount`, change-me sentinels).
> - v1.0.1: Linked actual Helm chart directory.
> - v1.0.0: Initial K8s orchestration guide.

SMSLY (Grid) can orchestrate deployments onto existing Kubernetes clusters. "Grid" is a legacy code name for the same product.

## Prerequisites
1. A running Kubernetes cluster (K3s, GKE, EKS, AKS).
2. A `KUBECONFIG` file or a Service Account Token with namespace admin privileges.

## Helm Chart
The SMSLY control plane can be installed directly into your K8s cluster using our Helm chart.
You can find the charts in [charts/smsly-hosting/](../charts/smsly-hosting/).

## Architecture
When deploying services to Kubernetes, SMSLY uses the `kubernetes` Python client to generate Deployments, Services, and Ingresses dynamically.
Each service gets its own Namespace or shares a project Namespace.

## Chart Features (2026-06 update)

The W2 hardening pass added the following defaults to `charts/smsly-hosting/`. Every workload that consumes the chart picks them up by default; you do not need to opt in.

### `securityContext` (file: `templates/_securitycontext.tpl`)

Pod- and container-level defaults baked into the chart:

- `runAsNonRoot: true`, `runAsUser: 1000`, `runAsGroup: 1000`, `fsGroup: 1000`
- `seccompProfile.type: RuntimeDefault`
- Container: `allowPrivilegeEscalation: false`, `readOnlyRootFilesystem: true`, `capabilities.drop: [ALL]`

Includes:

```yaml
spec:
  securityContext:
    {{- include "smsly.podSecurityContext" . | nindent 8 }}
  containers:
    - name: foo
      securityContext:
        {{- include "smsly.containerSecurityContext" . | nindent 8 }}
```

### `NetworkPolicy` (file: `templates/networkpolicy.yaml`)

`networkPolicy.enabled: true` by default in `values.yaml`. Renders:

- **Default-deny** for both Ingress and Egress (`<release>-default-deny`).
- **Allow intra-namespace** traffic for the release namespace, plus DNS egress to any namespace (UDP/TCP 53).
- **Scoped Ingress controller access**: `ingress-nginx` and `traefik` namespaces may reach only `backend:8000` and `frontend:3000`.
- **Hardened Egress**: public HTTPS (`443`) egress excludes RFC1918 (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), CGNAT (`100.64.0.0/10`), and link-local/IMDS (`169.254.0.0/16`).

### Stateful Backing Services (PostgreSQL, Redis, RabbitMQ)

`templates/postgresql.yaml`, `templates/redis.yaml`, and `templates/rabbitmq.yaml` provision non-root `StatefulSet` + `Service` resources gated on `.Values.postgresql.enabled`, `.Values.redis.enabled`, and `.Values.rabbitmq.enabled`. Use `persistence.accessMode` (default `ReadWriteOnce`) to adapt PVCs to your StorageClass (`ReadWriteMany` when scaling `backend` across nodes with shared media).

### Backend & Celery Runtime Wiring

- `backend-deployment.yaml` runs `wait-for-db`, `wait-for-redis`, `migrate`, and `collectstatic` initContainers (populating the `/app/staticfiles` `emptyDir`) and mounts writable `emptyDir`s for `/home/smsly` and `/app/backups` under `readOnlyRootFilesystem: true`.
- `REDIS_PASSWORD`, `GATEWAY_SECRET`, `EDGE_JWT_SECRET`, and `CADDY_ASK_SECRET` are wired into both `secret-env.yaml` and the backend/migration pods.
- `celery-beat` uses `strategy.type: Recreate` (preventing duplicate beat schedulers during rollouts), and Celery workers set `terminationGracePeriodSeconds: 360`.

### Change-me sentinels (file: `templates/_validators.tpl`)

`include "smsly.validateValues"` is called at the top of every workload template. It `fail`s the render if any of these are still placeholder values:

- `secrets.secretKey`, `secrets.dbPassword`, `secrets.fieldEncryptionKey`, `secrets.githubWebhookSecret` (when `secrets.existingSecret` is empty) — refuses `change-me`, `change-me-in-prod`, `latest`, `""`.
- `secrets.redisPassword` — required when `redis.auth.enabled: true` (and `secrets.existingSecret` is empty); `redis.auth.enabled: false` and `secrets.existingSecret` both validate cleanly.
- `backend.image.tag`, `frontend.image.tag`, `celery.image.tag`, `postgresql.image.tag`, `redis.image` — refuses `latest` and `""`.

The result: a production install cannot be booted with a placeholder secret or a `:latest` image tag; the chart will refuse to render.

## Other chart features

- `networkPolicy`, `pdb`, `serviceAccount` are all toggled per-component in `values.yaml`.
- `ingress.className` defaults to `traefik`; TLS is terminated via cert-manager (annotation `cert-manager.io/cluster-issuer`).
- `observability`, `socketProxy`, `registry`, `caddy`, `backup` are all off by default — opt in per environment.
- `nginx.enabled: false` is the default (k8s nginx kept only for backward compatibility; see `docs/REVERSE_PROXY_DECISION.md`).
