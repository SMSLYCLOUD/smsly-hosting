# SMSLY Hosting vs. Aeon (`aeonsec.io`) — Parity & Surpass Security Architecture Roadmap

> **Document Purpose:** Architectural blueprint for matching and surpassing **every** capability offered by Aeon (`Aeon Console`, `AEON CORE`, `CNAPP`, `ASPM`, `XDR`, `Aeon Edge`, Compliance/GRC, Surface Coverage, and Integrations) inside `smsly-hosting`, without writing code prematurely.
>
> **Stability Note:** None of the feature gaps documented below affect `smsly-hosting`'s production availability or core platform security guarantees today. All live runtime, database, transport, RBAC, containment, and edge protections continue operating cleanly; this document defines **additive product and security capabilities** to outcompete Aeon.

---

## 1. Executive Summary & Strategic Thesis

Aeon (`aeonsec.io`, spun out of PipeOps) packages **posture visibility, pre-deploy DevSecOps, runtime XDR telemetry, multi-cloud CNAPP, compliance automation (SOC 2 / ISO 27001 / NDPR), and on-site edge appliances** under one AI-assisted platform (`AEON CORE` + `Cortex AI`).

`smsly-hosting` **already owns the harder half** that most security dashboards lack: **direct infrastructure, kernel, network, identity, and database enforcement inside the live control plane and worker fleet**:

- **Runtime Sandboxing & Host Hardening:** gVisor (`runsc`) & Kata Containers (`kata-runtime`) (`backend/apps/deployments/services/container_runtime.py`), AppArmor (`lib/harden_apparmor.sh`), `auditd` (`lib/harden_auditd.sh`), `ufw`, `fail2ban`, kernel hardening (`lib/harden_kernel.sh`), and strict compose/build containment (`backend/apps/deployments/services/pipeline/compose_networking.py`, `backend/apps/cloud/services/builder.py`).
- **Edge & L7 Defence:** Coraza OWASP CRS WAF in Caddy (`http.handlers.waf`), open-appsec Envoy WAF (`lib/harden_openappsec.sh`), CrowdSec + Cloudflare Bouncer (`backend/apps/crowdsec/`), and strict Traefik middleware headers.
- **Zero-Trust East-West Workload Identity:** SPIRE Server/Agent + Envoy SPIFFE mTLS sidecars + `MtlsAuthorizationPolicy` (`backend/apps/mtls/`), per-project bridge isolation, and iptables egress filtering (`backend/apps/deployments/services/network_scope.py`).
- **Cryptography & Data-in-Transit/at-Rest:** TLS-enforced shared PostgreSQL (`hostssl`, `backend/apps/addons/services/shared_postgres.py`), PgBouncer mutual/server-verified TLS (`backend/apps/addons/services/tenant_pooler.py`), LUKS/fscrypt encrypted addon volumes (`scripts/smsly-volume-crypt.sh`), Fernet-encrypted secrets/backups, and atomic HMAC nonce & recovery phrase lifecycle controls.
- **Runtime eBPF Detection & Image Security:** Falco (`lib/harden_falco.sh`), Trivy (`lib/harden_trivy.sh`), Cosign image signing & verification (`backend/apps/deployments/services/pipeline/signing.py`, `scripts/cosign-verify.sh`), and Loki/Prometheus correlation (`SecurityStatusView`, `SecurityEventsView`, `SecurityAnalysisView` in `backend/apps/core/views/security.py`).

### How We Beat Aeon
1. **Unite Enforcement + Visibility:** Aeon primarily *observes and routes tickets* across third-party tools. `smsly-hosting` controls the proxy, firewall, container runtime, database pooler, SPIRE CA, and CI/deploy pipeline directly—meaning any finding can execute **zero-handoff enforcement, auto-patching, and cryptographic verification** natively.
2. **Evidence-Backed Sovereign PaaS + External Fleet:** Extend our existing self-verification (`scripts/verify_platform_integrity.sh`, `AuditLog`, TLS/mTLS state) into automated GRC audit bundles, full ASPM deploy gates, external multi-cloud/K8s read-only connectors, MITRE ATT&CK XDR correlation, and an installable `SMSLY Edge Gateway` software/hardware appliance.
3. **Agentic Auto-Remediation (`SMSLY Intelligence`):** Instead of only summarizing issues (`Cortex AI`), leverage our multi-provider LLM router (`backend/apps/intelligence/`) and autonomous PR/patch workflows to **generate tested remediation PRs, Dockerfile/IaC fixes, and WAF/network rules automatically**.

