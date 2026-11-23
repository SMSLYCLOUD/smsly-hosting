# SMSLY Hosting vs. Aeon (`aeonsec.io`) — Complete Parity & Surpass Architecture Roadmap

> **Purpose**: Map every architectural pillar, product module (`AEON CORE`, `CNAPP`, `ASPM`, `XDR`), cross-platform capability, surface layer, integration, and `Aeon Edge` feature from `https://aeonsec.io/` against `smsly-hosting`, clearly distinguishing **LIVE & VERIFIED** capabilities in the repository today from **PARTIAL** implementations and **NET-NEW GAPS**.

---

## Status Legend

| Badge | Meaning |
| :--- | :--- |
| `[LIVE]` | Implemented, wired end-to-end in `smsly-hosting`, and verified via automated tests / live checks. |
| `[PARTIAL]` | Core engine exists in repo, with additional external connectors or runtime agents planned for deep multi-cloud parity. |
| `[GAP]` | Net-new capability still open on the roadmap. |

---

## Executive Summary: Where SMSLY Surpasses Aeon Today

Unlike Aeon—which operates primarily as an external cloud/endpoint security posture and XDR overlay—`smsly-hosting` is the **sovereign execution plane itself**. With the wiring of `apps.security` (`SMSLY Aegis`) alongside `grid-cryptd`, CrowdSec First-Strike, Coraza/open-appsec WAF, SPIFFE/SPIRE mTLS, and the hardened Alert/Monitoring pipeline, `smsly-hosting` enforces both **inline runtime/crypto controls** AND **AEON CORE / ASPM / CNAPP / XDR / GRC telemetry**:

1. **Service-Scoped Sovereign Zero-Trust Encryption-at-Rest (`grid-cryptd`) `[LIVE]`**:
   - Opt-in per `Service` (`Service.encryption_at_rest_enabled`, migration `backend/apps/deployments/migrations/0255_encryption_opt_in.py`).
   - Go daemon (`cryptd/cmd/grid-cryptd/main.go`, `cryptd/internal/epoch/manager.go`, `lib/cryptd.sh`) holding weekly SMKs and per-service HKDF SEKs in `mlock()`'d memory (`cryptd/internal/crypto/`), Linux `cryptsetup` LUKS2/`fscrypt` encrypted volumes (`cryptd/internal/volumes/volumes.go`), PostgreSQL wire gate `PGProxy` (`cryptd/internal/dbproxy/pg_proxy.go`), `RedisProxy` (`cryptd/internal/dbproxy/redis_proxy.go`) with transparent value encryption, wrapped backup DEKs (`backend/apps/core/crypto_daemon.py`), emergency `/v1/vault/lockdown`, and opt-in lock/unlock controls (`backend/apps/deployments/views/service/encryption.py`, `frontend/src/components/settings/EncryptionAtRestCard.tsx`).
2. **SMSLY Aegis Unified Security Engine (`backend/apps/security/`) `[LIVE]`**:
   - **OCSF v1.1 Telemetry Lake** (`NormalizedSecurityEvent`), **Deduplicated Risk-Fusion Findings** (`SecurityFinding` with SLA tracking & Toxic Combinations Graph), **MITRE ATT&CK Heatmap Matrix**, **ASPM Pre-Build Gatekeeper** (`Secrets`, `IaC`, `SAST`, `Trivy CVE Sync`, `CycloneDX v1.5 SBOM`, `SLSA v1.0 / in-toto Provenance`), **CNAPP Auditors** (`CSPM`, `KSPM`, `CIEM`, `DSPM`, `AI-SPM` + prompt firewall), **XDR / SOAR** (`ITDR/UEBA`, `Threat Intel IoC Hub`, **Post-Deploy SSRF-Safe DAST**, granular `lock_service_cryptd` / `quarantine_service` / `capture_forensics` SHA-256 DFIR bundles / `ban_ip`), and **Continuous GRC** (`SOC 2 Type II`, `ISO 27001:2022`, `NDPR`, `PCI-DSS v4.0` with signed JSON export & `AuditLog` SHA-256 chain verification).
   - Wired end-to-end into `PipelineManager`, `NixpacksBuilder` (`backend/apps/deployments/services/builders.py`), `log_exhaustive_push_diagnostics` (`backend/apps/deployments/utils/diagnostics.py`), `_post_deploy_success` (`backend/apps/deployments/tasks/deploy/state.py`), Celery beat (`backend/config/celery.py`), REST `/api/v1/security/*`, and Frontend `SecurityCenterView` (`frontend/src/components/intelligence/SecurityCenterView.tsx`, `/intelligence` Security Intel tab, and `/services/[id]`).
