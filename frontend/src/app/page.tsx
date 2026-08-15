"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import {
    Server, Activity, Globe, Database, Cpu, Network, Shield, HardDrive, Lock,
    CheckCircle2, ArrowRight, Zap, Code, Sparkles, Terminal, Cloud, Rocket, GitBranch,
    ArrowUpRight, Blocks, RefreshCw, Brain, FileCode, ShieldCheck, Eye, Wifi,
    Container, Fingerprint, ScanLine, Bug, ShieldAlert,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { ScrollReveal, StaggerChild, ParallaxReveal } from "@/components/ui/ScrollReveal";
import dynamic from "next/dynamic";
const EcosystemDeployVisual = dynamic(() => import("@/components/effects/EcosystemDeployVisual").then(m => m.EcosystemDeployVisual), { ssr: false });

// ============================================
// HERO SLIDES — Carousel data
// ============================================
const heroSlides = [
    {
        badge: "Open Source PaaS",
        heading: "The sovereign PaaS for ",
        gradient: "modern infrastructure.",
        subtitle: "Connect your VPS. Deploy apps, services, databases, and workers. PostgreSQL HA with Patroni. Redis Sentinel failover. AI auto-remediation. WireGuard VPN mesh. No vendor lock-in.",
    },
    {
        badge: "Multi-Server by Design",
        heading: "One control plane, ",
        gradient: "infinite possibilities.",
        subtitle: "Orchestrate a mesh of VPS nodes through a single dashboard. Zero-trust WireGuard VPN, automated peer discovery, and built-in load balancing. Grow from one server to one hundred — no configuration drift.",
    },
    {
        badge: "AI-Powered Reliability",
        heading: "Your infrastructure ",
        gradient: "heals itself.",
        subtitle: "The AI Senate continuously monitors your fleet. When a node degrades, it auto-remediates before you notice. Self-healing PostgreSQL HA, Redis Sentinel failover, and predictive disk alerts — zero manual toil.",
    },
];

// ============================================
// DASHBOARD MOCKUP — Real product visualization
// ============================================
function DashboardMockup() {
    const services = [
        { name: "api-gateway", status: "healthy", ram: "1.2GB / 2GB", cpu: "34%", uptime: "12d" },
        { name: "auth-service", status: "healthy", ram: "890MB / 1.5GB", cpu: "22%", uptime: "30d" },
        { name: "web-frontend", status: "healthy", ram: "456MB / 1GB", cpu: "11%", uptime: "8d" },
        { name: "worker-payments", status: "healthy", ram: "678MB / 2GB", cpu: "45%", uptime: "14d" },
    ];

    return (
        <div className="bg-[#0f172a] rounded-2xl overflow-hidden border border-slate-800 shadow-2xl w-full">
            {/* Header */}
            <div className="flex items-center justify-between px-5 py-3 border-b border-slate-800 bg-slate-900">
                <div className="flex items-center gap-3">
                    <div className="flex gap-1.5">
                        <div className="w-3 h-3 rounded-full bg-red-500/70" />
                        <div className="w-3 h-3 rounded-full bg-amber-500/70" />
                        <div className="w-3 h-3 rounded-full bg-emerald-500/70" />
                    </div>
                    <span className="text-xs font-mono text-slate-400">Grid Dashboard — Production Cluster</span>
                </div>
                <div className="flex items-center gap-2">
                    <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
                    <span className="text-[10px] font-mono text-emerald-400 font-bold">ALL SYSTEMS OPERATIONAL</span>
                </div>
            </div>

            {/* Stats Row */}
            <div className="grid grid-cols-4 gap-px bg-slate-800">
                {[
                    { label: "Services", value: "12", sub: "4 active" },
                    { label: "HA Status", value: "Active", sub: "PostgreSQL + Redis" },
                    { label: "WireGuard", value: "Connected", sub: "3 peers" },
                    { label: "AI Guardian", value: "Watching", sub: "7 anomalies today" },
                ].map((s, i) => (
                    <div key={i} className="bg-[#0f172a] p-4 text-center">
                        <div className="text-xs text-slate-500 font-mono mb-1">{s.label}</div>
                        <div className="text-lg font-bold text-white">{s.value}</div>
                        <div className="text-[10px] text-emerald-400">{s.sub}</div>
                    </div>
                ))}
            </div>

            {/* Service Cards */}
            <div className="p-5 space-y-3">
                {services.map((svc, i) => (
                    <div key={i} className="flex items-center gap-4 p-3.5 bg-slate-900/60 rounded-xl border border-slate-800/60 hover:border-slate-700 transition-colors">
                        <div className={`w-2.5 h-2.5 rounded-full ${svc.status === "healthy" ? "bg-emerald-400" : "bg-amber-400"}`} />
                        <div className="flex-1">
                            <div className="text-sm font-bold text-white">{svc.name}</div>
                            <div className="text-[10px] text-slate-500 font-mono">{svc.uptime} uptime</div>
                        </div>
                        <div className="text-right">
                            <div className="text-sm text-slate-300 font-mono">{svc.ram}</div>
                            <div className="text-[10px] text-slate-500">{svc.cpu} CPU</div>
                        </div>
                        <div className="flex gap-1.5">
                            <button className="px-2 py-1 bg-slate-800 rounded text-[10px] text-slate-400 font-mono hover:bg-slate-700">LOGS</button>
                            <button className="px-2 py-1 bg-slate-800 rounded text-[10px] text-slate-400 font-mono hover:bg-slate-700">SSH</button>
                        </div>
                    </div>
                ))}
            </div>

            {/* Bottom bar */}
            <div className="flex items-center justify-between px-5 py-2.5 border-t border-slate-800 bg-slate-900/50">
                <span className="text-[10px] text-slate-500 font-mono">PostgreSQL HA: Primary → Replica (0ms lag)</span>
                <span className="text-[10px] text-slate-500 font-mono">Redis Sentinel: 3 nodes healthy</span>
            </div>
        </div>
    );
}

// ============================================
// SECURITY PIPELINE — Visual hardening stack
// ============================================
function SecurityPipelineVisual() {
    const stages = [
        { label: 'gVisor', icon: Container, desc: 'Sandbox', color: 'bg-emerald-500' },
        { label: 'Falco', icon: ShieldAlert, desc: 'Syscall Monitor', color: 'bg-violet-500' },
        { label: 'fail2ban', icon: Bug, desc: 'Intrusion Block', color: 'bg-amber-500' },
        { label: 'Trivy', icon: ScanLine, desc: 'CVE Scan', color: 'bg-blue-500' },
        { label: 'Cosign', icon: FileCode, desc: 'Image Verify', color: 'bg-rose-500' },
        { label: 'Registry', icon: Fingerprint, desc: 'Scoped RBAC', color: 'bg-cyan-500' },
    ];
    return (
        <div className="bg-[#0f172a] rounded-2xl p-5 border border-slate-800 w-full">
            <div className="text-xs font-mono text-slate-500 mb-4 flex items-center gap-2">
                <Shield className="w-3.5 h-3.5 text-emerald-400" /> Container Hardening Pipeline
            </div>
            <div className="flex items-center gap-1 mb-3">
                {stages.map((s, i) => (
                    <div key={i} className="flex-1 flex flex-col items-center gap-1">
                        <div className={`w-8 h-8 rounded-lg ${s.color} flex items-center justify-center shadow-lg`}>
                            <s.icon className="w-4 h-4 text-white" />
                        </div>
                        <span className="text-[9px] font-bold text-slate-300">{s.label}</span>
                        <span className="text-[8px] text-slate-500">{s.desc}</span>
                    </div>
                ))}
            </div>
            <div className="h-px bg-gradient-to-r from-emerald-500/50 via-violet-500/50 to-cyan-500/50" />
            <div className="flex items-center gap-2 mt-3 text-[10px] text-slate-500 font-mono">
                <div className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
                Every container passes through all 6 stages before reaching production
            </div>
        </div>
    );
}

