# Taste (Continuously Learned by [CommandCode][cmd])

[cmd]: https://commandcode.ai/

# architecture
See [architecture/taste.md](architecture/taste.md)
# infrastructure
- Registry TLS certificates are managed by Traefik — do not regenerate certs with standalone openssl; investigate Traefik cert generation if cert/key mismatch occurs. Confidence: 0.88

# env-resolver
- When manifest env resolver cannot resolve a var from any real source, fill it with a mock/placeholder value instead of leaving it empty or marking it unresolved. Confidence: 0.70

# security
- Make security scan severity threshold configurable via settings with options to enable/disable and choose threshold level (low, medium, high, critical). Confidence: 0.70
- Security tab in service Insights should show the service's own vulnerability scan data (Trivy results from the deployment model), not system-level hardening status. Confidence: 0.65

# typescript
- In .tsx files, generic arrow functions need a trailing comma on the type parameter (e.g., `<T,>` not `<T>`) to prevent TSX from parsing it as a JSX tag. Confidence: 0.70

# workflow
See [workflow/taste.md](workflow/taste.md)
# testing
- When fixing a Django endpoint that silently crashes (e.g., `result.save()` fails), check required model FK fields — the ORM won't validate defaults and `.save()` will raise `IntegrityError` swallowed by broad `except Exception`. Confidence: 0.65
- Service detail tabs (Insights, Monitoring, Security) must scope data to the current service only — do not fetch or display platform-wide metrics from within a service context. Confidence: 0.75

# verification
- After code changes, perform deep E2E verification: trace every path across all changed files with exact timing (T+n notation), identify race conditions, and walk through multiple scenario outcomes before/after the change. Confidence: 0.75

# frontend-navigation
- Before creating a new page, check the navbar component (and any other navigation components) for existing links to that page — the route may already exist under a different path than what glob/file-search suggests. Confidence: 0.65
