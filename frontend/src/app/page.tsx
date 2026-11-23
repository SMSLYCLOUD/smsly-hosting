import type { Metadata } from "next";
import Link from "next/link";
import {
  ArrowRight,
  ArrowUpRight,
  CheckCircle2,
  Database,
  FileCode,
  Globe,
  KeyRound,
  Lock,
  Network,
  Rocket,
  Server,
  Shield,
  Sparkles,
  Terminal,
} from "lucide-react";
import { EcosystemDeployVisual } from "@/components/effects/EcosystemDeployVisual";
import { HeroCarousel } from "@/components/landing/HeroCarousel";
import { MeshTopologyVisual } from "@/components/landing/MeshTopologyVisual";
import { PlatformCapabilityExplorer } from "@/components/landing/PlatformCapabilityExplorer";

export const metadata: Metadata = {
  title: "Trulay Grid — Free Open-Source Sovereign PaaS & Cryptographic Cloud",
  description:
    "Self-hosted MIT open-source sovereign PaaS. Deploy apps, serverless functions, Patroni PostgreSQL HA, Redis Sentinel, 40+ addons, opt-in grid-cryptd encryption, CNAPP/ASPM/XDR, WireGuard mesh, SPIFFE mTLS, and 17-provider AI Senate on your own VPS.",
  alternates: { canonical: "/" },
};

const DEFENSE_PIPELINE_STAGES = [
  {
    phase: "STAGE I · SUPPLY CHAIN & BUILD CONTAINMENT",
    badge: "Layers 01 – 04",
    summary:
      "Every commit or image passes through an isolated BuildKit container and pre-build ASPM gate before a single layer is pushed to the scoped registry.",
    layers: [
      {
        num: "01",
        name: "Ephemeral BuildKit & Env Isolation",
        spec: "Zero Control-Plane Secret Leak · Custom DNS",
        detail:
          "Builds execute inside throwaway docker-container BuildKit workers with sanitized process environments—preventing tenant builds from reading host or PaaS secrets.",
      },
      {
        num: "02",
        name: "Pre-Build ASPM & Trivy CVE Gate",
        spec: "Secret Scanner · Dockerfile/K8s IaC · SAST",
        detail:
          "Blocks hardcoded API keys, privileged Kubernetes/Compose manifests, dangerous deserialization patterns, and critical OS/package CVEs prior to image export.",
      },
      {
        num: "03",
        name: "CycloneDX v1.5 SBOM & SLSA v1.0 Provenance",
        spec: "Searchable Package Graph · Sigstore Cosign",
        detail:
          "Generates a queryable software bill of materials across npm, PyPI, Go, Cargo, and Debian packages and signs the image digest with Cosign.",
      },
      {
        num: "04",
        name: "Scoped OCI Registry & Filtered Socket Proxy",
        spec: "Per-Project JWT RBAC · Zero Raw docker.sock",
        detail:
          "Images push to the internal TLS registry (:5000) under project-scoped credentials while a filtered socket proxy blocks privileged daemon calls.",
      },
    ],
  },
  {
    phase: "STAGE II · CRYPTOGRAPHIC DATA PLANE & KERNEL SANDBOX",
    badge: "Layers 05 – 08",
    summary:
      "At runtime, workloads execute inside user-space kernel sandboxes while opted-in services route database, cache, and volume I/O through grid-cryptd.",
    layers: [
      {
        num: "05",
        name: "gVisor (runsc) & Kata Kernel Sandboxing",
        spec: "User-Space Syscall Interception · Auto-Runtime Match",
        detail:
          "Isolates untrusted tenant containers in gVisor or Kata VMs, automatically selecting shared-loopback runc only when SPIFFE Envoy mTLS sidecars are attached.",
      },
      {
        num: "06",
        name: "Opt-In grid-cryptd & External Root Vault",
        spec: "RAM-Only mlock() DEKs · HKDF-SHA256 · Epoch Rotation",
        detail:
          "Derives project, service, and table keys in non-swappable RAM from your external vault and gates PostgreSQL (:15432) and Redis (:16379) connections in real time.",
      },
      {
        num: "07",
        name: "LUKS2 DM-Crypt Volumes & SOPS + age Secrets",
        spec: "aes-xts-plain64 Loop Volumes · Per-Service age Keys",
        detail:
          "Encrypts persistent service and addon volumes on disk and seals environment variables with SOPS + age recipients and fail-closed startup checks.",
      },
      {
        num: "08",
        name: "Falco eBPF XDR & SSRF / DNS-Rebinding Guard",
        spec: "Real-Time Kernel Drift Detection · RFC 1918 Shield",
        detail:
          "Monitors container syscalls via eBPF and blocks serverless or webhook egress to private subnets, localhost, and cloud metadata endpoints (169.254.169.254).",
      },
    ],
  },
  {
    phase: "STAGE III · ZERO-TRUST MESH, WAF & AUTONOMOUS RECOVERY",
    badge: "Layers 09 – 11",
    summary:
      "Traffic enters through a shadow-tested ML WAF and dual-edge proxy, traverses an mTLS + WireGuard mesh, and self-heals via the AI Senate and Google Jules.",
    layers: [
      {
        num: "09",
        name: "Dual-Edge Caddy + Traefik, CrowdSec & open-appsec WAF",
        spec: "On-Demand Wildcard ACME TLS · Shadow-Parity ML WAF",
        detail:
          "Combines automatic wildcard TLS, CrowdSec LAPI + Cloudflare bouncer IP reputation, fail2ban jails, and open-appsec machine-learning WAF inspection.",
      },
      {
        num: "10",
        name: "WireGuard VPN Mesh, CoreDNS & SPIFFE/SPIRE mTLS",
        spec: "HMAC-SHA256 Node Attestation · Envoy X.509 SVIDs",
        detail:
          "Encrypts cross-region node traffic over WireGuard (10.100.0.0/24) with split-horizon CoreDNS and short-lived SPIFFE workload certificates across 2 trust domains.",
      },
      {
        num: "11",
        name: "AI Senate, Jules Auto-PR & 5-Framework Compliance",
        spec: "OCSF Event Graph · AI-SPM Redaction · SOC2/ISO/HIPAA",
        detail:
          "Correlates telemetry into OCSF findings, redacts secrets before LLM calls, dispatches Jules to open fix PRs on crash loops, and continuously scores compliance controls.",
      },
    ],
  },
];