3. **Multi-Layer Edge & First-Strike Autonomous Defense `[LIVE]`**:
   - Caddy L7 Coraza OWASP CRS WAF + `open-appsec` contextual ML sidecar + 21 custom first-strike CrowdSec scenarios (`infrastructure/crowdsec/scenarios/smsly-first-strike.yaml`) + 7 official Hub collections + Cloudflare edge bouncer (`scripts/crowdsec-cloudflare-sync.sh`).
4. **Runtime Workload Containment, SPIRE mTLS & Hardened Observability `[LIVE]`**:
   - SPIFFE/SPIRE mTLS (`backend/apps/mtls/`), gVisor/Kata sandboxing, `SMSLY-EGRESS-ISO` iptables blocks (`lib/egress_isolation.sh`), Prometheus/Alertmanager rules, SSRF-validated multi-channel alerts, outside-in DoH/RPKI & TLS watchdogs, and tenant-isolated `/incident-report/` correlation.

---

## 1. Architectural Engine Parity (`AEON CORE` → `SMSLY AEGIS CORE`)

| Aeon Core Stage | Aeon Capability | Current SMSLY Status & Verified Paths | Remaining Depth to Add |
| :--- | :--- | :--- | :--- |
| **Detection** | Continuous discovery across cloud, endpoints, network, identity, and code. | `[LIVE]` CrowdSec (`infrastructure/crowdsec/`), Coraza WAF, open-appsec ML, Trivy (`backend/apps/deployments/services/builders.py` & `utils/diagnostics.py`), Falco (`lib/harden_falco.sh`), `loki-log-bridge.py`, `edge_shield_watchdog.py`, ASPM pre-build Secrets/IaC/SAST (`backend/apps/security/aspm.py`), Post-Deploy DAST (`backend/apps/security/xdr.py`), CNAPP DSPM/CSPM/CIEM/KSPM (`backend/apps/security/cnapp.py`). | `[PARTIAL]` Add external internet-wide EASM subdomain/port drift discovery (`subfinder`/`naabu`) and Sigma YAML rule pack execution. |
| **Analysis** | Contextual correlation, attack-path mapping, blast-radius scoring, root-cause synthesis. | `[LIVE]` Risk Fusion scoring (`backend/apps/security/risk.py`), Toxic Combination Attack-Path Graph (`compute_attack_paths` in `cnapp.py`), `SecurityAnalysisView` (`backend/apps/core/views/security.py`), `SecurityAiHuntView` (`backend/apps/security/views.py`), and `/incident-report/` timeline correlation (`backend/apps/deployments/views/service/incident.py`). | Add visual interactive force-graph canvas in frontend UI over `/api/v1/security/attack-paths/`. |
| **Governance** | Policy-as-Code enforcement, compliance drift prevention, guardrails across environments. | `[LIVE]` `SecurityPolicy` gates (`aspm_block_on_secrets`, `aspm_block_on_critical_sast`, `aspm_block_on_critical_iac`, `dast_on_deploy_enabled`, `ai_prompt_firewall_enabled`), `trivy_fail_on_severity`, `cosign_require_verification`, `backup_require_encryption`, `verify_platform_integrity.sh`, and `grid-cryptd` service isolation. | `[GAP]` Add custom customer-authored Rego / OPA policy rules evaluated per team/project. |
| **Remediation** | Automated response, guided fix playbooks, self-healing infrastructure, PR generation. | `[LIVE]` `SecuritySoarExecuteView` (`execute_soar_action` in `xdr.py`: `lock_service_cryptd`, `quarantine_service`, `capture_forensics`, `ban_ip`, `run_dast`), AI Senate + Jules CVE PR dispatch (`builders.py:_dispatch_security_remediation`), auto-rollback (`auto_rollback.py`), and self-healing OOM/scale remediator (`remediator.py`). | Add direct one-click Dockerfile/lockfile PR remediation for ASPM findings directly from `SecurityCenterView`. |
| **Normalization** | Unified schema mapping all telemetry into one queryable data lake. | `[LIVE]` `NormalizedSecurityEvent` OCSF v1.1 model (`backend/apps/security/models.py`), `ocsf.py` (`build_ocsf_payload`, `record_ocsf_event`, `ingest_activity_list`, `ingest_from_audit_and_alerts`), wired into `SecurityEventsView` (`backend/apps/core/views/security.py`), `sync_security_telemetry_task`, and `GET /api/v1/security/events/`. | Add optional external S3/Parquet OCSF long-term archive shipper. |

