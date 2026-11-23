# Security Best Practices & Architectural Audit Report

**Last Updated:** 2026-11-23

## 1. Control-Plane & Deployment Hardening (2026-11-19)

1. **Cluster Recovery Phrase Removal**
   - Removed recovery-phrase cluster adoption and encrypted metadata mirroring.
   - Server cluster onboarding and control-plane management remain anchored on authenticated user ownership and SSH/WireGuard verification.

2. **Server Backup Download Authorization**
   - Hardened `ServerViewSet.download_backup` (`backend/apps/deployments/views/server.py`) so only superusers or the owning user of the target server can download backup archives, with traversal-safe path validation.

3. **Platform Route Recheck & Rate Limiting**
   - Scoped `recheck_platform_routes` to staff users or the authenticated user's own services, and enforced a 60-second per-user cache cooldown to prevent synchronous Docker/proxy route-sync abuse.

4. **Compose & Nixpacks Build Containment**
   - Blocked unsafe host bind mounts, `privileged: true`, host namespace sharing (`network_mode: host`, `pid: host`, `ipc: host`), device mappings, and sensitive cap/security overrides in user-supplied Compose configurations.
   - Kept Nixpacks build plans inside the repository without passing `--config` flags that older Nixpacks CLIs reject.

---

## 2. Edge WAF & CrowdSec First-Strike Hardening (2026-11-19)

1. **Zero-Tolerance First-Strike CrowdSec Policy**
   - Configured zero-tolerance first-strike scenarios in `infrastructure/crowdsec/scenarios/smsly-first-strike.yaml` with `infrastructure/crowdsec/profiles.yaml` escalation and Cloudflare edge bouncer sync (`scripts/crowdsec-cloudflare-sync.sh`).

2. **Dual-Engine L7 WAF (Coraza CRS v4 + open-appsec Contextual ML)**
   - Wired Coraza OWASP Core Rule Set v4 (`http.handlers.waf`) inline in Caddy alongside the `open-appsec` contextual ML sidecar and host-level `crowdsec-firewall-bouncer` (`iptables`/`nftables`).

---

## 3. Service-Scoped Sovereign Encryption-at-Rest (`grid-cryptd`) (2026-11-22)

1. **Opt-In Per-Service Scope (`Service.encryption_at_rest_enabled`)**
   - Added `Service.encryption_at_rest_enabled` (`BooleanField(default=False)`, migration `backend/apps/deployments/migrations/0255_encryption_opt_in.py`).
   - Only opted-in services route encrypted volumes, secrets transit, backup DEK wrapping, and Postgres/Redis point-of-contact gates through `grid-cryptd` (`backend/apps/core/crypto_daemon.py`, `backend/apps/deployments/views/service/encryption.py`, `frontend/src/components/settings/EncryptionAtRestCard.tsx`).

2. **Volatile Vault & Kernel/Proxy Enforcement**
   - Implemented the `cryptd/` Go daemon (`cryptd/cmd/grid-cryptd/main.go`, `cryptd/internal/epoch/manager.go`, `cryptd/internal/crypto/`, `cryptd/internal/volumes/volumes.go`, `cryptd/internal/dbproxy/pg_proxy.go`, `cryptd/internal/dbproxy/redis_proxy.go`, installed via `lib/cryptd.sh`) holding weekly SMKs and per-service HKDF SEKs in `mlock()`'d RAM, cutting active DB/Redis streams and locking volumes on `/v1/vault/lockdown` or `/v1/services/{id}/lock`.

---

## 4. Alert, Monitoring & Incident Pipeline Remediation (2026-11-23)

1. **SSRF, Secret Redaction & Tenant Isolation**
   - Enforced `validate_webhook_url()` SSRF checks across all notification channels in `backend/apps/notifications/webhooks.py` and `backend/apps/notifications/tasks/__init__.py`.
   - Added `_safe_audit_metadata()` in `backend/apps/notifications/views/__init__.py` to redact webhook secrets/passwords before writing `AuditLog` entries.
   - Eliminated cross-tenant substring leaks (`target__icontains=service.name`) and PostgreSQL-only JSON lookups in `backend/apps/deployments/views/service/incident.py`, restricted global infrastructure events to staff/superusers, hardened Grafana anonymous viewer settings, and removed `--privileged` + host-IP exposure on auto-deployed `cAdvisor` and `node_exporter` containers.

2. **Alert Accuracy, Cooldowns & Self-Healing Coverage**
   - Fixed Prometheus alert expressions in `infrastructure/monitoring/rules/platform.yml`, Alertmanager routing/inhibition in `scripts/alertmanager-entrypoint.sh` and `infrastructure/monitoring/alertmanager.yml`, per-job cursors in `infrastructure/monitoring/loki-log-bridge.py`, `ResourceAlert` persistence in `backend/apps/core/tasks/metrics.py`, offline addon alerting in `backend/apps/addons/services/alerts.py`, and superuser paging across watchdogs.

---

## 5. Aeon-Parity Unified Security Engine (`apps.security` / SMSLY Aegis) (2026-11-23)

1. **AEON CORE & ASPM Pre-Build / Supply-Chain Gates**
   - Added OCSF v1.1 telemetry normalization (`NormalizedSecurityEvent`), deduplicated Risk-Fusion findings (`SecurityFinding` + Toxic Combinations Attack Paths), MITRE ATT&CK technique tagging, pre-build Secret/IaC/SAST scanners, CycloneDX v1.5 SBOM + SLSA v1.0/in-toto provenance (`SBOMArtifact`), and Trivy CVE finding sync wired into `PipelineManager`, `NixpacksBuilder`, and `log_exhaustive_push_diagnostics`.

2. **CNAPP, XDR, SOAR Containment & Continuous GRC**
   - Wired automated CSPM/KSPM/CIEM/DSPM/AI-SPM audits, ITDR/UEBA anomaly detection, Threat Intel IoC matching, SSRF-safe post-deploy DAST (`run_post_deploy_dast`), granular SOAR playbooks (`lock_service_cryptd`, `quarantine_service`, `capture_forensics` SHA-256 DFIR bundles, `ban_ip`), AI-SPM prompt injection firewall on `SecurityAiHuntView`, and automated SOC 2 / ISO 27001 / NDPR / PCI-DSS evaluation with `AuditLog` hash-chain cryptographic verification (`backend/apps/security/`).
