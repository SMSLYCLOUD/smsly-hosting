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
- Do not offer or attempt to SSH into the user's remote servers — debug and operate via their shell session or local access only. Confidence: 0.75
- Before committing changes to installer scripts (install.sh, lib/*.sh), thoroughly review exit code patterns and ensure failures warn but don't abort — do not commit until the error-handling flow is verified. Confidence: 0.80
- When stale or incorrect .env values are discovered (e.g., wrong hostnames, missing vars), add the fix to the installer scripts under lib/ rather than giving the user manual sed commands to run. Confidence: 0.85
- Review all unstaged changes before pushing — present a summary of each diff and wait for explicit approval before committing and pushing. Confidence: 0.70