---

## 2. Pillar 1: `CNAPP` (Cloud-Native Application Protection Platform)

| Sub-Module | Current SMSLY Status | Verified Implementation & Remaining Roadmap Items |
| :--- | :--- | :--- |
| **1. CSPM** | `[LIVE]` / `[PARTIAL]` `run_cspm_audit()` (`backend/apps/security/cnapp.py`) audits `ManagedServer` TLS verification/pinning (`verify_tls`, `tls_cert_sha256`), `PlatformConfig` WAF/SSL/backup encryption posture, and `verify_platform_integrity.sh`. | Add read-only AWS/GCP/Azure Config/Bucket CIS API connectors using `boto3` / `google-cloud-resource-manager` / `azure-mgmt-*` already present in `backend/requirements.txt`. |
| **2. KSPM** | `[LIVE]` / `[PARTIAL]` `run_kspm_audit()` audits container sandboxing (`runsc`/`kata` vs `runc`), `waf_opt_out` on public services, Docker socket-proxy isolation, AppArmor/seccomp posture. | Add external Kubernetes RBAC / PodSecurity / NetworkPolicy posture connector using `kubernetes` Python client (`backend/requirements.txt`). |
| **3. CIEM** | `[LIVE]` `run_ciem_audit()` (`backend/apps/security/cnapp.py`) flags dormant `APIToken`s (>30d unused or >14d never used), unscoped wildcard tokens (`scopes=[]`, `projects=[]`), and superuser sprawl. | Add external cloud IAM role/policy least-privilege graph analyzer. |
| **4. DSPM** | `[LIVE]` Encryption plane via `grid-cryptd` (`cryptd/`, `backend/apps/core/crypto_daemon.py`, `backend/apps/deployments/views/service/encryption.py`) + **Discovery plane via `run_dspm_audit()`** detecting sensitive DB addons on services lacking `encryption_at_rest_enabled=True`, `is_bucket_public=True` object buckets, and unmarked secret `EnvVar`s. | Add optional read-only SQL column PII sampler (detecting unencrypted PAN/SSN/BVN columns inside opted-in tenant DBs). |
| **5. CWPP** | `[LIVE]` / `[PARTIAL]` gVisor (`runsc`) / Kata isolation, Falco kernel eBPF monitoring (`SecurityEventsView`), cgroup caps, and `container_restart_loop_watchdog_task`. | Wire Falco/Tetragon container binary-drift auto-quarantine webhook triggering `execute_soar_action('quarantine_service', ...)`. |
| **6. AI-SPM** | `[LIVE]` `run_ai_spm_audit()` + `inspect_llm_prompt()` (`backend/apps/security/cnapp.py`) guarding `SecurityAiHuntView` against prompt injections and redacting AWS keys/Bearer tokens/SSNs/cards before outbound LLM requests. | Extend prompt firewall middleware to `apps/intelligence/` generic chat & MCP tool boundaries. |
| **7. IaC Scan** | `[LIVE]` `scan_iac()` (`backend/apps/security/aspm.py`) runs during `PipelineManager._run_aspm_scan()`, checking `Dockerfile`, `docker-compose*.yml`, and `.tf` files for root users, unpinned `:latest`, `ENV` secrets, `chmod 777`, `curl | sh`, `privileged: true`, `network_mode: host`, `docker.sock` mounts, and `0.0.0.0/0` ingress. | Add optional `checkov` / `trivy config` deep Terraform module JSON fusion when binary is installed on workers. |

