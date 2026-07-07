'use client';

import Link from 'next/link';
import Image from 'next/image';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { CloudHeroAnimation } from '@/components/animations/CloudHeroAnimation';
import { ParallaxLayer } from '@/components/animations/ParallaxLayer';
import {
    ArrowRight,
    Zap,
    Shield,
    Globe,
    Cpu,
    GitBranch,
    Cloud,
    Sparkles,
    Server,
    Rocket,
    Lock,
    Activity,
    Terminal,
    Boxes,
    CheckCircle2,
    XCircle,
    Users,
    BarChart3,
    Workflow,
    Command,
    RefreshCw,
    Network,
    Key,
    Blocks,
    ArrowUpRight,
    Search,
    BrainCircuit,
    MessageSquare,
    Fingerprint,
    TrendingUp,
    Brain,
    Database,
    HardDrive,
    Radio,
    Waypoints,
} from 'lucide-react';
import { motion } from 'framer-motion';
import { useMemo } from 'react';
import StoryTellingSection from '@/components/sections/StoryTellingSection';

// ============================================
// SVG ILLUSTRATIONS FOR FEATURE CARDS
// ============================================

function CardIllustration({ index, className = '' }: { index: number; className?: string }) {
    const svg = useMemo(() => {
        const patterns = [
            // 0 — Network nodes
            <svg key="net" viewBox="0 0 120 120" fill="none" xmlns="http://www.w3.org/2000/svg" className={className}>
                <circle cx="20" cy="20" r="4" className="fill-current" />
                <circle cx="60" cy="10" r="3" className="fill-current" />
                <circle cx="90" cy="25" r="5" className="fill-current" />
                <circle cx="40" cy="45" r="3" className="fill-current" />
                <circle cx="75" cy="50" r="4" className="fill-current" />
                <circle cx="100" cy="60" r="3" className="fill-current" />
                <circle cx="30" cy="75" r="4" className="fill-current" />
                <circle cx="65" cy="80" r="3" className="fill-current" />
                <circle cx="50" cy="95" r="5" className="fill-current" />
                <line x1="20" y1="20" x2="60" y2="10" stroke="currentColor" strokeWidth="0.8" />
                <line x1="60" y1="10" x2="90" y2="25" stroke="currentColor" strokeWidth="0.8" />
                <line x1="20" y1="20" x2="40" y2="45" stroke="currentColor" strokeWidth="0.8" />
                <line x1="90" y1="25" x2="75" y2="50" stroke="currentColor" strokeWidth="0.8" />
                <line x1="40" y1="45" x2="75" y2="50" stroke="currentColor" strokeWidth="0.8" />
                <line x1="75" y1="50" x2="100" y2="60" stroke="currentColor" strokeWidth="0.8" />
                <line x1="40" y1="45" x2="30" y2="75" stroke="currentColor" strokeWidth="0.8" />
                <line x1="75" y1="50" x2="65" y2="80" stroke="currentColor" strokeWidth="0.8" />
                <line x1="30" y1="75" x2="65" y2="80" stroke="currentColor" strokeWidth="0.8" />
                <line x1="65" y1="80" x2="50" y2="95" stroke="currentColor" strokeWidth="0.8" />
            </svg>,

            // 1 — Circuit board
            <svg key="circuit" viewBox="0 0 120 120" fill="none" xmlns="http://www.w3.org/2000/svg" className={className}>
                <path d="M10 10 L40 10 L40 30 L60 30" stroke="currentColor" strokeWidth="1" strokeLinecap="round" />
                <path d="M60 30 L60 50 L80 50" stroke="currentColor" strokeWidth="1" strokeLinecap="round" />
                <path d="M80 50 L80 70 L100 70" stroke="currentColor" strokeWidth="1" strokeLinecap="round" />
                <path d="M100 70 L100 90 L110 90" stroke="currentColor" strokeWidth="1" strokeLinecap="round" />
                <path d="M20 60 L20 80 L40 80 L40 95" stroke="currentColor" strokeWidth="1" strokeLinecap="round" />
                <path d="M50 15 L50 25" stroke="currentColor" strokeWidth="1" strokeLinecap="round" />
                <path d="M90 40 L90 55" stroke="currentColor" strokeWidth="1" strokeLinecap="round" />
                <circle cx="10" cy="10" r="2" className="fill-current" />
                <circle cx="40" cy="30" r="2" className="fill-current" />
                <circle cx="60" cy="30" r="2" className="fill-current" />
                <circle cx="50" cy="15" r="1.5" className="fill-current" />
                <circle cx="50" cy="25" r="1.5" className="fill-current" />
                <circle cx="80" cy="50" r="2" className="fill-current" />
                <circle cx="90" cy="40" r="1.5" className="fill-current" />
                <circle cx="90" cy="55" r="1.5" className="fill-current" />
            </svg>,

            // 2 — Waveform / signal
            <svg key="wave" viewBox="0 0 120 80" fill="none" xmlns="http://www.w3.org/2000/svg" className={className}>
                <path d="M0 40 Q10 10 20 40 T40 40 T60 40 T80 40 T100 40 T120 40" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" fill="none" />
                <path d="M0 55 Q15 25 30 55 T60 55 T90 55 T120 55" stroke="currentColor" strokeWidth="0.8" strokeLinecap="round" fill="none" opacity="0.5" />
                <path d="M0 25 Q15 10 30 25 T60 25 T90 25 T120 25" stroke="currentColor" strokeWidth="0.8" strokeLinecap="round" fill="none" opacity="0.5" />
            </svg>,

            // 3 — Shield / concentric arcs
            <svg key="shield" viewBox="0 0 80 100" fill="none" xmlns="http://www.w3.org/2000/svg" className={className}>
                <path d="M40 5 L70 18 L70 48 Q70 75 40 92 Q10 75 10 48 L10 18 Z" stroke="currentColor" strokeWidth="1" fill="none" />
                <path d="M40 15 L60 24 L60 48 Q60 68 40 82 Q20 68 20 48 L20 24 Z" stroke="currentColor" strokeWidth="0.8" fill="none" opacity="0.6" />
                <path d="M40 26 L50 31 L50 48 Q50 60 40 70 Q30 60 30 48 L30 31 Z" stroke="currentColor" strokeWidth="0.6" fill="none" opacity="0.4" />
            </svg>,

            // 4 — Database / cylinders
            <svg key="db" viewBox="0 0 80 110" fill="none" xmlns="http://www.w3.org/2000/svg" className={className}>
                <ellipse cx="40" cy="15" rx="25" ry="8" stroke="currentColor" strokeWidth="1" />
                <path d="M15 15 L15 42" stroke="currentColor" strokeWidth="1" />
                <path d="M65 15 L65 42" stroke="currentColor" strokeWidth="1" />
                <path d="M15 42 Q40 52 65 42" stroke="currentColor" strokeWidth="1" fill="none" />
                <path d="M15 42 L15 70" stroke="currentColor" strokeWidth="1" />
                <path d="M65 42 L65 70" stroke="currentColor" strokeWidth="1" />
                <path d="M15 70 Q40 80 65 70" stroke="currentColor" strokeWidth="1" fill="none" />
                <path d="M15 70 L15 97" stroke="currentColor" strokeWidth="1" />
                <path d="M65 70 L65 97" stroke="currentColor" strokeWidth="1" />
                <ellipse cx="40" cy="97" rx="25" ry="8" stroke="currentColor" strokeWidth="1" />
                <line x1="25" y1="28" x2="55" y2="28" stroke="currentColor" strokeWidth="0.6" opacity="0.4" />
                <line x1="25" y1="56" x2="55" y2="56" stroke="currentColor" strokeWidth="0.6" opacity="0.4" />
                <line x1="25" y1="82" x2="55" y2="82" stroke="currentColor" strokeWidth="0.6" opacity="0.4" />
            </svg>,

            // 5 — Terminal / angle brackets
            <svg key="term" viewBox="0 0 100 80" fill="none" xmlns="http://www.w3.org/2000/svg" className={className}>
                <path d="M15 25 L30 40 L15 55" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" fill="none" />
                <path d="M45 55 L65 55" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" fill="none" />
                <circle cx="78" cy="40" r="3" stroke="currentColor" strokeWidth="1" fill="none" />
                <circle cx="88" cy="40" r="3" stroke="currentColor" strokeWidth="1" fill="none" />
                <path d="M45 25 L55 40 L45 55" stroke="currentColor" strokeWidth="0.8" strokeLinecap="round" strokeLinejoin="round" fill="none" opacity="0.5" />
                <path d="M35 55 L45 55" stroke="currentColor" strokeWidth="0.8" strokeLinecap="round" fill="none" opacity="0.5" />
            </svg>,
        ];
        return patterns[index % patterns.length];
    }, [index, className]);

    return (
        <div className="absolute bottom-0 right-0 w-28 h-28 md:w-36 md:h-36 opacity-[0.04] dark:opacity-[0.07] pointer-events-none select-none">
            {svg}
        </div>
    );
}