const ADDON_MATRIX = [
  {
    category: "Relational, Analytical & Time-Series SQL",
    tag: "HA + Pooled",
    description:
      "Automated Patroni primary/replica streaming, PgCat read/write query splitting, per-tenant PgBouncer logical DB pooling, and pg_stat_statements Slow Query analysis.",
    engines: [
      "PostgreSQL 16 HA (Patroni)",
      "PgCat Read/Write Pooler",
      "PgBouncer Multi-Tenant",
      "MySQL 8",
      "MariaDB 11",
      "ClickHouse",
      "TimescaleDB",
      "CockroachDB",
    ],
  },
  {
    category: "In-Memory Caches, Message Brokers & Streams",
    tag: "3-Node Quorum",
    description:
      "Sub-second cache and Celery/worker queue failover via 3-node Redis Sentinel quorum, plus high-throughput event streaming wired onto your project's private bridge.",
    engines: [
      "Redis 7 Sentinel HA",
      "Valkey",
      "Dragonfly",
      "KeyDB",
      "RabbitMQ",
      "Apache Kafka",
      "NATS JetStream",
      "Redpanda",
    ],
  },
  {
    category: "Vector Databases, Search & Local AI Inference",
    tag: "RAG & Agent Ready",
    description:
      "Deploy sovereign AI pipelines without external API lock-in: high-dimensional vector search, hybrid full-text indexing, and self-hosted LLM inference servers.",
    engines: [
      "Qdrant",
      "Milvus",
      "Weaviate",
      "pgvector",
      "Chroma",
      "Ollama",
      "vLLM",
      "Meilisearch",
      "Typesense",
      "Elasticsearch",
    ],
  },
  {
    category: "Object Storage, Identity, Secrets & Workflow",
    tag: "Sovereign Stack",
    description:
      "S3-compatible object storage for encrypted backups, OIDC/SAML identity providers, secret vaults, durable execution engines, and full-stack observability.",
    engines: [
      "MinIO (S3)",
      "Garage S3",
      "Keycloak",
      "Authentik",
      "Infisical",
      "HashiCorp Vault",
      "Temporal",
      "n8n",
      "Grafana + Loki + Prometheus",
      "MongoDB ReplicaSet",
    ],
  },
];