// ============================================
// WIREGUARD MESH TOPOLOGY VISUAL — Animated canvas
// ============================================
function MeshTopologyVisual() {
    const canvasRef = useRef<HTMLCanvasElement>(null);

    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas) return;
        const ctx = canvas.getContext('2d');
        if (!ctx) return;

        let animId = 0;
        let w = 0;
        let h = 0;

        const nodes = [
            { label: 'Master', region: 'Frankfurt', color: '#10b981', tier: 'control' as const },
            { label: 'EU-West', region: 'Berlin', color: '#3b82f6', tier: 'worker' as const },
            { label: 'EU-East', region: 'Warsaw', color: '#6366f1', tier: 'worker' as const },
            { label: 'US-East', region: 'NYC', color: '#f59e0b', tier: 'worker' as const },
            { label: 'US-West', region: 'SF', color: '#06b6d4', tier: 'worker' as const },
            { label: 'Asia', region: 'Singapore', color: '#a855f7', tier: 'worker' as const },
            { label: 'Oceania', region: 'Sydney', color: '#ec4899', tier: 'worker' as const },
            { label: 'S-Am', region: 'São Paulo', color: '#f97316', tier: 'worker' as const },
        ];

        const positions: { x: number; y: number }[] = [];
        const packets: { from: number; to: number; t: number; speed: number; color: string }[] = [];

        function layout() {
            positions.length = 0;
            const cx = w / 2;
            const cy = h / 2;
            const rx = w * 0.36;
            const ry = h * 0.36;
            positions.push({ x: cx, y: cy - ry * 0.85 });
            for (let i = 1; i < nodes.length; i++) {
                const angle = ((i - 1) / (nodes.length - 1)) * Math.PI * 2 - Math.PI / 2;
                positions.push({
                    x: cx + Math.cos(angle) * rx,
                    y: cy + Math.sin(angle) * ry * 0.9 + ry * 0.15,
                });
            }
        }

        function spawnPacket() {
            const from = Math.floor(Math.random() * nodes.length);
            let to = from;
            while (to === from) to = Math.floor(Math.random() * nodes.length);
            packets.push({
                from, to, t: 0,
                speed: 0.006 + Math.random() * 0.01,
                color: nodes[from].color,
            });
            if (packets.length > 40) packets.shift();
        }

        function resize() {
            const rect = canvas!.getBoundingClientRect();
            const dpr = window.devicePixelRatio || 1;
            w = rect.width * dpr;
            h = rect.height * dpr;
            canvas!.width = w;
            canvas!.height = h;
            ctx!.scale(dpr, dpr);
            w = rect.width;
            h = rect.height;
            layout();
        }

        function draw() {
            if (!ctx) return;
            const c = ctx;
            c.clearRect(0, 0, w, h);
            const time = Date.now() * 0.001;

            // Mesh connections — bold, enterprise
            for (let i = 0; i < nodes.length; i++) {
                for (let j = i + 1; j < nodes.length; j++) {
                    const a = positions[i];
                    const b = positions[j];
                    const dx = b.x - a.x;
                    const dy = b.y - a.y;
                    const dist = Math.sqrt(dx * dx + dy * dy);
                    const maxDist = Math.min(w, h) * 0.7;
                    if (dist > maxDist) continue;

                    const alpha = 0.1 + (1 - dist / maxDist) * 0.2;
                    c.strokeStyle = `rgba(16,185,129,${alpha})`;
                    c.lineWidth = 1.2;
                    c.setLineDash([6, 4]);
                    c.lineDashOffset = -time * 25;
                    c.beginPath();
                    c.moveTo(a.x, a.y);
                    c.lineTo(b.x, b.y);
                    c.stroke();
                    c.setLineDash([]);
                }
            }

            // Draw packets — bigger, bolder
            for (let i = packets.length - 1; i >= 0; i--) {
                const p = packets[i];
                p.t += p.speed;
                if (p.t >= 1) { packets.splice(i, 1); continue; }
                const a = positions[p.from];
                const b = positions[p.to];
                const x = a.x + (b.x - a.x) * p.t;
                const y = a.y + (b.y - a.y) * p.t;
                c.globalAlpha = 1 - p.t * 0.5;
                c.beginPath();
                c.arc(x, y, 3, 0, Math.PI * 2);
                c.fillStyle = p.color;
                c.fill();
                c.beginPath();
                c.arc(x, y, 8, 0, Math.PI * 2);
                c.fillStyle = p.color.replace(')', ',0.15)').replace('rgb', 'rgba');
                c.fill();
                c.globalAlpha = 1;
            }

            // Draw nodes — bigger, tiered sizing
            for (let i = 0; i < nodes.length; i++) {
                const n = nodes[i];
                const p = positions[i];
                const pulse = 0.7 + Math.sin(time * 2 + i) * 0.3;
                const r = n.tier === 'control' ? 16 : 10;

                // Outer glow
                c.globalAlpha = 0.12 * pulse;
                c.beginPath();
                c.arc(p.x, p.y, r * 3, 0, Math.PI * 2);
                c.fillStyle = n.color;
                c.fill();

                // Ring
                c.globalAlpha = 0.35;
                c.beginPath();
                c.arc(p.x, p.y, r * 1.6, 0, Math.PI * 2);
                c.strokeStyle = n.color;
                c.lineWidth = 1.5;
                c.stroke();

                // Body
                c.globalAlpha = 0.9;
                c.beginPath();
                c.arc(p.x, p.y, r, 0, Math.PI * 2);
                c.fillStyle = n.color;
                c.fill();

                // Center dot
                c.globalAlpha = 1;
                c.beginPath();
                c.arc(p.x, p.y, r * 0.3, 0, Math.PI * 2);
                c.fillStyle = '#fff';
                c.fill();

                // Labels
                c.globalAlpha = 0.85;
                c.font = 'bold 11px monospace';
                c.fillStyle = '#e2e8f0';
                c.textAlign = 'center';
                c.fillText(n.label, p.x, p.y + r + 14);
                c.globalAlpha = 0.5;
                c.font = '9px monospace';
                c.fillStyle = '#94a3b8';
                c.fillText(n.region, p.x, p.y + r + 26);
            }

            c.globalAlpha = 1;
            if (Math.random() < 0.18) spawnPacket();
            animId = requestAnimationFrame(draw);
        }

        resize();
        draw();
        window.addEventListener('resize', resize);
        return () => {
            window.removeEventListener('resize', resize);
            cancelAnimationFrame(animId);
        };
    }, []);

    return (
        <div className="w-full rounded-2xl overflow-hidden border border-slate-200 dark:border-slate-800 bg-[#0a0f1a]">
            <div className="px-5 py-3 border-b border-slate-800 flex items-center gap-3">
                <div className="flex gap-1.5">
                    <div className="w-3 h-3 rounded-full bg-red-500/80" />
                    <div className="w-3 h-3 rounded-full bg-yellow-500/80" />
                    <div className="w-3 h-3 rounded-full bg-green-500/80" />
                </div>
                <span className="text-xs font-mono text-slate-500">
                    WireGuard VPN Mesh — 8 Nodes · 28 Encrypted Links · Multi-Region
                </span>
            </div>
            <canvas ref={canvasRef} className="w-full h-[340px] md:h-[400px]" />
            <div className="px-5 py-3 border-t border-slate-800 flex flex-wrap items-center justify-between gap-3 text-[11px] font-mono text-slate-500">
                <span className="flex items-center gap-2">
                    <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
                    All traffic encrypted · WireGuard tunnel
                </span>
                <span>Avg latency: 12ms · 0 packet loss · 99.999% uptime</span>
            </div>
        </div>
    );
}

// ============================================
// TERMINAL MOCKUP
// ============================================
function TerminalMockup({ commands, height = 'h-[300px] md:h-[360px]' }: { commands: string[]; height?: string }) {
    return (
        <div className="bg-[#0f172a] rounded-2xl shadow-2xl overflow-hidden border border-slate-800 w-full">
            <div className="flex items-center justify-between px-4 py-3 border-b border-slate-800 bg-slate-900">
                <div className="flex gap-2">
                    <div className="w-3 h-3 rounded-full bg-red-500/80" />
                    <div className="w-3 h-3 rounded-full bg-amber-500/80" />
                    <div className="w-3 h-3 rounded-full bg-emerald-500/80" />
                </div>
                <div className="text-xs font-mono text-slate-500 flex items-center gap-1">
                    <Terminal className="w-3 h-3" /> user@grid-cluster:~
                </div>
                <div className="w-10" />
            </div>
            <div className={`p-6 font-mono text-sm overflow-x-auto leading-relaxed ${height}`}>
                <div className="text-slate-300 space-y-2.5">
                    {commands.map((cmd, i) => (
                        <div key={i} dangerouslySetInnerHTML={{ __html: cmd }} />
                    ))}
                </div>
            </div>
        </div>
    );
}