---

## 2. Current Reality Check: Are Existing Gaps Critical?

| Question | Answer | Technical Justification |
|---|---|---|
| **Can missing Aeon modules break `smsly-hosting`?** | **No.** | Missing features (SAST/SBOM export, DAST, CSPM/CIEM/DSPM on external AWS/GCP accounts, SOC 2/NDPR PDF generators, TPRM vendor questionnaires, corporate endpoint USB device control, Jira/Asana sync) are additive product capabilities, not runtime dependencies of your proxy, DB, Celery, or deploy pipeline. |
| **Do core tenant workloads stay protected today?** | **Yes.** | Ingress TLS + Coraza OWASP CRS WAF, CrowdSec IP reputation, SPIRE mTLS, project bridge isolation + iptables egress rules, non-privileged compose enforcement, Trivy/Cosign hooks, Falco eBPF alerts, and Postgres/PgBouncer TLS protect running services independently of these roadmap modules. |
| **What *must* remain maintained continuously?** | **Core operational controls** | Host/container TLS validity, Caddy Coraza & open-appsec Prevent mode, hourly `verify_platform_integrity.sh` healing, and baseline regression/security suites. Everything else below should be built **behind modular feature flags & async Celery queues** so new scanners never slow down or destabilize deployments. |

---

## 3. Exhaustive Parity & Surpass Matrix (All Aeon Layers Covered)

### 3.1 Platform Lifecycle & Core Operating Layer (`AEON CORE`)

| Aeon Component | What Aeon Does | SMSLY Match Plan | How SMSLY Surpasses Aeon |
|---|---|---|---|
| **Detection** | Detect exposures, drift, suspicious activity | Unify Loki, Falco, CrowdSec, Trivy, `verify_platform_integrity.sh`, and deploy gates into a canonical `SecurityFinding` stream | Cryptographic runtime verification (e.g., SPIRE SVID attestation + eBPF kernel events) rather than polling-only snapshots |
| **Analysis** | Correlate telemetry & asset context | Build an Asset-Graph linking `Organization -> Project -> Service -> Container -> Addon/DB -> Domain -> ManagedServer` | Live request-path tracing from Caddy/Traefik -> Envoy SPIFFE -> Container -> Shared Postgres DB (`tx` / connection attribution) |
| **Governance** | Policies, approvals, ownership, audit trail | Policy-as-code ruleset (`PolicyRule` model) tied to deploy gates, PR approvals, RBAC, and `AuditLog` | Enforce policies fail-closed at container creation, network attach, and ingress route promotion—cannot be bypassed via console UI |
| **Remediation** | Assign owners, track exceptions/SLAs | Finding lifecycle (`OPEN`, `IN_PROGRESS`, `SUPPRESSED_WITH_EXPIRY`, `RESOLVED`, `VERIFIED`) with Slack/Jira/Webhook sync | **Closed-loop auto-verification:** re-runs probe/scanner automatically to verify a fix before marking resolved; can open code PRs via `apps/intelligence` |
| **AEON CORE Normalization** | Normalize asset/telemetry/policy/evidence schemas | Introduce unified `SecurityEvent`, `SecurityAsset`, `SecurityFinding`, and `ComplianceEvidence` PostgreSQL models + Celery normalizers | Partitioned retention + Timescale/index optimization + zero-copy streaming from existing Loki/Prometheus/Postgres stack |

---

### 3.2 CNAPP — Cloud-Native Application Protection Platform

