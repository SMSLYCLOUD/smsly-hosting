import type { Metadata } from "next";
import Link from "next/link";
import {
  Server, Activity, Globe, Database, Cpu, Network, Shield, HardDrive, Lock,
  CheckCircle2, ArrowRight, Zap, Code, Sparkles, Terminal, Cloud, Rocket, GitBranch,
  ArrowUpRight, Blocks, RefreshCw, Brain, FileCode, ShieldCheck, Eye, Wifi,
  Container, Fingerprint, ScanLine, Bug, ShieldAlert,
} from "lucide-react";
import { ScrollReveal, StaggerChild } from "@/components/ui/ScrollReveal";
import { EcosystemDeployVisual } from "@/components/effects/EcosystemDeployVisual";
import { HeroCarousel } from "@/components/landing/HeroCarousel";
import { MeshTopologyVisual } from "@/components/landing/MeshTopologyVisual";

export const metadata: Metadata = {
  title: "Trulay Grid — Free Open-Source Sovereign PaaS",
  description:
    "Self-hosted MIT open-source PaaS. Deploy apps, PostgreSQL HA, Redis Sentinel, workers, and WireGuard multi-server meshes on your own VPS with zero vendor lock-in.",
  alternates: { canonical: "/" },
};

function DashboardMockup() {
  const services = [
    { name: "api-gateway", status: "healthy", ram: "1.2GB / 2GB", cpu: "34%", uptime: "12d" },
    { name: "auth-service", status: "healthy", ram: "890MB / 1.5GB", cpu: "22%", uptime: "30d" },
    { name: "web-frontend", status: "healthy", ram: "456MB / 1GB", cpu: "11%", uptime: "8d" },
    { name: "worker-payments", status: "healthy", ram: "678MB / 2GB", cpu: "45%", uptime: "14d" },
  ];

  return (
    <div
      className="bg-[#0f172a] rounded-2xl overflow-hidden border border-slate-800 shadow-xl w-full"
      aria-label="Preview of the Grid Dashboard production cluster status"
    >
      <div className="flex flex-wrap items-center justify-between gap-2 px-5 py-3 border-b border-slate-800 bg-slate-900">
        <span className="text-xs font-mono text-slate-300">Grid Dashboard — Production Cluster</span>
        <div className="flex items-center gap-2">
          <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse motion-reduce:animate-none" aria-hidden="true" />
          <span className="text-xs font-mono text-emerald-400 font-bold">ALL SYSTEMS OPERATIONAL</span>
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-px bg-slate-800">
        {[
          { label: "Services", value: "12", sub: "4 active" },
          { label: "HA Status", value: "Active", sub: "PostgreSQL + Redis" },
          { label: "WireGuard", value: "Connected", sub: "3 peers" },
          { label: "AI Guardian", value: "Watching", sub: "7 checks today" },
        ].map((s) => (
          <div key={s.label} className="bg-[#0f172a] p-4 text-center">
            <div className="text-xs text-slate-400 font-mono mb-1">{s.label}</div>
            <div className="text-lg font-bold text-white tabular-nums">{s.value}</div>
            <div className="text-xs text-emerald-400 mt-0.5">{s.sub}</div>
          </div>
        ))}
      </div>

      <div className="p-4 sm:p-5 space-y-3">
        {services.map((svc) => (
          <div
            key={svc.name}
            className="flex flex-wrap sm:flex-nowrap items-center justify-between gap-3 p-3.5 bg-slate-900/70 rounded-xl border border-slate-800"
          >
            <div className="flex items-center gap-3 min-w-0">
              <span className="w-2.5 h-2.5 rounded-full bg-emerald-400 shrink-0" aria-hidden="true" />
              <div className="min-w-0">
                <div className="text-sm font-bold text-white truncate">{svc.name}</div>
                <div className="text-xs text-slate-400 font-mono">{svc.uptime} uptime</div>
              </div>
            </div>
            <div className="flex items-center gap-4 ml-auto">
              <div className="text-right">
                <div className="text-xs sm:text-sm text-slate-200 font-mono tabular-nums">{svc.ram}</div>
                <div className="text-xs text-slate-400 tabular-nums">{svc.cpu} CPU</div>
              </div>
              <div className="hidden sm:flex gap-1.5" aria-hidden="true">
                <span className="px-2 py-1 bg-slate-800 rounded text-xs text-slate-300 font-mono">LOGS</span>
                <span className="px-2 py-1 bg-slate-800 rounded text-xs text-slate-300 font-mono">SSH</span>
              </div>
            </div>
          </div>
        ))}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 px-5 py-2.5 border-t border-slate-800 bg-slate-900/60 text-xs text-slate-300 font-mono">
        <span>PostgreSQL HA: Primary → Replica (0ms lag)</span>
        <span>Redis Sentinel: 3 nodes healthy</span>
      </div>
    </div>
  );
}