const ARCHITECTURAL_LEDGER = [
  {
    capability: "Control Plane License & Exit Freedom",
    grid: "100% MIT Open Source · Standard Docker containers keep running even if Grid is removed",
    managed: "Proprietary closed control plane · Workflow and runtime lock-in",
    hyperscaler: "Proprietary service primitives (ECS/Cloud Run/IAM/VPC lock-in)",
  },
  {
    capability: "Compute & Bandwidth Economics",
    grid: "Raw VPS / Bare-Metal cost ($4–$40/mo) · $0 per-seat tax · $0 platform bandwidth markup",
    managed: "$20–$36+/seat/mo + 300–600% vCPU/RAM and bandwidth overage markups",
    hyperscaler: "High compute premium + punishing NAT Gateway & cross-AZ egress fees",
  },
  {
    capability: "Workload & Deployment Engines",
    grid: "5 Engines: Git (Nixpacks/BuildKit), Docker/Registry, Compose Waves, Upload/100+ Blueprints, Serverless Functions",
    managed: "Typically 1–2 modes (Git or Container) with strict execution time limits",
    hyperscaler: "Fragmented across 6+ separate paid services (CodeBuild, ECR, ECS, Lambda)",
  },
  {
    capability: "Opt-In Cryptographic Data Plane (grid-cryptd)",
    grid: "External Root Vault + RAM-only mlock() DEKs + PG (:15432) & Redis (:16379) wire gates + LUKS2 volumes + SOPS/age",
    managed: "Provider-held keys only · No tenant-controlled wire gates or RAM-only lease revocation",
    hyperscaler: "KMS envelope disk encryption only (keys remain accessible to mounted OS runtime)",
  },
  {
    capability: "Built-In CNAPP (ASPM, SBOM, SLSA, CWPP & XDR)",
    grid: "Included: Pre-build Secret/IaC/SAST gate, CycloneDX SBOM, SLSA v1.0 + Cosign, gVisor/Kata, Falco eBPF, OCSF XDR",
    managed: "Not included (requires $500+/mo third-party Snyk/Wiz/Prisma integrations)",
    hyperscaler: "Sold as 5+ separate metered security products (Inspector, GuardDuty, SecurityHub, WAF)",
  },
  {
    capability: "High-Availability Databases & Connection Pooling",
    grid: "Included: Patroni PostgreSQL Primary/Replica + PgCat + PgBouncer Tenants + 3-Node Redis Sentinel HA",
    managed: "HA & pooling gated behind Enterprise plans ($100–$500+/mo per database)",
    hyperscaler: "Multi-AZ RDS + RDS Proxy + ElastiCache billed separately per instance-hour",
  },
  {
    capability: "Multi-Server Mesh & Workload mTLS Identity",
    grid: "Included: WireGuard VPN mesh (10.100.0.0/24) + CoreDNS + HMAC node attestation + SPIFFE/SPIRE Envoy mTLS",
    managed: "Single-provider private networking (often billed per GB or Enterprise-only)",
    hyperscaler: "Manual VPC Peering, Transit Gateway, and AWS Private CA ($400+/mo per CA)",
  },
  {
    capability: "Scale-to-Zero, Private Tunnels & Visual Topology",
    grid: "Included: Sablier + napd scale-to-zero, built-in FRP (frps) tunnels, and 2D/3D ReactFlow + Three.js topology canvas",
    managed: "Partial scale-to-zero · No built-in private tunnel server or 3D bridge topology",
    hyperscaler: "Requires custom KEDA/Knative + external ngrok/Cloudflare tunnel tooling",
  },
  {
    capability: "AI Operations, Auto-Fix PRs & Native MCP Server",
    grid: "Included: 17-provider AI Senate consensus, Google Jules auto-PR remediation, AI-SPM redaction, and native /mcp server",
    managed: "Basic single-LLM log summary · No multi-model Senate or built-in MCP server",
    hyperscaler: "Single-vendor assistant · No automated GitHub fix-PR loop",
  },
  {
    capability: "Observability, Multi-Tenant RBAC & Reseller Mode",
    grid: "Included: Prometheus, Grafana, Loki Log Bridge, Alertmanager, Teams RBAC, FinOps P&L, and White-Label Reseller portal",
    managed: "Short log retention on base tiers · No white-label reseller hosting mode",
    hyperscaler: "High CloudWatch ingestion/query charges · No turnkey PaaS reseller portal",
  },
];

const TRULAY_ECOSYSTEM_SUITE = [
  {
    code: "01 / BUILD",
    title: "Trulay Grid PaaS",
    desc: "MIT open-source sovereign control plane for apps, HA databases, WireGuard meshes, and cryptographic workloads.",
    icon: Rocket,
  },
  {
    code: "02 / SECURE",
    title: "Security Gateway",
    desc: "Zero-trust edge enforcement, CrowdSec + open-appsec ML WAF, and OCSF threat telemetry.",
    icon: Shield,
  },
  {
    code: "03 / VERIFY",
    title: "Identity Service",
    desc: "High-assurance verification, SPIFFE workload identity, and carrier-grade SilentOTP authentication.",
    icon: Lock,
  },
  {
    code: "04 / COMMUNICATE",
    title: "Global Messaging",
    desc: "Programmable SMS, WhatsApp, Voice, and Email APIs with cryptographic delivery proofs.",
    icon: Globe,
  },
  {
    code: "05 / GROW",
    title: "Trulay Ignite",
    desc: "AI-orchestrated workflow automation, demand analytics, and revenue operations.",
    icon: Sparkles,
  },
];