| Aeon Module | Match Specification | Surpass Differentiator |
|---|---|---|
| **CSPM** *(Cloud Security Posture Management)* | Scheduled read-only connectors (`aws` via boto3/IAM AssumeRole, `gcp` Service Account, `azure` Service Principal) evaluating CIS Benchmarks, public buckets, open security groups, KMS, and IAM MFA via embedded Prowler/Steampipe workers | Correlate cloud-account misconfigs **directly against live SMSLY-deployed workloads & blast radius**; one-click Terraform/IaC remediation patch generation |
| **KSPM** *(Kubernetes Security Posture Management)* | Connect external kubeconfigs / K3s clusters (we already have `infrastructure/aws-k3s-recommendation` & K8s docs); embed **Kubescape / Trivy K8s** for Pod Security Standards, RBAC over-privilege, exposed LoadBalancers, and NetworkPolicy gaps | Generate ready-to-apply hardened YAML `NetworkPolicy`, `PodDisruptionBudget`, and SPIFFE sidecar specs tailored to the discovered workloads |
| **CIEM** *(Cloud Infrastructure Entitlement Management)* | Build an effective-permission graph mapping IAM Users, Roles, ServiceAccounts, GitHub OIDC, and `APIToken`/`ManagedServer` keys to detect toxic privilege combinations & dormant admin keys | Extend CIEM **inside tenant workloads**: audit SPIRE `spiffe://` SVID selectors, least-privilege DB roles (`smsly_monitor`, tenant DB users), and container cap sets |
| **DSPM** *(Data Security Posture Management)* | Automated PII/PCI/secret schema & object-store classifier scanning S3/GCS buckets and opt-in PostgreSQL/MySQL/Redis metadata & sample rows (preserving tenant privacy via opt-in metadata/regex signatures) | Cross-reference sensitive data stores against **verified TLS (`hostssl`), LUKS encryption status (`smsly-volume-crypt`), network isolation, and backup encryption status** |
| **CWPP** *(Cloud Workload Protection)* | Continuous inventory of running processes, open ports, kernel runtime (`runsc`/`kata`/`runc`), package CVEs, and memory/CPU anomalies across fleet nodes | Combine **preventive sandboxing (`runsc`/Kata/AppArmor/seccomp)** with Falco detection so workloads are blocked at syscall level, not just alerted after compromise |
| **AI-SPM** *(AI Security Posture)* | Catalog AI providers/keys (`apps/intelligence`), RAG vector stores (`pgvector`), MCP servers (`apps/mcp`), agent tool permissions, prompt-injection firewall logs, and LLM spend/token abuse caps (`UserAICap`) | **Built-in LLM Prompt/Egress Firewall & MCP Scope Enforcement:** inspect/redact PII/secrets before hitting external LLMs and enforce scoped MCP tool permissions natively |
| **IaC Scan** | Trigger **Checkov / Trivy config** on Git repos, uploaded zip/Compose bundles, Terraform, CloudFormation, and Helm charts pre-deploy (`deployments/services/pipeline/build.py`) | Pair static IaC checks with our **merged/interpolated Compose AST gate (`compose_networking.py`)**, catching runtime escape vectors (`host` networks, sensitive bind mounts, caps) even after variable expansion |

---

### 3.3 ASPM — Application Security Posture Management

