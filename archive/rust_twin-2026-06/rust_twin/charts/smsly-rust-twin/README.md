# smsly-rust-twin

Helm chart for the SMSLY PaaS control plane (Rust implementation: `api` + `worker` + `cli`).

This chart deploys the Rust control plane into a Kubernetes cluster. It does not deploy PostgreSQL or Redis — wire those to existing services via the `postgres` and `redis` values.

## TL;DR

```bash
helm lint charts/smsly-rust-twin
helm upgrade --install smsly ./charts/smsly-rust-twin \
  --namespace smsly --create-namespace \
  --set image.tag=0.1.0 \
  --set auth.jwtSecret="$(openssl rand -hex 32)"
```

## Prerequisites

- Kubernetes >= 1.27
- Helm >= 3.10
- A reachable PostgreSQL 14+ instance
- A reachable Redis 6+ instance
- A container image built from `archive/rust_twin-2026-06/rust_twin/Dockerfile` (targets: `api`, `worker`)

## Configuration

The most important values in `values.yaml`:

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `image.repository` | string | `smsly/rust-twin` | Container image repository. |
| `image.tag` | string | `0.1.0` | Image tag. Pin a specific version in production. |
| `image.pullPolicy` | string | `IfNotPresent` | Image pull policy. |
| `api.replicas` | int | `2` | Number of `api` pods. |
| `api.resources` | object | `{cpu: 100m, memory: 128Mi}` | `api` pod resources. |
| `worker.replicas` | int | `1` | Number of `worker` pods. |
| `worker.resources` | object | `{cpu: 100m, memory: 128Mi}` | `worker` pod resources. |
| `service.type` | string | `ClusterIP` | Kubernetes Service type for the api. |
| `service.port` | int | `8080` | Service / container port. |
| `ingress.enabled` | bool | `false` | Whether to create an Ingress. |
| `ingress.className` | string | `nginx` | IngressClass name. |
| `ingress.hosts` | list | `[{host: chart-example.local, paths: [/]}]` | Ingress hosts. |
| `ingress.tls` | list | `[]` | Ingress TLS blocks. |
| `postgres.host` | string | `postgres` | PostgreSQL host. |
| `postgres.port` | int | `5432` | PostgreSQL port. |
| `postgres.database` | string | `smsly_rust_twin` | Database name. |
| `postgres.user` | string | `smsly_admin` | Database user. |
| `postgres.existingSecret` | string | `""` | Name of a Secret holding `POSTGRES_PASSWORD`. |
| `redis.host` | string | `redis` | Redis host. |
| `redis.port` | int | `6379` | Redis port. |
| `auth.jwtSecret` | string | `""` | JWT signing secret. **Required.** |
| `migrateJob.enabled` | bool | `true` | Run a one-shot migration Job on install/upgrade. |
| `networkPolicy.enabled` | bool | `true` | Create NetworkPolicies. |
| `podDisruptionBudget.enabled` | bool | `true` | Create a PDB for the api Deployment. |
| `serviceAccount.create` | bool | `true` | Create a ServiceAccount. |

## Install

```bash
helm upgrade --install smsly ./charts/smsly-rust-twin \
  --namespace smsly --create-namespace \
  --set image.tag="$(git -C archive/rust_twin-2026-06/rust_twin describe --tags --abbrev=0)" \
  --set auth.jwtSecret="$(openssl rand -hex 32)" \
  --set postgres.host=postgres.smsly.svc.cluster.local \
  --set postgres.existingSecret=smsly-db-credentials \
  --set postgres.existingSecretKey=password
```

## Upgrade

```bash
helm upgrade smsly ./charts/smsly-rust-twin \
  --namespace smsly \
  --reuse-values
```

`helm.sh/hook` annotations on the migrate Job will run it on every `helm upgrade`.

## Uninstall

```bash
helm uninstall smsly --namespace smsly
```

## See also

- `install.sh` in the repository root for a non-Kubernetes local install.
- `archive/rust_twin-2026-06/rust_twin/docker-compose.yml` for local development.
- `charts/smsly-hosting/` for the parent Django chart (style reference).
