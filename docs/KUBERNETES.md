# Kubernetes Deployment Guide

> [!NOTE]
> **Version**: 1.1.0
> **Last Updated**: 2026-06-16
> **Changelog**:
> - v1.1.0: Documented the 2026-06 chart security additions (`securityContext`, `NetworkPolicy`, `PodDisruptionBudget`, per-component `ServiceAccount`, change-me sentinels).
> - v1.0.1: Linked actual Helm chart directory.
> - v1.0.0: Initial K8s orchestration guide.

SMSLY (Grid) can orchestrate deployments onto existing Kubernetes clusters. "Grid" is a legacy code name for the same product.

## Prerequisites
1. A running Kubernetes cluster (K3s, GKE, EKS, AKS).
2. A `KUBECONFIG` file or a Service Account Token with namespace admin privileges.

## Helm Chart
The SMSLY control plane can be installed directly into your K8s cluster using our Helm chart.
You can find the charts in [charts/smsly-hosting/](file:///c:/Users/osaretin/Documents/SMSLY/SMSLY_CORE/smsly-hosting/charts/smsly-hosting/).

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

External ingress is granted by the `Ingress` resource, not the `NetworkPolicy`.

### `PodDisruptionBudget` (file: `templates/pdb.yaml`)

`pdb.<component>.enabled: true` by default for `backend`, `frontend`, and `celery`, with `minAvailable: 1`. The chart loops over `.Values.pdb` so adding a new component is a one-line `values.yaml` change.

### Per-component `ServiceAccount` (file: `templates/serviceaccount.yaml`)

Renders one `ServiceAccount` per component (currently `backend`, `frontend`, `celery`) with `automountServiceAccountToken: false`. Workloads that genuinely need the API get their own per-component token mounted explicitly.

### Change-me sentinels (file: `templates/_validators.tpl`)

`include "smsly.validateValues"` is called at the top of every workload template. It `fail`s the render if any of these are still placeholder values:

- `secrets.secretKey`, `secrets.dbPassword`, `secrets.redisPassword` — refuses `change-me`, `change-me-in-prod`, `latest`, `""`.
- `backend.image.tag`, `frontend.image.tag` — refuses `latest` and `""`.

The result: a production install cannot be booted with a placeholder secret or a `:latest` image tag; the chart will refuse to render.

## Other chart features

- `networkPolicy`, `pdb`, `serviceAccount` are all toggled per-component in `values.yaml`.
- `ingress.className` defaults to `traefik`; TLS is terminated via cert-manager (annotation `cert-manager.io/cluster-issuer`).
- `observability`, `socketProxy`, `registry`, `caddy`, `backup` are all off by default — opt in per environment.
- `nginx.enabled: false` is the default (k8s nginx kept only for backward compatibility; see `docs/REVERSE_PROXY_DECISION.md`).