| Aeon Module | Match Specification | Surpass Differentiator |
|---|---|---|
| **SAST** | Run **Semgrep** (OWASP Top 10, language-specific rules) asynchronously inside isolated build sandboxes before Nixpacks/Dockerfile image build | Map SAST findings directly to line-level PR suggestions / AI auto-fix patches and severity-based deploy policies (warn vs block on `CRITICAL`) |
| **SCA** *(Software Composition Analysis)* | Parse `package-lock.json`, `poetry.lock`, `requirements.txt`, `go.mod`, `Cargo.lock`, `pom.xml` via Trivy FS / OSV-Scanner; tie CVEs to service owners and SLAs | **Reachability + Exposure Prioritization:** elevate CVEs in packages actually loaded inside internet-exposed services without WAF rules over unreachable dev dependencies |
| **SBOM** | Generate **CycloneDX & SPDX** JSON SBOMs via **Syft / Trivy** during every build; store immutable SBOM artifacts linked to `Deployment.id` and container digest | Instant **zero-day blast-radius query**: when a new CVE drops (e.g., Log4j / React2Shell / PHP2Shell), query all active `Deployment` SBOMs across the entire fleet in <1 second |
| **Secrets Scanning** | Pre-build **Gitleaks / TruffleHog** scan on git commit history, working trees, uploaded archives, `.env` configs, and build logs | **Zero-Trust Secret Auto-Quarantine:** block deploys containing leaked cloud/DB/API tokens and trigger SMSLY's internal secret rotation / Vault/Infisical sync workflow |
| **Image Scan** | Expand existing `lib/harden_trivy.sh` + `cosign-verify.sh` into per-build structured JSON ingestion (`ImageVulnerability` records), configurable CVE severity/EPSS gates, and scheduled re-scans of running registry digests | Enforce **signed attestation checks before Caddy/Traefik route promotion**—unsigned or expired-scan images cannot serve production traffic |
| **DAST / Web Scan** | Schedule **Nuclei / OWASP ZAP** baseline/API scans against newly promoted staging/production URLs (plus OpenAPI/GraphQL endpoint fuzzing) | **WAF-Aware Verification:** run probes both *directly* against the backend container port and *through* Caddy Coraza/open-appsec WAF so users see both raw app bugs and whether edge WAF actively mitigates them |
| **SLSA Provenance** | Build with Docker Buildx `--provenance=true --sbom=true`, attach in-toto DSSE attestations, and sign manifests using our existing **Cosign** key infrastructure | End-to-end **admission verification on remote worker nodes (`aws-full-2`, `upcloud-full-1`)**: worker nodes refuse to pull/start any image lacking a valid control-plane Cosign signature + SLSA predicate |

---

### 3.4 XDR — Extended Detection, Response & Threat Operations

| Aeon Module | Match Specification | Surpass Differentiator |
|---|---|---|
| **CDR** *(Cloud Detection & Response)* | Ingest AWS CloudTrail / GCP Audit Logs / Azure Activity Logs + SMSLY control-plane `AuditLog` into correlation rules detecting anomalous API calls, IAM edits, or snapshot exports | Correlate cloud API events directly with host SSH sessions (`SSHClient`), WireGuard peer joins, and `ManagedServer` heartbeats |
| **EDR / Runtime** | Deploy Falco + eBPF host/container sensors fleet-wide (`trulay-vps`, worker nodes) capturing process execs (`execve`), reverse shells, sensitive file writes, kernel modules, and unexpected outbound sockets; optional lightweight endpoint agent (`osquery`/`Fleet`) for operator workstations | **Contain without full shutdown:** leverage Docker network disconnect, container `pause`, cgroup freeze, read-only rootfs enforcement, or instant blue-green replacement on known-clean digest |
| **ITDR / UEBA** | Track anomalous logins, impossible travel, brute-force spikes, abnormal API token creation, recovery-phrase attempts, 2FA bypass anomalies, and Okta/Entra/GitHub OAuth events | Automatically trigger short-TTL session revocation, step-up TOTP/2FA challenge, or API token scoping freeze inside `apps/core/` |
| **Device Control** | For customers requiring corporate endpoint management: ship optional Fleet/osquery/MDM policy module auditing/restricting removable USB storage, disk encryption (FileVault/BitLocker/LUKS), OS patch level, and firewall posture | Tie **endpoint posture to PaaS admin access** (Zero-Trust Admin Access: deny SSH/admin console/recovery actions if operator device fails posture checks) |
| **Threat Intel** | Ingest CrowdSec CAPI, AbuseIPDB, AlienVault OTX, CISA KEV, EPSS, and CVE feeds into a normalized IOC/KEV lookup cache | Push high-confidence IOCs straight into **NFTables/UFW, Cloudflare Bouncer, Caddy/open-appsec, and internal DNS sinkholes (`CoreDNS`)** across every cluster node in seconds |
| **Correlation & ATT&CK Coverage** | Map every Falco rule, CrowdSec scenario, Coraza CRS rule, AuditLog trigger, and host-security check to **MITRE ATT&CK Tactics & Techniques** (`T1190`, `T1059`, `T1078`, etc.) and render a visual coverage heatmap showing gaps | Live **rule-testing / purple-team simulator** verifying that simulated SQLi, container escape, or reverse-shell indicators actually fire the mapped ATT&CK detection & alert |
| **Active Response** | Automated & 1-click playbook engine (`PlaybookRule` -> `Trigger` -> `Action`): ban IP, quarantine container network, freeze API token, revoke SSH key, snapshot container state, notify Slack/PagerDuty | Safe **guardrailed execution with TTL auto-rollback & blast-radius limits** (never accidentally brick master ingress or self-lock admin IPs) |
| **Forensics / DFIR** | On incident trigger or 1-click button: capture container diff (`docker diff`), live process/socket table (`ps`/`ss`), memory/filesystem tarballs (`tar` / gCore), Falco/Loki timeline slice, and SHA-256 chain-of-custody manifest | Export a **tamper-evident cryptographic DFIR evidence bundle** (signed zip + SHA-256 hashes + timeline JSON) directly to encrypted object storage |