---

## 3. Pillar 2: `ASPM` (Application Security Posture Management)

| Sub-Module | Current SMSLY Status | Verified Implementation & Remaining Roadmap Items |
| :--- | :--- | :--- |
| **1. SAST** | `[LIVE]` `scan_sast()` (`backend/apps/security/aspm.py`) runs Semgrep (when installed) + built-in Python AST (`eval`/`exec`, `pickle.loads`, `shell=True`, unsafe `yaml.load`, SQL f-string interpolation) & JS/TS static rules inside `PipelineManager`, persisting CWE-tagged `SecurityFinding`s. | Install `semgrep` binary in worker image build (`lib/harden_trivy.sh`) for multi-language framework rulesets. |
| **2. SCA & SBOM** | `[LIVE]` `generate_sbom()` (`backend/apps/security/aspm.py`) produces CycloneDX v1.5 BOMs (`SBOMArtifact`) from Trivy (when present) and repository lockfiles (`requirements.txt`, `package-lock.json`, `package.json`, `go.mod`) on every deploy, searchable platform-wide via `GET /api/v1/security/sbom/?package=<name>`. | Add CISA KEV / live OSV API EPSS enrichment job for newly disclosed CVEs against stored `SBOMArtifact.packages`. |
| **3. Secrets** | `[LIVE]` `scan_secrets()` (`backend/apps/security/aspm.py`) runs `gitleaks` (when present) + built-in secret rules (AWS, GitHub, Stripe live keys, PEM private keys, Slack, Google API, DB URL passwords) pre-build with configurable `aspm_block_on_secrets` policy gate. | Add Git commit-history deep scan option for historical commits on first onboarding. |
| **4. Image Scan** | `[LIVE]` `NixpacksBuilder.scan_image()` (`builders.py`) + `log_exhaustive_push_diagnostics()` (`diagnostics.py`) run Trivy and sync CVEs cleanly into `SecurityFinding` (`sync_deployment_trivy_findings`) with Risk Fusion scores & auto-resolution on clean builds. | Complete. |
| **5. DAST / Web Scan** | `[LIVE]` `run_post_deploy_dast()` (`backend/apps/security/xdr.py`) runs SSRF-safe live URL probes (`/.env`, `/.git/HEAD`, wildcard CORS with credentials, security headers), auto-dispatched async after promotion in `_post_deploy_success` (`state.py`) and on-demand via `SOAR run_dast`. | Optionally fuse `nuclei` template runner when binary is available on workers. |
| **6. SLSA Provenance & Signing** | `[LIVE]` `SigningMixin` (`signing.py`) signs & verifies images via Cosign, and `generate_slsa_provenance()` attaches in-toto Statement v1 / SLSA v1.0 provenance onto every deployment's `SBOMArtifact.slsa_provenance`. | Add `cosign attest` OCI registry push of `.slsa` predicate when Cosign private key is mounted. |