function SecurityPipelineVisual() {
  const stages = [
    { label: "gVisor", icon: Container, desc: "Sandbox", color: "bg-emerald-700" },
    { label: "Falco", icon: ShieldAlert, desc: "Syscall Monitor", color: "bg-violet-700" },
    { label: "fail2ban", icon: Bug, desc: "Intrusion Block", color: "bg-amber-700" },
    { label: "Trivy", icon: ScanLine, desc: "CVE Scan", color: "bg-blue-700" },
    { label: "Cosign", icon: FileCode, desc: "Image Verify", color: "bg-rose-700" },
    { label: "Registry", icon: Fingerprint, desc: "Scoped RBAC", color: "bg-cyan-700" },
  ];
  return (
    <div className="bg-[#0f172a] rounded-2xl p-5 border border-slate-800 w-full">
      <div className="text-xs font-mono text-slate-300 mb-4 flex items-center gap-2">
        <Shield className="w-4 h-4 text-emerald-400" aria-hidden="true" />
        Container Hardening Pipeline
      </div>
      <div className="grid grid-cols-3 sm:grid-cols-6 gap-4 mb-4">
        {stages.map((s) => (
          <div key={s.label} className="flex flex-col items-center gap-1.5 text-center">
            <div className={`w-9 h-9 rounded-lg ${s.color} flex items-center justify-center`}>
              <s.icon className="w-4 h-4 text-white" aria-hidden="true" />
            </div>
            <span className="text-xs font-bold text-slate-200">{s.label}</span>
            <span className="text-xs text-slate-400">{s.desc}</span>
          </div>
        ))}
      </div>
      <div className="h-px bg-slate-800" />
      <div className="flex items-center gap-2 mt-3 text-xs text-slate-300 font-mono">
        <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 shrink-0" aria-hidden="true" />
        Every container passes through all 6 stages before reaching production
      </div>
    </div>
  );
}

function DeployPipelineTerminal() {
  return (
    <div className="bg-[#0f172a] rounded-2xl shadow-xl overflow-hidden border border-slate-800 w-full">
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-800 bg-slate-900">
        <div className="text-xs font-mono text-slate-300 flex items-center gap-1.5">
          <Terminal className="w-3.5 h-3.5 text-emerald-400" aria-hidden="true" /> user@grid-cluster:~
        </div>
        <span className="text-xs font-mono text-slate-400">pipeline log</span>
      </div>
      <div className="p-5 sm:p-6 font-mono text-xs sm:text-sm overflow-x-auto leading-relaxed min-h-[380px]">
        <pre className="text-slate-200 whitespace-pre">
          <code>{`➜ ~/my-app git push origin main
⚡ Webhook received — triggering deploy pipeline

┌─ Phase 1: Build ─────────────────────────────┐
│ Nixpacks auto-detecting framework...
│ ✔ Detected Next.js 15 + TypeScript + PostgreSQL
│ ✔ Installing dependencies & building image
│ ✔ Build completed in 18.4s

┌─ Phase 2: Security Scan ──────────────────────┐
│ Trivy scanning container image for CVEs...
│ ✔ 0 critical, 0 high findings
│ ✔ Cosign signature verified (Sigstore keyless)
│ ✔ Falco baseline syscall profile updated

┌─ Phase 3: Deploy ─────────────────────────────┐
│ Blue-green: spinning up my-app@v47 (gVisor)
│ ✔ WireGuard mesh peer registered (10.0.1.42)
│ ✔ Traefik & Caddy TLS certificate ready

┌─ Phase 4: Health & Release ────────────────────┐
│ ✔ GET /health → 200 OK
│ ✔ Traffic switched live (0ms downtime)
✔ Deploy complete — v47 live`}</code>
        </pre>
      </div>
    </div>
  );
}

const problemCards = [
  {
    problem: "You're paying 3-10x too much for cloud hosting",
    description:
      "AWS, GCP, Azure mark up compute by 300-1000%. Managed PaaS adds another layer. Your infrastructure costs are eating your runway.",
    icon: Server,
    color: "text-red-600 dark:text-red-400",
  },
  {
    problem: "Vendor lock-in traps your business",
    description:
      "Once you build on a managed platform, you can't leave. Proprietary APIs, custom runtimes, opaque pricing. Your infrastructure becomes a liability.",
    icon: Lock,
    color: "text-amber-600 dark:text-amber-400",
  },
  {
    problem: "High availability is sold as a premium add-on",
    description:
      "PostgreSQL replication, Redis failover, auto-scaling — these are table stakes. Yet every platform charges extra for basic reliability.",
    icon: Activity,
    color: "text-blue-600 dark:text-blue-400",
  },
  {
    problem: "DevOps complexity slows your team down",
    description:
      "Kubernetes, Terraform, Helm charts, IAM policies. Your developers spend more time on infrastructure than building product.",
    icon: Cpu,
    color: "text-violet-600 dark:text-violet-400",
  },
];