// ============================================
// DATA: GLOBAL STATS
// ============================================
const globalStats = [
    { label: 'Uptime SLA', value: '99.99%', icon: Activity, color: 'text-emerald-500' },
    { label: 'Failover Time', value: '< 30s', icon: RefreshCw, color: 'text-blue-500' },
    { label: 'Global Edge Locations', value: '24+', icon: Globe, color: 'text-cyan-500' },
    { label: 'Active Deployments', value: '50K+', icon: Rocket, color: 'text-violet-500' }
];

// ============================================
// DATA: BATTLE CARDS (COMPARISON)
// ============================================
const battleCards = [
    {
        name: "Grid",
        logo: Cloud,
        description: "The Sovereign PaaS",
        price: "$0",
        priceDetail: "Open Source & Free",
        features: ["PostgreSQL HA Streaming Replication", "Redis Sentinel Auto-Failover", "AI Predictive Auto-Scaling", "Disaster Recovery & Backups", "Multi-Git (GitHub, GitLab, Bitbucket)", "Nixpacks Any-Language Builds", "Self-Healing Orchestration", "100% Open Source"],
        color: "bg-emerald-500",
        textColor: "text-emerald-500",
        borderColor: "border-emerald-500",
        highlight: true
    },
    {
        name: "AWS Fargate",
        logo: Server,
        description: "The Cloud Giant",
        price: "$36/mo",
        priceDetail: "per 1 vCPU / 2GB",
        features: ["High Vendor Lock in", "Complex IAM & VPC", "Opaque Billing", "Proprietary Runtime"],
        color: "bg-slate-500",
        textColor: "text-slate-500",
        borderColor: "border-slate-200",
        highlight: false
    },
    {
        name: "Vercel / Railway",
        logo: Zap,
        description: "The Managed PaaS",
        price: "$20+",
        priceDetail: "per seat + usage fees",
        features: ["Platform Lock in", "Expensive at Scale", "Black Box Runtime", "Open Core Only"],
        color: "bg-slate-500",
        textColor: "text-slate-500",
        borderColor: "border-slate-200",
        highlight: false
    },
    {
        name: "Heroku / Render",
        logo: ArrowUpRight,
        description: "The Legacy PaaS",
        price: "$25+",
        priceDetail: "per standard dyno",
        features: ["Sleeping dynos", "Expensive scaling", "Rigid buildpacks", "Slow support"],
        color: "bg-slate-500",
        textColor: "text-slate-500",
        borderColor: "border-slate-200",
        highlight: false
    }
];

// ============================================
// ============================================
// DATA: COMPLIANCE
// ============================================
const complianceStandards = [
    { name: 'SOC 2 Type II', icon: CheckCircle2 },
    { name: 'GDPR Compliant', icon: Globe },
    { name: 'ISO 27001', icon: Shield },
    { name: 'HIPAA Ready', icon: Activity }
];

// ============================================
// DATA: SMSLYCLOUD ECOSYSTEM SERVICES
// ============================================
const ecosystemServices = [
    {
        category: 'The Trust Layer',
        icon: Shield,
        color: 'text-emerald-500',
        bg: 'bg-emerald-500/10',
        services: [
            { name: 'Security Gateway', desc: 'Zero-trust routing & policy enforcement' },
            { name: 'Browser Shield', desc: 'Real-time Deepfake Live Detection (WASM)' },
            { name: 'Identity Service', desc: 'High-assurance identity management' }
        ]
    },
    {
        category: 'Communication Channels',
        icon: MessageSquare,
        color: 'text-blue-500',
        bg: 'bg-blue-500/10',
        services: [
            { name: 'SMS & WhatsApp', desc: 'Global messaging infrastructure' },
            { name: 'Voice & SFU Video', desc: 'Low-latency interactive media' },
            { name: 'Managed Email', desc: 'High-deliverability SMTP & APIs' }
        ]
    },
    {
        category: 'Intelligence',
        icon: Brain,
        color: 'text-purple-500',
        bg: 'bg-purple-500/10',
        services: [
            { name: 'Smart Automation', desc: 'Advanced workflow orchestration' },
            { name: 'Conversional AI', desc: 'Neural-link chatbots & NLP' },
            { name: 'Data Intelligence', desc: 'Deep analytics & predictive modeling' }
        ]
    },
    {
        category: 'Infrastructure',
        icon: Cpu,
        color: 'text-indigo-500',
        bg: 'bg-indigo-500/10',
        services: [
            { name: 'Grid Hosting', desc: 'Multi-node PaaS orchestration' },
            { name: 'Platform API', desc: 'Unified product interaction layer' },
            { name: 'Secure Tunneling', desc: 'End-to-end private infra tunnels' }
        ]
    }
];

