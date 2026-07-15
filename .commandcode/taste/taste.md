# Taste (Continuously Learned by [CommandCode][cmd])

[cmd]: https://commandcode.ai/

# architecture
See [architecture/taste.md](architecture/taste.md)
# integrations
- When adding a new integration method that overlaps with an existing one (e.g., GitHub App alongside OAuth), keep the existing integration intact and add the new one as a parallel alternative — do not remove or replace the existing flow. Confidence: 0.70

# infrastructure
- Registry TLS certificates are managed by Traefik — do not regenerate certs with standalone openssl; investigate Traefik cert generation if cert/key mismatch occurs. Confidence: 0.88

# env-resolver
- When manifest env resolver cannot resolve a var from any real source, fill it with a mock/placeholder value instead of leaving it empty or marking it unresolved. Confidence: 0.70

# security
- Make security scan severity threshold configurable via settings with options to enable/disable and choose threshold level (low, medium, high, critical). Confidence: 0.70
- Security tab in service Insights should show the service's own vulnerability scan data (Trivy results from the deployment model), not system-level hardening status. Confidence: 0.65

# content
- Do not fabricate usage stats (deployment counts, user numbers, trust indicators) for new/early-stage products — the product is "new and fresh" and fake numbers undermine credibility. Confidence: 0.65
- When highlighting security features on the Grid product landing page, include the advanced/production-hardening features: gVisor sandboxing, Falco runtime security, fail2ban intrusion prevention, Trivy vulnerability scanning, scoped container registry, and cosigning/signature verification. Confidence: 0.65
- When rewriting a page and losing original content, recover feature lists from git history (e.g., `git show <commit>:path/to/page.tsx`) — the old commits contain detailed feature descriptions that should be preserved or adapted. Confidence: 0.65
- Landing page and marketing copy must only describe features that actually exist in the codebase — verify backend capabilities before writing demo code samples, feature descriptions, or architecture visuals. Confidence: 0.70

# typescript
- In .tsx files, generic arrow functions need a trailing comma on the type parameter (e.g., `<T,>` not `<T>`) to prevent TSX from parsing it as a JSX tag. Confidence: 0.70

# workflow
See [workflow/taste.md](workflow/taste.md)

# shell-commands
- When providing shell commands for the user to run on the VPS, use separate single-line commands instead of long multi-line commands joined with `&&` — the user's terminal loses or mangles parts of pasted multi-line commands due to line wrapping, causing partial execution and confusing errors. Confidence: 0.70
# testing
- When fixing a Django endpoint that silently crashes (e.g., `result.save()` fails), check required model FK fields — the ORM won't validate defaults and `.save()` will raise `IntegrityError` swallowed by broad `except Exception`. Confidence: 0.65
- Service detail tabs (Insights, Monitoring, Security) must scope data to the current service only — do not fetch or display platform-wide metrics from within a service context. Confidence: 0.75

# verification
- After code changes, perform deep E2E verification: trace every path across all changed files with exact timing (T+n notation), identify race conditions, and walk through multiple scenario outcomes before/after the change. Confidence: 0.75

# frontend-navigation
- Before creating a new page, check the navbar component (and any other navigation components) for existing links to that page — the route may already exist under a different path than what glob/file-search suggests. Confidence: 0.65

# celery
- Every Docker-dependent Celery task must be explicitly routed to the `deploy` queue in `app.conf.task_routes` — the default `celery` queue worker (`celery-1`) has no DOCKER_HOST, BUILDKIT_HOST, or Docker socket proxy; silent failures result when Docker operations run on the wrong worker. Route both `tasks.py` and specialized `tasks_*.py` copies so Celery resolves correctly regardless of import path. Confidence: 0.80
- Duplicate task definitions in `tasks.py` that mirror specialized modules (`tasks_addons.py`, `tasks_platform_update.py`, `tasks_transfer.py`, etc.) are dangerous — the `tasks.py` copies lack Celery route entries, silently landing on the default queue. Replace duplicates with re-exports or stub dispatches to the authoritative module, and add route entries for both task name variants. Confidence: 0.75

# provisioner
- `provision_server` must check `server.node_type` and bail early for unsupported topologies (media nodes, etc.) before SSH + install — otherwise it silently installs the wrong stack (Docker Compose on a bare-metal media node). Log the failure with a clear message pointing to the correct manual provisioning path. Confidence: 0.75