const solutions = [
  {
    title: "Run on Your Own VPS",
    description:
      "Connect any VPS from any provider. Grid orchestrates your infrastructure while you keep full ownership. Compute costs drop dramatically.",
    icon: Server,
    color: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-400",
  },
  {
    title: "Built-In High Availability",
    description:
      "PostgreSQL HA with Patroni streaming replication. Redis Sentinel auto-failover. WireGuard VPN mesh. All included with zero extra tiers.",
    icon: Network,
    color: "bg-blue-100 text-blue-700 dark:bg-blue-500/20 dark:text-blue-400",
  },
  {
    title: "AI Autoscaling & Self-Healing",
    description:
      "Three autoscaler engines watch your metrics. AI detects anomalies and auto-remediates daemon, container, and disk failures automatically.",
    icon: Brain,
    color: "bg-violet-100 text-violet-700 dark:bg-violet-500/20 dark:text-violet-400",
  },
  {
    title: "Push to Deploy",
    description:
      "Connect GitHub, GitLab, or Bitbucket. Every push triggers a Nixpacks build across any language or framework, plus PR preview environments.",
    icon: GitBranch,
    color: "bg-cyan-100 text-cyan-700 dark:bg-cyan-500/20 dark:text-cyan-400",
  },
  {
    title: "Multi-Cloud Deploy Anywhere",
    description:
      "BYO-VPS (Hetzner, DO), AWS, GCP, Azure, bare metal, or air-gapped. Transfer services between nodes with zero downtime.",
    icon: Globe,
    color: "bg-amber-100 text-amber-700 dark:bg-amber-500/20 dark:text-amber-400",
  },
  {
    title: "100% Open Source",
    description:
      "MIT licensed. No open-core tricks. If Grid is ever removed, your workloads keep running as standard Docker containers on your servers.",
    icon: Code,
    color: "bg-rose-100 text-rose-700 dark:bg-rose-500/20 dark:text-rose-400",
  },
];

const rareFeatures = [
  {
    icon: Brain,
    title: "AI Senate Committee",
    desc: "17 LLM providers. 2+ models deliberate on infrastructure decisions via Propose → Review → Synthesize with per-user cost caps.",
    stat: "17 providers",
    iconColor: "text-violet-400",
    bg: "bg-violet-500/15",
  },
  {
    icon: FileCode,
    title: "Jules Auto-Fix Loop",
    desc: "Failed deployment? Jules analyzes the error, opens a Pull Request with the fix, and deploys cleanly once approved.",
    stat: "Auto-PR",
    iconColor: "text-emerald-400",
    bg: "bg-emerald-500/15",
  },
  {
    icon: ShieldCheck,
    title: "SSRF Guard & Rebind Defense",
    desc: "Serverless runtime blocks RFC 1918, link-local, and cloud metadata IPs via resolve-then-check protections.",
    stat: "Runtime shield",
    iconColor: "text-rose-400",
    bg: "bg-rose-500/15",
  },
  {
    icon: Blocks,
    title: "Custom Addon Bundles",
    desc: "Provision PostgreSQL, Redis, MongoDB, Qdrant, MinIO, and 40+ data engines alongside your apps with automated lifecycle management.",
    stat: "40+ addons",
    iconColor: "text-blue-400",
    bg: "bg-blue-500/15",
  },
  {
    icon: Lock,
    title: "Docker Socket Isolation",
    desc: "Build containers never receive direct Docker socket access. A filtered socket proxy mediates every daemon API request.",
    stat: "Filtered proxy",
    iconColor: "text-cyan-400",
    bg: "bg-cyan-500/15",
  },
  {
    icon: RefreshCw,
    title: "Zero-Downtime Server Transfers",
    desc: "Move services between nodes with automated backup, SSH transfer, restore, DNS cutover, and pre-transfer snapshots.",
    stat: "Auto-rollback",
    iconColor: "text-amber-400",
    bg: "bg-amber-500/15",
  },
  {
    icon: Eye,
    title: "Zero-Trust Node Attestation",
    desc: "Every server proves its identity via challenge-response HMAC-SHA256 before joining the cluster mesh.",
    stat: "Crypto attested",
    iconColor: "text-indigo-400",
    bg: "bg-indigo-500/15",
  },
  {
    icon: Wifi,
    title: "Inter-Server TLS Enforcement",
    desc: "TLS between nodes with strict certificate and SSH host key verification (STRICT mode by default).",
    stat: "TLS enforced",
    iconColor: "text-teal-400",
    bg: "bg-teal-500/15",
  },
  {
    icon: Shield,
    title: "Fail-Closed Security Model",
    desc: "If SECRET_KEY or encryption keys are missing, critical operations fail closed instead of falling back to defaults.",
    stat: "Fail-closed",
    iconColor: "text-pink-400",
    bg: "bg-pink-500/15",
  },
  {
    icon: Fingerprint,
    title: "SPIRE mTLS Identity",
    desc: "Workloads obtain SPIFFE/SPIRE identities with Envoy sidecars across platform and ecosystem trust domains.",
    stat: "2 trust domains",
    iconColor: "text-sky-400",
    bg: "bg-sky-500/15",
  },
  {
    icon: ScanLine,
    title: "WAF Shadow Parity",
    desc: "open-appsec WAF shadow validation verifies edge policy decisions before traffic cutover on 8GB+ hosts.",
    stat: "Shadow tested",
    iconColor: "text-orange-400",
    bg: "bg-orange-500/15",
  },
  {
    icon: Cpu,
    title: "Idle-Minimal, Burst-Allowed",
    desc: "Burst workers idle-stop on empty queues, KSM merges identical Python pages, and defaults scale automatically to host hardware.",
    stat: "Auto-sized",
    iconColor: "text-lime-400",
    bg: "bg-lime-500/15",
  },
];