const smslycloudPillars = [
    {
        title: 'Communication APIs',
        description: 'SMS, voice, WhatsApp, email, OTP, and customer messaging infrastructure.',
        icon: MessageSquare,
        color: 'text-blue-500',
        bg: 'bg-blue-500/10'
    },
    {
        title: 'Identity & Trust',
        description: 'SilentOTP, verification, abuse prevention, media integrity, and trust-layer systems.',
        icon: Fingerprint,
        color: 'text-emerald-500',
        bg: 'bg-emerald-500/10'
    },
    {
        title: 'Deployment Infrastructure',
        description: 'Grid powered by CloudNeuron: a free open-source PaaS for apps, services, and full ecosystems.',
        icon: Cloud,
        color: 'text-indigo-500',
        bg: 'bg-indigo-500/10'
    },
    {
        title: 'Growth Automation',
        description: 'Ignite: AI-assisted marketing planning, publishing, listening, leads, and analytics.',
        icon: TrendingUp,
        color: 'text-amber-500',
        bg: 'bg-amber-500/10'
    }
];

// ============================================
// DATA: HIGH AVAILABILITY FEATURES
// ============================================
const haFeatures = [
    {
        icon: Database,
        title: 'PostgreSQL HA Streaming Replication',
        desc: 'Patroni-managed primary with streaming replicas. Automatic failover in seconds. PgCat read/write splitting for zero-downtime upgrades.',
        stat: 'Sub-second failover',
        color: 'from-blue-500 to-cyan-500',
        bg: 'bg-blue-500/10',
    },
    {
        icon: RefreshCw,
        title: 'Redis Sentinel HA',
        desc: 'Automatic cache and broker failover via Redis Sentinel. Configurable quorum, replica priorities, and down-after-milliseconds tuning.',
        stat: 'Auto-failover',
        color: 'from-red-500 to-rose-500',
        bg: 'bg-red-500/10',
    },
    {
        icon: Radio,
        title: 'AI-Powered Autoscaler',
        desc: 'Three engines: Classic CPU hysteresis, AI-enhanced with Prometheus + Loki anomaly detection, and K8s/Docker admin surface. Predictive, not reactive.',
        stat: '3 engines',
        color: 'from-violet-500 to-purple-500',
        bg: 'bg-violet-500/10',
    },
    {
        icon: HardDrive,
        title: 'Disaster Recovery',
        desc: 'Tiered backup schedules (6h/24h/7d), cloud replication to S3/R2/MinIO, encryption key rotation with multi-key support, and defined RPO/RTO targets.',
        stat: 'RPO < 6h',
        color: 'from-amber-500 to-orange-500',
        bg: 'bg-amber-500/10',
    },
    {
        icon: Waypoints,
        title: 'Self-Healing Orchestration',
        desc: 'Automatic failure classification: Docker daemon down, disk full, OOM, container crashed. Escalates to AI after 5 attempts with auto-remediation.',
        stat: 'Auto-remediate',
        color: 'from-emerald-500 to-teal-500',
        bg: 'bg-emerald-500/10',
    },
    {
        icon: Network,
        title: 'WireGuard VPN Mesh',
        desc: 'Encrypted node-to-node mesh networking across your fleet. Auto-allocated IPs, per-peer latency tracking, and multiple named meshes.',
        stat: 'Encrypted mesh',
        color: 'from-indigo-500 to-blue-500',
        bg: 'bg-indigo-500/10',
    },
];

