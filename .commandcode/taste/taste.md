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
# code-generation
- When porting or refactoring code, always use scripts to extract/move existing source code rather than writing it manually — writing from scratch introduces hallucinations and errors. Confidence: 0.82

# testing
- When fixing a Django endpoint that silently crashes (e.g., `result.save()` fails), check required model FK fields — the ORM won't validate defaults and `.save()` will raise `IntegrityError` swallowed by broad `except Exception`. Confidence: 0.65
- Service detail tabs (Insights, Monitoring, Security) must scope data to the current service only — do not fetch or display platform-wide metrics from within a service context. Confidence: 0.75

# verification
- After code changes, perform deep E2E verification: trace every path across all changed files with exact timing (T+n notation), identify race conditions, and walk through multiple scenario outcomes before/after the change. Confidence: 0.75
- After major refactoring (splitting files into subpackages), deploy multiple (8-10) verification agents to independently check: re-export chains, import paths, syntax validity, method completeness, MRO order, and orphaned decorators. Confidence: 0.70

# frontend-navigation
- Before creating a new page, check the navbar component (and any other navigation components) for existing links to that page — the route may already exist under a different path than what glob/file-search suggests. Confidence: 0.65

# celery
- Every Docker-dependent Celery task must be explicitly routed to the `deploy` queue in `app.conf.task_routes` — the default `celery` queue worker (`celery-1`) has no DOCKER_HOST, BUILDKIT_HOST, or Docker socket proxy; silent failures result when Docker operations run on the wrong worker. Route both `tasks.py` and specialized `tasks_*.py` copies so Celery resolves correctly regardless of import path. Confidence: 0.80
- Duplicate task definitions in `tasks.py` that mirror specialized modules (`tasks_addons.py`, `tasks_platform_update.py`, `tasks_transfer.py`, etc.) are dangerous — the `tasks.py` copies lack Celery route entries, silently landing on the default queue. Replace duplicates with re-exports or stub dispatches to the authoritative module, and add route entries for both task name variants. Confidence: 0.75

# refactoring
- After extracting methods from large files via script, scan all extracted files for orphaned decorators (@action lines separated from their def by blank lines) — the extraction script's method boundary detection can leave trailing decorator lines as dead code artifacts. Confidence: 0.70
- When splitting a views.py monolith into a views/ package with subdirectories, all extracted files inside subpackages (views/service/, views/deployment/, views/backup/) need relative imports with `...` (three dots) instead of `..` (two dots) because they are 3 levels deep from the app root. Confidence: 0.70
- After extracting methods from a class into separate mixin files using a script, verify each extracted file has all its own imports — methods that relied on imports at the top of the original file will cause NameError at runtime in the extracted file. Run syntax validation then grep for symbols used in method bodies vs. imported names. Confidence: 0.70

# automation-install
- New systemd services and opt-in features should default to enabled and auto-installed during update flow — do not leave manual cp/systemctl instructions for the user to run. Confidence: 0.70

# provisioner
- `provision_server` must check `server.node_type` and bail early for unsupported topologies (media nodes, etc.) before SSH + install — otherwise it silently installs the wrong stack (Docker Compose on a bare-metal media node). Log the failure with a clear message pointing to the correct manual provisioning path. Confidence: 0.75