---

### 3.5 Cross-Platform Core Capabilities & Enterprise Operations

| Aeon Module | Match Specification | Surpass Differentiator |
|---|---|---|
| **Unified Asset Inventory** | Continuous graph inventory of Clouds, K8s clusters, VMs/Bare-metal (`ManagedServer`), Containers, Domains, TLS Certs, Addon DBs, Identities, Repositories, SaaS, and Edge Appliances—each with team Owner & environment tier | Real-time **Shadow Asset / Port Discovery**: cross-compare declared Caddy/Traefik routes + DNS records against external Nmap/masscan/CT-log discovery (`External Surface`) |
| **Risk Fusion** | Composite score $0\text{–}100$ fusing: `(Severity × EPSS × CISA_KEV) × Internet_Exposure × Missing_WAF_Or_mTLS × Sensitive_Data_Sensitivity × Runtime_Reachability` | Dynamically **de-prioritize theoretical CVEs mitigated by live runtime controls** (e.g., behind active Coraza + mTLS + gVisor) while surfacing immediately reachable exploit paths at #1 |
| **Vuln Mgmt & Lifecycle** | Single queue across Trivy, SAST, SCA, DAST, CSPM, and Host checks with deduplication, SLA countdowns, false-positive suppression, time-bound risk acceptance, and fix verification | Native tie-in with **SMSLY Deployments**: clicking "Fix" on a container base-image CVE can rebuild & canary-deploy the patched image via `Blue/Green` or `Canary` rollouts |
| **Compliance & GRC** | Continuous automated control evaluation mapped to **SOC 2 Type II, ISO 27001, NDPR (Nigeria Data Protection Regulation), PCI-DSS, HIPAA, CIS, and Custom Policies**; auditor read-only portal & signed PDF/ZIP evidence exports | **Live Technical Proof Instead of Screenshots:** automatically attach live `verify_platform_integrity.sh` outputs, Postgres `hostssl` state, SPIRE rotation metrics, `AuditLog` exports, and backup-restore drill proofs |
| **Cortex AI Alternative (`SMSLY Security AI`)** | Natural-language Q&A over findings, executive/CISO risk summaries, attack-path narratives, and remediation guidance powered by `apps/intelligence/` | **Actionable Agentic Engineering:** multi-model consensus + codebase-aware remediation PR drafting (leveraging existing multi-LLM / Jules pipelines) scoped safely with human approval |
| **Vendor / TPRM** | Third-party vendor registry, security questionnaires, SLA/cert expiry tracking (SOC2/ISO/DPA renewal alerts), public trust-page monitoring, and breach-feed correlation | Automatic **sub-processor inventory discovery**: detect third-party SaaS SDKs, webhooks, and outbound domains used by deployed apps via SBOMs + DNS/egress telemetry |
| **Enterprise Admin** | Multi-org/multi-tenant hierarchy, SAML 2.0 / OIDC SSO (Okta, Google, Entra), granular custom RBAC, immutable `AuditLog` retention, API scopes, and MSSP multi-customer switcher | Isolated tenant cryptographic boundaries (per-tenant KMS/Fernet keys, dedicated Postgres/PgBouncer pools, scoped Docker/WireGuard bridges) |

