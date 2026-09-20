# SMSLY Hosting — Features & Security (Landing Source of Truth)

> For landing-page agent. Status tags: `[LIVE]` shipped, `[PLANNED]` on roadmap.
> Tiers: **Docker OSS** = fully open-source, self-hostable. **K3s Enterprise** = free-but-closed,
> for industry/defense/banks needing scale, HA, and air-gap paperwork.

## Positioning

- Docker OSS: single-host to multi-node Compose PaaS. Code-to-cloud in minutes, auditable, sovereign.
- K3s Enterprise (free-but-closed): same UX on lightweight Kubernetes for 60+ services, HPA, multi-VPS mesh, compliance evidence packs.
- Both: no SaaS phone-home in Sovereign mode, signed SBOM+SLSA per release, NDPR-native.

## Tier matrix (summary)

| Area | Docker OSS | K3s Enterprise |
|---|---|---|
| Deploy SINGLE/COMPOSE, blue-green, rollback | [LIVE] | [LIVE] (via K8s Deployment + HPA) |
| Registry + build cache | [LIVE] | [LIVE] |
| Edge: Caddy + Traefik + on-demand TLS | [LIVE] | [LIVE] (Traefik Ingress + cert-manager) |
| Runtime isolation | [LIVE] gVisor/Kata best-effort | [LIVE] namespaces + NetworkPolicy + PDB |
| Autoscale | [LIVE] Celery reconciler | [LIVE] HPA + Karpenter/Cluster-Autoscaler [PLANNED] |
| Multi-VPS mesh | [LIVE] WireGuard/FRP + remote/agent-lite/media nodes | [LIVE] k3s + Tailscale/WireGuard + Flannel |
| Air-gap bundle | [PLANNED] | [PLANNED] signed ISO/OVA + offline DBs |
| Compliance evidence | [PLANNED] NDPR pack first | [PLANNED] SOC2/ISO/PCI/HIPAA packs |

## Platform features

### Deploy & runtime
- [LIVE] Source kinds: GIT/DOCKER/UPLOAD/TEMPLATE/FUNCTION (`service.py:189-199`).
- [LIVE] Modes SINGLE|COMPOSE with `compose_file` pinning (`service.py:609-623`).
- [LIVE] Blue-green promote, `ROLLBACK_RETAIN_DEPLOYMENTS=2`, stale-green reaper.
- [LIVE] Private `registry:5000` + pull-through `docker-mirror` + `apt-cacher-ng` + `verdaccio`.
- [LIVE] Fleet: master/node/agent-lite/media-node (`platform.py:122-134`), `allow_user_workloads`, per-project bridge subnet.
- [PLANNED] Runtime picker per service: `DOCKER` vs `KUBERNETES` (K8s: namespace per project, requests/limits, HPA 2-6, VPA, PDB, default-deny NetworkPolicy).
- [PLANNED] `docker-compose → manifest` exporter + K8s `Deployment/Service/Ingress/HPA` via `ClusterManager`.

### Networking & edge
- [LIVE] Caddy 2 (custom `caddy-dns/cloudflare`, `on_demand_tls ask`, single-writer `generate→apply`), Traefik v3.6 (`exposedbydefault=false`, rate-limit, circuit-breaker), `socket-proxy:0.1.2` least-privilege, `route-fallback` 503.
- [LIVE] Dual-home `smsly-platform-net`, per-project bridge, Cloudflare DNS-01.
- [PLANNED] K8s-native: Traefik Ingress (preinstalled in k3s) + `cert-manager` wildcard, IngressRoute per service.

### Data & ops
- [LIVE] PostgreSQL 16 (+PgCat pooler, `local-ha` replica, `patroni` etcd+Spilo+HAProxy), Redis 7 HA Sentinel, RabbitMQ broker, Celery 5.6.3 (~70 beat tasks, RedBeat).
- [LIVE] Backups encrypted (`BACKUP_REQUIRE_ENCRYPTION=true`, Fernet `FIELD_ENCRYPTION_KEY` + `_FILE`), GDPR purge, object-archive to S3.
- [LIVE] Observability: Prometheus 2.48, Grafana 11, Loki/promtail 2.9.3, Alertmanager, cAdvisor, node-exporter, Sentry, Django-Prometheus.
- [LIVE] Topology/dependency graph, `Service/ManagedServer/CloudResource` inventory, AI ops diagnosis + auto-remediation (`SCALE_UP/ROLLBACK/CLEANUP/REBUILD`).
- [PLANNED] Unified security asset inventory (SBOM-linked), risk fusion score (`exposure*exploit*reachability+business`), vuln lifecycle (ingest/dedupe/accept/suppress/verify + KEV/EPSS/SLA).

## Security