// ============================================
// PROBLEM CARDS
// ============================================
const problemCards = [
    {
        problem: "You're paying 3-10x too much for cloud hosting",
        description: 'AWS, GCP, Azure mark up compute by 300-1000%. Managed PaaS adds another layer. Your infrastructure costs are eating your runway.',
        icon: Server,
        color: 'text-red-500',
    },
    {
        problem: 'Vendor lock-in traps your business',
        description: "Once you build on a managed platform, you can't leave. Proprietary APIs, custom runtimes, opaque pricing. Your infrastructure becomes a liability.",
        icon: Lock,
        color: 'text-amber-500',
    },
    {
        problem: 'High availability is sold as a premium add-on',
        description: 'PostgreSQL replication, Redis failover, auto-scaling — these are table stakes. Yet every platform charges extra for basic reliability.',
        icon: Activity,
        color: 'text-blue-500',
    },
    {
        problem: 'DevOps complexity slows your team down',
        description: 'Kubernetes, Terraform, Helm charts, IAM policies. Your developers spend more time on infrastructure than building product.',
        icon: Cpu,
        color: 'text-violet-500',
    },
];

// ============================================
// SOLUTIONS
// ============================================
const solutions = [
    {
        title: 'Run on Your Own VPS',
        description: 'Connect any VPS from any provider. Grid orchestrates your infrastructure while you keep full ownership. Compute costs drop by up to 90%.',
        icon: Server,
        color: 'bg-emerald-100 text-emerald-600 dark:bg-emerald-500/20 dark:text-emerald-400',
    },
    {
        title: 'Built-In High Availability',
        description: 'PostgreSQL HA with Patroni streaming replication. Redis Sentinel auto-failover. WireGuard VPN mesh. All included. Zero extra cost.',
        icon: Network,
        color: 'bg-blue-100 text-blue-600 dark:bg-blue-500/20 dark:text-blue-400',
    },
    {
        title: 'AI Autoscaling & Self-Healing',
        description: 'Three autoscaler engines watch your metrics. AI detects anomalies and auto-remediates. Docker daemon down? Disk full? Grid fixes itself.',
        icon: Brain,
        color: 'bg-violet-100 text-violet-600 dark:bg-violet-500/20 dark:text-violet-400',
    },
    {
        title: 'Push to Deploy',
        description: 'Connect GitHub, GitLab, or Bitbucket. Every push triggers a Nixpacks build — any language, any framework. PR previews with full-stack environments.',
        icon: GitBranch,
        color: 'bg-cyan-100 text-cyan-600 dark:bg-cyan-500/20 dark:text-cyan-400',
    },
    {
        title: 'Multi-Cloud Deploy Anywhere',
        description: 'BYO-VPS (Hetzner, DO), AWS, GCP, Azure, bare metal, or air-gapped. No closed garden. Transfer services between nodes with zero downtime.',
        icon: Globe,
        color: 'bg-amber-100 text-amber-600 dark:bg-amber-500/20 dark:text-amber-400',
    },
    {
        title: '100% Open Source',
        description: 'AGPL v3 licensed. No open-core tricks. If Grid disappears, your workloads keep running as standard Docker containers on your servers.',
        icon: Code,
        color: 'bg-rose-100 text-rose-600 dark:bg-rose-500/20 dark:text-rose-400',
    },
];

// ============================================
// RARE FEATURES — No other PaaS does this
// ============================================
const rareFeatures = [
    {
        icon: Brain,
        title: 'AI Senate Committee',
        desc: '17 LLM providers. 2+ models deliberate on infrastructure decisions via Propose → Review → Synthesize. Multi-model consensus with per-user cost caps before any automated action.',
        stat: '17 providers',
        color: 'from-violet-500 to-purple-500',
        bg: 'bg-violet-500/10',
    },
    {
        icon: FileCode,
        title: 'Jules Auto-Fix Loop',
        desc: 'Failed deployment? Jules analyzes the error, opens a Pull Request with the fix, and deploys automatically. AI remediation that ships code, not just alerts.',
        stat: 'Auto-PR',
        color: 'from-emerald-500 to-teal-500',
        bg: 'bg-emerald-500/10',
    },
    {
        icon: ShieldCheck,
        title: 'SSRF Guard with DNS Rebind Protection',
        desc: 'Serverless runtime monkey-patches fetch/http/urllib to block RFC 1918, link-local, and cloud metadata IPs. Resolve-then-check — immune to DNS rebinding attacks.',
        stat: 'Runtime shield',
        color: 'from-red-500 to-rose-500',
        bg: 'bg-red-500/10',
    },
    {
        icon: Blocks,
        title: 'Custom Addon Bundles',
        desc: 'Declare Postgres, Redis, Meilisearch, MinIO clusters, Kafka, and 40+ database engines as infrastructure-as-code alongside your app. Full lifecycle: logs, health, backup, metrics, deprovision.',
        stat: 'Unlimited addons',
        color: 'from-indigo-500 to-blue-500',
        bg: 'bg-indigo-500/10',
    },
    {
        icon: Lock,
        title: 'Docker Socket Isolation',
        desc: 'Build containers never get direct Docker socket access. A read-only proxy mediates every command. No other PaaS isolates the build surface at this level.',
        stat: 'Read-only proxy',
        color: 'from-cyan-500 to-teal-500',
        bg: 'bg-cyan-500/10',
    },
    {
        icon: RefreshCw,
        title: 'Zero-Downtime Server Transfers',
        desc: 'Drag services between nodes with automatic backup, SSH transfer, restore, and DNS update. 48-hour rollback window with pre-transfer safety snapshot.',
        stat: 'Auto-rollback',
        color: 'from-amber-500 to-orange-500',
        bg: 'bg-amber-500/10',
    },
    {
        icon: Eye,
        title: 'Zero-Trust Node Attestation',
        desc: 'Every server proves its identity via challenge-response HMAC-SHA256 before joining the mesh. Per-node gateway secrets, encrypted Celery tasks, strict SSH host key verification.',
        stat: 'Crypto attested',
        color: 'from-blue-500 to-indigo-500',
        bg: 'bg-blue-500/10',
    },
    {
        icon: Wifi,
        title: 'Inter-Server TLS Enforcement',
        desc: 'TLS between all nodes with certificate validation. No plaintext inter-node traffic. `STRICT` mode by default — no trust-on-first-use for SSH.',
        stat: 'TLS enforced',
        color: 'from-emerald-500 to-cyan-500',
        bg: 'bg-emerald-500/10',
    },
    {
        icon: Shield,
        title: 'Fail-Closed Security Model',
        desc: 'If SECRET_KEY or encryption keys are missing, Grid crashes on boot — no silent fallback to hardcoded defaults. Defense in depth, not hope.',
        stat: 'Crash-secure',
        color: 'from-rose-500 to-pink-500',
        bg: 'bg-rose-500/10',
    },
];

// ============================================
// COMPARISON CARDS
// ============================================
const comparisons = [
    {
        name: 'Grid',
        logo: Cloud,
        description: 'The Sovereign PaaS',
        price: 'Free & Open Source',
        features: [
            'Run on your own VPS',
            'PostgreSQL HA built-in',
            'Redis Sentinel included',
            'AI predictive autoscaling',
            'Multi-Git provider support',
            'WireGuard VPN mesh',
            'Custom addon bundles',
            '100% open source (AGPL v3)',
        ],
        highlight: true,
    },
    {
        name: 'Managed PaaS',
        logo: Zap,
        description: 'Vercel / Railway / Heroku',
        price: '$20-36+/mo per seat',
        features: [
            'Platform lock-in',
            'No HA by default',
            'Opaque pricing at scale',
            'Proprietary runtime',
            'Limited git providers',
            'No VPN mesh',
            'No custom addons',
            'Open-core only',
        ],
        highlight: false,
    },
    {
        name: 'Cloud Giants',
        logo: Server,
        description: 'AWS / GCP / Azure',
        price: 'Variable + hidden costs',
        features: [
            'Extreme vendor lock-in',
            'Complex IAM & VPC setup',
            'Unpredictable billing',
            'Requires DevOps team',
            'Manual HA configuration',
            'Separate VPN service',
            'No addon system',
            'Proprietary everything',
        ],
        highlight: false,
    },
];