---

### 3.6 Surface Coverage (5 Layers) & Bi-Directional Integrations

| Surface Layer | Aeon Coverage | SMSLY Match & Surpass Plan |
|---|---|---|
| **1. Hosts & Servers** | Packages, processes, FIM, users, weak configs, drift | Unify `host_security.sh`, `verify_platform_integrity.sh`, Lynis/OpenSCAP CIS host audits, AIDE/Falco FIM, SSH key hygiene, and systemd drift detection into fleet dashboards |
| **2. Containers & K8s** | Images, pods, namespaces, exposed services, RBAC, drift | Combine existing container runtime inspection (`runsc`/`kata`, non-root checks, readonly rootfs audit) with Kubescape/Trivy K8s operator for external clusters |
| **3. Code & CI/CD** | Repos, pipelines, secrets, artifacts, IaC, branch/approval gates | GitHub App / GitLab webhook scanner checking branch protection, required reviewers, OIDC trust policies, PR SAST/Secrets/IaC diff comments, and SLSA signing |
| **4. Network** | Ports, routes, VPNs, firewalls, DNS, east-west paths | Map WireGuard mesh (`wg0`), UFW/iptables egress chains, Coraza/Envoy routes, CoreDNS zones, and SPIFFE `MtlsAuthorizationPolicy` in an interactive topology visualizer |
| **5. External Surface (EASM)** | Public domains, IPs, certs, cloud endpoints, shadow IT | Continuous external CT-log subdomain enum, TLS cipher/expiry grading, DNSSEC/SPF/DMARC/DKIM checks, exposed DB/admin-port probing from external probers, and Nuclei HTTP fingerprinting |

**Integrations Parity (`14+` Tools & Bi-Directional Sync):**
* *Cloud & Infra:* AWS, GCP, Azure, Cloudflare (already partially wired via Bouncer/DNS), HashiCorp (Vault/Terraform)
* *Code & CI/CD:* GitHub, GitLab, Bitbucket, CircleCI, GitHub Actions (`action` / CLI gate)
* *Observability & SIEM:* Prometheus/Loki/Grafana (native), Datadog, Splunk, Syslog/CEF, OpenTelemetry OTLP exporter
* *Identity & Ticketing:* Okta, Google Workspace, Microsoft Entra ID, Jira, Asana, Linear, Slack, Microsoft Teams, PagerDuty, Generic Signed Webhooks

---

### 3.7 `Aeon Edge` Counterpart — **`SMSLY Edge Gateway`** (Software + Optional Hardware Appliance)

Aeon markets **Aeon Edge** as an on-premise network protection appliance for device discovery, unauthorized egress blocking, and instant compromised-device quarantine.

`smsly-hosting` can match **and beat** `Aeon Edge` by shipping a lightweight **Linux / ARM64 / x86_64 edge image** (`smsly-edge-agent` ISO / Debian package / Docker Compose appliance profile) built on proven open-source components:

