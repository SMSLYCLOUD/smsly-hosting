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

Trulay Grid is a self-hosted PaaS control plane. The threat model assumes:

- The host machine is trusted (operator's responsibility)
- Internal Docker network traffic is partially trusted (HMAC-signed for control-plane calls)
- External traffic terminates at Caddy/Traefik with TLS 1.2+
- All stored secrets are encrypted at rest (Fernet, key in `.env`)
- SSH host-key verification is enforced by default (`SMSLY_STRICT_SSH_HOST_KEY_CHECK=True`)

## Acknowledgements

We follow responsible disclosure and credit reporters in the next release notes.