// ============================================
// HA FEATURES
// ============================================
const haFeatures = [
    {
        icon: Database,
        title: 'PostgreSQL HA Streaming Replication',
        desc: 'Patroni-managed primary with streaming replicas. Automatic failover in seconds. PgCat read/write splitting for zero-downtime upgrades.',
        color: 'from-blue-500 to-cyan-500',
        bg: 'bg-blue-500/10',
    },
    {
        icon: Activity,
        title: 'Redis Sentinel HA',
        desc: 'Automatic cache and broker failover via Redis Sentinel. Configurable quorum, replica priorities, and down-after-milliseconds tuning.',
        color: 'from-red-500 to-rose-500',
        bg: 'bg-red-500/10',
    },
    {
        icon: Cpu,
        title: 'AI-Powered Autoscaler',
        desc: 'Three engines: Classic CPU hysteresis, AI-enhanced with Prometheus + Loki anomaly detection, and K8s/Docker admin surface.',
        color: 'from-violet-500 to-purple-500',
        bg: 'bg-violet-500/10',
    },
    {
        icon: HardDrive,
        title: 'Disaster Recovery',
        desc: 'Tiered backup schedules (6h/24h/7d), cloud replication to S3/R2/MinIO, encryption key rotation with multi-key support.',
        color: 'from-amber-500 to-orange-500',
        bg: 'bg-amber-500/10',
    },
    {
        icon: Network,
        title: 'WireGuard VPN Mesh',
        desc: 'Encrypted node-to-node mesh networking across your fleet. Auto-allocated IPs, per-peer latency tracking, multiple named meshes.',
        color: 'from-indigo-500 to-blue-500',
        bg: 'bg-indigo-500/10',
    },
    {
        icon: Shield,
        title: 'Self-Healing Orchestration',
        desc: 'Failure classification: Docker daemon down, disk full, OOM. Auto-escalates to AI after 5 failed attempts with intelligent remediation.',
        color: 'from-emerald-500 to-teal-500',
        bg: 'bg-emerald-500/10',
    },
];

// ============================================
// ECOSYSTEM PILLARS
// ============================================
const ecosystemPillars = [
    { label: 'Build', title: 'Grid PaaS', desc: 'Free open-source PaaS. Deploy entire ecosystems on your infrastructure with zero lock-in.', icon: Rocket, color: 'text-emerald-400', bg: 'bg-emerald-500/20', border: 'hover:border-emerald-500/50' },
    { label: 'Secure', title: 'Security Gateway', desc: 'Zero-trust routing and policy enforcement. Real-time threat detection across all channels.', icon: Shield, color: 'text-violet-400', bg: 'bg-violet-500/20', border: 'hover:border-violet-500/50' },
    { label: 'Verify', title: 'Identity Service', desc: 'High-assurance identity management. Carrier-level verification with SilentOTP™.', icon: Lock, color: 'text-blue-400', bg: 'bg-blue-500/20', border: 'hover:border-blue-500/50' },
    { label: 'Communicate', title: 'Global Messaging', desc: 'SMS, WhatsApp, Voice, and Email. Cryptographic proof of delivery built on Transaction Chain™.', icon: Globe, color: 'text-cyan-400', bg: 'bg-cyan-500/20', border: 'hover:border-cyan-500/50' },
    { label: 'Grow', title: 'Ignite', desc: 'AI-powered marketing that runs itself. Trend intel, leads, and content on autopilot.', icon: Sparkles, color: 'text-amber-400', bg: 'bg-amber-500/20', border: 'hover:border-amber-500/50' },
];

// ============================================
// STATS — Live platform numbers
// ============================================
const platformStats = [
    { value: '17', label: 'AI Providers', sub: 'Senate Committee consensus' },
    { value: '40+', label: 'Addons', sub: 'Postgres, Redis, Kafka...' },
    { value: '5', label: 'Deploy Types', sub: 'Git, Docker, Upload, Template, Function' },
    { value: '0', label: 'Vendor Lock-In', sub: 'Standard Docker containers' },
];

