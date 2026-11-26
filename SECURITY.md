# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 2.1.x   | :white_check_mark: |
| 2.0.x   | :white_check_mark: |
| < 2.0   | :x:                |

## Reporting a Vulnerability

**Please do not file a public issue.** Email security@trulay.co with:

1. A description of the vulnerability and its impact
2. Steps to reproduce or a proof-of-concept
3. Any known workarounds

We aim to acknowledge within 48 hours and provide a remediation timeline within 7 days.

## Security Model

Trulay Grid is a self-hosted PaaS control plane. The threat model and default posture assume:

- The host machine is trusted (operator's responsibility), with UFW + `DOCKER-USER` firewall isolation for internal ports
- Startup fails closed if required secrets (`SECRET_KEY`, `FIELD_ENCRYPTION_KEY`) are missing
- Observability & access controls default closed (`GRAFANA_ANON_ENABLED=false`, `SMSLY_DISABLE_TIER_GATES=false`, SPIRE `insecure_bootstrap=false`)
- Internal Docker network traffic is authenticated (HMAC-V2 for control-plane calls; optional SPIFFE/SPIRE mTLS)
- External traffic terminates at Caddy/Traefik with TLS 1.2+
- All stored secrets are encrypted at rest (Fernet, key in `.env`)
- Egress mirror applies both nginx `$host` SSRF filtering and kernel `iptables` `OUTPUT` restrictions against loopback/RFC1918/link-local/CGNAT targets
- SSH host-key verification is enforced by default (`SMSLY_STRICT_SSH_HOST_KEY_CHECK=True`, `ALLOW_SSH_AUTOADD=False`)

## Acknowledgements

We follow responsible disclosure and credit reporters in the next release notes.