---

## 4. Pillar 3: `XDR`, Compliance/GRC & `Aeon Edge`

| Sub-Module | Current SMSLY Status | Verified Implementation & Remaining Roadmap Items |
| :--- | :--- | :--- |
| **1. CDR & Edge** | `[LIVE]` CrowdSec first-strike + Coraza + open-appsec + `loki-log-bridge.py` + `edge_shield_watchdog.py` + `watch_platform_edge_task`. | `[GAP]` Add external AWS CloudTrail / GCP Audit Log importer. |
| **2. EDR / Runtime** | `[LIVE]` / `[PARTIAL]` Falco eBPF (`SecurityEventsView`), `fail2ban`, `auditd`, `container_restart_loop_watchdog_task`. | Add Sysdig/Tetragon execve process-tree drift webhook into `record_ocsf_event()`. |
| **3. ITDR / UEBA** | `[LIVE]` `run_itdr_ueba_scan()` (`backend/apps/security/xdr.py`) flags multi-IP actor hopping and high-frequency privileged `AuditLog` bursts. | Add GeoIP impossible-travel distance calculation using GeoLite2 / resolved traffic geolocations. |
| **4. Threat Intel & MITRE ATT&CK** | `[LIVE]` `match_threat_intel()` (`xdr.py`) + `get_mitre_matrix_summary()` (`ocsf.py`) mapping 14 core MITRE ATT&CK techniques to live OCSF hits and findings. | Add scheduled hourly Abuse.ch URLhaus / ThreatFox / CISA KEV cache refresh. |
| **5. Active Response (SOAR) & Forensics (DFIR)** | `[LIVE]` `execute_soar_action()` (`xdr.py`) executes `lock_service_cryptd`, `quarantine_service`, `capture_forensics` (SHA-256 `ForensicSnapshot`), `ban_ip`, and `run_dast`. | Add optional WORM S3 upload for `ForensicSnapshot.bundle`. |
| **6. Compliance & GRC** | `[LIVE]` `evaluate_all_compliance_controls()` & `export_compliance_evidence_bundle()` (`compliance.py`) evaluate **SOC 2 Type II**, **ISO 27001:2022**, **NDPR**, and **PCI-DSS v4.0**, cryptographically verify the `AuditLog` SHA-256 chain, and export signed JSON evidence. | Add formatted PDF auditor report export. |
| **7. Integrations & Sovereign Edge** | `[LIVE]` / `[PARTIAL]` Slack, Discord, Email, WhatsApp, SSRF-guarded Generic Webhooks (`notifications/webhooks.py`), Cloudflare CrowdSec bouncer (`crowdsec-cloudflare-sync.sh`), GitHub/GitLab/Bitbucket webhooks, `grid-cryptd` Shamir/Vault daemon. | `[GAP]` Add PagerDuty Events v2, Jira/Linear 2-way tickets, and air-gapped `.tar.zst` offline Trivy/Semgrep/CRS bundle exporter (`scripts/smsly-airgap-bundle.sh`). |

---

## 5. Remaining Net-New Roadmap Priorities

1. **External Cloud Read-Only Connectors (`CSPM`/`KSPM`)**: Poll AWS/GCP/Azure IAM, KMS, Security Groups, Storage ACLs, and K8s manifests into `SecurityFinding`.
2. **Continuous Threat Intel & KEV Sync + Falco Auto-Quarantine Webhook**: Hourly Abuse.ch/CISA KEV download + optional Falco critical-rule auto-quarantine.
3. **ITSM 2-Way Connectors**: Bi-directional Jira, Linear, ServiceNow, and PagerDuty Events v2 synchronization for `CRITICAL` findings.
4. **SMSLY Sovereign Edge Air-Gap Packager**: `scripts/smsly-airgap-bundle.sh` for offline signed `.tar.zst` vulnerability/rule bundles & store-and-forward edge sync.