1. **Zero-Touch Network Discovery & Fingerprinting:** Passive ARP/DHCP/mDNS/LLDP + Zeek/Suricata telemetry identifying laptops, servers, IoT, IP cameras, and **OT/ICS protocols** (Modbus, DNP3, BACnet telemetry inspection).
2. **Inline Firewall & DLP/Egress Enforcement:** WireGuard/BGP/bridge inline gateway using **Suricata + eBPF XDP/nftables + CoreDNS sinkholing** to inspect suspicious payloads and block unauthorized C2 or sensitive data egress in sub-millisecond hardware/software paths.
3. **Instant Micro-Quarantine:** Automated 802.1X/RADIUS CoA, switch ACL push, VLAN quarantine, or local ARP/nftables isolation cleanly cutting off compromised devices while preserving critical traffic.
4. **Hardware & Air-Gapped Readiness:** Runs equally well on **off-the-shelf mini-PCs/industrial DIN-rail gateways (Protectli / Raspberry Pi 5 CM / x86 appliances)**, cloud VPC bastion gateways, or air-gapped sovereign networks — syncing policy & DFIR bundles back to `smsly-hosting` over SPIFFE mTLS / WireGuard without proprietary hardware lock-in.

---

## 4. Phased Execution Sequence (Low Risk, High Leverage)

1. **Phase 1 — Unified Data & Supply-Chain Foundation (Weeks 1–6):**
   - Introduce `SecurityAsset`, `SecurityFinding`, and `ComplianceControlStatus` models + async Celery ingestion workers.
   - Add **Syft (SBOM)**, **Semgrep (SAST)**, **Gitleaks (Secrets)**, and **Trivy IaC/Image JSON ingestion** inside ephemeral build sandboxes ([builder.py](file:///c:/Users/osare/Documents/smsly-hosting/backend/apps/cloud/services/builder.py)).
   - Add **SLSA/Cosign enforcement gate** + automated post-deploy **Nuclei (DAST)** baseline check.
2. **Phase 2 — Compliance Engine (SOC 2 / ISO 27001 / NDPR) & EASM (Weeks 7–12):**
   - Map live technical telemetry (`verify_platform_integrity.sh`, `AuditLog`, Postgres TLS, mTLS, 2FA, backup encryption/restore tests) to SOC 2 / ISO 27001 / NDPR controls with continuous pass/fail percentages and signed auditor ZIP/PDF exports.
   - Ship External Attack Surface Monitoring (CT logs, DNS/TLS grading, exposed port checks) + Jira/Slack/Splunk/Datadog/Okta connectors.
3. **Phase 3 — Risk Fusion, MITRE ATT&CK XDR, DFIR & Active Playbooks (Weeks 13–18):**
   - Tag Falco/CrowdSec/WAF/AuditLog events with MITRE ATT&CK techniques; build interactive Topology + Attack Path graph + Risk Fusion scoring.
   - Add guardrailed Active Response playbooks + 1-click signed DFIR capture bundles + `SMSLY Security AI` auto-fix PR workflow.
4. **Phase 4 — Multi-Cloud CNAPP (`CSPM`/`KSPM`/`CIEM`/`DSPM`/`AI-SPM`), TPRM & `SMSLY Edge` (Weeks 19–28):**
   - Read-only AWS/GCP/Azure/K8s CSPM/CIEM/KSPM connectors + DSPM & AI-SPM posture checks.
   - Vendor/TPRM portal + downloadable `SMSLY Edge` appliance package (Suricata/Zeek + CoreDNS + nftables + WireGuard telemetry forwarder).

---

## 5. Key Differentiation Summary (Why This Beats Aeon)

* **Enforcement vs. Alert Fatigue:** Most security platforms stop at alerting you that a container or database is misconfigured; `smsly-hosting` actually runs the sandboxing (`runsc`/`kata`), mTLS (`SPIRE`), WAF (`Coraza`/`open-appsec`), volume encryption (`LUKS`/`fscrypt`), and DB TLS (`hostssl`), and can remediate drift live.
* **Open Standard Interoperability:** Uses CycloneDX/SPDX SBOMs, SLSA in-toto attestations, OCSF-compatible schemas, MITRE ATT&CK mappings, and standard Linux/cloud primitives—avoiding closed proprietary lock-in.
* **Single Sovereign Control Plane:** Operators run both application infrastructure **and** full-stack cyber defence + compliance evidence generation from one self-hostable stack across AWS, UpCloud, bare metal, or air-gapped sovereign DCs.
