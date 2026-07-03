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

# workflow
- Do not offer or attempt to SSH into the user's remote servers — debug and operate via their shell session or local access only. Confidence: 0.75
- Before committing changes to installer scripts (install.sh, lib/*.sh), thoroughly review exit code patterns and ensure failures warn but don't abort — do not commit until the error-handling flow is verified. Confidence: 0.80
