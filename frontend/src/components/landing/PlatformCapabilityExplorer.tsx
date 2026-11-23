"use client";

import { useState } from "react";
import Link from "next/link";
import {
  ArrowUpRight,
  Terminal,
} from "lucide-react";

interface CapabilitySubItem {
  title: string;
  spec: string;
  detail: string;
}

interface CapabilityDomain {
  id: string;
  index: string;
  shortTitle: string;
  title: string;
  tagline: string;
  summary: string;
  docHref: string;
  docLabel: string;
  secondaryHref?: string;
  secondaryLabel?: string;
  metrics: { label: string; value: string }[];
  capabilities: CapabilitySubItem[];
  terminalTitle: string;
  terminalLines: string[];
}

const CAPABILITY_DOMAINS: CapabilityDomain[] = [
  {
    id: "deployments",
    index: "01",
    shortTitle: "5 Deploy Engines & Canvas",
    title: "5 Workload Engines, Visual Topology Canvas & Zero-Downtime Promotion",
    tagline: "DEPLOY ANYTHING — FROM GIT REPOS TO SERVERLESS FUNCTIONS & MULTI-REPO ECOSYSTEMS",
    summary:
      "Every workload runs inside an isolated build and runtime pipeline with automatic framework detection, ephemeral BuildKit sandboxes, intelligent healthcheck path probing, and instant blue-green rollback.",
    docHref: "/docs/deployments",
    docLabel: "Deployment Pipeline Docs",
    secondaryHref: "/store",
    secondaryLabel: "Browse 100+ App Store Blueprints",
    metrics: [
      { label: "Workload Modes", value: "Git · Docker · Compose · Upload · Functions" },
      { label: "Build Isolation", value: "Ephemeral BuildKit + Filtered Socket Proxy" },
      { label: "Cutover Latency", value: "0ms Blue-Green + Auto Health Probe" },
      { label: "Local Caches", value: "Registry :5000 · Mirror · Verdaccio · apt-cacher" },
    ],
    capabilities: [
      {
        title: "Git Push-to-Deploy & PR Previews",
        spec: "GitHub App · GitLab · Bitbucket · Nixpacks",
        detail:
          "Auto-detects Next.js, Django, Go, Rust, Rails, Laravel, and 30+ stacks. Every Pull Request spins up an isolated preview domain and sleeps via Sablier when idle.",
      },
      {
        title: "Docker Compose & Multi-Repo Ecosystem Wave Planner",
        spec: "Topological Dependency Graph · Wave Rollout",
        detail:
          "Scans multi-service repositories, provisions shared databases/brokers in Wave 1, injects scoped network aliases, and promotes dependent apps in topological order.",
      },
      {
        title: "Serverless Functions & Monaco Studio",
        spec: "In-Browser IDE · SSRF & DNS-Rebinding Guard",
        detail:
          "Author and deploy event or HTTP functions directly in the browser with resolve-then-check runtime protections blocking RFC 1918 and cloud metadata IPs.",
      },
      {
        title: "Interactive 2D/3D Service Topology & Project Canvas",
        spec: "ReactFlow + Three.js 3D Force Graph",
        detail:
          "Visualize live traffic edges, attach addons, inspect container bridges, and trigger one-click environment refreshes without losing dynamic PORT or healthcheck configs.",
      },
    ],
    terminalTitle: "pipeline://build-and-promote (ephemeral buildkit)",
    terminalLines: [
      "$ smsly deploy --service api-gateway --strategy blue-green",
      "[BUILDKIT] Spawned isolated builder (custom DNS + zero control-plane env leak)",
      "[NIXPACKS] Detected Python 3.11 + Gunicorn/Uvicorn + PostgreSQL addon",
      "[CACHE]    Hit local registry:5000 mirror & wheel cache (build time: 11.2s)",
      "[PROBE]    Candidate health path /api/health answered HTTP 200 (14ms)",
      "[PROMOTE]  Attached project bridge + platform bridge aliases (0ms cutover)",
      "[RELEASE]  api-gateway@v52 promoted live — previous container drained cleanly",
    ],
  },
  {
    id: "cryptd",
    index: "02",
    shortTitle: "Opt-In Encryption (grid-cryptd)",
    title: "Opt-In Per-Service Cryptographic Control Plane & Wire-Protocol Gates",
    tagline: "EXTERNAL ROOT VAULT + RAM-ONLY DEK KEYSTORE + POSTGRES/REDIS POINT-OF-CONTACT GATES",
    summary:
      "Choose exactly which services or addons require hardware-grade encryption at rest. Keys live exclusively in mlock() RAM derived from your external Root Vault—if a VPS disk is snapshotted or stolen, attackers get unreadable ciphertext.",
    docHref: "/docs/security",
    docLabel: "Cryptographic Architecture Docs",
    secondaryHref: "/docs/install",
    secondaryLabel: "Configure External Vault",
    metrics: [
      { label: "Vault Providers", value: "HTTP Root Vault · AWS KMS · GCP KMS · HashiCorp Vault" },
      { label: "Key Hierarchy", value: "HKDF-SHA256 (Root KEK → Project → Service → Table/Epoch)" },
      { label: "Wire Gates", value: "PostgreSQL (:15432) & Redis (:16379) Fail-Closed Proxies" },
      { label: "Disk & Env Crypto", value: "LUKS2 DM-Crypt Volumes + SOPS & age Recipients" },
    ],
    capabilities: [
      {
        title: "RAM-Only mlock() Keystore & External Root Vault",
        spec: "Zero Disk Persistence · Heartbeat Lease Renewal",
        detail:
          "grid-cryptd fetches the root KEK from your external vault at boot and pins derived DEKs in non-swappable RAM. When a lease expires or is revoked, keys are cryptographically zeroed.",
      },
      {
        title: "PostgreSQL (:15432) & Redis (:16379) Point-of-Contact Gates",
        spec: "Live Lease Verification · Instant Connection Severing",
        detail:
          "Opted-in containers connect through grid-cryptd wire gates via host.docker.internal. Locking a service immediately blocks new handshakes and severs active database/cache streams.",
      },
      {
        title: "Epoch Key Rotation & Envelope AEAD",
        spec: "ChaCha20-Poly1305 & AES-256-GCM · Grace Window",
        detail:
          "Rotate service encryption epochs on a schedule or on-demand. Historical epochs remain decryptable during the configurable grace window while all new writes use the latest epoch key.",
      },
      {
        title: "LUKS2 Encrypted Volumes & SOPS+age Secret Isolation",
        spec: "aes-xts-plain64 Loop Volumes · Build-Time Redaction",
        detail:
          "Persistent service and addon volumes mount over LUKS2 DM-Crypt devices, while environment variables support per-service age keys and strict build-log secret redaction.",
      },
    ],
    terminalTitle: "grid-cryptd://lease-and-wire-gate (:9105 / :15432 / :16379)",
    terminalLines: [
      "$ curl -H 'X-Cryptd-Secret: ***' http://127.0.0.1:9105/v1/status",
      '{"enabled":true,"vault_provider":"http_vault","epoch":42,"mlock":true,"keys_in_ram":18}',
      "[KEYSTORE] Derived HKDF-SHA256 DEK for service=svc_9f3a (project=prj_core, epoch=42)",
      "[VOLUME]   Mounted LUKS2 encrypted volume /var/lib/smsly/encrypted-volumes/svc_9f3a.img",
      "[PG-GATE]  127.0.0.1:15432 → verified active lease for svc_9f3a → upstream PgCat :6432",
      "[REDIS]    127.0.0.1:16379 → verified active lease for svc_9f3a → upstream Sentinel :6379",
      "[ROTATE]   Epoch #42 → #43 rotation complete (grace window: 24h, 0 dropped queries)",
    ],
  },
  {
    id: "cnapp",
    index: "03",
    shortTitle: "CNAPP, ASPM, XDR & AI-SPM",
    title: "Built-In Cloud-Native Application Protection Platform (CNAPP) & XDR",
    tagline: "PRE-BUILD ASPM + CYCLONEDX SBOM + SLSA v1.0 + FALCO eBPF XDR + COMPLIANCE ENGINE",
    summary:
      "Security isn't an external SaaS bolt-on. Grid embeds code-to-runtime security posture management, supply-chain attestation, kernel syscall detection, ML WAF shadow testing, and continuous compliance auditing in one control plane.",
    docHref: "/docs/security",
    docLabel: "11-Layer Security Specification",
    secondaryHref: "/status",
    secondaryLabel: "Inspect Host Security Status",
    metrics: [
      { label: "Pre-Build ASPM", value: "Secret Scanner · IaC Audit (K8s/Docker) · SAST" },
      { label: "Supply Chain", value: "CycloneDX v1.5 SBOM · SLSA v1.0 · Cosign Signing" },
      { label: "Runtime CWPP/XDR", value: "gVisor/Kata · Falco eBPF · CrowdSec · open-appsec WAF" },
      { label: "Compliance & AI", value: "SOC 2 · ISO 27001 · PCI-DSS · HIPAA · GDPR + AI-SPM" },
    ],
    capabilities: [
      {
        title: "ASPM Gate: Secret, IaC & SAST Scanning",
        spec: "Blocks Committed Keys & Privileged Containers",
        detail:
          "Scans source trees, test directories, Dockerfiles, Compose files, and Kubernetes manifests before build—blocking leaked Stripe/AWS/DB credentials or host-namespace escapes.",
      },
      {
        title: "CycloneDX SBOM Inventory & SLSA v1.0 Provenance",
        spec: "Searchable Package Graph · Sigstore Cosign",
        detail:
          "Every build generates a searchable CycloneDX SBOM across npm, PyPI, Go, Cargo, and OS packages, paired with a signed SLSA v1.0 provenance attestation verified at deploy.",
      },
      {
        title: "OCSF XDR Correlation, Falco eBPF & open-appsec WAF",
        spec: "Kernel Syscall Telemetry · Shadow Parity WAF",
        detail:
          "Correlates Falco container drift alerts, CrowdSec IP decisions, fail2ban jails, and open-appsec ML WAF shadow-mode parity checks into standardized OCSF security findings.",
      },
      {
        title: "AI-SPM Prompt Shield & 5-Framework Compliance Engine",
        spec: "PII/Key Redaction · Continuous Control Checks",
        detail:
          "Inspects and redacts secrets or prompt-injection payloads before calling external LLMs, and evaluates live technical controls against SOC 2, ISO 27001, PCI-DSS, HIPAA, and GDPR.",
      },
    ],
    terminalTitle: "security-center://aspm-sbom-xdr-audit",
    terminalLines: [
      "[ASPM]     Scanned 412 files: 0 hardcoded secrets, 0 privileged IaC rules, 0 SAST blockers",
      "[SBOM]     Generated CycloneDX 1.5 artifact (148 packages indexed, purl queryable)",
      "[SLSA]     Signed SLSA v1.0 provenance + Cosign digest sha256:9b15f3f3a2dbf9a7...",
      "[SANDBOX]  Workload isolated in gVisor (runsc) user-space kernel + seccomp profile",
      "[XDR/OCSF] Falco eBPF: 0 unexpected execve/ptrace syscalls · CrowdSec bouncer synced",
      "[AI-SPM]   Redacted 2 credential tokens from diagnostic prompt prior to LLM dispatch",
      "[COMPLY]   SOC 2 (100%) · ISO 27001 (98%) · PCI-DSS v4 (97%) · HIPAA (98%) · GDPR (100%)",
    ],
  },
  {
    id: "data-plane",
    index: "04",
    shortTitle: "40+ Addons, HA DBs & Transfers",
    title: "High-Availability Data Plane, 40+ Managed Addons & Live Node Transfers",
    tagline: "PATRONI POSTGRESQL HA + PGCAT + PGBOUNCER TENANTS + REDIS SENTINEL + LIVE MIGRATIONS",
    summary:
      "Provision production databases, vector stores, message brokers, and AI inference engines in one click. Enjoy automated streaming replication, connection pooling, slow-query diagnostics, encrypted S3 backups, and zero-downtime server transfers.",
    docHref: "/docs/addons",
    docLabel: "Managed Addons & HA Guide",
    secondaryHref: "/docs/transfers",
    secondaryLabel: "Zero-Downtime Transfer Docs",
    metrics: [
      { label: "PostgreSQL HA", value: "Patroni Primary/Replica + PgCat + PgBouncer Tenants" },
      { label: "Redis HA", value: "3-Node Redis Sentinel Quorum + Replica Promotion" },
      { label: "Catalog Size", value: "40+ Engines (Vector, SQL, Queue, S3, Auth, AI)" },
      { label: "Mobility", value: "Encrypted S3/R2 Backups + Live Cross-VPS Transfer" },
    ],
    capabilities: [
      {
        title: "PostgreSQL HA with PgCat & Multi-Tenant PgBouncer",
        spec: "Streaming Replication · Read/Write Split · Slow Query UI",
        detail:
          "Automated Patroni failover, PgCat read/write query routing, per-tenant logical database pooling via pgbouncer-tenants, and a built-in pg_stat_statements Slow Query Analyzer.",
      },
      {
        title: "Redis Sentinel HA & High-Throughput Brokers",
        spec: "3-Sentinel Quorum · RabbitMQ · Kafka · NATS",
        detail:
          "Sub-second cache and Celery broker failover with 3 Sentinel nodes, plus one-click RabbitMQ, Kafka, and NATS clusters wired directly into your project's private bridge.",
      },
      {
        title: "Vector Databases & Self-Hosted AI Inference Addons",
        spec: "Qdrant · Milvus · Weaviate · pgvector · Ollama · vLLM",
        detail:
          "Run RAG and agent workloads on your own metal with pre-configured vector stores and local LLM servers—complete with automatic internal DNS aliases and gVisor /etc/hosts sync.",
      },
      {
        title: "Encrypted Backups & Zero-Downtime Server Transfers",
        spec: "HKDF Envelope Backups · Live SSH Cutover & Rollback",
        detail:
          "Schedule automated snapshots to S3, Cloudflare R2, or MinIO, and migrate live stateful services between any two VPS or cloud nodes with pre-transfer snapshots and DNS cutover.",
      },
    ],
    terminalTitle: "data-plane://ha-topology-and-live-transfer",
    terminalLines: [
      "[POSTGRES] smsly-postgres-primary (streaming) → smsly-postgres-replica (lag: 0.18ms)",
      "[POOLER]   pgcat (:6432) healthy · pgbouncer-tenants pooled 24 logical app databases",
      "[REDIS-HA] sentinel-1, sentinel-2, sentinel-3 quorum=OK (master: redis-primary:6379)",
      "[ADDONS]   Provisioned qdrant-vector + rabbitmq-bus on scoped bridge smsly-net-96e85eee",
      "[BACKUP]   Encrypted dump pg_prod_20261123.enc uploaded to s3://grid-vault (SHA256 OK)",
      "[TRANSFER] Live transfer svc=billing-api from eu-west-1 → us-east-1 completed (0 dropped reqs)",
    ],
  },
  {
    id: "mesh-edge",
    index: "05",
    shortTitle: "WireGuard Mesh, SPIRE & Edge",
    title: "Zero-Trust WireGuard Mesh, SPIFFE/SPIRE mTLS, FRP Tunnels & Scale-to-Zero",
    tagline: "MULTI-SERVER WIREGUARD + COREDNS + ENVOY SPIFFE SIDECARS + SABLIER SCALE-TO-ZERO",
    summary:
      "Connect servers across Hetzner, OVH, DigitalOcean, AWS, or on-prem into a single encrypted mesh. Workloads authenticate via cryptographic SPIFFE IDs while idle services sleep at 0MB RAM and wake on the first HTTP packet.",
    docHref: "/docs/multi-server",
    docLabel: "Multi-Server & Mesh Docs",
    secondaryHref: "/compare",
    secondaryLabel: "Compare vs Kubernetes & Managed PaaS",
    metrics: [
      { label: "Overlay Network", value: "WireGuard VPN (10.100.0.0/24) + CoreDNS Split-Horizon" },
      { label: "Workload Identity", value: "SPIFFE/SPIRE X.509 SVIDs (2 Trust Domains) + Envoy" },
      { label: "Edge & Tunnels", value: "Caddy + Traefik Dual Edge + Built-In FRP (frps)" },
      { label: "Efficiency", value: "Sablier Scale-to-Zero + napd Cooperative Tier Sleep" },
    ],
    capabilities: [
      {
        title: "WireGuard VPN Mesh & CoreDNS Service Discovery",
        spec: "HMAC-SHA256 Node Attestation · <service>.mesh.local",
        detail:
          "New nodes join via challenge-response cryptographic attestation. CoreDNS dynamically updates internal zones without restarts so containers resolve peers across regions.",
      },
      {
        title: "SPIFFE / SPIRE Workload mTLS & Envoy Sidecars",
        spec: "Platform & Ecosystem Trust Domains · Auto Rotation",
        detail:
          "Issues short-lived X.509 SVID certificates to workloads and Envoy sidecars, automatically selecting compatible kernel loopback runtimes and verifying SAN URIs before expiry.",
      },
      {
        title: "Dual-Edge Caddy + Traefik & Built-In FRP Tunnels",
        spec: "On-Demand Wildcard ACME TLS · Private Webhook Tunnels",
        detail:
          "Caddy handles on-demand wildcard TLS and WAF filtering while Traefik routes dynamic container labels, paired with a custom route-fallback UI and built-in FRP tunnel server.",
      },
      {
        title: "Sablier Scale-to-Zero & napd Resource Efficiency",
        spec: "0MB Idle Containers · Wake-on-Request + KSM",
        detail:
          "Preview environments and low-traffic microservices pause automatically when idle and wake transparently on incoming HTTP traffic, while Kernel Samepage Merging deduplicates RAM.",
      },
    ],
    terminalTitle: "mesh-control://wireguard-spire-sablier",
    terminalLines: [
      "[ATTEST]   Node eu-warsaw-03 verified via HMAC-SHA256 challenge → assigned 10.100.0.14",
      "[COREDNS]  Updated mesh.hosts zone (11 active records, reload-free hosts plugin)",
      "[SPIRE]    Minted X.509 SVID spiffe://ecosystem.smsly/ns/prod/sa/checkout (TTL: 1h)",
      "[ENVOY]    Sidecar envoy-checkout active (mTLS strict, upstream TLS verified)",
      "[CADDY]    On-demand TLS cert active for *.apps.trulay.cloud (caddy_logs UID 1000 OK)",
      "[SABLIER]  Woke sleeping service preview-pr-142 in 164ms on first GET request",
    ],
  },
  {
    id: "ai-mcp",
    index: "06",
    shortTitle: "AI Senate, Jules & MCP Server",
    title: "17-Provider AI Senate Committee, Autonomous Jules Remediation & MCP Server",
    tagline: "MULTI-MODEL DELIBERATION + GOOGLE JULES AUTO-FIX PR LOOP + NATIVE /MCP SERVER",
    summary:
      "Go beyond single-model chat widgets. Grid convenes an AI Senate of 2+ models to deliberate on infrastructure changes, dispatches Google Jules to open fix Pull Requests when builds or containers fail, and exposes a native MCP server for your AI agents.",
    docHref: "/docs/ai",
    docLabel: "AI Senate & Remediation Docs",
    secondaryHref: "/docs/intelligence",
    secondaryLabel: "Predictive Intelligence Guide",
    metrics: [
      { label: "AI Senate Pool", value: "17+ Providers (Claude, GPT-4o, Gemini, DeepSeek, Ollama)" },
      { label: "Consensus Flow", value: "Propose → Peer Review → Chair Synthesis + Cost Caps" },
      { label: "Code Auto-Fix", value: "Log Watchdog → Root Cause → Google Jules Fix PR" },
      { label: "Agent Protocol", value: "Native MCP Server (/mcp) + 3-Engine Autoscaler" },
    ],
    capabilities: [
      {
        title: "Multi-Model AI Senate Committee",
        spec: "17+ LLM Providers · Per-User Token & Budget Caps",
        detail:
          "Critical architecture reviews, security remediation plans, and scaling decisions are debated across multiple independent models before a Chair model synthesizes the final action.",
      },
      {
        title: "Autonomous Log Watchdog & Google Jules Auto-PR Loop",
        spec: "Crash-Loop Detection · Automated GitHub Pull Requests",
        detail:
          "When a deployment fails or a service enters a runtime crash burst, Grid captures the separated runtime_logs, diagnoses the stack trace, and triggers Jules to open a patched PR.",
      },
      {
        title: "Built-In Model Context Protocol (MCP) Server",
        spec: "Claude Desktop · Cursor · Antigravity · Custom Agents",
        detail:
          "Connect any MCP-compatible AI agent directly to your Grid cluster with scoped API credentials to inspect logs, query metrics, provision addons, and trigger safe rollouts.",
      },
      {
        title: "3-Engine Predictive Autoscaler",
        spec: "CPU/RAM Hysteresis + Prometheus/Loki Anomaly Lookahead",
        detail:
          "Combines classic resource thresholds with Prometheus latency/queue trends and AI traffic spike forecasting to scale replicas horizontally or vertically before saturation.",
      },
    ],
    terminalTitle: "ai-senate://deliberation-jules-and-mcp",
    terminalLines: [
      "[WATCHDOG] Detected 5xx burst on service=checkout-api (KeyError: 'STRIPE_WEBHOOK_TOLERANCE')",
      "[SENATE]   Convened Claude 3.7 Sonnet + Gemini 2.5 Pro + DeepSeek-R1 (consensus: 0.96)",
      "[JULES]    Session jules_881a created → opened PR #119 with env fallback & unit test",
      "[AUTOSCALE] Engine 2 (Prometheus anomaly) scaled checkout-api 2 → 4 replicas ahead of spike",
      "[MCP]      Agent tool call mcp.deployments.get_service_logs(service='checkout-api') → 200 OK",
    ],
  },
  {
    id: "observability",
    index: "07",
    shortTitle: "Observability, Teams & Reseller",
    title: "Full-Stack Observability, Multi-Tenant RBAC, Cost Analytics & Reseller Cloud",
    tagline: "PROMETHEUS + LOKI LOG BRIDGE + GRAFANA + ALERTMANAGER + TEAMS RBAC + WHITE-LABEL",
    summary:
      "Everything required to run an internal developer platform or your own commercial hosting business: pre-wired metrics, log-to-alert correlation, granular team permissions, immutable audit trails, P&L cost breakdowns, and white-label reseller billing.",
    docHref: "/docs/api",
    docLabel: "REST API & Operations Reference",
    secondaryHref: "/pricing",
    secondaryLabel: "Self-Hosted vs Enterprise Licensing",
    metrics: [
      { label: "Metrics & Dashboards", value: "Prometheus + Grafana + cAdvisor + Node/PG Exporters" },
      { label: "Log Intelligence", value: "Loki + Promtail + Loki Log Bridge → Alertmanager" },
      { label: "Governance", value: "Organizations · Teams RBAC · Immutable Audit Logs" },
      { label: "Commercial Mode", value: "Cost & P&L Analytics · White-Label Reseller Portal" },
    ],
    capabilities: [
      {
        title: "Prometheus, Grafana & Dynamic Target Discovery",
        spec: "Auto-Registered Local & Remote WireGuard Nodes",
        detail:
          "Every container and remote mesh node is automatically registered into Prometheus targets with docker-labels, cAdvisor, node-exporter, and PostgreSQL exporter dashboards.",
      },
      {
        title: "Loki Log Bridge & Deduplicated Alertmanager Routing",
        spec: "Real-Time Crash Correlation ·Per-Status Cooldowns",
        detail:
          "Streams container stdout/stderr into Loki while the Loki Log Bridge correlates fatal error bursts into Alertmanager notifications across Slack, Discord, Email, and webhooks.",
      },
      {
        title: "Separated Build vs Runtime Crash Logs & Web SSH Console",
        spec: "Dedicated runtime_logs Field · Xterm.js Browser Shell",
        detail:
          "Build output and post-start runtime crash logs live in dedicated database fields—paired with a live streaming log viewer and one-click browser terminal into any container.",
      },
      {
        title: "Multi-Tenant Organizations, RBAC, Cost P&L & Reseller Mode",
        spec: "Fine-Grained Permissions · White-Label Hosting",
        detail:
          "Manage multiple organizations and teams with role-based access control, track per-service infrastructure cost and P&L margins, or offer white-label hosting to your own clients.",
      },
    ],
    terminalTitle: "observability://prometheus-loki-alertmanager-rbac",
    terminalLines: [
      "[TARGETS]  Wrote 1 local + 2 remote docker-labels, cadvisor, and node-exporter targets",
      "[LOKI]     loki-log-bridge streaming 47 containers → 0 fatal crash bursts in 15m window",
      "[ALERTS]   Alertmanager healthy · per-(service, status) escalation cooldown active",
      "[AUDIT]    Recorded event SERVICE_ENV_UPDATED actor=osare@trulay.cloud ip=89.58.45.52",
      "[FINOPS]   Cluster monthly compute: $34.00 across 3 VPS nodes (estimated AWS equiv: $410/mo)",
    ],
  },
];