export default function Home() {
  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "SoftwareApplication",
    name: "Trulay Grid",
    applicationCategory: "DeveloperApplication",
    operatingSystem: "Linux",
    license: "https://opensource.org/licenses/MIT",
    offers: {
      "@type": "Offer",
      price: "0",
      priceCurrency: "USD",
    },
    featureList: [
      "5 Deployment Engines: Git Push-to-Deploy (Nixpacks + Ephemeral BuildKit), Docker Image/Private Registry, Docker Compose Wave Planner, Archive Upload & 100+ App Store Blueprints, Serverless Functions",
      "Opt-In grid-cryptd Cryptographic Control Plane: External Root Vault/KMS, RAM-only mlock() DEK keystore, HKDF-SHA256, PostgreSQL (:15432) & Redis (:16379) wire gates, LUKS2 encrypted volumes, SOPS + age",
      "Built-In CNAPP & XDR: Pre-build ASPM (Secrets, IaC, SAST), CycloneDX v1.5 SBOM, SLSA v1.0 Provenance + Cosign, gVisor/Kata sandboxes, Falco eBPF, CrowdSec, open-appsec WAF, OCSF correlation, SOC2/ISO27001/HIPAA/PCI-DSS/GDPR compliance",
      "High-Availability Data Plane: Patroni PostgreSQL HA + PgCat + PgBouncer multi-tenant pooling, 3-node Redis Sentinel HA, 40+ one-click addons, encrypted S3/R2 backups, zero-downtime live server transfers",
      "Zero-Trust Multi-Server Mesh: WireGuard VPN mesh, CoreDNS split-horizon DNS, SPIFFE/SPIRE mTLS Envoy sidecars, Caddy + Traefik dual edge, FRP private tunnels, Sablier scale-to-zero",
      "AI Senate & Native MCP Server: 17+ LLM provider deliberation, Google Jules automated fix Pull Requests, AI-SPM prompt redaction, 3-engine predictive autoscaler, and built-in /mcp server",
    ],
    description:
      "Self-hosted MIT open-source sovereign PaaS for deploying full-stack applications, HA databases, encrypted data planes, and multi-server WireGuard + SPIFFE mTLS infrastructure on your own VPS.",
  };

  return (
    <div className="relative overflow-x-hidden">
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />

      {/* 1. ASYMMETRIC SOVEREIGN COMMAND HERO + LIVE 3-TAB TELEMETRY CONSOLE */}
      <HeroCarousel />

      {/* 2. INTERACTIVE 7-DOMAIN PLATFORM CAPABILITY WORKBENCH (#features) */}
      <PlatformCapabilityExplorer />

      {/* 3. 11-LAYER ZERO-TRUST SUPPLY CHAIN, CRYPTOGRAPHIC RUNTIME & EDGE SCHEMATIC */}
      <section
        aria-labelledby="defense-architecture-heading"
        className="py-20 md:py-28 bg-slate-950 text-white border-b border-slate-800"
      >
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex flex-col lg:flex-row lg:items-end justify-between gap-6 pb-12 mb-12 border-b border-slate-800">
            <div className="max-w-3xl">
              <div className="inline-flex items-center gap-2 px-3 py-1 rounded-md bg-emerald-500/15 border border-emerald-500/30 text-emerald-300 text-xs font-mono font-bold mb-4">
                <KeyRound className="w-3.5 h-3.5" aria-hidden="true" />
                CODE-TO-KERNEL ZERO-TRUST ARCHITECTURE · 11 VERIFIED LAYERS
              </div>
              <h2
                id="defense-architecture-heading"
                className="text-3xl sm:text-4xl lg:text-5xl font-extrabold text-white tracking-tight leading-tight"
              >
                Defense-in-depth engineered into the deployment path.
              </h2>
            </div>
            <div className="flex flex-col sm:items-end gap-2">
              <p className="text-sm sm:text-base text-slate-300 max-w-md font-medium leading-relaxed">
                From sanitized BuildKit environments and SLSA v1.0 attestation to opt-in{" "}
                <code className="text-emerald-300 font-mono">grid-cryptd</code> wire gates and Falco eBPF runtime monitoring.
              </p>
              <Link
                href="/docs/security"
                className="inline-flex items-center gap-1.5 min-h-[44px] text-xs font-mono font-bold text-emerald-400 hover:text-emerald-300"
              >
                <span>Read Full 11-Layer Security Whitepaper</span>
                <ArrowUpRight className="w-4 h-4" aria-hidden="true" />
              </Link>
            </div>
          </div>

          {/* 3-Stage Architectural Blueprint Ledger */}
          <div className="space-y-8">
            {DEFENSE_PIPELINE_STAGES.map((stage) => (
              <div
                key={stage.phase}
                className="rounded-2xl border border-slate-800 bg-slate-900/70 overflow-hidden"
              >
                <div className="px-6 py-4 bg-slate-900 border-b border-slate-800 flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <span className="text-xs font-mono font-bold text-emerald-400 tracking-wider">
                      {stage.phase}
                    </span>
                    <p className="text-xs sm:text-sm text-slate-300 mt-1">
                      {stage.summary}
                    </p>
                  </div>
                  <span className="px-2.5 py-1 rounded bg-slate-800 border border-slate-700 text-xs font-mono font-bold text-slate-200">
                    {stage.badge}
                  </span>
                </div>

                <div
                  className={`grid divide-y lg:divide-y-0 lg:divide-x divide-slate-800 ${
                    stage.layers.length === 4 ? "lg:grid-cols-4" : "lg:grid-cols-3"
                  }`}
                >
                  {stage.layers.map((layer) => (
                    <div key={layer.num} className="p-6 flex flex-col justify-between">
                      <div>
                        <div className="flex items-center justify-between gap-2 mb-3">
                          <span className="font-mono text-xs font-bold px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-300 border border-emerald-500/30">
                            LAYER {layer.num}
                          </span>
                          <span className="text-xs font-mono text-slate-400">FAIL-CLOSED</span>
                        </div>
                        <h3 className="text-base font-bold text-white mb-1.5">
                          {layer.name}
                        </h3>
                        <div className="text-xs font-mono text-emerald-400 mb-2.5">
                          {layer.spec}
                        </div>
                        <p className="text-xs sm:text-sm text-slate-300 leading-relaxed">
                          {layer.detail}
                        </p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* 4. LIVE TOPOLOGICAL WAVE ORCHESTRATION & MULTI-REGION WIREGUARD + SPIRE MESH */}
      <section
        aria-labelledby="orchestration-mesh-heading"
        className="py-20 md:py-28 bg-slate-950 text-white border-b border-slate-800"
      >
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 space-y-20">
          {/* Part A: Ecosystem Wave Deploy */}
          <div>
            <div className="flex flex-col lg:flex-row lg:items-end justify-between gap-6 mb-10">
              <div className="max-w-3xl">
                <div className="inline-flex items-center gap-2 px-3 py-1 rounded-md bg-emerald-500/15 border border-emerald-500/30 text-emerald-300 text-xs font-mono font-bold mb-3">
                  <Rocket className="w-3.5 h-3.5" aria-hidden="true" />
                  TOPOLOGICAL ECOSYSTEM DEPLOYER · DEPENDENCY WAVE PLANNER
                </div>
                <h2
                  id="orchestration-mesh-heading"
                  className="text-3xl sm:text-4xl font-extrabold text-white tracking-tight"
                >
                  One command deploys your entire microservice graph in dependency waves.
                </h2>
              </div>
              <p className="text-sm sm:text-base text-slate-300 max-w-md font-medium leading-relaxed">
                Grid parses service dependencies, provisions shared PostgreSQL/Redis/RabbitMQ addons first, injects scoped bridge aliases, and rolls out dependent services wave by wave.
              </p>
            </div>
            <EcosystemDeployVisual />
          </div>

          {/* Part B: Multi-Region WireGuard + CoreDNS + SPIFFE/SPIRE Mesh */}
          <div className="grid lg:grid-cols-12 gap-8 items-center pt-8 border-t border-slate-800/90">
            <div className="lg:col-span-5 space-y-5">
              <div className="inline-flex items-center gap-2 px-3 py-1 rounded-md bg-blue-500/15 border border-blue-500/30 text-blue-300 text-xs font-mono font-bold">
                <Network className="w-3.5 h-3.5" aria-hidden="true" />
                MULTI-SERVER WIREGUARD + COREDNS + SPIRE mTLS
              </div>
              <h3 className="text-2xl sm:text-3xl font-extrabold text-white tracking-tight leading-tight">
                Span providers and continents as a single zero-trust cluster.
              </h3>
              <p className="text-sm sm:text-base text-slate-300 leading-relaxed">
                Mix Hetzner, OVH, DigitalOcean, AWS, and on-premise bare metal inside one encrypted{" "}
                <code className="text-emerald-300 font-mono">10.100.0.0/24</code> WireGuard mesh with zero Kubernetes YAML overhead.
              </p>

              <div className="space-y-3 pt-2">
                {[
                  {
                    title: "HMAC-SHA256 Node Attestation & Leader Election",
                    detail: "Every worker node proves cryptographic identity before receiving mesh routes or registry credentials.",
                  },
                  {
                    title: "Split-Horizon CoreDNS (<service>.mesh.local)",
                    detail: "Dynamic zone updates resolve cross-node containers over private WireGuard IPs with zero DNS reload downtime.",
                  },
                  {
                    title: "SPIFFE / SPIRE Workload SVIDs & Live Server Transfers",
                    detail: "Automatic X.509 mTLS rotation via Envoy sidecars and zero-downtime live service migration between nodes.",
                  },
                ].map((item) => (
                  <div
                    key={item.title}
                    className="p-3.5 rounded-xl bg-slate-900/90 border border-slate-800"
                  >
                    <div className="text-sm font-bold text-white flex items-center gap-2">
                      <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" aria-hidden="true" />
                      <span>{item.title}</span>
                    </div>
                    <p className="text-xs text-slate-300 mt-1 pl-6 leading-relaxed">
                      {item.detail}
                    </p>
                  </div>
                ))}
              </div>
            </div>

            <div className="lg:col-span-7">
              <MeshTopologyVisual />
            </div>
          </div>
        </div>
      </section>

      {/* 5. 40+ MANAGED DATA ADDONS, VECTOR STORES & HA DATABASE MATRIX */}
      <section
        aria-labelledby="addons-matrix-heading"
        className="py-20 md:py-28 border-b border-slate-200 dark:border-slate-800"
      >
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex flex-col lg:flex-row lg:items-end justify-between gap-6 pb-10 mb-10 border-b border-slate-200 dark:border-slate-800">
            <div className="max-w-3xl">
              <div className="inline-flex items-center gap-2 text-xs font-mono font-bold uppercase tracking-widest text-emerald-700 dark:text-emerald-400 mb-3">
                <Database className="w-3.5 h-3.5" aria-hidden="true" />
                MANAGED DATA PLANE · 40+ ONE-CLICK ENGINES &amp; 100+ APP STORE TEMPLATES
              </div>
              <h2
                id="addons-matrix-heading"
                className="text-3xl sm:text-4xl lg:text-5xl font-extrabold text-slate-950 dark:text-white tracking-tight leading-tight"
              >
                Stateful databases, vector engines, and AI inference—built in.
              </h2>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <Link
                href="/store"
                className="inline-flex items-center gap-2 min-h-[44px] px-5 py-2.5 rounded-xl bg-emerald-700 hover:bg-emerald-800 text-white text-sm font-bold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <span>Open App &amp; Addon Store</span>
                <ArrowRight className="w-4 h-4" aria-hidden="true" />
              </Link>
              <Link
                href="/docs/addons"
                className="inline-flex items-center gap-1.5 min-h-[44px] px-4 py-2.5 rounded-xl border border-slate-300 dark:border-slate-700 text-sm font-bold text-slate-800 dark:text-slate-200 hover:border-emerald-600 transition-colors"
              >
                <span>Addon Architecture Docs</span>
                <ArrowUpRight className="w-4 h-4" aria-hidden="true" />
              </Link>
            </div>
          </div>

          {/* 2x2 Architectural Hairline Matrix (Not generic icon cards) */}
          <div className="grid md:grid-cols-2 border border-slate-200 dark:border-slate-800 rounded-2xl overflow-hidden bg-card divide-y md:divide-y-0 md:divide-x divide-slate-200 dark:divide-slate-800">
            <div className="divide-y divide-slate-200 dark:divide-slate-800">
              {ADDON_MATRIX.slice(0, 2).map((group) => (
                <div key={group.category} className="p-6 sm:p-8">
                  <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
                    <h3 className="text-lg sm:text-xl font-extrabold text-slate-950 dark:text-white">
                      {group.category}
                    </h3>
                    <span className="px-2.5 py-0.5 rounded-md bg-emerald-500/10 border border-emerald-500/30 text-emerald-800 dark:text-emerald-300 text-xs font-mono font-bold">
                      {group.tag}
                    </span>
                  </div>
                  <p className="text-sm text-slate-600 dark:text-slate-400 leading-relaxed mb-5">
                    {group.description}
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {group.engines.map((eng) => (
                      <span
                        key={eng}
                        className="px-3 py-1.5 rounded-lg bg-slate-100 dark:bg-slate-900 border border-slate-200/90 dark:border-slate-800 text-xs font-mono font-semibold text-slate-800 dark:text-slate-200"
                      >
                        {eng}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>

            <div className="divide-y divide-slate-200 dark:divide-slate-800">
              {ADDON_MATRIX.slice(2, 4).map((group) => (
                <div key={group.category} className="p-6 sm:p-8">
                  <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
                    <h3 className="text-lg sm:text-xl font-extrabold text-slate-950 dark:text-white">
                      {group.category}
                    </h3>
                    <span className="px-2.5 py-0.5 rounded-md bg-emerald-500/10 border border-emerald-500/30 text-emerald-800 dark:text-emerald-300 text-xs font-mono font-bold">
                      {group.tag}
                    </span>
                  </div>
                  <p className="text-sm text-slate-600 dark:text-slate-400 leading-relaxed mb-5">
                    {group.description}
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {group.engines.map((eng) => (
                      <span
                        key={eng}
                        className="px-3 py-1.5 rounded-lg bg-slate-100 dark:bg-slate-900 border border-slate-200/90 dark:border-slate-800 text-xs font-mono font-semibold text-slate-800 dark:text-slate-200"
                      >
                        {eng}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* 6. ARCHITECTURAL & ECONOMIC SPECIFICATION LEDGER (TABULAR COMPARISON) */}
      <section
        aria-labelledby="comparison-ledger-heading"
        className="py-20 md:py-28 border-b border-slate-200 dark:border-slate-800"
      >
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex flex-col lg:flex-row lg:items-end justify-between gap-6 mb-10">
            <div className="max-w-3xl">
              <div className="text-xs font-mono font-bold uppercase tracking-widest text-emerald-700 dark:text-emerald-400 mb-3">
                ARCHITECTURAL &amp; ECONOMIC SPECIFICATION LEDGER
              </div>
              <h2
                id="comparison-ledger-heading"
                className="text-3xl sm:text-4xl lg:text-5xl font-extrabold text-slate-950 dark:text-white tracking-tight"
              >
                Stop paying the managed cloud tax.
              </h2>
            </div>
            <Link
              href="/compare"
              className="inline-flex items-center gap-1.5 min-h-[44px] text-sm font-bold text-emerald-700 dark:text-emerald-400 hover:underline"
            >
              <span>View Full Line-by-Line Benchmark Table</span>
              <ArrowUpRight className="w-4 h-4" aria-hidden="true" />
            </Link>
          </div>

          <div className="rounded-2xl border border-slate-200 dark:border-slate-800 bg-card overflow-hidden shadow-sm">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[760px] text-left border-collapse">
                <thead>
                  <tr className="border-b border-slate-200 dark:border-slate-800 bg-slate-100/80 dark:bg-slate-900/90 text-xs font-mono uppercase">
                    <th scope="col" className="p-4 sm:p-5 font-bold text-slate-700 dark:text-slate-300 w-[24%]">
                      Subsystem / Dimension
                    </th>
                    <th
                      scope="col"
                      className="p-4 sm:p-5 font-bold text-emerald-800 dark:text-emerald-300 bg-emerald-500/10 border-x border-emerald-500/25 w-[32%]"
                    >
                      Trulay Grid (MIT Sovereign PaaS)
                    </th>
                    <th scope="col" className="p-4 sm:p-5 font-bold text-slate-600 dark:text-slate-400 w-[22%]">
                      Managed PaaS (Vercel / Railway / Render)
                    </th>
                    <th scope="col" className="p-4 sm:p-5 font-bold text-slate-600 dark:text-slate-400 w-[22%]">
                      Hyperscalers (AWS / GCP / Azure)
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-200 dark:divide-slate-800 text-xs sm:text-sm">
                  {ARCHITECTURAL_LEDGER.map((row) => (
                    <tr
                      key={row.capability}
                      className="hover:bg-slate-50/70 dark:hover:bg-slate-900/40 transition-colors"
                    >
                      <th
                        scope="row"
                        className="p-4 sm:p-5 font-bold text-slate-900 dark:text-white align-top"
                      >
                        {row.capability}
                      </th>
                      <td className="p-4 sm:p-5 font-semibold text-slate-900 dark:text-slate-100 bg-emerald-500/[0.04] border-x border-emerald-500/20 align-top">
                        <div className="flex items-start gap-2">
                          <CheckCircle2
                            className="w-4 h-4 text-emerald-600 dark:text-emerald-400 shrink-0 mt-0.5"
                            aria-hidden="true"
                          />
                          <span>{row.grid}</span>
                        </div>
                      </td>
                      <td className="p-4 sm:p-5 text-slate-600 dark:text-slate-400 align-top">
                        {row.managed}
                      </td>
                      <td className="p-4 sm:p-5 text-slate-600 dark:text-slate-400 align-top">
                        {row.hyperscaler}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </section>

      {/* 7. TRULAY SOVEREIGN ECOSYSTEM STRIP & TERMINAL QUICK-START CTA */}
      <section className="py-20 md:py-24 bg-slate-950 text-white">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 space-y-16">
          {/* Trulay Suite Architectural Strip */}
          <div>
            <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-4 pb-8 mb-8 border-b border-slate-800">
              <div>
                <div className="text-xs font-mono font-bold text-emerald-400 uppercase tracking-widest mb-2">
                  THE SOVEREIGN PLATFORM SUITE BEHIND GRID
                </div>
                <h2 className="text-2xl sm:text-3xl font-extrabold text-white">
                  Engineered by Trulay for mission-critical infrastructure.
                </h2>
              </div>
              <a
                href="https://Trulay.co"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 min-h-[44px] px-4 py-2 rounded-lg bg-slate-900 border border-slate-700 hover:border-emerald-500/50 text-xs font-mono font-bold text-emerald-300 transition-colors"
              >
                <span>Explore Trulay.co</span>
                <ArrowUpRight className="w-4 h-4" aria-hidden="true" />
              </a>
            </div>

            <div className="grid sm:grid-cols-2 lg:grid-cols-5 border border-slate-800 rounded-2xl overflow-hidden bg-slate-900/60 divide-y sm:divide-y-0 sm:divide-x divide-slate-800">
              {TRULAY_ECOSYSTEM_SUITE.map((item) => (
                <div key={item.title} className="p-5 flex flex-col justify-between">
                  <div>
                    <div className="flex items-center justify-between gap-2 mb-3">
                      <span className="text-xs font-mono font-bold text-emerald-400">
                        {item.code}
                      </span>
                      <item.icon className="w-4 h-4 text-slate-400" aria-hidden="true" />
                    </div>
                    <h3 className="text-base font-bold text-white mb-1.5">{item.title}</h3>
                    <p className="text-xs text-slate-300 leading-relaxed">{item.desc}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Final Terminal + Instant Deployment Callout */}
          <div className="rounded-2xl border border-emerald-500/30 bg-slate-900/90 p-6 sm:p-10 lg:p-12">
            <div className="grid lg:grid-cols-12 gap-8 items-center">
              <div className="lg:col-span-7 space-y-4">
                <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-emerald-500/15 border border-emerald-500/30 text-emerald-300 text-xs font-mono font-bold">
                  <Server className="w-3.5 h-3.5" aria-hidden="true" />
                  READY FOR UBUNTU / DEBIAN VPS &amp; BARE METAL
                </div>
                <h2 className="text-3xl sm:text-4xl font-extrabold text-white tracking-tight leading-tight">
                  Boot your sovereign cloud control plane in minutes.
                </h2>
                <p className="text-sm sm:text-base text-slate-300 leading-relaxed max-w-2xl">
                  100% free and MIT open-source. Deploy directly on any clean Linux server or manage your cluster from the{" "}
                  <code className="text-emerald-300 font-mono">smsly</code> CLI and web console.
                </p>

                <div className="pt-2 flex flex-wrap gap-3.5">
                  <Link
                    href="/register"
                    className="inline-flex items-center justify-center gap-2 min-h-[48px] px-7 py-3.5 bg-emerald-700 hover:bg-emerald-600 text-white text-sm sm:text-base font-bold rounded-xl transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
                  >
                    <span>Launch Grid Console</span>
                    <ArrowRight className="w-4 h-4" aria-hidden="true" />
                  </Link>
                  <Link
                    href="/docs/install"
                    className="inline-flex items-center justify-center gap-2 min-h-[48px] px-6 py-3.5 bg-slate-950 hover:bg-slate-800 border border-slate-700 text-white text-sm sm:text-base font-bold rounded-xl transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
                  >
                    <FileCode className="w-4 h-4 text-emerald-400" aria-hidden="true" />
                    <span>Self-Hosted Install Guide</span>
                  </Link>
                  <Link
                    href="/get-started"
                    className="inline-flex items-center justify-center gap-2 min-h-[48px] px-5 py-3.5 text-slate-300 hover:text-white text-sm font-bold transition-colors"
                  >
                    <span>AI Architecture Planner</span>
                    <ArrowUpRight className="w-4 h-4" aria-hidden="true" />
                  </Link>
                </div>
              </div>

              <div className="lg:col-span-5">
                <div className="rounded-xl border border-slate-800 bg-[#070b14] p-4 sm:p-5 font-mono text-xs text-slate-200 space-y-2.5">
                  <div className="flex items-center justify-between text-slate-400 pb-2 border-b border-slate-800">
                    <span className="flex items-center gap-1.5">
                      <Terminal className="w-3.5 h-3.5 text-emerald-400" aria-hidden="true" />
                      root@vps:~ (Quick Start)
                    </span>
                    <span className="text-emerald-400">MIT LICENSE</span>
                  </div>
                  <div className="text-slate-400"># 1. Install on a fresh Ubuntu 22.04/24.04 VPS</div>
                  <div className="text-emerald-300 overflow-x-auto whitespace-pre">
                    git clone https://github.com/SMSLYCLOUD/smsly-hosting.git
                  </div>
                  <div className="text-emerald-300 overflow-x-auto whitespace-pre">
                    cd smsly-hosting &amp;&amp; sudo bash backend/install.sh
                  </div>
                  <div className="text-slate-400 pt-1"># 2. Or deploy from your workstation via CLI</div>
                  <div className="text-slate-200 overflow-x-auto whitespace-pre">
                    pip install smsly-cli &amp;&amp; smsly login &amp;&amp; smsly deploy
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