const haFeatures = [
  {
    icon: Database,
    title: "PostgreSQL HA Streaming Replication",
    desc: "Patroni-managed primary with streaming replicas and PgCat pooling for automatic failover and zero-downtime upgrades.",
    iconColor: "text-blue-600 dark:text-blue-400",
    bg: "bg-blue-500/15",
  },
  {
    icon: Activity,
    title: "Redis Sentinel HA",
    desc: "Automatic cache and broker failover via Redis Sentinel with configurable quorum and replica priorities.",
    iconColor: "text-rose-600 dark:text-rose-400",
    bg: "bg-rose-500/15",
  },
  {
    icon: Cpu,
    title: "AI-Powered Autoscaler",
    desc: "Classic CPU hysteresis combined with Prometheus and Loki anomaly signals and manual scaling overrides.",
    iconColor: "text-violet-600 dark:text-violet-400",
    bg: "bg-violet-500/15",
  },
  {
    icon: HardDrive,
    title: "Encrypted Backups & Recovery",
    desc: "Scheduled backups with S3/R2/MinIO targets, HKDF envelope encryption, and key rotation support.",
    iconColor: "text-amber-600 dark:text-amber-400",
    bg: "bg-amber-500/15",
  },
  {
    icon: Network,
    title: "WireGuard VPN Mesh",
    desc: "Encrypted node-to-node mesh networking across your fleet with peer latency tracking.",
    iconColor: "text-indigo-600 dark:text-indigo-400",
    bg: "bg-indigo-500/15",
  },
  {
    icon: Shield,
    title: "Self-Healing Orchestration",
    desc: "Automated remediation for Docker daemon stalls, disk pressure, and container OOMs before escalating.",
    iconColor: "text-emerald-600 dark:text-emerald-400",
    bg: "bg-emerald-500/15",
  },
];

const comparisons = [
  {
    name: "Grid",
    logo: Cloud,
    description: "The Sovereign PaaS",
    price: "Free & Open Source",
    features: [
      "Run on your own VPS",
      "PostgreSQL HA (Patroni + PgCat)",
      "Redis Sentinel failover",
      "AI predictive autoscaling",
      "Multi-Git provider support",
      "WireGuard VPN mesh",
      "Custom addon bundles (40+ engines)",
      "Docker socket isolation",
      "Zero-downtime server transfers",
      "Zero-trust node attestation",
      "Inter-server TLS & SPIRE mTLS",
      "100% open source (MIT)",
    ],
    highlight: true,
  },
  {
    name: "Managed PaaS",
    logo: Zap,
    description: "Vercel / Railway / Heroku",
    price: "$20-36+/mo per seat",
    features: [
      "Platform lock-in",
      "HA gated on higher tiers",
      "Opaque usage markups at scale",
      "Proprietary runtime constraints",
      "Limited Git integration flexibility",
      "Restricted or paid mesh networking",
      "Per-addon service surcharges",
      "No custom host hardening layers",
      "Complex cross-provider migrations",
      "Closed source control plane",
    ],
    highlight: false,
  },
  {
    name: "Cloud Giants",
    logo: Server,
    description: "AWS / GCP / Azure",
    price: "Variable + egress fees",
    features: [
      "Deep cloud vendor lock-in",
      "Complex IAM & VPC boilerplate",
      "Unpredictable egress billing",
      "Requires dedicated DevOps team",
      "Manual HA & failover scripting",
      "Separate managed VPN pricing",
      "Fragmented monitoring setup",
      "Steep learning curve for small teams",
      "Costly managed database premiums",
      "Proprietary platform services",
    ],
    highlight: false,
  },
];

const ecosystemPillars = [
  {
    label: "Build",
    title: "Grid PaaS",
    desc: "Free open-source PaaS. Deploy entire ecosystems on your infrastructure with zero lock-in.",
    icon: Rocket,
    color: "text-emerald-400",
    bg: "bg-emerald-500/15",
  },
  {
    label: "Secure",
    title: "Security Gateway",
    desc: "Zero-trust routing and policy enforcement with real-time runtime threat detection.",
    icon: Shield,
    color: "text-violet-400",
    bg: "bg-violet-500/15",
  },
  {
    label: "Verify",
    title: "Identity Service",
    desc: "High-assurance verification and carrier-level authentication with SilentOTP.",
    icon: Lock,
    color: "text-blue-400",
    bg: "bg-blue-500/15",
  },
  {
    label: "Communicate",
    title: "Global Messaging",
    desc: "SMS, WhatsApp, Voice, and Email APIs with cryptographic delivery proofs.",
    icon: Globe,
    color: "text-cyan-400",
    bg: "bg-cyan-500/15",
  },
  {
    label: "Grow",
    title: "Ignite",
    desc: "AI-assisted workflow automation, trend analytics, and lead operations.",
    icon: Sparkles,
    color: "text-amber-400",
    bg: "bg-amber-500/15",
  },
];

