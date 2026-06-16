# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased] - 2026-06

### Security
- Untrack committed secrets (`certs/registry.key`, `auth/htpasswd`, caddy-config runtime files) and rotate
- Kill SSH MITM backdoor in `install.sh` (was patching `paramiko.AutoAddPolicy` onto production Python)
- Replace `.secrets.tmp` plaintext secrets with in-memory process substitution
- Default `SMSLY_STRICT_SSH_HOST_KEY_CHECK` to `true` (was `false`)
- Move auth from `localStorage` to HttpOnly cookies; remove dev-fallback auth provider
- Add `USER smsly` to monolithic `Dockerfile`; drop `cap_add: NET_ADMIN` from backend; drop `privileged: true` from cAdvisor
- Remove Traefik `--api.insecure=true`; disable Grafana anonymous; pin all `:latest` images
- Bind all internal service ports to `127.0.0.1`
- Add Helm chart `securityContext`, NetworkPolicy, PDB, per-component ServiceAccount
- Add `tls_verify.py` centralised policy; replace 18+ scattered `verify=False` calls
- Add Caddy HSTS, Permissions-Policy, on_demand_tls allowlist
- Pin all GitHub Actions to commit SHAs; add `permissions:` block; add `pip-audit`, `bandit`, `gitleaks`, `npm audit` to CI
- Add `SECURITY.md`, `CODEOWNERS`, `dependabot.yml`, `.pre-commit-config.yaml`
- Add `pytest-cov` + custom markers; remove `--exit-zero` from pylint; add `tsc --noEmit` to CI

### Architecture
- Document the three reverse-proxy configs (Caddy / Traefik / nginx legacy) in `docs/REVERSE_PROXY_DECISION.md`
- Mark `nginx.conf` as LEGACY
- Document CLI unification decision (Node CLI wins)
- Move dead stubs (`custom-addons/`, `rust_twin/`, `console/`, Click CLI) to `archive/`
- Add `docs/REFACTOR_PLAN_VIEWS_TASKS.md` for splitting the 5,827-line `views.py` and 5,400-line `tasks.py` god files

### Documentation
- Stale-claim sweep across `CONTRIBUTING.md`, `README.md`, `docs/DEVELOPER_GUIDE.md`, `docs/FRONTEND_GUIDE.md`, `docs/setup/PRODUCTION_DEPLOYMENT.md`, `docs/setup/RUNBOOK.md`, `docs/setup/INSTALL_GUIDE.md`, `docs/multi-server.md`, `docs/100_PERCENT_READINESS_PLAN.md`, `docs/SECURITY_AUDIT.md`, `docs/KUBERNETES.md`: corrected the dev guide's Python baseline to match the Dockerfile (3.11) and `.devcontainer`, replaced `python manage.py test` with the actual pytest invocation, softened the pylint threshold wording, fixed the Tailwind major-version mismatch (the package manifest pins v3), updated the Caddy image reference, removed the dual-`-f` compose misuse in the prod build section, retired the legacy nginx-bridge port references in firewall, health-check, and inter-server examples, and updated legacy "Grid" brand references where the doc referred to the active product.

## [Unreleased] - 2026-05
- Add versioning section to ecosystem deployment documentation.
- Link automation scripts in deployment guides.
- Include a high‑level Mermaid diagram of the deployment flow.
- Quarantine four dead-code stubs to `archive/` (2026-06 cleanup):
  - `custom-addons/`, `rust_twin/`, `console/`, and the legacy `cli/smsly.py`
    (Click-based) are no longer built, tested, or deployed. They remain
    tracked in git under `archive/<name>-2026-06/` for historical reference.
    See `archive/DEAD_CODE_QUARANTINE.md` for the full manifest. A follow-up
    is required to retire the `RUST_TWIN_MODE` branch in `install.sh`,
    the `pytest.ini` testpath entry, and the `.github/workflows/rust-ci.yml`
    workflow.

## [0.1.0] - 2026-05-08
- Initial release of the SMSLY hosting repository.
