# Security Audit Report

## 2026-06 Deep Sweep Update

The 2026-06 deep-sweep remediation cycle is in flight. The consolidated audit conversation log is the canonical record for this update; remediation is split into four PRs (W1–W4).

- **W1 — Code & config fixes**: paramiko `AutoAddPolicy` kill, `tls_verify.py` centralisation, `.secrets.tmp` removal, `localStorage`→HttpOnly cookie auth, Docker `USER smsly`, drop `cap_add: NET_ADMIN` / `privileged: true`, Traefik `--api.insecure` removal, Grafana anonymous disabled, all `:latest` tags pinned, all internal ports bound to `127.0.0.1`, dead stubs (`custom-addons/`, `rust_twin/`, `console/`, Click CLI) quarantined to `archive/`.
- **W2 — Helm & Kubernetes hardening**: `securityContext` defaults (runAsNonRoot, readOnlyRootFilesystem, drop ALL caps, seccomp RuntimeDefault), default-deny + intra-namespace `NetworkPolicy`, per-component `PodDisruptionBudget`, per-component `ServiceAccount` with `automountServiceAccountToken: false`, `change-me` / `latest` chart validators.
- **W3 — Reverse-proxy & CLI unification**: three configs documented (Caddy / Traefik / nginx-LEGACY) in `docs/REVERSE_PROXY_DECISION.md`; `nginx.conf` marked LEGACY; CLI unification decision recorded (Node CLI wins).
- **W4 — CI & supply-chain**: GitHub Actions pinned to commit SHAs with `permissions:` block; `pip-audit`, `bandit`, `gitleaks`, `npm audit` added to CI; `pytest-cov` + custom markers; `--exit-zero` removed from pylint; `tsc --noEmit` to CI; `SECURITY.md`, `CODEOWNERS`, `dependabot.yml`, `.pre-commit-config.yaml` added.

See the conversation log + CHANGELOG for the full ticket list.

## Zero Trust Hardening (2026-01-30)

This document summarizes the security audit performed on smsly-hosting.

### Critical Fixes Applied

#### 1. Settings Hardening

- `SECRET_KEY` - Fail-fast in production (no default)
- `FIELD_ENCRYPTION_KEY` - Fail-fast in production
- `DEBUG=True` - Blocked in production
- `ALLOWED_HOSTS='*'` - Blocked in production

#### 2. Ownership Filtering

All ViewSets now filter by `owner`:

- ServiceViewSet
- DeploymentViewSet
- AddonViewSet
- CronJobViewSet
- VolumeViewSet
- TopologyViewSet
- MetricsViewSet

#### 3. Authentication Enforcement

Added `IsAuthenticated` to:

- TemplateViewSet
- MetricsViewSet
- TopologyViewSet
- RepoAnalysisView
- AIChatView

#### 4. Input Validation

- RepoAnalysisView: SSRF protection (only GitHub/GitLab/Bitbucket)
- AIChatView: 2000 char message limit

#### 5. WebSocket Security

- Token authentication required
- Deployment ownership verification
- Connection rejected if auth fails

#### 6. Prometheus Metrics

- IP-based restriction (internal networks only)

### Commit

`49f320c` - "security: Zero Trust hardening for smsly-hosting"