export function PlatformCapabilityExplorer() {
  const [activeId, setActiveId] = useState<string>(CAPABILITY_DOMAINS[0].id);
  const active = CAPABILITY_DOMAINS.find((d) => d.id === activeId) || CAPABILITY_DOMAINS[0];

  return (
    <section
      id="features"
      aria-labelledby="capabilities-heading"
      className="py-20 md:py-28 scroll-mt-16 border-b border-slate-200 dark:border-slate-800"
    >
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        {/* Section Editorial Header (Left-aligned asymmetric hierarchy) */}
        <div className="flex flex-col lg:flex-row lg:items-end justify-between gap-6 pb-10 mb-10 border-b border-slate-200 dark:border-slate-800">
          <div className="max-w-3xl">
            <div className="text-xs font-mono font-bold uppercase tracking-widest text-emerald-700 dark:text-emerald-400 mb-3">
              COMPLETE PLATFORM ARCHITECTURE · 7 ENGINEERING DOMAINS
            </div>
            <h2
              id="capabilities-heading"
              className="text-3xl sm:text-4xl lg:text-5xl font-extrabold text-slate-950 dark:text-white tracking-tight leading-tight"
            >
              Every layer of modern cloud infrastructure. Zero black boxes.
            </h2>
          </div>
          <p className="text-sm sm:text-base text-slate-600 dark:text-slate-300 max-w-md font-medium leading-relaxed">
            Select any subsystem below to inspect its exact runtime mechanics, protocols, security guarantees, and documentation.
          </p>
        </div>

        {/* Master-Detail Workbench: 4-Col Domain Rail + 8-Col Deep Technical Spec */}
        <div className="grid lg:grid-cols-12 gap-8 items-start">
          {/* LEFT 4 COLS: Interactive Domain Selector Rail */}
          <div
            className="lg:col-span-4 flex flex-col gap-2"
            role="tablist"
            aria-label="Platform capability domains"
          >
            {CAPABILITY_DOMAINS.map((domain) => {
              const isSelected = domain.id === active.id;
              return (
                <button
                  key={domain.id}
                  type="button"
                  role="tab"
                  aria-selected={isSelected}
                  aria-controls={`panel-${domain.id}`}
                  id={`tab-${domain.id}`}
                  onClick={() => setActiveId(domain.id)}
                  className={`group w-full text-left p-4 rounded-xl border transition-all min-h-[56px] flex items-center justify-between gap-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                    isSelected
                      ? "bg-slate-900 text-white border-slate-900 dark:bg-emerald-500/15 dark:border-emerald-500/50 shadow-md"
                      : "bg-card text-slate-800 dark:text-slate-200 border-border hover:border-emerald-500/40"
                  }`}
                >
                  <div className="flex items-center gap-3.5 min-w-0">
                    <span
                      className={`font-mono text-xs font-bold px-2 py-1 rounded ${
                        isSelected
                          ? "bg-emerald-500/20 text-emerald-300"
                          : "bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400"
                      }`}
                    >
                      {domain.index}
                    </span>
                    <span className="font-bold text-sm sm:text-base truncate">
                      {domain.shortTitle}
                    </span>
                  </div>
                  <span
                    className={`h-2 w-2 rounded-full shrink-0 ${
                      isSelected
                        ? "bg-emerald-400"
                        : "bg-slate-300 dark:bg-slate-700 group-hover:bg-emerald-500/60"
                    }`}
                    aria-hidden="true"
                  />
                </button>
              );
            })}
          </div>

          {/* RIGHT 8 COLS: Selected Domain Deep Architectural Specification */}
          <div
            id={`panel-${active.id}`}
            role="tabpanel"
            aria-labelledby={`tab-${active.id}`}
            className="lg:col-span-8 rounded-2xl border border-slate-200 dark:border-slate-800 bg-card overflow-hidden shadow-sm"
          >
            {/* Domain Header Banner */}
            <div className="p-6 sm:p-8 border-b border-slate-200 dark:border-slate-800 bg-slate-50/70 dark:bg-slate-900/50">
              <div className="text-xs font-mono font-bold text-emerald-700 dark:text-emerald-400 uppercase tracking-wider mb-2">
                DOMAIN {active.index} · {active.tagline}
              </div>
              <h3 className="text-2xl sm:text-3xl font-extrabold text-slate-950 dark:text-white tracking-tight mb-3">
                {active.title}
              </h3>
              <p className="text-sm sm:text-base text-slate-600 dark:text-slate-300 leading-relaxed font-medium">
                {active.summary}
              </p>

              {/* Key Technical Specs Bar */}
              <div className="mt-6 grid sm:grid-cols-2 gap-3">
                {active.metrics.map((m) => (
                  <div
                    key={m.label}
                    className="px-3.5 py-2.5 rounded-lg bg-white dark:bg-slate-950 border border-slate-200/90 dark:border-slate-800 flex flex-col justify-between"
                  >
                    <span className="text-xs font-mono text-slate-500 dark:text-slate-400">
                      {m.label}
                    </span>
                    <span className="text-xs sm:text-sm font-bold text-slate-900 dark:text-slate-100 mt-0.5 font-mono">
                      {m.value}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            {/* 4 Sub-Capabilities Ledger (2x2 Architectural Hairline Grid) */}
            <div className="grid md:grid-cols-2 divide-y md:divide-y-0 md:divide-x divide-slate-200 dark:divide-slate-800 border-b border-slate-200 dark:border-slate-800">
              <div className="divide-y divide-slate-200 dark:divide-slate-800">
                {active.capabilities.slice(0, 2).map((cap) => (
                  <div key={cap.title} className="p-6">
                    <div className="flex items-center justify-between gap-2 mb-1.5">
                      <h4 className="text-base font-bold text-slate-950 dark:text-white">
                        {cap.title}
                      </h4>
                    </div>
                    <div className="text-xs font-mono font-semibold text-emerald-700 dark:text-emerald-400 mb-2">
                      {cap.spec}
                    </div>
                    <p className="text-sm text-slate-600 dark:text-slate-400 leading-relaxed">
                      {cap.detail}
                    </p>
                  </div>
                ))}
              </div>

              <div className="divide-y divide-slate-200 dark:divide-slate-800">
                {active.capabilities.slice(2, 4).map((cap) => (
                  <div key={cap.title} className="p-6">
                    <div className="flex items-center justify-between gap-2 mb-1.5">
                      <h4 className="text-base font-bold text-slate-950 dark:text-white">
                        {cap.title}
                      </h4>
                    </div>
                    <div className="text-xs font-mono font-semibold text-emerald-700 dark:text-emerald-400 mb-2">
                      {cap.spec}
                    </div>
                    <p className="text-sm text-slate-600 dark:text-slate-400 leading-relaxed">
                      {cap.detail}
                    </p>
                  </div>
                ))}
              </div>
            </div>

            {/* Live Subsystem Telemetry / Terminal Proof */}
            <div className="bg-[#0a0f1d] text-slate-200 p-5 sm:p-6 font-mono text-xs">
              <div className="flex flex-wrap items-center justify-between gap-2 pb-3 mb-3 border-b border-slate-800">
                <span className="flex items-center gap-2 text-slate-300 font-bold">
                  <Terminal className="w-3.5 h-3.5 text-emerald-400" aria-hidden="true" />
                  {active.terminalTitle}
                </span>
                <span className="text-emerald-400">VERIFIED RUNTIME TRACE</span>
              </div>
              <div className="space-y-1.5 overflow-x-auto leading-relaxed">
                {active.terminalLines.map((line) => (
                  <div key={line} className="whitespace-pre text-slate-300">
                    {line}
                  </div>
                ))}
              </div>
            </div>

            {/* Action Footer */}
            <div className="px-6 py-4 bg-slate-50 dark:bg-slate-900/70 border-t border-slate-200 dark:border-slate-800 flex flex-wrap items-center justify-between gap-3">
              <Link
                href={active.docHref}
                className="inline-flex items-center gap-1.5 min-h-[44px] px-4 py-2 rounded-lg bg-emerald-700 hover:bg-emerald-800 text-white text-xs sm:text-sm font-bold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <span>{active.docLabel}</span>
                <ArrowUpRight className="w-4 h-4" aria-hidden="true" />
              </Link>

              {active.secondaryHref && active.secondaryLabel && (
                <Link
                  href={active.secondaryHref}
                  className="inline-flex items-center gap-1.5 min-h-[44px] px-3 py-2 text-xs sm:text-sm font-bold text-slate-700 dark:text-slate-300 hover:text-emerald-700 dark:hover:text-emerald-400 transition-colors"
                >
                  <span>{active.secondaryLabel}</span>
                  <ArrowUpRight className="w-4 h-4" aria-hidden="true" />
                </Link>
              )}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