### Current [LIVE]
- WAF: open-appsec 1.1.9 + envoy-attachment 1.34.4 (shadow `:18081`, moving to prevent).
- Runtime: Falco 0.44.1 `modern_ebpf` + `falco_rules.local.yaml`, auditd (shadow/passwd/sudoers/sshd/.env/docker-exec/mount), AppArmor, seccomp, kernel sysctl, Docker daemon hardening, gVisor/Kata.
- Network/host: CrowdSec 1.7.8 + Traefik bouncer 1.7.1 + Cloudflare bouncer, UFW deny-in/allow-out, fail2ban (sshd/recidive/caddy-auth/caddy-dos).
- Identity: SPIRE 1.9.6 mTLS (single-use join-token via `docker run`), Envoy sidecar (`spiffe://...` RBAC), SSO SAML/OIDC/Google/Azure + OTP + Argon2 + Fernet fields, org/team RBAC + tenancy filters.
- Supply chain: Trivy fs/image/config blocking (`exit-code:1`, daily runtime rescan), pip-audit strict, npm audit high, Gitleaks + detect-private-key, Bandit, CycloneDX SBOM signed with Cosign keyless, Dependabot weekly, deploy-time `scan_image` + `cosign verify` fail-closed.
- Audit: immutable hash-linked `deployments_auditlog` + `/api/v1/audit-logs/`, notification audit, TLS-verify audit.
- Sovereignty: opt-in telemetry only (`smsly_telemetry_optout`, consent banner, Settings→Privacy), no GTM.

### Planned [PLANNED]
- Posture: Prowler/CloudQuery (CSPM), Kubescape/Trivy-operator/Kyverno (KSPM), Cartography (CIEM), Presidio PII (DSPM), NeMo Guardrails/Rebuff (AI-SPM).
- AppSec: Semgrep CE + CodeQL (SAST JS+Python), Checkov/KICS (IaC), ZAP + Nuclei (DAST), Grype+Syft (EPSS/KEV + OpenVEX), TruffleHog verify, DefectDojo ASPM hub.
- Provenance: SLSA generator + Rekor + Fulcio + in-toto attestations.
- XDR (self-hosted `security` profile): Wazuh manager+agents (EDR/ITDR/UEBA/device), Suricata + Zeek (NDR), Velociraptor (DFIR), TheHive+Cortex+Shuffle/n8n (SOAR), MISP + OpenCTI + IntelOwl (intel, STIX/TAXII), Sigma + ATT&CK tagging, Abuse.ch/OTX feeds.
- ASM: Subfinder + Nuclei + cert-expiry beat task (domains/IPs/certs/SaaS/shadow).
- Edge Box: media/agent-lite + Suricata + Wazuh-agent + CrowdSec + WireGuard (Aeon-Edge parity, $80 box).
- Offline AI triage via Ollama (sovereign Cortex alternative).

## Compliance & regulated industries

- [LIVE] Flags: `hipaa/gdpr/soc2` + `data_residency` (`service.py:972-986`), backup encryption + erasure, audit log.
- [PLANNED] Evidence automation: NDPR/CBN/Cybercrimes-Act pack first (Global South wedge), then SOC2/ISO27001/PCI-DSS/HIPAA control→evidence collectors from Prom/Loki/Trivy/Falco; control mapping JSON + signed export; 7-yr WORM (S3 Object Lock) + syslog RFC5424 to bank SIEM; PAN/BVN/NIN masking + DLP egress rules; HSM/PKCS#11 keys + rotation; FIPS-mode toggle; 4-eyes prod approvals + break-glass offline tokens; classification labels (`OFFICIAL/SECRET` → scheduling constraints); `SMSLY_OFFLINE=1` (kills telemetry/enroll/version checks); signed air-gap bundle + offline vuln-DB + dark-site runbooks + `support-bundle` via sneakernet; STIG/CIS SCAP pack; VDP + pen-test letter.

## OS & install

- Hosts: Ubuntu 24.04 LTS minimal (HWE kernel for eBPF/KVM, Pro FIPS/STIG/Livepatch for banks/defense). Debian 12/13 works (same apt paths) for lean air-gap.
- Containers: `bookworm-slim` builders (glibc), `alpine` runners/edge. No Ubuntu images inside.
- Modes: `--mode=master|node|agent-lite|media-node`, `--update/--wipe`, profiles default/`local-ha`/`patroni`/`medium`/`full`/`security [PLANNED]`/`build-cache`.

## CTAs for landing

- Docker OSS: `Deploy in minutes. Audit everything. Keep data home.` → Get Started / Self-Host.
- K3s Enterprise: `Same PaaS on Kubernetes. Scale to 60+ services. Pass the audit.` → Talk to Us / NDPR Pack / Air-Gap Demo.