export default function Home() {
    return (
        <main className="min-h-screen relative overflow-x-hidden">


            {/* HERO SECTION */}
            <section className="relative pt-24 pb-16 lg:pt-40 lg:pb-32 overflow-hidden" style={{ background: 'linear-gradient(to bottom, #87CEEB, #56CCF2 30%, #B8E8F5 60%, #ffffff)' }}>
                {/* Dark mode override */}
                <div className="absolute inset-0 bg-gradient-to-b from-slate-950 via-slate-900 to-slate-950 dark:block hidden" />

                {/* Cloud Animation */}
                <div className="absolute inset-0 z-0 opacity-60 dark:opacity-40 pointer-events-none">
                    <CloudHeroAnimation />
                </div>

                <div className="relative max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 text-center z-10">
                    <div className="flex flex-col sm:flex-row items-center justify-center gap-3 mb-8">
                        <motion.div
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{ duration: 0.5 }}
                            className="inline-flex items-center gap-2 px-4 py-1.5 bg-emerald-50/80 dark:bg-emerald-950/30 border border-emerald-200 dark:border-emerald-800 rounded-full shadow-sm backdrop-blur-sm"
                        >
                            <Sparkles className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
                            <span className="text-xs md:text-sm font-semibold text-emerald-800 dark:text-emerald-300 uppercase tracking-wide">
                                Open Source PaaS
                            </span>
                        </motion.div>
                        <motion.div
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{ duration: 0.5, delay: 0.1 }}
                            className="inline-flex items-center gap-2 px-4 py-1.5 bg-blue-50/80 dark:bg-blue-950/30 border border-blue-200 dark:border-blue-800 rounded-full shadow-sm backdrop-blur-sm"
                        >
                            <Rocket className="w-4 h-4 text-blue-600 dark:text-blue-400" />
                            <span className="text-xs md:text-sm font-semibold text-blue-800 dark:text-blue-300 uppercase tracking-wide">
                                Secured by SMSLYCLOUD
                            </span>
                        </motion.div>
                    </div>

                    <motion.p
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.05, duration: 0.5 }}
                        className="text-sm md:text-base font-medium text-slate-500 dark:text-slate-400 mb-3 tracking-wide"
                    >
                        By SMSLYCLOUD · Infrastructure Trust Ecosystem
                    </motion.p>

                    <motion.h1
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.1, duration: 0.5 }}
                        className="text-4xl md:text-6xl lg:text-7xl font-extrabold text-slate-900 dark:text-white tracking-tight leading-none mb-6"
                    >
                        The sovereign PaaS for <br className="hidden md:block" />
                        <span className="text-transparent bg-clip-text bg-gradient-to-r from-emerald-600 via-teal-500 to-cyan-500">
                            modern infrastructure.
                        </span>
                    </motion.h1>

                    <motion.p
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.2, duration: 0.5 }}
                        className="mt-6 text-lg md:text-xl text-slate-600 dark:text-slate-300 max-w-3xl mx-auto leading-relaxed font-light"
                    >
                        Grid is an open-source Platform-as-a-Service built by SMSLYCLOUD. An infrastructure trust ecosystem serving modern internet businesses across communications, identity, deployment, and growth automation.
                    </motion.p>

                    <motion.p
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.25, duration: 0.5 }}
                        className="mt-4 text-base md:text-lg text-slate-500 dark:text-slate-400 max-w-2xl mx-auto"
                    >
                        Connect your VPS. Deploy connected apps, services, databases, workers, AI auto-remediation, tunnels, and multi-server clusters. PostgreSQL HA with streaming replication. Redis Sentinel failover. No DevOps pain.
                    </motion.p>

                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.3, duration: 0.5 }}
                        className="mt-10 flex flex-col sm:flex-row gap-4 justify-center items-center"
                    >
                        <Link href="/register" className="group relative inline-flex items-center justify-center gap-2 px-8 py-4 text-base font-bold text-white bg-gradient-to-r from-emerald-600 to-teal-600 rounded-xl hover:from-emerald-500 hover:to-teal-500 transition-all shadow-md shadow-emerald-500/10 hover:shadow-emerald-500/30 hover:-translate-y-0.5">
                            Get Grid Free <ArrowRight className="w-5 h-5 group-hover:translate-x-1 transition-transform" />
                        </Link>
                        <Link href="/docs" className="inline-flex items-center justify-center gap-2 px-8 py-4 text-base font-bold text-slate-700 dark:text-slate-200 bg-white/50 dark:bg-slate-800/50 backdrop-blur-md border border-slate-200 dark:border-slate-700 rounded-xl hover:bg-white dark:hover:bg-slate-700 transition-all hover:-translate-y-0.5">
                            Read Documentation
                        </Link>
                    </motion.div>

                    <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        transition={{ delay: 0.5 }}
                        className="mt-12 flex flex-col sm:flex-row flex-wrap justify-center gap-x-8 gap-y-3 text-xs md:text-sm font-semibold text-slate-500 dark:text-slate-400"
                    >
                        <span className="flex items-center justify-center gap-2"><CheckCircle2 className="w-4 h-4 text-emerald-500" /> Free & Open Source</span>
                        <span className="flex items-center justify-center gap-2"><CheckCircle2 className="w-4 h-4 text-emerald-500" /> Self-hosted on your VPS</span>
                        <span className="flex items-center justify-center gap-2"><CheckCircle2 className="w-4 h-4 text-emerald-500" /> SOC2 Type II Compliant</span>
                    </motion.div>
                </div>

                {/* Floating Elements (Decorations) */}
                <ParallaxLayer speed={0.2} className="absolute bottom-20 left-10 hidden xl:block pointer-events-none">
                    <motion.div
                        animate={{ y: [0, -15, 0], rotate: [0, 5, 0] }}
                        transition={{ duration: 4, repeat: Infinity, ease: "easeInOut" }}
                        className="p-4 rounded-xl bg-white/90 dark:bg-slate-800/90 backdrop-blur-xl border border-slate-200 dark:border-slate-700 shadow-xl"
                    >
                        <GitBranch className="w-8 h-8 text-emerald-600 dark:text-emerald-400" />
                        <div className="mt-2 text-xs font-mono font-bold text-slate-500">git push origin main</div>
                    </motion.div>
                </ParallaxLayer>
                <ParallaxLayer speed={0.35} className="absolute top-40 right-20 hidden xl:block pointer-events-none">
                    <motion.div
                        animate={{ y: [0, 20, 0], rotate: [0, -5, 0] }}
                        transition={{ duration: 5, repeat: Infinity, ease: "easeInOut", delay: 1 }}
                        className="p-4 rounded-xl bg-white/90 dark:bg-slate-800/90 backdrop-blur-xl border border-slate-200 dark:border-slate-700 shadow-xl"
                    >
                        <Cpu className="w-8 h-8 text-violet-500" />
                        <div className="mt-2 text-xs font-mono font-bold text-slate-500">HA: Active</div>
                    </motion.div>
                </ParallaxLayer>
            </section>

            {/* INTERACTIVE CLOUD GRID SHOWCASE SECTION */}
            <section className="relative -mt-12 md:-mt-24 z-20 px-4 sm:px-6 lg:px-8 max-w-5xl mx-auto mb-16 md:mb-24">
                <motion.div 
                    initial={{ opacity: 0, y: 40 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.6, duration: 0.7 }}
                    className="bg-slate-900 rounded-2xl md:rounded-[2rem] p-2 md:p-4 shadow-2xl border border-slate-800 relative group overflow-hidden"
                >
                    <div className="absolute inset-0 bg-gradient-to-tr from-emerald-500/20 to-blue-500/20 opacity-0 group-hover:opacity-100 transition-opacity duration-700 pointer-events-none" />
                    <div className="bg-black/90 rounded-xl md:rounded-[1.5rem] overflow-hidden relative border border-slate-800/80 p-4 md:p-8">
                        {/* Terminal / Showcase Header */}
                        <div className="flex items-center justify-between pb-4 mb-6 border-b border-slate-800/80 text-xs text-slate-400">
                            <div className="flex items-center gap-2">
                                <div className="w-3 h-3 rounded-full bg-rose-500/80" />
                                <div className="w-3 h-3 rounded-full bg-yellow-500/80" />
                                <div className="w-3 h-3 rounded-full bg-emerald-500/80" />
                                <span className="ml-2 font-mono text-slate-300 font-bold flex items-center gap-1.5">
                                    <Terminal className="w-3.5 h-3.5 text-emerald-400 inline" /> smsly-cloud@grid-orchestrator ~ live-session
                                </span>
                            </div>
                            <div className="flex items-center gap-2">
                                <span className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-emerald-950/60 border border-emerald-800/50 text-emerald-400 font-mono text-[10px]">
                                    <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
                                    ACTIVE MESH
                                </span>
                            </div>
                        </div>

                        {/* Interactive Showcase Content Grid */}
                        <div className="grid md:grid-cols-12 gap-6 items-center">
                            {/* Terminal Logs Simulation */}
                            <div className="md:col-span-7 bg-slate-950/90 rounded-lg p-4 font-mono text-xs text-slate-300 space-y-2.5 border border-slate-900 shadow-inner">
                                <div className="flex items-center gap-2 text-slate-500">
                                    <span>$ smsly deploy --ecosystem production --auto-scope</span>
                                </div>
                                <div className="flex items-center gap-2 text-cyan-400">
                                    <span>[00:00.12] ⚙️ Initializing isolated project scope & DB addons...</span>
                                </div>
                                <div className="flex items-center gap-2 text-emerald-400">
                                    <CheckCircle2 className="w-3.5 h-3.5 shrink-0" />
                                    <span>[00:00.45] PostgreSQL & Redis bound to anchor service network</span>
                                </div>
                                <div className="flex items-center gap-2 text-emerald-400">
                                    <CheckCircle2 className="w-3.5 h-3.5 shrink-0" />
                                    <span>[00:00.89] Smart deployment queue filled 14 missing env secrets (48-char URL safe)</span>
                                </div>
                                <div className="flex items-center gap-2 text-emerald-400">
                                    <CheckCircle2 className="w-3.5 h-3.5 shrink-0" />
                                    <span>[00:01.24] Zero-downtime rolling deployment complete across 24 edge regions</span>
                                </div>
                                <div className="pt-2 border-t border-slate-900/80 flex items-center justify-between text-slate-400 text-[11px]">
                                    <span>Status: <strong className="text-emerald-400">Healthy</strong></span>
                                    <span>Latency: <strong className="text-cyan-400">18ms</strong></span>
                                </div>
                            </div>

                            {/* Status Stats Badges */}
                            <div className="md:col-span-5 grid grid-cols-2 gap-3 font-sans">
                                <div className="bg-slate-900/60 p-3.5 rounded-xl border border-slate-800/60">
                                    <div className="text-slate-400 text-[11px] font-medium flex items-center gap-1.5 mb-1">
                                        <Activity className="w-3.5 h-3.5 text-emerald-400" /> Mesh Uptime
                                    </div>
                                    <div className="text-lg font-bold text-white">99.998%</div>
                                    <div className="text-[10px] text-emerald-400 mt-0.5">0 incidents today</div>
                                </div>
                                <div className="bg-slate-900/60 p-3.5 rounded-xl border border-slate-800/60">
                                    <div className="text-slate-400 text-[11px] font-medium flex items-center gap-1.5 mb-1">
                                        <Globe className="w-3.5 h-3.5 text-cyan-400" /> Global PoPs
                                    </div>
                                    <div className="text-lg font-bold text-white">24 Active</div>
                                    <div className="text-[10px] text-cyan-400 mt-0.5">Sub-50ms routing</div>
                                </div>
                                <div className="bg-slate-900/60 p-3.5 rounded-xl border border-slate-800/60 col-span-2 flex items-center justify-between">
                                    <div className="flex items-center gap-2.5">
                                        <div className="w-8 h-8 rounded-lg bg-violet-500/10 border border-violet-500/20 flex items-center justify-center">
                                            <Cpu className="w-4 h-4 text-violet-400" />
                                        </div>
                                        <div>
                                            <div className="text-xs font-bold text-white">HA Infrastructure</div>
                                            <div className="text-[10px] text-slate-400">PostgreSQL replication + Sentinel failover</div>
                                        </div>
                                    </div>
                                    <span className="px-2 py-1 rounded bg-violet-500/10 text-violet-300 font-mono text-[10px] font-bold">READY</span>
                                </div>
                            </div>
                        </div>
                    </div>
                </motion.div>
            </section>

            {/* GLOBAL STATISTICS BAR */}
            <section className="bg-white dark:bg-slate-900 border-y border-slate-200 dark:border-slate-800 relative z-20">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="grid grid-cols-2 md:grid-cols-4 divide-y md:divide-y-0 md:divide-x divide-slate-100 dark:divide-slate-800">
                        {globalStats.map((stat, i) => (
                            <div key={i} className="py-8 md:py-10 md:px-6 flex flex-col items-center justify-center text-center hover:bg-slate-50 dark:hover:bg-slate-800/50 transition-colors group">
                                <div className="flex items-center gap-2 mb-2 transform group-hover:scale-105 transition-transform duration-300">
                                    <stat.icon className={`w-5 h-5 md:w-6 md:h-6 ${stat.color}`} />
                                    <span className="text-[10px] md:text-xs font-bold text-slate-400 uppercase tracking-widest">{stat.label}</span>
                                </div>
                                <span className="text-3xl md:text-4xl font-extrabold text-slate-900 dark:text-white font-mono tracking-tight">{stat.value}</span>
                            </div>
                        ))}
                    </div>
                </div>
            </section>



            {/* STORYTELLING FEATURES — Narrative Journey */}
            <StoryTellingSection />

            {/* HIGH AVAILABILITY INFRASTRUCTURE */}
            <section className="relative py-20 md:py-32 overflow-hidden">
                <div className="absolute inset-0 galaxy-bg" />
                <div className="stars-layer" />
                <div className="stars-twinkle" />
                <ParallaxLayer speed={0.15} className="absolute inset-0 pointer-events-none">
                    <div className="nebula-patch w-[600px] h-[400px] bg-blue-600 top-[10%] right-[5%] opacity-15" />
                </ParallaxLayer>
                <motion.div
                    className="cosmic-body cosmic-pulsar w-[350px] h-[350px] md:w-[480px] md:h-[480px] bottom-[8%] left-[-3%] hidden md:block"
                    animate={{ y: [0, -15, 0] }}
                    transition={{ duration: 16, repeat: Infinity, ease: 'easeInOut' }}
                />

                <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="text-center mb-16 md:mb-20">
                        <div className="inline-flex items-center gap-2 px-3 py-1.5 bg-blue-500/10 border border-blue-500/20 text-blue-400 text-xs md:text-sm font-bold rounded-full mb-6">
                            <HardDrive className="w-3 h-3 md:w-3.5 md:h-3.5" />
                            High Availability
                        </div>
                        <h2 className="text-3xl md:text-5xl font-extrabold text-white mb-6 tracking-tight">
                            Zero Downtime.<br />
                            <span className="text-transparent bg-clip-text bg-gradient-to-r from-blue-400 via-cyan-400 to-emerald-400">
                                Every Layer.
                            </span>
                        </h2>
                        <p className="text-base md:text-xl text-slate-400 max-w-3xl mx-auto leading-relaxed">
                            PostgreSQL streaming replication with Patroni. Redis Sentinel auto-failover. AI-powered predictive autoscaling. Disaster recovery with defined RPO/RTO targets. Your infrastructure survives anything.
                        </p>
                    </div>

                    <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6 md:gap-8 mb-12">
                        {haFeatures.map((feat, i) => (
                            <motion.div
                                key={i}
                                initial={{ opacity: 0, y: 20 }}
                                whileInView={{ opacity: 1, y: 0 }}
                                viewport={{ once: true }}
                                transition={{ delay: i * 0.1 }}
                                className="group relative bg-slate-900/40 backdrop-blur-sm p-8 rounded-2xl border border-slate-700/30 hover:border-slate-600/80 transition-all overflow-hidden"
                            >
                                <div className={`absolute -top-16 -right-16 w-32 h-32 bg-gradient-to-bl ${feat.color} opacity-[0.06] group-hover:opacity-[0.12] rounded-full blur-2xl transition-opacity pointer-events-none`} />
                                <div className="relative z-10">
                                    <div className={`w-12 h-12 rounded-xl ${feat.bg} flex items-center justify-center mb-6 group-hover:scale-110 transition-transform`}>
                                        <feat.icon className={`w-6 h-6 bg-gradient-to-br ${feat.color} text-transparent`} style={{ color: 'white' }} />
                                    </div>
                                    <h3 className="text-xl font-bold text-white mb-3">{feat.title}</h3>
                                    <p className="text-sm text-slate-400 leading-relaxed mb-4">{feat.desc}</p>
                                    <div className="inline-flex items-center gap-1.5 px-3 py-1 bg-slate-800/80 rounded-full">
                                        <Zap className="w-3 h-3 text-emerald-400" />
                                        <span className="text-xs font-bold text-slate-300">{feat.stat}</span>
                                    </div>
                                </div>
                            </motion.div>
                        ))}
                    </div>

                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        whileInView={{ opacity: 1, y: 0 }}
                        viewport={{ once: true }}
                        className="max-w-3xl mx-auto p-6 bg-blue-50 dark:bg-blue-950/20 border border-blue-200 dark:border-blue-800/30 rounded-2xl"
                    >
                        <div className="flex items-center gap-3 mb-2">
                            <Shield className="w-5 h-5 text-blue-600 dark:text-blue-400" />
                            <span className="font-bold text-blue-800 dark:text-blue-300">Battle-tested HA. Not a paid add-on.</span>
                        </div>
                        <p className="text-sm text-blue-700 dark:text-blue-400/80">
                            PostgreSQL streaming replication, Redis Sentinel, AI autoscaling, and disaster recovery are all built-in. No extra infrastructure, no vendor lock-in, no per-seat fees.
                        </p>
                    </motion.div>
                </div>
            </section>

            {/* THE SMSLYCLOUD ECOSYSTEM — Galaxy background + Red Supergiant */}
            <section className="relative py-20 md:py-32 overflow-hidden">
                <div className="absolute inset-0 galaxy-bg" />
                <div className="stars-layer" />
                <div className="stars-twinkle" />
                <ParallaxLayer speed={0.15} className="absolute inset-0 pointer-events-none">
                    <div className="nebula-patch w-[600px] h-[400px] bg-indigo-600 top-[10%] left-[5%] opacity-15" />
                </ParallaxLayer>
                <ParallaxLayer speed={-0.1} className="absolute inset-0 pointer-events-none">
                    <div className="nebula-patch w-[500px] h-[500px] bg-purple-600 bottom-[5%] right-[10%] opacity-12" style={{ animationDelay: '-15s' }} />
                </ParallaxLayer>
                <div className="galaxy-arm w-[900px] h-[900px] border border-indigo-500/15 top-[-10%] left-[10%]" />
                <div className="dust-lane w-[80%] top-[50%] left-[10%]" />
                <motion.div
                    className="cosmic-body cosmic-quasar w-[340px] h-[340px] md:w-[480px] md:h-[480px] bottom-[8%] right-[-3%] hidden md:block"
                    animate={{ y: [0, -15, 0] }}
                    transition={{ duration: 16, repeat: Infinity, ease: 'easeInOut' }}
                />

                <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="text-center mb-16 md:mb-20">
                        <div className="inline-flex items-center gap-2 px-3 py-1.5 bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-xs md:text-sm font-bold rounded-full mb-6">
                            <Rocket className="w-3 h-3 md:w-3.5 md:h-3.5" />
                            The Ecosystem Behind Grid
                        </div>
                        <h2 className="text-3xl md:text-5xl font-extrabold text-white mb-6 tracking-tight">
                            Secured by SMSLYCLOUD
                        </h2>
                        <p className="text-base md:text-xl text-slate-400 max-w-3xl mx-auto leading-relaxed">
                            Grid is one product in the SMSLYCLOUD infrastructure trust ecosystem —
                            building the tools modern internet businesses need to communicate, verify, deploy, and grow.
                        </p>
                    </div>

                    <div className="grid md:grid-cols-2 lg:grid-cols-4 gap-6 md:gap-8 mb-16">
                        {ecosystemServices.map((cat, i) => (
                            <motion.div
                                key={i}
                                initial={{ opacity: 0, y: 20 }}
                                whileInView={{ opacity: 1, y: 0 }}
                                viewport={{ once: true }}
                                transition={{ delay: i * 0.1 }}
                                className="flex flex-col bg-slate-900/40 backdrop-blur-sm p-8 rounded-2xl border border-slate-700/30 hover:border-slate-600/80 transition-all group overflow-hidden relative"
                            >
                                <CardIllustration index={i + 22} />
                                <div className={`w-12 h-12 rounded-xl ${cat.bg} ${cat.color} flex items-center justify-center mb-6 group-hover:scale-110 transition-transform relative z-10`}>
                                    <cat.icon className="w-6 h-6" />
                                </div>
                                <h3 className="text-xl font-bold text-white mb-6">{cat.category}</h3>
                                <ul className="space-y-6 flex-1">
                                    {cat.services.map((svc, j) => (
                                        <li key={j} className="relative pl-4 border-l-2 border-slate-700 hover:border-emerald-500 transition-colors">
                                            <h4 className="font-bold text-sm text-white mb-1">{svc.name}</h4>
                                            <p className="text-xs text-slate-400 leading-normal">{svc.desc}</p>
                                        </li>
                                    ))}
                                </ul>
                            </motion.div>
                        ))}
                    </div>

                    <div className="text-center">
                        <Link
                            href="https://smsly.cloud"
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex items-center gap-2 px-6 py-3 bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 font-bold rounded-xl hover:bg-emerald-500/20 transition-all"
                        >
                            Explore SMSLYCLOUD <ArrowUpRight className="w-4 h-4" />
                        </Link>
                    </div>
                </div>
            </section>

            {/* BATTLE CARDS COMPARISON — Space bg + Binary Star */}
            <section className="relative py-16 md:py-24 overflow-hidden">
                <div className="absolute inset-0 space-bg" />
                <div className="stars-layer" />
                <div className="stars-twinkle" />
                <ParallaxLayer speed={0.15} className="absolute inset-0 pointer-events-none">
                    <div className="nebula-patch w-[400px] h-[300px] bg-rose-600 top-[10%] right-[5%] opacity-10" />
                </ParallaxLayer>
                <motion.div
                    className="cosmic-body cosmic-neutronstar w-[300px] h-[300px] md:w-[440px] md:h-[440px] bottom-[5%] right-[8%] hidden md:block"
                    animate={{ y: [0, -10, 0] }}
                    transition={{ duration: 14, repeat: Infinity, ease: 'easeInOut' }}
                />

                <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="text-center mb-12 md:mb-16">
                        <h2 className="text-2xl md:text-4xl lg:text-5xl font-extrabold text-white mb-4 md:mb-6">Stop Paying the &quot;Cloud Tax&quot;</h2>
                        <p className="text-base md:text-xl text-slate-400 max-w-2xl mx-auto">
                            Grid runs on <strong>your infrastructure</strong>, saving you up to 90% on compute costs compared to managed services.
                        </p>
                    </div>

                    <div className="grid md:grid-cols-2 lg:grid-cols-4 gap-6 md:gap-8">
                        {battleCards.map((card, i) => (
                            <motion.div
                                key={i}
                                initial={{ opacity: 0, y: 20 }}
                                whileInView={{ opacity: 1, y: 0 }}
                                viewport={{ once: true }}
                                transition={{ delay: i * 0.1 }}
                                className={`relative p-6 md:p-8 rounded-2xl md:rounded-3xl bg-slate-900/40 backdrop-blur-sm border-2 ${card.highlight ? 'border-emerald-500 shadow-xl shadow-emerald-500/10' : 'border-slate-700/50'} flex flex-col`}
                            >
                                {card.highlight && (
                                    <div className="absolute -top-3 left-1/2 -translate-x-1/2 px-3 py-1 bg-emerald-500 text-white text-[10px] md:text-xs font-bold uppercase tracking-widest rounded-full shadow-md">
                                        Best Value
                                    </div>
                                )}

                                <div className="mb-4 md:mb-6 flex items-center justify-between">
                                    <div className={`p-2 md:p-3 rounded-xl md:rounded-2xl ${card.highlight ? 'bg-emerald-500/20' : 'bg-slate-800/50'}`}>
                                        <card.logo className={`w-6 h-6 md:w-8 md:h-8 ${card.highlight ? 'text-emerald-400' : card.textColor}`} />
                                    </div>
                                    <h3 className="text-lg md:text-xl font-bold text-white">{card.name}</h3>
                                </div>

                                <div className="mb-6 md:mb-8">
                                    <span className="text-3xl md:text-4xl font-extrabold text-white">{card.price}</span>
                                    <span className="block text-xs md:text-sm text-slate-400 mt-1">{card.priceDetail}</span>
                                </div>

                                <ul className="space-y-3 md:space-y-4 mb-6 md:mb-8 flex-1">
                                    {card.features.map((feat, j) => (
                                        <li key={j} className="flex items-center gap-2 md:gap-3 text-xs md:text-sm font-medium text-slate-300">
                                            {card.highlight ? (
                                                <CheckCircle2 className="w-4 h-4 md:w-5 md:h-5 text-emerald-500 flex-shrink-0" />
                                            ) : (
                                                <XCircle className="w-4 h-4 md:w-5 md:h-5 text-slate-600 flex-shrink-0" />
                                            )}
                                            {feat}
                                        </li>
                                    ))}
                                </ul>

                                <Link
                                    href={card.highlight ? "/register" : (card.name.includes("Vercel") ? "/compare" : "/pricing")}
                                    className={`w-full py-2.5 md:py-3 rounded-lg md:rounded-xl text-sm md:text-base font-bold text-center transition-all ${
                                        card.highlight
                                        ? 'bg-emerald-500 hover:bg-emerald-600 text-white shadow-md hover:shadow-emerald-500/20'
                                        : 'bg-slate-800 hover:bg-slate-700 text-slate-300'
                                    }`}
                                >
                                    {card.highlight ? 'Install Grid' : (card.name.includes("Vercel") ? 'View Comparison' : 'View Pricing')}
                                </Link>
                            </motion.div>
                        ))}
                    </div>
                </div>
            </section>



            {/* DEVELOPER EXPERIENCE & CLI — Space bg with Uranus */}
            <section className="relative py-16 md:py-32 overflow-hidden">
                <div className="absolute inset-0 space-bg" />
                <div className="stars-layer" />
                <div className="stars-twinkle" />
                <ParallaxLayer speed={-0.12} className="absolute inset-0 pointer-events-none">
                    <div className="nebula-patch w-[500px] h-[400px] bg-cyan-600 bottom-[10%] left-[5%] opacity-12" style={{ animationDelay: '-8s' }} />
                </ParallaxLayer>
                <motion.div
                    className="cosmic-body cosmic-blazar w-[280px] h-[280px] md:w-[400px] md:h-[400px] bottom-[8%] left-[5%] hidden md:block"
                    animate={{ y: [0, -12, 0] }}
                    transition={{ duration: 16, repeat: Infinity, ease: 'easeInOut' }}
                />

                <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="flex flex-col lg:flex-row items-center gap-12 lg:gap-20">
                        <div className="lg:w-1/2">
                            <div className="inline-flex items-center gap-2 px-3 py-1.5 bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-xs md:text-sm font-bold rounded-full mb-6 md:mb-8">
                                <Terminal className="w-3 h-3 md:w-3.5 md:h-3.5" />
                                Developer First Experience
                            </div>
                            <h2 className="text-3xl md:text-4xl lg:text-5xl font-extrabold text-white mb-6 md:mb-8 tracking-tight">Power at Your Fingertips</h2>
                            <p className="text-base md:text-lg text-slate-400 mb-8 md:mb-10 leading-relaxed">
                                Control your entire infrastructure from the command line. The <code className="bg-slate-800 px-1 py-0.5 rounded font-mono text-emerald-400">grid</code> CLI gives you instant access to logs, deployments, and secrets.
                            </p>

                            <div className="flex flex-col gap-6">
                                <div className="flex items-start gap-4 md:gap-5 group">
                                    <div className="p-2.5 md:p-3 bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300 rounded-lg md:rounded-xl group-hover:scale-105 transition-transform"><Zap className="w-5 h-5 md:w-6 md:h-6" /></div>
                                    <div>
                                        <h4 className="font-bold text-base md:text-lg text-slate-900 dark:text-white mb-1">Instant Deployments</h4>
                                        <p className="text-sm md:text-base text-slate-500 dark:text-slate-400">Push to git or use the CLI to deploy in seconds.</p>
                                    </div>
                                </div>
                                <div className="flex items-start gap-4 md:gap-5 group">
                                    <div className="p-2.5 md:p-3 bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 rounded-lg md:rounded-xl group-hover:scale-105 transition-transform"><Command className="w-5 h-5 md:w-6 md:h-6" /></div>
                                    <div>
                                        <h4 className="font-bold text-base md:text-lg text-slate-900 dark:text-white mb-1">Full Control</h4>
                                        <p className="text-sm md:text-base text-slate-500 dark:text-slate-400">Manage environment variables, domains, and certificates.</p>
                                    </div>
                                </div>
                                <div className="flex items-start gap-4 md:gap-5 group">
                                    <div className="p-2.5 md:p-3 bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300 rounded-lg md:rounded-xl group-hover:scale-105 transition-transform"><Lock className="w-5 h-5 md:w-6 md:h-6" /></div>
                                    <div>
                                        <h4 className="font-bold text-base md:text-lg text-slate-900 dark:text-white mb-1">Secure by Default</h4>
                                        <p className="text-sm md:text-base text-slate-500 dark:text-slate-400">Automatic SSL, encrypted secrets, and isolated builds.</p>
                                    </div>
                                </div>
                            </div>
                        </div>

                        <div className="lg:w-1/2 w-full">
                            <div className="bg-[#0f172a] rounded-xl md:rounded-2xl shadow-2xl overflow-hidden border border-slate-800 ring-1 ring-white/10 relative">
                                {/* Window Controls */}
                                <div className="flex items-center justify-between px-3 md:px-4 py-2 md:py-3 border-b border-slate-800 bg-slate-900">
                                    <div className="flex gap-1.5 md:gap-2">
                                        <div className="w-2.5 h-2.5 md:w-3 md:h-3 rounded-full bg-red-500/80" />
                                        <div className="w-2.5 h-2.5 md:w-3 md:h-3 rounded-full bg-amber-500/80" />
                                        <div className="w-2.5 h-2.5 md:w-3 md:h-3 rounded-full bg-emerald-500/80" />
                                    </div>
                                    <div className="text-[10px] md:text-xs font-mono text-slate-500 flex items-center gap-1">
                                        <Terminal className="w-3 h-3" />
                                        user@devbox:~
                                    </div>
                                    <div className="w-8 md:w-10"></div>
                                </div>

                                {/* Terminal Content */}
                                <div className="p-4 md:p-6 font-mono text-xs md:text-sm overflow-x-auto leading-relaxed h-[300px] md:h-[400px]">
                                    <div className="text-slate-300">
                                        <span className="text-emerald-400">➜</span> <span className="text-blue-400">~</span> <span className="text-slate-400">cloudneuron login</span>
                                        <br />
                                        <span className="text-emerald-500">✔</span> Authenticated as <span className="text-white font-bold">team@cloudneuron.io</span>
                                        <br /><br />
                                        <span className="text-emerald-400">➜</span> <span className="text-blue-400">~/my-app</span> <span className="text-slate-400">cloudneuron init</span>
                                        <br />
                                        <span className="text-slate-500">Detecting framework...</span>
                                        <br />
                                        <span className="text-emerald-500">✔</span> Detected <span className="text-white font-bold">Next.js 14</span>
                                        <br />
                                        <span className="text-emerald-500">✔</span> Created <span className="text-white">cloudneuron.yaml</span>
                                        <br /><br />
                                        <span className="text-emerald-400">➜</span> <span className="text-blue-400">~/my-app</span> <span className="text-slate-400">cloudneuron deploy --prod</span>
                                        <br />
                                        <span className="text-slate-500">Building application...</span>
                                        <div className="w-full bg-slate-800 h-1 mt-2 mb-2 rounded-full overflow-hidden">
                                            <div className="bg-emerald-500 h-full w-3/4 animate-pulse"></div>
                                        </div>
                                        <span className="text-emerald-500">✔</span> Build completed in 23s
                                        <br />
                                        <span className="text-emerald-500">✔</span> Deployment active
                                        <br />
                                        <span className="text-emerald-500">✔</span> Available at: <a href="https://cloudneuron.app" target="_blank" rel="noopener noreferrer" className="text-blue-400 underline hover:text-blue-300">https://my-app.cloudneuron.app</a>
                                        <br /><br />
                                        <span className="text-emerald-400">➜</span> <span className="text-blue-400">~/my-app</span> <span className="animate-pulse">_</span>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </section>



            {/* ENTERPRISE SECURITY — Space bg with Pluto */}
            <section className="relative py-16 md:py-32 overflow-hidden">
                <div className="absolute inset-0 space-bg" />
                <div className="stars-layer" />
                <div className="stars-twinkle" />
                <ParallaxLayer speed={0.2} className="absolute inset-0 pointer-events-none">
                    <div className="nebula-patch w-[400px] h-[350px] bg-rose-600 top-[15%] right-[8%] opacity-10" style={{ animationDelay: '-12s' }} />
                </ParallaxLayer>
                <motion.div
                    className="cosmic-body cosmic-androgiant w-[350px] h-[350px] md:w-[500px] md:h-[500px] bottom-[5%] right-[3%] hidden md:block"
                    animate={{ y: [0, -10, 0] }}
                    transition={{ duration: 18, repeat: Infinity, ease: 'easeInOut' }}
                />

                <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="flex flex-col lg:flex-row items-center gap-12 lg:gap-20">
                        <div className="lg:w-1/2">
                            <div className="inline-flex items-center gap-2 px-3 py-1.5 bg-rose-500/10 border border-rose-500/20 text-rose-400 text-xs md:text-sm font-bold rounded-full mb-6">
                                <Shield className="w-3 h-3 md:w-3.5 md:h-3.5" />
                                Enterprise Security
                            </div>
                            <h2 className="text-3xl md:text-4xl lg:text-5xl font-extrabold mb-6 tracking-tight text-white">Hardened for Production</h2>
                            <p className="text-base md:text-lg text-slate-400 mb-8 leading-relaxed">
                                Don&apos;t compromise on compliance. Grid wraps your Docker clusters in military-grade WireGuard VPNs, powered by Grid&apos;s orchestration engine.
                            </p>
                            <ul className="space-y-4 mb-8">
                                <li className="flex items-center gap-3 text-slate-300">
                                    <CheckCircle2 className="w-5 h-5 text-emerald-500" /> PostgreSQL HA streaming replication with Patroni
                                </li>
                                <li className="flex items-center gap-3 text-slate-300">
                                    <CheckCircle2 className="w-5 h-5 text-emerald-500" /> End-to-end VPN mesh across regions
                                </li>
                                <li className="flex items-center gap-3 text-slate-300">
                                    <CheckCircle2 className="w-5 h-5 text-emerald-500" /> Comprehensive Audit Logs for every action
                                </li>
                                <li className="flex items-center gap-3 text-slate-300">
                                    <CheckCircle2 className="w-5 h-5 text-emerald-500" /> Disaster recovery with RPO/RTO targets
                                </li>
                                <li className="flex items-center gap-3 text-slate-300">
                                    <CheckCircle2 className="w-5 h-5 text-emerald-500" /> Zero-Trust Addon architecture
                                </li>
                            </ul>
                        </div>
                        <div className="lg:w-1/2 w-full grid grid-cols-1 sm:grid-cols-2 gap-4">
                            {complianceStandards.map((std, i) => (
                                <motion.div
                                    key={i}
                                    whileHover={{ scale: 1.02 }}
                                    className="p-6 bg-slate-900/40 backdrop-blur-sm border border-slate-700/30 rounded-2xl flex flex-col items-center justify-center text-center hover:border-emerald-500/30 transition-colors"
                                >
                                    <std.icon className="w-8 h-8 text-emerald-500 mb-4" />
                                    <h4 className="font-bold text-white">{std.name}</h4>
                                </motion.div>
                            ))}
                        </div>
                    </div>
                </div>
            </section>

            {/* CTA SECTION — Space bg + Supernova */}
            <section className="relative py-20 md:py-32 overflow-hidden">
                <div className="absolute inset-0 space-bg" />
                <div className="stars-layer" />
                <div className="stars-twinkle" />
                <ParallaxLayer speed={0.1} className="absolute inset-0 pointer-events-none">
                    <div className="nebula-patch w-[700px] h-[500px] bg-emerald-600 top-[20%] left-[20%] opacity-10" />
                </ParallaxLayer>
                <ParallaxLayer speed={-0.08} className="absolute inset-0 pointer-events-none">
                    <div className="nebula-patch w-[500px] h-[400px] bg-indigo-600 bottom-[10%] right-[10%] opacity-8" style={{ animationDelay: '-15s' }} />
                </ParallaxLayer>
                <div className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-emerald-500/30 to-transparent" />
                <motion.div
                    className="cosmic-body cosmic-protostar w-[350px] h-[350px] md:w-[500px] md:h-[500px] top-[10%] right-[10%] hidden md:block"
                    animate={{ y: [0, -10, 0] }}
                    transition={{ duration: 14, repeat: Infinity, ease: 'easeInOut' }}
                />

                <div className="relative z-10 max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 text-center">
                    <h2 className="text-3xl md:text-5xl lg:text-6xl font-extrabold mb-6 md:mb-8 tracking-tight text-white">Ready to Transform Your Workflow?</h2>
                    <p className="text-lg md:text-xl lg:text-2xl mb-10 md:mb-12 text-slate-400 max-w-3xl mx-auto font-light">
                        Deploy your first cluster in minutes. 100% free and open-source.
                    </p>
                    <div className="flex flex-col sm:flex-row gap-4 md:gap-6 justify-center">
                        <Link href="/register" className="inline-flex items-center justify-center gap-2 px-8 py-4 md:px-10 md:py-5 text-base md:text-lg font-bold text-slate-950 bg-emerald-400 rounded-xl md:rounded-2xl hover:bg-emerald-300 transition-all shadow-[0_0_15px_rgba(52,211,153,0.3)] hover:shadow-[0_0_25px_rgba(52,211,153,0.5)] transform hover:-translate-y-0.5">
                            Install Grid Now <ArrowRight className="w-4 h-4 md:w-5 md:h-5" />
                        </Link>
                    </div>
                </div>
            </section>
        </main>
    );
}