export default function Home() {
    const [current, setCurrent] = useState(0);
    const [isHovered, setIsHovered] = useState(false);

    useEffect(() => {
        if (isHovered) return;
        const timer = setInterval(() => {
            setCurrent((prev) => (prev + 1) % heroSlides.length);
        }, 7000);
        return () => clearInterval(timer);
    }, [isHovered]);

    return (
        <main className="min-h-screen relative overflow-x-hidden">

            {/* ============================================
                HERO — Operational grid, one clear message
               ============================================ */}
            <section className="relative min-h-[70vh] md:min-h-[78vh] pt-32 pb-28 md:pb-32 overflow-hidden">
                <div className="relative max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 text-center z-10"
                    onMouseEnter={() => setIsHovered(true)}
                    onMouseLeave={() => setIsHovered(false)}
                >
                    {/* Badge */}
                    <AnimatePresence mode="wait">
                        <motion.div
                            key={heroSlides[current].badge}
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            exit={{ opacity: 0, y: -20 }}
                            transition={{ duration: 0.35 }}
                            className="inline-flex items-center gap-2 px-4 py-2 bg-emerald-50 dark:bg-emerald-500/10 border border-emerald-100 dark:border-emerald-500/20 rounded-full mb-8 shadow-sm"
                        >
                            <span className="flex h-2 w-2 rounded-full bg-emerald-500 animate-pulse" />
                            <span className="text-sm font-semibold text-emerald-800 dark:text-emerald-400">{heroSlides[current].badge}</span>
                        </motion.div>
                    </AnimatePresence>

                    {/* Heading */}
                    <AnimatePresence mode="wait">
                        <motion.h1
                            key={heroSlides[current].heading}
                            initial={{ opacity: 0, y: 30 }}
                            animate={{ opacity: 1, y: 0 }}
                            exit={{ opacity: 0, y: -30 }}
                            transition={{ duration: 0.35 }}
                            className="text-4xl md:text-5xl lg:text-6xl font-extrabold text-slate-900 dark:text-white tracking-tight leading-tight mb-6"
                        >
                            {heroSlides[current].heading}
                            <span className="text-transparent bg-clip-text bg-gradient-to-r from-emerald-600 to-teal-500">
                                {heroSlides[current].gradient}
                            </span>
                        </motion.h1>
                    </AnimatePresence>

                    {/* Subtitle */}
                    <AnimatePresence mode="wait">
                        <motion.p
                            key={heroSlides[current].subtitle}
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            exit={{ opacity: 0, y: -20 }}
                            transition={{ duration: 0.35 }}
                            className="text-lg md:text-xl text-slate-600 dark:text-slate-400 max-w-3xl mx-auto leading-relaxed font-medium"
                        >
                            {heroSlides[current].subtitle}
                        </motion.p>
                    </AnimatePresence>

                    {/* Dots */}
                    <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        transition={{ delay: 0.3, duration: 0.5 }}
                        className="mt-8 flex items-center justify-center gap-3"
                    >
                        {heroSlides.map((_, i) => (
                            <button
                                key={i}
                                onClick={() => setCurrent(i)}
                                className={`w-2.5 h-2.5 rounded-full transition-all duration-300 ${
                                    i === current
                                        ? "bg-emerald-500 w-8"
                                        : "bg-slate-300 dark:bg-slate-600 hover:bg-emerald-300 dark:hover:bg-emerald-600"
                                }`}
                                aria-label={`Go to slide ${i + 1}`}
                            />
                        ))}
                    </motion.div>

                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.3, duration: 0.5 }}
                        className="mt-10 flex flex-col sm:flex-row gap-4 justify-center items-center"
                    >
                        <Link href="/register" className="inline-flex items-center justify-center gap-2 px-8 py-4 text-base font-bold text-white bg-emerald-600 rounded-xl hover:bg-emerald-700 transition-all hover:-translate-y-0.5">
                            Get Grid Free <ArrowRight className="w-5 h-5" />
                        </Link>
                        <Link href="/docs" className="inline-flex items-center justify-center gap-2 px-8 py-4 text-base font-bold text-slate-700 dark:text-slate-200 bg-white dark:bg-white/5 border-2 border-slate-200 dark:border-slate-700 rounded-xl hover:border-emerald-300 dark:hover:border-emerald-500/30 transition-all hover:-translate-y-0.5">
                            Read Documentation
                        </Link>
                    </motion.div>

                    <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        transition={{ delay: 0.5 }}
                        className="mt-12 pt-8 border-t border-slate-100 dark:border-slate-800 flex flex-wrap justify-center gap-x-12 gap-y-4 text-sm font-semibold text-slate-500 dark:text-slate-400"
                    >
                        <span className="flex items-center gap-2"><CheckCircle2 className="w-4 h-4 text-emerald-500" /> Free & Open Source (AGPL v3)</span>
                        <span className="flex items-center gap-2"><Shield className="w-4 h-4 text-emerald-500" /> Zero-Trust Multi-Server</span>
                        <span className="flex items-center gap-2"><Brain className="w-4 h-4 text-emerald-500" /> AI Senate Auto-Remediation</span>
                        <span className="flex items-center gap-2"><Globe className="w-4 h-4 text-emerald-500" /> Deploy Anywhere</span>
                    </motion.div>
                </div>
            </section>

            {/* ============================================
                DASHBOARD SHOWCASE
               ============================================ */}
            <section className="relative -mt-12 md:-mt-24 z-20 px-4 sm:px-6 lg:px-8 max-w-5xl mx-auto mb-16 md:mb-24">
                <motion.div
                    initial={{ opacity: 0, y: 40 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.6, duration: 0.7 }}
                >
                    <DashboardMockup />
                </motion.div>
            </section>

            {/* ============================================
                PLATFORM STATS — Key numbers
               ============================================ */}
            <section className="py-16 border-t border-slate-100 dark:border-slate-800">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <ScrollReveal variant="staggerContainer">
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-8">
                            {platformStats.map((stat, i) => (
                                <StaggerChild key={i}>
                                    <motion.div
                                        whileHover={{ scale: 1.05 }}
                                        transition={{ type: "spring", stiffness: 400, damping: 25 }}
                                        className="text-center"
                                    >
                                        <div className="text-3xl md:text-4xl font-extrabold text-emerald-600 dark:text-emerald-400 mb-1">{stat.value}</div>
                                        <div className="text-sm font-bold text-slate-900 dark:text-white mb-1">{stat.label}</div>
                                        <div className="text-xs text-slate-500 dark:text-slate-400">{stat.sub}</div>
                                    </motion.div>
                                </StaggerChild>
                            ))}
                        </div>
                    </ScrollReveal>
                </div>
            </section>

            {/* ============================================
                THE PROBLEM
               ============================================ */}
            <section className="py-20">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="text-center mb-16">
                        <h2 className="text-3xl md:text-4xl font-bold text-slate-900 dark:text-white mb-4">
                            Cloud hosting is broken.
                        </h2>
                        <p className="text-lg text-slate-600 dark:text-slate-400 max-w-2xl mx-auto font-medium">
                            These aren&apos;t edge cases. They&apos;re the standard experience for teams deploying to the cloud.
                        </p>
                    </div>

                    <ScrollReveal variant="staggerContainer">
                        <div className="grid md:grid-cols-2 lg:grid-cols-4 gap-6">
                            {problemCards.map((card, i) => (
                                <StaggerChild key={i}>
                                    <motion.div
                                        whileHover={{ y: -4, boxShadow: "0 20px 40px -12px rgba(0,0,0,0.15)" }}
                                        transition={{ type: "spring", stiffness: 400, damping: 25 }}
                                        className="bg-white dark:bg-white/5 p-6 rounded-2xl border border-slate-200 dark:border-slate-700 hover:border-slate-300 dark:hover:border-slate-600 h-full"
                                    >
                                        <card.icon className={`w-8 h-8 ${card.color} mb-4`} />
                                        <h3 className="text-lg font-bold text-slate-900 dark:text-white mb-2">{card.problem}</h3>
                                        <p className="text-sm text-slate-600 dark:text-slate-400 leading-relaxed">{card.description}</p>
                                    </motion.div>
                                </StaggerChild>
                            ))}
                        </div>
                    </ScrollReveal>
                </div>
            </section>

            {/* ============================================
                HOW GRID SOLVES IT
               ============================================ */}
            <section className="py-20">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="text-center mb-16">
                        <h2 className="text-3xl md:text-4xl font-bold text-slate-900 dark:text-white mb-4">
                            How Grid Solves It
                        </h2>
                        <p className="text-lg text-slate-600 dark:text-slate-400 max-w-2xl mx-auto font-medium">
                            Each problem gets a platform-level architectural solution. Not patches. Permanent fixes.
                        </p>
                    </div>

                    <ScrollReveal variant="staggerContainer">
                        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-8">
                            {solutions.map((sol, i) => (
                                <StaggerChild key={i}>
                                    <motion.div
                                        whileHover={{ y: -6 }}
                                        transition={{ type: "spring", stiffness: 300, damping: 20 }}
                                        className="group relative bg-white dark:bg-white/5 p-8 rounded-2xl border border-slate-200 dark:border-slate-700 hover:border-emerald-300 dark:hover:border-emerald-500/30 h-full"
                                    >
                                        <div className={`w-12 h-12 rounded-xl ${sol.color} flex items-center justify-center mb-6`}>
                                            <sol.icon className="w-6 h-6" />
                                        </div>
                                        <h3 className="text-xl font-bold text-slate-900 dark:text-white mb-3">{sol.title}</h3>
                                        <p className="text-sm text-slate-600 dark:text-slate-400 leading-relaxed">{sol.description}</p>
                                    </motion.div>
                                </StaggerChild>
                            ))}
                        </div>
                    </ScrollReveal>
                </div>
            </section>

            {/* ============================================
                RARE FEATURES — No Other PaaS Does This
               ============================================ */}
            <section className="py-24 relative overflow-hidden">
                <div className="relative max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="text-center mb-16">
                        <div className="inline-flex items-center gap-2 px-3 py-1.5 bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-xs font-bold rounded-full mb-6">
                            <Sparkles className="w-3 h-3" />
                            Unique Capabilities
                        </div>
                        <h2 className="text-3xl md:text-4xl lg:text-5xl font-bold text-white mb-6">
                            No other PaaS does this.
                        </h2>
                        <p className="text-lg text-slate-400 max-w-3xl mx-auto font-medium leading-relaxed">
                            Features that separate Grid from every other platform. Built-in,
                            not bolted on. Open-source, not proprietary.
                        </p>
                    </div>

                    <ScrollReveal variant="staggerContainer">
                        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
                            {rareFeatures.map((feat, i) => (
                                <StaggerChild key={i}>
                                    <motion.div
                                        whileHover={{ y: -6, borderColor: "rgba(16,185,129,0.3)" }}
                                        transition={{ type: "spring", stiffness: 300, damping: 20 }}
                                        className="group relative bg-slate-900/80 p-8 rounded-2xl border border-slate-800 h-full"
                                    >
                                        <div className="relative z-10">
                                            <div className={`w-12 h-12 rounded-xl ${feat.bg} flex items-center justify-center mb-6`}>
                                                <feat.icon className={`w-6 h-6 bg-gradient-to-br ${feat.color} text-white`} />
                                            </div>
                                            <h3 className="text-xl font-bold text-white mb-3">{feat.title}</h3>
                                            <p className="text-sm text-slate-400 leading-relaxed mb-4">{feat.desc}</p>
                                            <div className="inline-flex items-center gap-1.5 px-3 py-1 bg-slate-800 rounded-full">
                                                <Zap className="w-3 h-3 text-emerald-400" />
                                                <span className="text-xs font-bold text-slate-300">{feat.stat}</span>
                                            </div>
                                        </div>
                                    </motion.div>
                                </StaggerChild>
                            ))}
                        </div>
                    </ScrollReveal>
                </div>
            </section>

            {/* ============================================
                ECOSYSTEM DEPLOY — Full Visual
               ============================================ */}
            <section className="py-24 relative overflow-hidden">
                <div className="relative max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <ScrollReveal variant="fadeUp">
                        <div className="text-center mb-12">
                            <div className="inline-flex items-center gap-2 px-3 py-1.5 bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-xs font-bold rounded-full mb-6">
                                <Rocket className="w-3 h-3" />
                                Ecosystem Deploy
                            </div>
                            <h2 className="text-3xl md:text-4xl lg:text-5xl font-bold text-white mb-6">
                                One Command. Full Stack.
                            </h2>
                            <p className="text-lg text-slate-400 max-w-3xl mx-auto font-medium leading-relaxed">
                                AI scans your repos, generates a deploy plan with dependency-aware waves,
                                then orchestrates the full stack — addons first, services in topological order.
                                All addons auto-provisioned. Zero manual config.
                            </p>
                        </div>
                    </ScrollReveal>
                    <ScrollReveal variant="scaleIn" delay={0.2}>
                        <EcosystemDeployVisual />
                    </ScrollReveal>
                </div>
            </section>

            {/* ============================================
                HIGH AVAILABILITY INFRASTRUCTURE
               ============================================ */}
            <section className="py-24">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="text-center mb-16">
                        <div className="inline-flex items-center gap-2 px-3 py-1.5 bg-blue-500/10 border border-blue-500/20 text-blue-600 dark:text-blue-400 text-xs font-bold rounded-full mb-6">
                            <HardDrive className="w-3 h-3" />
                            High Availability
                        </div>
                        <h2 className="text-3xl md:text-4xl lg:text-5xl font-bold text-slate-900 dark:text-white mb-6 tracking-tight">
                            Zero Downtime. Every Layer.
                        </h2>
                        <p className="text-lg text-slate-600 dark:text-slate-400 max-w-3xl mx-auto font-medium leading-relaxed">
                            PostgreSQL streaming replication with Patroni. Redis Sentinel auto-failover.
                            AI-powered predictive autoscaling. Disaster recovery with defined RPO/RTO targets.
                            Your infrastructure survives anything.
                        </p>
                    </div>

                    <ScrollReveal variant="staggerContainer">
                        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6 mb-12">
                            {haFeatures.map((feat, i) => (
                                <StaggerChild key={i}>
                                    <motion.div
                                        whileHover={{ y: -4, scale: 1.01 }}
                                        transition={{ type: "spring", stiffness: 400, damping: 25 }}
                                        className="group relative bg-white dark:bg-white/5 p-8 rounded-2xl border border-slate-200 dark:border-slate-700 hover:border-slate-300 dark:hover:border-slate-600 h-full"
                                    >
                                        <div className="relative z-10">
                                            <div className={`w-12 h-12 rounded-xl ${feat.bg} flex items-center justify-center mb-6`}>
                                                <feat.icon className={`w-6 h-6 bg-gradient-to-br ${feat.color} text-white`} />
                                            </div>
                                            <h3 className="text-xl font-bold text-slate-900 dark:text-white mb-3">{feat.title}</h3>
                                            <p className="text-sm text-slate-600 dark:text-slate-400 leading-relaxed">{feat.desc}</p>
                                        </div>
                                    </motion.div>
                                </StaggerChild>
                            ))}
                        </div>
                    </ScrollReveal>

                    <ScrollReveal variant="scaleIn" delay={0.1}>
                        <div className="max-w-3xl mx-auto p-6 bg-blue-50 dark:bg-blue-500/10 border border-blue-200 dark:border-blue-500/20 rounded-2xl">
                            <div className="flex items-center gap-3 mb-2">
                                <Shield className="w-5 h-5 text-blue-600 dark:text-blue-400" />
                                <span className="font-bold text-blue-800 dark:text-blue-300">Battle-tested HA. Not a paid add-on.</span>
                            </div>
                            <p className="text-sm text-blue-700 dark:text-blue-400/80 font-medium">
                                PostgreSQL streaming replication, Redis Sentinel, AI autoscaling, and disaster recovery
                                are all built-in. No extra infrastructure, no vendor lock-in, no per-seat fees.
                            </p>
                        </div>
                    </ScrollReveal>

                    <ScrollReveal variant="fadeUp" delay={0.3}>
                        <div className="mt-10 w-full">
                            <MeshTopologyVisual />
                        </div>
                    </ScrollReveal>
                </div>
            </section>

            {/* ============================================
                DEVELOPER EXPERIENCE — Dashboard + Terminal
               ============================================ */}
            <section className="py-24">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="flex flex-col lg:flex-row items-center gap-16">
                        <ScrollReveal variant="slideLeft" className="lg:w-1/2">
                            <div className="inline-flex items-center gap-2 px-3 py-1 bg-emerald-100 dark:bg-emerald-500/20 text-emerald-700 dark:text-emerald-400 text-sm font-bold rounded-full mb-6">
                                <Terminal className="w-3.5 h-3.5" />
                                Developer First
                            </div>
                            <h2 className="text-3xl md:text-4xl font-bold text-slate-900 dark:text-white mb-6">
                                Control your infrastructure from anywhere.
                            </h2>
                            <p className="text-lg text-slate-600 dark:text-slate-400 mb-8 font-medium leading-relaxed">
                                The <code className="bg-slate-200 dark:bg-slate-800 px-1.5 py-0.5 rounded font-mono text-emerald-600 dark:text-emerald-400 text-sm">grid</code> CLI +
                                a real-time dashboard. Every push triggers a full pipeline:
                                <strong> build → scan → sign → deploy → health check → monitor</strong>.
                                AI auto-remediates failures. Zero-downtime blue-green releases.
                            </p>

                            <ScrollReveal variant="staggerContainer">
                                <div className="flex flex-col gap-5">
                                    {[
                                        { icon: Zap, title: 'Full Pipeline in Seconds', desc: 'Build → Scan → Sign → Deploy → Health Check. Blue-green releases with zero downtime.', color: 'bg-emerald-100 text-emerald-600 dark:bg-emerald-500/20 dark:text-emerald-400' },
                                        { icon: Code, title: 'Any Language, Any Framework', desc: 'Nixpacks auto-detects and builds. 5 deployment types: Git, Docker, Upload, Template, Function.', color: 'bg-blue-100 text-blue-600 dark:bg-blue-500/20 dark:text-blue-400' },
                                        { icon: ShieldCheck, title: 'Signed & Hardened', desc: 'Trivy CVE scan, Cosign image signing, gVisor sandbox, Falco syscall monitoring. Every deploy.', color: 'bg-amber-100 text-amber-600 dark:bg-amber-500/20 dark:text-amber-400' },
                                        { icon: Brain, title: 'AI Self-Healing', desc: 'AI Guardian watches post-deploy. Detects anomalies, auto-remediates, opens PRs for failures.', color: 'bg-violet-100 text-violet-600 dark:bg-violet-500/20 dark:text-violet-400' },
                                    ].map((item, i) => (
                                        <StaggerChild key={i}>
                                            <div className="flex items-start gap-4 group">
                                                <div className={`p-2.5 rounded-xl ${item.color} group-hover:scale-105 transition-transform flex-shrink-0`}>
                                                    <item.icon className="w-5 h-5" />
                                                </div>
                                                <div>
                                                    <h4 className="font-bold text-slate-900 dark:text-white">{item.title}</h4>
                                                    <p className="text-sm text-slate-500 dark:text-slate-400 font-medium">{item.desc}</p>
                                                </div>
                                            </div>
                                        </StaggerChild>
                                    ))}
                                </div>
                            </ScrollReveal>
                        </ScrollReveal>

                        <ScrollReveal variant="slideRight" className="lg:w-1/2 w-full">
                            <TerminalMockup height="h-[420px] md:h-[480px]" commands={[
                                '<span class="text-slate-500"># ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━</span>',
                                '<span class="text-slate-500">#  Grid — Full Deployment Pipeline</span>',
                                '<span class="text-slate-500"># ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━</span>',
                                '',
                                '<span class="text-emerald-400">➜</span> <span class="text-blue-400">~/my-app</span> <span class="text-slate-400">git push origin main</span>',
                                '',
                                '<span class="text-violet-400">⚡</span> <span class="text-slate-300">Webhook received</span> <span class="text-slate-500">— triggering deploy pipeline</span>',
                                '',
                                '<span class="text-slate-500">┌─ Phase 1: Build ─────────────────────────────┐</span>',
                                '<span class="text-slate-500">│</span> <span class="text-blue-400">Nixpacks</span> <span class="text-slate-500">auto-detecting framework...</span>',
                                '<span class="text-slate-500">│</span> <span class="text-emerald-500">✔</span> Detected <span class="text-white font-bold">Next.js 15</span> <span class="text-slate-500">+ TypeScript + PostgreSQL</span>',
                                '<span class="text-slate-500">│</span> <span class="text-emerald-500">✔</span> Installing dependencies <span class="text-slate-500">(npm ci)</span>',
                                '<span class="text-slate-500">│</span> <span class="text-emerald-500">✔</span> Running build <span class="text-slate-500">(next build)</span>',
                                '<div class="w-full bg-slate-800 h-1 my-1 rounded-full overflow-hidden"><div class="bg-emerald-500 h-full w-full"></div></div>',
                                '<span class="text-slate-500">│</span> <span class="text-emerald-500">✔</span> Build completed in <span class="text-white font-bold">18.4s</span>',
                                '',
                                '<span class="text-slate-500">┌─ Phase 2: Security Scan ──────────────────────┐</span>',
                                '<span class="text-slate-500">│</span> <span class="text-blue-400">Trivy</span> <span class="text-slate-500">scanning container image for CVEs...</span>',
                                '<span class="text-slate-500">│</span> <span class="text-emerald-500">✔</span> 0 critical, 0 high, 2 low <span class="text-slate-500">(acceptable)</span>',
                                '<span class="text-slate-500">│</span> <span class="text-blue-400">Cosign</span> <span class="text-slate-500">signing image with Sigstore keyless...</span>',
                                '<span class="text-slate-500">│</span> <span class="text-emerald-500">✔</span> Signature verified <span class="text-slate-500">(OIDC identity: deploy@grid)</span>',
                                '<span class="text-slate-500">│</span> <span class="text-blue-400">Falco</span> <span class="text-slate-500">baseline syscall profile updated</span>',
                                '',
                                '<span class="text-slate-500">┌─ Phase 3: Deploy ─────────────────────────────┐</span>',
                                '<span class="text-slate-500">│</span> <span class="text-slate-500">Blue-green: spinning up</span> <span class="text-white">my-app@v47</span>',
                                '<span class="text-slate-500">│</span> <span class="text-emerald-500">✔</span> Container started <span class="text-slate-500">(gVisor sandbox, 512MB/2GB)</span>',
                                '<span class="text-slate-500">│</span> <span class="text-emerald-500">✔</span> WireGuard mesh <span class="text-slate-500">peer registered (10.0.1.42)</span>',
                                '<span class="text-slate-500">│</span> <span class="text-emerald-500">✔</span> Traefik routing <span class="text-slate-500">→ my-app.grid.internal</span>',
                                '<span class="text-slate-500">│</span> <span class="text-emerald-500">✔</span> SSL certificate <span class="text-slate-500">provisioned (Let\'s Encrypt)</span>',
                                '',
                                '<span class="text-slate-500">┌─ Phase 4: Health & Release ────────────────────┐</span>',
                                '<span class="text-slate-500">│</span> <span class="text-blue-400">Health check</span> <span class="text-slate-500">— GET /health → 200 OK</span>',
                                '<span class="text-slate-500">│</span> <span class="text-blue-400">gVisor</span> <span class="text-slate-500">sandbox attestation passed</span>',
                                '<span class="text-slate-500">│</span> <span class="text-emerald-500">✔</span> Traffic switched <span class="text-slate-500">→ live (0ms downtime)</span>',
                                '<span class="text-slate-500">│</span> <span class="text-blue-400">AI Guardian</span> <span class="text-slate-500">monitoring enabled (auto-remediate)</span>',
                                '',
                                '<span class="text-emerald-500">✔</span> <span class="text-white font-bold">Deploy complete</span> <span class="text-slate-500">— v47 live in 23s</span>',
                                '<span class="text-emerald-500">✔</span> <span class="text-blue-400 underline">https://my-app.example.app</span>',
                                '<span class="text-emerald-500">✔</span> <span class="text-slate-500">PostgreSQL HA bound · Redis Sentinel connected</span>',
                                '',
                                '<span class="text-emerald-400">➜</span> <span class="text-blue-400">~/my-app</span> <span class="animate-pulse">_</span>',
                            ]} />
                        </ScrollReveal>
                    </div>
                </div>
            </section>

            {/* ============================================
                COMPARISON
               ============================================ */}
            <section className="py-24">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="text-center mb-16">
                        <h2 className="text-3xl md:text-4xl font-bold text-slate-900 dark:text-white mb-4">
                            Stop Paying the Cloud Tax
                        </h2>
                        <p className="text-lg text-slate-600 dark:text-slate-400 max-w-2xl mx-auto font-medium">
                            Grid runs on <strong>your infrastructure</strong>.
                            No managed service markup. No per-seat pricing.
                        </p>
                    </div>

                    <ScrollReveal variant="staggerContainer">
                        <div className="grid md:grid-cols-3 gap-8 max-w-5xl mx-auto">
                            {comparisons.map((card, i) => (
                                <StaggerChild key={i}>
                                    <motion.div
                                        whileHover={{ y: -6, scale: 1.02 }}
                                        transition={{ type: "spring", stiffness: 300, damping: 20 }}
                                        className={`relative p-8 rounded-3xl bg-white dark:bg-white/5 border-2 ${card.highlight ? 'border-emerald-500 shadow-xl shadow-emerald-500/10' : 'border-slate-200 dark:border-slate-700'} flex flex-col h-full`}
                                    >
                                        {card.highlight && (
                                            <div className="absolute -top-3 left-1/2 -translate-x-1/2 px-3 py-1 bg-emerald-500 text-white text-xs font-bold uppercase tracking-widest rounded-full shadow-md">
                                                Best Value
                                            </div>
                                        )}

                                        <div className="mb-6 flex items-center gap-4">
                                            <div className={`p-3 rounded-2xl ${card.highlight ? 'bg-emerald-500/20' : 'bg-slate-100 dark:bg-slate-800'}`}>
                                                <card.logo className={`w-8 h-8 ${card.highlight ? 'text-emerald-400' : 'text-slate-500'}`} />
                                            </div>
                                            <div>
                                                <h3 className="text-xl font-bold text-slate-900 dark:text-white">{card.name}</h3>
                                                <p className="text-xs text-slate-400">{card.description}</p>
                                            </div>
                                        </div>

                                        <div className="mb-8">
                                            <span className="text-3xl font-extrabold text-slate-900 dark:text-white">{card.price}</span>
                                        </div>

                                        <ul className="space-y-3 mb-8 flex-1">
                                            {card.features.map((feat, j) => (
                                                <li key={j} className="flex items-center gap-3 text-sm font-medium text-slate-600 dark:text-slate-300">
                                                    {card.highlight ? (
                                                        <CheckCircle2 className="w-5 h-5 text-emerald-500 flex-shrink-0" />
                                                    ) : (
                                                        <span className="w-5 h-5 flex items-center justify-center flex-shrink-0 text-slate-400">—</span>
                                                    )}
                                                    {feat}
                                                </li>
                                            ))}
                                        </ul>

                                        <Link
                                            href={card.highlight ? '/register' : '/docs'}
                                            className={`w-full py-3 rounded-xl text-sm font-bold text-center transition-all ${
                                                card.highlight
                                                    ? 'bg-emerald-500 hover:bg-emerald-600 text-white shadow-md'
                                                    : 'bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 text-slate-700 dark:text-slate-300'
                                            }`}
                                        >
                                            {card.highlight ? 'Install Grid Free' : 'Learn More'}
                                        </Link>
                                    </motion.div>
                                </StaggerChild>
                            ))}
                        </div>
                    </ScrollReveal>
                </div>
            </section>

            {/* ============================================
                ECOSYSTEM — Secured by Trulay
               ============================================ */}
            <section className="py-24 relative overflow-hidden">
                <div className="relative max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="text-center mb-16">
                        <div className="inline-flex items-center gap-2 px-3 py-1.5 bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-xs font-bold rounded-full mb-6">
                            <Rocket className="w-3 h-3" />
                            The Ecosystem Behind Grid
                        </div>
                        <h2 className="text-3xl md:text-4xl lg:text-5xl font-bold text-white mb-6">
                            Secured by Trulay
                        </h2>
                        <p className="text-lg text-slate-400 max-w-3xl mx-auto font-medium leading-relaxed">
                            Grid is one product in the Trulay ecosystem — the trust layer for internet
                            communications, building the tools modern businesses need to communicate, verify, deploy, and grow.
                        </p>
                    </div>

                    {/* Trulay Pillars */}
                    <ScrollReveal variant="staggerContainer">
                        <div className="grid md:grid-cols-5 gap-4 mb-16">
                            {ecosystemPillars.map((pillar, i) => (
                                <StaggerChild key={i}>
                                    <motion.div
                                        whileHover={{ y: -6, scale: 1.03 }}
                                        transition={{ type: "spring", stiffness: 300, damping: 20 }}
                                        className={`group bg-slate-900/80 border border-slate-800 rounded-2xl p-6 ${pillar.border} h-full`}
                                    >
                                        <div className="flex items-center gap-3 mb-4">
                                            <div className={`w-10 h-10 ${pillar.bg} rounded-lg flex items-center justify-center`}>
                                                <pillar.icon className={`w-5 h-5 ${pillar.color}`} />
                                            </div>
                                            <span className={`text-xs font-bold ${pillar.color} uppercase tracking-wider`}>{pillar.label}</span>
                                        </div>
                                        <h3 className="text-xl font-bold text-white mb-2">{pillar.title}</h3>
                                        <p className="text-sm text-slate-400 leading-relaxed">{pillar.desc}</p>
                                    </motion.div>
                                </StaggerChild>
                            ))}
                        </div>
                    </ScrollReveal>

                    <div className="text-center mt-10">
                        <Link
                            href="https://Trulay.co"
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex items-center gap-2 px-6 py-3 bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 font-bold rounded-xl hover:bg-emerald-500/20 transition-all"
                        >
                            Explore Trulay <ArrowUpRight className="w-4 h-4" />
                        </Link>
                    </div>
                </div>
            </section>

            {/* ============================================
                SECURITY & COMPLIANCE
               ============================================ */}
            <section className="py-24">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="flex flex-col lg:flex-row items-center gap-16">
                        <ScrollReveal variant="slideLeft" className="lg:w-1/2">
                            <div className="inline-flex items-center gap-2 px-3 py-1 bg-rose-100 dark:bg-rose-500/20 text-rose-700 dark:text-rose-400 text-sm font-bold rounded-full mb-6">
                                <Shield className="w-3.5 h-3.5" />
                                Enterprise Security
                            </div>
                            <h2 className="text-3xl md:text-4xl font-bold text-slate-900 dark:text-white mb-6">
                                Hardened for Production
                            </h2>
                            <p className="text-lg text-slate-600 dark:text-slate-400 mb-8 font-medium leading-relaxed">
                                Every container runs in its own gVisor sandbox. Falco monitors
                                syscalls for anomalies in real-time. fail2ban blocks intrusion attempts.
                                Trivy scans for CVEs at push and daily. Cosign verifies every image
                                before deployment. Scoped registry RBAC controls who can pull what.
                            </p>
                            <ScrollReveal variant="staggerContainer">
                                <ul className="space-y-4 mb-8">
                                    {[
                                        'Zero-trust multi-server identity attestation (HMAC-SHA256)',
                                        'End-to-end WireGuard VPN mesh across all regions',
                                        'SSRF guard — runtime protection with DNS rebinding defense',
                                        'Docker socket isolation via read-only proxy',
                                        'Inter-server TLS enforcement (no plaintext traffic)',
                                        'Fail-closed config — crash on missing keys, never fall back',
                                        '13-secret formal rotation runbook with upstream action matrix',
                                        'Strict SSH host key verification (no trust-on-first-use)',
                                    ].map((item, i) => (
                                        <StaggerChild key={i}>
                                            <li className="flex items-center gap-3 text-slate-600 dark:text-slate-300 font-medium">
                                                <CheckCircle2 className="w-5 h-5 text-emerald-500 flex-shrink-0" />
                                                {item}
                                            </li>
                                        </StaggerChild>
                                    ))}
                                </ul>
                            </ScrollReveal>
                        </ScrollReveal>
                        <ScrollReveal variant="slideRight" className="lg:w-1/2 w-full">
                            <div className="grid grid-cols-2 gap-4">
                                {[
                                    { name: 'gVisor Sandboxing', desc: 'User-space kernel per container. No shared kernel surface, no breakout vectors.', icon: Container },
                                    { name: 'Falco Runtime Security', desc: 'Real-time syscall monitoring. Detects anomalies, cryptomining, container escapes.', icon: ShieldAlert },
                                    { name: 'fail2ban Intrusion Prevention', desc: 'Automatic IP ban on repeated auth failures. Works across SSH, API, and registry.', icon: Bug },
                                    { name: 'Trivy CVE Scanning', desc: 'Continuous scanning at push + daily runtime. Filesystem, image, and IaC.', icon: ScanLine },
                                    { name: 'Scoped Container Registry', desc: 'Per-project pull/push RBAC. JWT auth backed by platform credentials.', icon: Fingerprint },
                                    { name: 'Cosign Image Signing', desc: 'Sigstore keyless signing. Every image verified before deployment.', icon: FileCode },
                                ].map((feat, i) => (
                                    <motion.div
                                        key={i}
                                        whileHover={{ scale: 1.03, borderColor: "rgba(16,185,129,0.3)" }}
                                        transition={{ type: "spring", stiffness: 400, damping: 25 }}
                                        className="p-5 bg-white dark:bg-white/5 border border-slate-200 dark:border-slate-700 rounded-2xl text-center h-full"
                                    >
                                        <feat.icon className="w-7 h-7 text-emerald-500 mx-auto mb-3" />
                                        <h4 className="font-bold text-sm text-slate-900 dark:text-white mb-1">{feat.name}</h4>
                                        <p className="text-xs text-slate-500 dark:text-slate-400 font-medium leading-relaxed">{feat.desc}</p>
                                    </motion.div>
                                ))}
                            </div>
                        </ScrollReveal>
                    </div>

                    {/* Security Pipeline Visual */}
                    <ScrollReveal variant="scaleIn" delay={0.2}>
                        <div className="mt-12">
                            <SecurityPipelineVisual />
                        </div>
                    </ScrollReveal>
                </div>
            </section>

            {/* ============================================
                COMPLIANCE STRIP
               ============================================ */}
            <section className="py-8 bg-slate-900">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <ScrollReveal variant="staggerContainer">
                        <div className="flex flex-wrap justify-center items-center gap-8 md:gap-16">
                            {[
                                { name: 'SOC 2 Type II', icon: Shield },
                                { name: 'GDPR Compliant', icon: Globe },
                                { name: 'ISO 27001', icon: FileCode },
                                { name: 'HIPAA Ready', icon: Activity },
                            ].map((std, i) => (
                                <StaggerChild key={i}>
                                    <motion.div
                                        whileHover={{ scale: 1.1 }}
                                        transition={{ type: "spring", stiffness: 400, damping: 20 }}
                                        className="flex items-center gap-2 text-slate-400"
                                    >
                                        <std.icon className="w-5 h-5 text-emerald-500" />
                                        <span className="font-bold text-sm">{std.name}</span>
                                    </motion.div>
                                </StaggerChild>
                            ))}
                        </div>
                    </ScrollReveal>
                </div>
            </section>

            {/* ============================================
                FINAL CTA
               ============================================ */}
            <section className="py-24 bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 text-white text-center relative overflow-hidden">
                <div className="absolute inset-0">
                </div>
                <ScrollReveal variant="scaleIn">
                    <div className="relative max-w-4xl mx-auto px-4 z-10">
                        <span className="inline-flex items-center gap-2 px-4 py-2 bg-emerald-500/10 border border-emerald-500/20 rounded-full mb-6">
                            <span className="flex h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
                            <span className="text-sm font-semibold text-emerald-400">Ready to deploy?</span>
                        </span>
                        <h2 className="text-3xl md:text-5xl font-bold mb-6">
                            Deploy your first cluster in minutes.
                        </h2>
                        <p className="text-xl text-slate-400 mb-10 max-w-2xl mx-auto font-medium">
                            100% free and open-source. Connect your VPS and start deploying.
                            No credit card required.
                        </p>
                        <div className="flex flex-col sm:flex-row gap-4 justify-center">
                            <Link href="/register" className="inline-flex items-center justify-center gap-2 px-8 py-4 bg-emerald-500 text-white text-lg font-bold rounded-xl hover:bg-emerald-400 transition-colors">
                                Install Grid Now <ArrowRight className="w-5 h-5" />
                            </Link>
                            <Link href="/docs" className="inline-flex items-center justify-center gap-2 px-8 py-4 bg-transparent border-2 border-slate-600 text-white text-lg font-bold rounded-xl hover:bg-slate-800 transition-colors">
                                Read the Docs
                            </Link>
                        </div>
                    </div>
                </ScrollReveal>
            </section>
        </main>
    );
}