const platformStats = [
  { value: "17", label: "AI Providers", sub: "Senate Committee consensus" },
  { value: "40+", label: "Addons", sub: "Postgres, Redis, Kafka..." },
  { value: "5", label: "Deploy Types", sub: "Git, Docker, Upload, Template, Function" },
  { value: "0", label: "Vendor Lock-In", sub: "Standard Docker containers" },
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
    description:
      "Self-hosted MIT open-source PaaS for deploying full-stack applications, HA databases, and multi-server WireGuard infrastructure on your own VPS.",
  };

  return (
    <div className="relative overflow-x-hidden">
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />

      {/* HERO */}
      <HeroCarousel />

      {/* DASHBOARD SHOWCASE */}
      <section aria-label="Cluster dashboard preview" className="relative -mt-10 md:-mt-16 z-20 px-4 sm:px-6 lg:px-8 max-w-5xl mx-auto mb-16 md:mb-20">
        <DashboardMockup />
      </section>

      {/* PLATFORM STATS */}
      <section aria-label="Platform capabilities overview" className="py-12 border-t border-slate-200 dark:border-slate-800">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-8">
            {platformStats.map((stat) => (
              <div key={stat.label} className="text-center">
                <div className="text-3xl md:text-4xl font-extrabold text-emerald-700 dark:text-emerald-400 tabular-nums mb-1">
                  {stat.value}
                </div>
                <div className="text-sm font-bold text-slate-900 dark:text-white mb-1">{stat.label}</div>
                <div className="text-xs text-slate-600 dark:text-slate-400">{stat.sub}</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* THE PROBLEM */}
      <section className="py-16 md:py-20">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="text-center mb-12">
            <h2 className="text-3xl md:text-4xl font-bold text-slate-900 dark:text-white mb-4">
              Cloud hosting is broken.
            </h2>
            <p className="text-lg text-slate-600 dark:text-slate-300 max-w-2xl mx-auto font-medium">
              These aren&apos;t edge cases. They&apos;re the standard experience for teams deploying to managed clouds.
            </p>
          </div>

          <ScrollReveal variant="staggerContainer">
            <div className="grid md:grid-cols-2 lg:grid-cols-4 gap-6">
              {problemCards.map((card) => (
                <StaggerChild key={card.problem}>
                  <div className="bg-card p-6 rounded-2xl border border-border h-full">
                    <card.icon className={`w-7 h-7 ${card.color} mb-4`} aria-hidden="true" />
                    <h3 className="text-lg font-bold text-slate-900 dark:text-white mb-2">{card.problem}</h3>
                    <p className="text-sm text-slate-600 dark:text-slate-400 leading-relaxed">{card.description}</p>
                  </div>
                </StaggerChild>
              ))}
            </div>
          </ScrollReveal>
        </div>
      </section>

      {/* HOW GRID SOLVES IT (target for /#features) */}
      <section id="features" className="py-16 md:py-24 scroll-mt-20">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="text-center mb-12">
            <h2 className="text-3xl md:text-4xl font-bold text-slate-900 dark:text-white mb-4">
              How Grid Solves It
            </h2>
            <p className="text-lg text-slate-600 dark:text-slate-300 max-w-2xl mx-auto font-medium">
              Each problem gets a platform-level architectural solution. Own your hardware, keep your velocity.
            </p>
          </div>

          <ScrollReveal variant="staggerContainer">
            <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
              {solutions.map((sol) => (
                <StaggerChild key={sol.title}>
                  <div className="bg-card p-7 rounded-2xl border border-border hover:border-emerald-500/40 transition-colors h-full">
                    <div className={`w-11 h-11 rounded-xl ${sol.color} flex items-center justify-center mb-5`}>
                      <sol.icon className="w-5 h-5" aria-hidden="true" />
                    </div>
                    <h3 className="text-xl font-bold text-slate-900 dark:text-white mb-2">{sol.title}</h3>
                    <p className="text-sm text-slate-600 dark:text-slate-400 leading-relaxed">{sol.description}</p>
                  </div>
                </StaggerChild>
              ))}
            </div>
          </ScrollReveal>
        </div>
      </section>

      {/* RARE CAPABILITIES (Explicit dark showcase container works cleanly in both Light & Dark themes) */}
      <section className="py-20 bg-slate-950 text-white border-y border-slate-800">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="text-center mb-14">
            <div className="inline-flex items-center gap-2 px-3 py-1.5 bg-emerald-500/15 border border-emerald-500/30 text-emerald-300 text-xs font-bold rounded-full mb-5">
              <Sparkles className="w-3.5 h-3.5" aria-hidden="true" />
              Unique Capabilities
            </div>
            <h2 className="text-3xl md:text-4xl lg:text-5xl font-bold text-white mb-5">
              No other PaaS does this.
            </h2>
            <p className="text-lg text-slate-300 max-w-3xl mx-auto font-medium leading-relaxed">
              Platform-grade capabilities built in and open-source—not gated behind enterprise contracts.
            </p>
          </div>

          <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
            {rareFeatures.map((feat) => (
              <div
                key={feat.title}
                className="bg-slate-900 p-7 rounded-2xl border border-slate-800 hover:border-emerald-500/40 transition-colors flex flex-col justify-between"
              >
                <div>
                  <div className={`w-11 h-11 rounded-xl ${feat.bg} flex items-center justify-center mb-5`}>
                    <feat.icon className={`w-5 h-5 ${feat.iconColor}`} aria-hidden="true" />
                  </div>
                  <h3 className="text-lg font-bold text-white mb-2">{feat.title}</h3>
                  <p className="text-sm text-slate-300 leading-relaxed mb-4">{feat.desc}</p>
                </div>
                <div className="inline-flex items-center gap-1.5 px-3 py-1 bg-slate-800 rounded-full w-fit">
                  <Zap className="w-3.5 h-3.5 text-emerald-400" aria-hidden="true" />
                  <span className="text-xs font-bold text-slate-200">{feat.stat}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ECOSYSTEM DEPLOY VISUAL */}
      <section className="py-20 bg-slate-950 text-white border-b border-slate-800">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="text-center mb-12">
            <div className="inline-flex items-center gap-2 px-3 py-1.5 bg-emerald-500/15 border border-emerald-500/30 text-emerald-300 text-xs font-bold rounded-full mb-5">
              <Rocket className="w-3.5 h-3.5" aria-hidden="true" />
              Ecosystem Deploy
            </div>
            <h2 className="text-3xl md:text-4xl lg:text-5xl font-bold text-white mb-5">
              One Command. Full Stack.
            </h2>
            <p className="text-lg text-slate-300 max-w-3xl mx-auto font-medium leading-relaxed">
              Grid scans your repositories, builds a dependency-aware wave plan, provisions addons first, and rolls out services in topological order.
            </p>
          </div>
          <EcosystemDeployVisual />
        </div>
      </section>

      {/* HIGH AVAILABILITY INFRASTRUCTURE */}
      <section className="py-20">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="text-center mb-12">
            <div className="inline-flex items-center gap-2 px-3 py-1.5 bg-blue-500/10 border border-blue-500/25 text-blue-700 dark:text-blue-300 text-xs font-bold rounded-full mb-5">
              <HardDrive className="w-3.5 h-3.5" aria-hidden="true" />
              High Availability
            </div>
            <h2 className="text-3xl md:text-4xl lg:text-5xl font-bold text-slate-900 dark:text-white mb-5 tracking-tight">
              Zero Downtime. Every Layer.
            </h2>
            <p className="text-lg text-slate-600 dark:text-slate-300 max-w-3xl mx-auto font-medium leading-relaxed">
              PostgreSQL streaming replication with Patroni, Redis Sentinel failover, AI-powered autoscaling, and encrypted backups—included out of the box.
            </p>
          </div>

          <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6 mb-10">
            {haFeatures.map((feat) => (
              <div key={feat.title} className="bg-card p-7 rounded-2xl border border-border h-full">
                <div className={`w-11 h-11 rounded-xl ${feat.bg} flex items-center justify-center mb-5`}>
                  <feat.icon className={`w-5 h-5 ${feat.iconColor}`} aria-hidden="true" />
                </div>
                <h3 className="text-lg font-bold text-slate-900 dark:text-white mb-2">{feat.title}</h3>
                <p className="text-sm text-slate-600 dark:text-slate-400 leading-relaxed">{feat.desc}</p>
              </div>
            ))}
          </div>

          <MeshTopologyVisual />
        </div>
      </section>

      {/* DEVELOPER EXPERIENCE */}
      <section className="py-20 border-t border-slate-200 dark:border-slate-800">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex flex-col lg:flex-row items-center gap-12 lg:gap-16">
            <div className="lg:w-1/2">
              <div className="inline-flex items-center gap-2 px-3 py-1 bg-emerald-100 dark:bg-emerald-500/20 text-emerald-800 dark:text-emerald-300 text-sm font-bold rounded-full mb-5">
                <Terminal className="w-3.5 h-3.5" aria-hidden="true" />
                Developer First
              </div>
              <h2 className="text-3xl md:text-4xl font-bold text-slate-900 dark:text-white mb-5">
                Control your infrastructure from anywhere.
              </h2>
              <p className="text-lg text-slate-600 dark:text-slate-300 mb-8 font-medium leading-relaxed">
                The <code className="bg-slate-200 dark:bg-slate-800 px-1.5 py-0.5 rounded font-mono text-emerald-700 dark:text-emerald-400 text-sm">smsly</code> CLI plus
                a real-time dashboard. Every push triggers a full pipeline:
                <strong> build → scan → sign → deploy → health check → monitor</strong>.
              </p>

              <div className="space-y-5">
                {[
                  { icon: Zap, title: "Full Pipeline in Seconds", desc: "Build → Scan → Sign → Deploy → Health Check with zero-downtime blue-green releases.", color: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-400" },
                  { icon: Code, title: "Any Language, Any Framework", desc: "Nixpacks auto-detects and builds Git, Docker, Upload, Template, and Function deployments.", color: "bg-blue-100 text-blue-700 dark:bg-blue-500/20 dark:text-blue-400" },
                  { icon: ShieldCheck, title: "Signed & Hardened", desc: "Trivy CVE scan, Cosign image signing, gVisor sandbox, and Falco syscall monitoring on every deploy.", color: "bg-amber-100 text-amber-700 dark:bg-amber-500/20 dark:text-amber-400" },
                  { icon: Brain, title: "AI Self-Healing", desc: "Runtime guardian watches post-deploy metrics, detects anomalies, and proposes fixes automatically.", color: "bg-violet-100 text-violet-700 dark:bg-violet-500/20 dark:text-violet-400" },
                ].map((item) => (
                  <div key={item.title} className="flex items-start gap-4">
                    <div className={`p-2.5 rounded-xl ${item.color} shrink-0`}>
                      <item.icon className="w-5 h-5" aria-hidden="true" />
                    </div>
                    <div>
                      <h3 className="font-bold text-slate-900 dark:text-white">{item.title}</h3>
                      <p className="text-sm text-slate-600 dark:text-slate-400 font-medium">{item.desc}</p>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="lg:w-1/2 w-full">
              <DeployPipelineTerminal />
            </div>
          </div>
        </div>
      </section>

      {/* COMPARISON */}
      <section className="py-20 border-t border-slate-200 dark:border-slate-800">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="text-center mb-14">
            <h2 className="text-3xl md:text-4xl font-bold text-slate-900 dark:text-white mb-4">
              Stop Paying the Cloud Tax
            </h2>
            <p className="text-lg text-slate-600 dark:text-slate-300 max-w-2xl mx-auto font-medium">
              Grid runs on <strong>your infrastructure</strong>. No managed service markup. No per-seat pricing.
            </p>
          </div>

          <div className="grid md:grid-cols-3 gap-6 max-w-5xl mx-auto">
            {comparisons.map((card) => (
              <div
                key={card.name}
                className={`relative p-7 rounded-2xl bg-card border-2 ${
                  card.highlight ? "border-emerald-600 dark:border-emerald-500" : "border-border"
                } flex flex-col h-full`}
              >
                {card.highlight && (
                  <div className="absolute -top-3 left-1/2 -translate-x-1/2 px-3 py-1 bg-emerald-700 text-white text-xs font-bold uppercase tracking-wider rounded-full">
                    Best Value
                  </div>
                )}

                <div className="mb-5 flex items-center gap-3.5">
                  <div className={`p-3 rounded-xl ${card.highlight ? "bg-emerald-500/15" : "bg-muted"}`}>
                    <card.logo className={`w-7 h-7 ${card.highlight ? "text-emerald-700 dark:text-emerald-400" : "text-slate-600 dark:text-slate-400"}`} aria-hidden="true" />
                  </div>
                  <div>
                    <h3 className="text-xl font-bold text-slate-900 dark:text-white">{card.name}</h3>
                    <p className="text-xs text-slate-600 dark:text-slate-400">{card.description}</p>
                  </div>
                </div>

                <div className="mb-6">
                  <span className="text-2xl font-extrabold text-slate-900 dark:text-white tabular-nums">{card.price}</span>
                </div>

                <ul className="space-y-2.5 mb-7 flex-1">
                  {card.features.map((feat) => (
                    <li key={feat} className="flex items-start gap-2.5 text-sm font-medium text-slate-700 dark:text-slate-300">
                      {card.highlight ? (
                        <CheckCircle2 className="w-4 h-4 text-emerald-600 dark:text-emerald-400 shrink-0 mt-0.5" aria-hidden="true" />
                      ) : (
                        <span className="w-4 h-4 flex items-center justify-center shrink-0 text-slate-500" aria-hidden="true">—</span>
                      )}
                      <span>{feat}</span>
                    </li>
                  ))}
                </ul>

                <Link
                  href={card.highlight ? "/register" : "/compare"}
                  className={`inline-flex items-center justify-center w-full min-h-[44px] py-3 rounded-xl text-sm font-bold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                    card.highlight
                      ? "bg-emerald-700 hover:bg-emerald-800 text-white"
                      : "bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 text-slate-900 dark:text-white"
                  }`}
                >
                  {card.highlight ? "Install Grid Free" : "Compare Full Table"}
                </Link>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* TRULAY ECOSYSTEM */}
      <section className="py-20 bg-slate-950 text-white border-y border-slate-800">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="text-center mb-14">
            <div className="inline-flex items-center gap-2 px-3 py-1.5 bg-emerald-500/15 border border-emerald-500/30 text-emerald-300 text-xs font-bold rounded-full mb-5">
              <Rocket className="w-3.5 h-3.5" aria-hidden="true" />
              The Ecosystem Behind Grid
            </div>
            <h2 className="text-3xl md:text-4xl lg:text-5xl font-bold text-white mb-5">
              Secured by Trulay
            </h2>
            <p className="text-lg text-slate-300 max-w-3xl mx-auto font-medium leading-relaxed">
              Grid is part of the Trulay platform suite—helping modern engineering teams communicate, verify, deploy, and scale.
            </p>
          </div>

          <div className="grid sm:grid-cols-2 md:grid-cols-5 gap-4 mb-10">
            {ecosystemPillars.map((pillar) => (
              <div key={pillar.title} className="bg-slate-900 border border-slate-800 rounded-2xl p-6 h-full">
                <div className="flex items-center gap-3 mb-4">
                  <div className={`w-10 h-10 ${pillar.bg} rounded-lg flex items-center justify-center shrink-0`}>
                    <pillar.icon className={`w-5 h-5 ${pillar.color}`} aria-hidden="true" />
                  </div>
                  <span className={`text-xs font-bold ${pillar.color} uppercase tracking-wider`}>{pillar.label}</span>
                </div>
                <h3 className="text-lg font-bold text-white mb-2">{pillar.title}</h3>
                <p className="text-sm text-slate-300 leading-relaxed">{pillar.desc}</p>
              </div>
            ))}
          </div>

          <div className="text-center">
            <a
              href="https://Trulay.co"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 min-h-[44px] px-6 py-3 bg-emerald-500/15 border border-emerald-500/35 text-emerald-300 font-bold rounded-xl hover:bg-emerald-500/25 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
            >
              Explore Trulay <ArrowUpRight className="w-4 h-4" aria-hidden="true" />
            </a>
          </div>
        </div>
      </section>

      {/* SECURITY & HARDENING */}
      <section className="py-20">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex flex-col lg:flex-row items-center gap-12 lg:gap-16">
            <div className="lg:w-1/2">
              <div className="inline-flex items-center gap-2 px-3 py-1 bg-rose-100 dark:bg-rose-500/20 text-rose-800 dark:text-rose-300 text-sm font-bold rounded-full mb-5">
                <Shield className="w-3.5 h-3.5" aria-hidden="true" />
                Enterprise Security
              </div>
              <h2 className="text-3xl md:text-4xl font-bold text-slate-900 dark:text-white mb-5">
                Hardened for Production
              </h2>
              <p className="text-lg text-slate-600 dark:text-slate-300 mb-7 font-medium leading-relaxed">
                Every container runs in an isolated sandbox with real-time syscall inspection, CVE scanning, signed provenance, and scoped registry RBAC.
              </p>
              <ul className="space-y-3.5 mb-8">
                {[
                  "Zero-trust multi-server identity attestation (HMAC-SHA256)",
                  "End-to-end WireGuard VPN mesh across all regions",
                  "SSRF guard — runtime protection with DNS rebinding defense",
                  "Docker socket isolation via filtered proxy",
                  "Inter-server TLS & strict SSH host key verification",
                  "Fail-closed secrets handling — crash on missing keys, never fall back",
                ].map((item) => (
                  <li key={item} className="flex items-start gap-3 text-slate-700 dark:text-slate-300 font-medium">
                    <CheckCircle2 className="w-5 h-5 text-emerald-600 dark:text-emerald-400 shrink-0 mt-0.5" aria-hidden="true" />
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            </div>

            <div className="lg:w-1/2 w-full grid grid-cols-1 sm:grid-cols-2 gap-4">
              {[
                { name: "gVisor Sandboxing", desc: "User-space kernel per container minimizing shared kernel attack surface.", icon: Container },
                { name: "Falco Runtime Security", desc: "Real-time eBPF syscall monitoring for escapes and anomalous execution.", icon: ShieldAlert },
                { name: "fail2ban Intrusion Prevention", desc: "Automatic IP bans across SSH, API, and container registry logins.", icon: Bug },
                { name: "Trivy CVE Scanning", desc: "Continuous vulnerability scanning on build images and filesystems.", icon: ScanLine },
                { name: "Scoped Container Registry", desc: "Per-project pull/push RBAC backed by platform JWT credentials.", icon: Fingerprint },
                { name: "Cosign Image Signing", desc: "Sigstore signing and provenance checks prior to container rollout.", icon: FileCode },
              ].map((feat) => (
                <div key={feat.name} className="p-5 bg-card border border-border rounded-2xl text-center h-full">
                  <feat.icon className="w-7 h-7 text-emerald-600 dark:text-emerald-400 mx-auto mb-3" aria-hidden="true" />
                  <h3 className="font-bold text-sm text-slate-900 dark:text-white mb-1">{feat.name}</h3>
                  <p className="text-xs text-slate-600 dark:text-slate-400 font-medium leading-relaxed">{feat.desc}</p>
                </div>
              ))}
            </div>
          </div>

          <div className="mt-10">
            <SecurityPipelineVisual />
          </div>
        </div>
      </section>

      {/* SECURITY & COMPLIANCE READINESS STRIP */}
      <section className="py-8 bg-slate-900 text-slate-200 border-t border-slate-800">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex flex-wrap justify-center items-center gap-6 md:gap-12 text-sm font-semibold">
          <Link href="/docs/security" className="inline-flex items-center gap-2 hover:text-emerald-400 transition-colors">
            <Shield className="w-4 h-4 text-emerald-400" aria-hidden="true" /> Self-Hosted Data Sovereignty
          </Link>
          <Link href="/docs/security" className="inline-flex items-center gap-2 hover:text-emerald-400 transition-colors">
            <Lock className="w-4 h-4 text-emerald-400" aria-hidden="true" /> Audit Log &amp; RBAC Controls
          </Link>
          <Link href="/docs/security" className="inline-flex items-center gap-2 hover:text-emerald-400 transition-colors">
            <FileCode className="w-4 h-4 text-emerald-400" aria-hidden="true" /> 11 Defense Layers Documented
          </Link>
        </div>
      </section>

      {/* FINAL CTA */}
      <section className="py-20 bg-slate-950 text-white text-center border-t border-slate-800">
        <div className="max-w-4xl mx-auto px-4">
          <span className="inline-flex items-center gap-2 px-4 py-1.5 bg-emerald-500/15 border border-emerald-500/30 rounded-full mb-6">
            <span className="flex h-2 w-2 rounded-full bg-emerald-400 animate-pulse motion-reduce:animate-none" aria-hidden="true" />
            <span className="text-sm font-semibold text-emerald-300">Ready to deploy?</span>
          </span>
          <h2 className="text-3xl md:text-5xl font-bold text-white mb-5">
            Deploy your first cluster in minutes.
          </h2>
          <p className="text-lg md:text-xl text-slate-300 mb-9 max-w-2xl mx-auto font-medium">
            100% free and MIT open-source. Connect your VPS and start deploying with no credit card required.
          </p>
          <div className="flex flex-col sm:flex-row gap-4 justify-center">
            <Link
              href="/register"
              className="inline-flex items-center justify-center gap-2 min-h-[48px] px-8 py-4 bg-emerald-700 hover:bg-emerald-600 text-white text-lg font-bold rounded-xl transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
            >
              Install Grid Now <ArrowRight className="w-5 h-5" aria-hidden="true" />
            </Link>
            <Link
              href="/docs"
              className="inline-flex items-center justify-center gap-2 min-h-[48px] px-8 py-4 bg-transparent border border-slate-600 text-white text-lg font-bold rounded-xl hover:bg-slate-800 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
            >
              Read the Docs
            </Link>
          </div>
        </div>
      </section>
    </div>
  );
}
