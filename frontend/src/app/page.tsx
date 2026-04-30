'use client';

import Link from 'next/link';
import Image from 'next/image';

import { Button } from '@/components/ui/button';
import { Footer } from '@/components/layout/Footer';
import { CloudHeroAnimation } from '@/components/animations/CloudHeroAnimation';
import {
    ArrowRight,
    Zap,
    Shield,
    Globe,
    Cpu,
    GitBranch,
    Database,
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
    Bot
} from 'lucide-react';
import { motion } from 'framer-motion';

// ============================================
// DATA: GLOBAL STATS
// ============================================
const globalStats = [
    { label: 'Uptime SLA', value: '99.99%', icon: Activity, color: 'text-emerald-500' },
    { label: 'Avg. Build Time', value: '< 45s', icon: Zap, color: 'text-amber-500' },
    { label: 'Global Edge Locations', value: '24+', icon: Globe, color: 'text-blue-500' },
    { label: 'Active Deployments', value: '50K+', icon: Rocket, color: 'text-violet-500' }
];

// ============================================
// DATA: CORE FEATURES
// ============================================
const coreFeatures = [
    {
        title: 'AI Router + Senate',
        subtitle: 'Ollama-native',
        description: 'Route every LLM call through LiteLLM with an AI Senate that can vote across Ollama and cloud models for safer answers.',
        icon: Sparkles,
        color: 'from-indigo-500 to-sky-600',
    },
    {
        title: 'Secure Transfers & Backups',
        subtitle: 'Disaster-ready',
        description: 'One-click service transfers, encrypted backups with retention pruning, and rollback windows keep migrations safe.',
        icon: Shield,
        color: 'from-emerald-500 to-teal-600',
    },
    {
        title: 'Mesh + Replication',
        subtitle: 'WireGuard + Patroni',
        description: 'WireGuard VPN mesh with Patroni PostgreSQL replication and HAProxy keeps data highly available across servers.',
        icon: Database,
        color: 'from-blue-500 to-cyan-600',
    }
];

// ============================================
// DATA: FEATURES GRID
// ============================================
const features = [
    {
        icon: GitBranch,
        title: "GitOps Deploy",
        description: "Connect GitHub, auto-provision builds, and gate releases with reviewable AI analysis.",
        color: "text-yellow-500",
        bg: "bg-yellow-500/10"
    },
    {
        icon: Server,
        title: "Server Mesh & VPN",
        description: "WireGuard mesh networking plus automatic host key pinning for all managed servers.",
        color: "text-emerald-500",
        bg: "bg-emerald-500/10"
    },
    {
        icon: Activity,
        title: "Autoscaler",
        description: "Node-side autoscaler tuned for container stats with authenticated control API.",
        color: "text-blue-500",
        bg: "bg-blue-500/10"
    },
    {
        icon: Lock,
        title: "Backups & Transfers",
        description: "Encrypted archives, retention pruning, and guarded server-to-server transfers.",
        color: "text-purple-500",
        bg: "bg-purple-500/10"
    },
    {
        icon: Workflow,
        title: "Templates & Blueprints",
        description: "One-click deploys for AI router, Ollama, functions, and data stacks with secret-aware env rendering.",
        color: "text-orange-500",
        bg: "bg-orange-500/10"
    },
    {
        icon: BarChart3,
        title: "Observability",
        description: "Traefik, health checks, and per-service metrics feed deployment insights and autoscale decisions.",
        color: "text-cyan-500",
        bg: "bg-cyan-500/10"
    },
    {
        icon: Shield,
        title: "Resilience & Alerts",
        description: "Encrypted backups with retention pruning plus Resend email fallbacks when services crash.",
        color: "text-rose-500",
        bg: "bg-rose-500/10"
    },
    {
        icon: CheckCircle2,
        title: "Template Preflight",
        description: "Docker image manifest checks and secret-aware env rendering stop bad one-click deploys early.",
        color: "text-indigo-500",
        bg: "bg-indigo-500/10"
    },
    {
        icon: Database,
        title: "Vector-Ready Data",
        description: "Managed Postgres ships with pgvector and Patroni replication for RAG stacks like Khoj and LibreChat.",
        color: "text-teal-500",
        bg: "bg-teal-500/10"
    }
];

// ============================================
// DATA: EXPANDED FEATURES
// ============================================
const expandedFeatures = [
    {
        title: "VPN Mesh Routing",
        description: "Zero-config WireGuard mesh networks connect your clusters globally, securing node-to-node traffic entirely under the hood.",
        icon: Network,
        color: "text-emerald-500",
        bg: "bg-emerald-500/10"
    },
    {
        title: "Zero-Downtime Server Transfers",
        description: "Move applications between regions or cloud providers seamlessly via SSH with DNS cutovers handled for you.",
        icon: RefreshCw,
        color: "text-blue-500",
        bg: "bg-blue-500/10"
    },
    {
        title: "HA Database Replication",
        description: "Patroni-backed PostgreSQL replication configured across your VPN mesh for automated primary/replica failovers.",
        icon: Database,
        color: "text-violet-500",
        bg: "bg-violet-500/10"
    },
    {
        title: "AI & LLM Blueprints",
        description: "One-click templates for Ollama, DeepSeek, vLLM, and Open-WebUI running entirely on your private GPUs.",
        icon: BrainCircuit,
        color: "text-pink-500",
        bg: "bg-pink-500/10"
    },
    {
        title: "100+ Add-on Catalog",
        description: "Provision Redis, MongoDB, ElasticSearch, RabbitMQ, and more with auto-injected secrets to your apps.",
        icon: Blocks,
        color: "text-orange-500",
        bg: "bg-orange-500/10"
    },
    {
        title: "Auto-Remediation",
        description: "AI-driven log analysis immediately diagnoses crash loops, suggesting fixes or auto-reverting broken commits.",
        icon: Bot,
        color: "text-cyan-500",
        bg: "bg-cyan-500/10"
    },
    {
        title: "Predictive Autoscaling",
        description: "HPA-style container replicas that spin up seamlessly as traffic spikes, tracked live in the dashboard.",
        icon: Activity,
        color: "text-rose-500",
        bg: "bg-rose-500/10"
    },
    {
        title: "Enterprise SSO & RBAC",
        description: "Secure your team with SAML/SSO integration, strict Audit Logs, and fine-grained Role-Based Access Controls.",
        icon: Key,
        color: "text-yellow-500",
        bg: "bg-yellow-500/10"
    },
    {
        title: "Global Edge Domains",
        description: "Automatic Let's Encrypt wildcard SSL and Caddy routing proxies traffic instantly to newly spawned containers.",
        icon: Globe,
        color: "text-teal-500",
        bg: "bg-teal-500/10"
    }
];

// ============================================
// DATA: BATTLE CARDS (COMPARISON)
// ============================================
const battleCards = [
    {
        name: "CloudNeuron",
        logo: Cloud,
        description: "The Sovereign PaaS",
        price: "$0",
        priceDetail: "Open Source & Free",
        features: ["Multi Cloud Deployment", "Zero Vendor Lock in", "Observability", "100% Open Source"],
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
// DATA: TESTIMONIALS
// ============================================
const testimonials = [
    {
        quote: "CloudNeuron's predictive scaling handled our Black Friday traffic without a single hiccup. It felt like magic.",
        author: "Sarah Jenkins",
        role: "VP Engineering, E-Shopify",
        image: "https://ui-avatars.com/api/?name=Sarah+Jenkins&background=6366f1&color=fff&format=png"
    },
    {
        quote: "We moved our entire microservices architecture from AWS ECS to CloudNeuron. Dev productivity is up 300%.",
        author: "David Chen",
        role: "Lead Architect, FinTech Global",
        image: "https://ui-avatars.com/api/?name=David+Chen&background=10b981&color=fff&format=png"
    },
    {
        quote: "The ability to deploy to bare metal and cloud simultaneously gives us the best of both worlds.",
        author: "Emily Ross",
        role: "CTO, StartupXYZ",
        image: "https://ui-avatars.com/api/?name=Emily+Ross&background=f59e0b&color=fff&format=png"
    }
];

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
// DATA: PLATFORM APPS
// ============================================
const platformApps = [
    {
        title: 'Container Orchestration',
        description: 'Deploy Docker containers with automatic health checks, zero downtime updates, and self healing.',
        icon: Boxes,
        color: 'bg-rose-500',
    },
    {
        title: 'Serverless Functions',
        description: 'Run code snippets on demand without provisioning servers. Pay only for execution time.',
        icon: Workflow,
        color: 'bg-violet-500',
    },
    {
        title: 'Static & Jamstack',
        description: 'Ultra fast CDN backed hosting for Next.js, React, Vue, and static sites with global replication.',
        icon: Globe,
        color: 'bg-blue-500',
    },
    {
        title: 'Managed Databases',
        description: 'Fully managed PostgreSQL, Redis, and MongoDB with automatic backups and point in time recovery.',
        icon: Database,
        color: 'bg-emerald-500',
    },
    {
        title: 'Team Collaboration',
        description: 'Role based access control (RBAC), audit logs, and team projects for enterprise governance.',
        icon: Users,
        color: 'bg-amber-500',
    },
    {
        title: 'Observability Suite',
        description: 'Built in real time logging, metrics, and tracing without external dependencies.',
        icon: BarChart3,
        color: 'bg-cyan-500',
    }
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
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ duration: 0.5 }}
                        className="inline-flex items-center gap-2 px-4 py-1.5 bg-emerald-50/80 dark:bg-emerald-950/30 border border-emerald-200 dark:border-emerald-800 rounded-full mb-8 shadow-sm backdrop-blur-sm"
                    >
                        <Sparkles className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
                        <span className="text-xs md:text-sm font-semibold text-emerald-800 dark:text-emerald-300 uppercase tracking-wide">
                            The Intelligent Cloud Platform
                        </span>
                    </motion.div>

                    <motion.h1
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.1, duration: 0.5 }}
                        className="text-4xl md:text-6xl lg:text-7xl font-extrabold text-slate-900 dark:text-white tracking-tight leading-none mb-6"
                    >
                        Deploy Smarter. <br className="hidden md:block" />
                        <span className="text-transparent bg-clip-text bg-gradient-to-r from-emerald-600 via-teal-500 to-cyan-500">
                            Scale Faster.
                        </span>
                    </motion.h1>

                    <motion.p
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.2, duration: 0.5 }}
                        className="mt-6 text-lg md:text-xl text-slate-600 dark:text-slate-300 max-w-3xl mx-auto leading-relaxed font-light"
                    >
                        Deploy like Heroku. Control like a VPS. Recover like an enterprise cloud. <br />
                        <strong>See your infrastructure, deployments, backups, and codebase as a living system.</strong>
                    </motion.p>

                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.3, duration: 0.5 }}
                        className="mt-10 flex flex-col sm:flex-row gap-4 justify-center items-center"
                    >
                        <Link href="/register" className="group relative inline-flex items-center justify-center gap-2 px-8 py-4 text-base font-bold text-white bg-gradient-to-r from-emerald-600 to-teal-600 rounded-xl hover:from-emerald-500 hover:to-teal-500 transition-all shadow-md shadow-emerald-500/10 hover:shadow-emerald-500/30 hover:-translate-y-0.5">
                            Start Deploying Free <ArrowRight className="w-5 h-5 group-hover:translate-x-1 transition-transform" />
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
                        <span className="flex items-center justify-center gap-2"><CheckCircle2 className="w-4 h-4 text-emerald-500" /> Free Forever</span>
                        <span className="flex items-center justify-center gap-2"><CheckCircle2 className="w-4 h-4 text-emerald-500" /> 100% Open Source</span>
                        <span className="flex items-center justify-center gap-2"><CheckCircle2 className="w-4 h-4 text-emerald-500" /> SOC2 Type II Compliant</span>
                    </motion.div>
                </div>

                {/* Floating Elements (Decorations) */}
                <div className="absolute bottom-20 left-10 hidden xl:block pointer-events-none">
                    <motion.div
                        animate={{ y: [0, -15, 0], rotate: [0, 5, 0] }}
                        transition={{ duration: 4, repeat: Infinity, ease: "easeInOut" }}
                        className="p-4 rounded-xl bg-white/90 dark:bg-slate-800/90 backdrop-blur-xl border border-slate-200 dark:border-slate-700 shadow-xl"
                    >
                        <GitBranch className="w-8 h-8 text-emerald-600 dark:text-emerald-400" />
                        <div className="mt-2 text-xs font-mono font-bold text-slate-500">git push origin main</div>
                    </motion.div>
                </div>
                <div className="absolute top-40 right-20 hidden xl:block pointer-events-none">
                    <motion.div
                        animate={{ y: [0, 20, 0], rotate: [0, -5, 0] }}
                        transition={{ duration: 5, repeat: Infinity, ease: "easeInOut", delay: 1 }}
                        className="p-4 rounded-xl bg-white/90 dark:bg-slate-800/90 backdrop-blur-xl border border-slate-200 dark:border-slate-700 shadow-xl"
                    >
                        <Cpu className="w-8 h-8 text-violet-500" />
                        <div className="mt-2 text-xs font-mono font-bold text-slate-500">Auto-Scaling: ON</div>
                    </motion.div>
                </div>
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

            {/* CORE PILLARS */}
            <section className="py-16 md:py-32 bg-slate-950 text-white overflow-hidden relative">
                <div className="absolute inset-0 opacity-10">
                    <div className="absolute inset-0 bg-[linear-gradient(to_right,#8882_1px,transparent_1px),linear-gradient(to_bottom,#8882_1px,transparent_1px)] bg-[size:48px_48px]" />
                </div>

                <div className="relative max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="text-center mb-16 md:mb-20">
                        <span className="text-emerald-400 font-bold tracking-wider uppercase text-xs md:text-sm mb-4 block">Why CloudNeuron?</span>
                        <h2 className="text-3xl md:text-5xl font-extrabold mb-4 md:mb-6 tracking-tight">Built for the Modern Stack</h2>
                        <p className="text-base md:text-xl text-slate-400 max-w-3xl mx-auto leading-relaxed">
                            We&apos;ve reimagined cloud deployment to be <span className="text-white font-semibold">intelligent, automated, and effortless</span>.
                        </p>
                    </div>

                    <div className="grid lg:grid-cols-3 gap-8 md:gap-10">
                        {coreFeatures.map((feature, i) => (
                            <motion.div
                                key={i}
                                initial={{ opacity: 0, y: 30 }}
                                whileInView={{ opacity: 1, y: 0 }}
                                viewport={{ once: true }}
                                transition={{ delay: i * 0.2 }}
                                className="relative p-8 md:p-10 rounded-2xl md:rounded-[2.5rem] bg-slate-900/50 border border-slate-800 hover:border-emerald-500/30 transition-all group overflow-hidden"
                            >
                                <div className="absolute inset-0 bg-gradient-to-br from-emerald-500/5 to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-500" />

                                <div className={`w-14 h-14 md:w-16 md:h-16 rounded-xl md:rounded-2xl bg-gradient-to-br ${feature.color} flex items-center justify-center mb-6 md:mb-8 shadow-lg relative z-10 group-hover:scale-105 transition-transform duration-300`}>
                                    <feature.icon className="w-6 h-6 md:w-8 md:h-8 text-white" />
                                </div>

                                <h3 className="text-xl md:text-2xl font-bold text-white mb-2 md:mb-3 relative z-10">{feature.title}</h3>
                                <p className="text-emerald-400 font-mono text-[10px] md:text-xs mb-4 md:mb-5 uppercase tracking-widest relative z-10">{feature.subtitle}</p>
                                <p className="text-sm md:text-base text-slate-400 leading-relaxed relative z-10">
                                    {feature.description}
                                </p>
                            </motion.div>
                        ))}
                    </div>
                </div>
            </section>

            {/* BATTLE CARDS COMPARISON */}
            <section className="py-16 md:py-24 bg-slate-50 dark:bg-slate-900 border-b border-slate-200 dark:border-slate-800">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="text-center mb-12 md:mb-16">
                        <h2 className="text-2xl md:text-4xl lg:text-5xl font-extrabold text-slate-900 dark:text-white mb-4 md:mb-6">Stop Paying the &quot;Cloud Tax&quot;</h2>
                        <p className="text-base md:text-xl text-slate-600 dark:text-slate-400 max-w-2xl mx-auto">
                            CloudNeuron runs on <strong>your infrastructure</strong>, saving you up to 90% on compute costs compared to managed services.
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
                                className={`relative p-6 md:p-8 rounded-2xl md:rounded-3xl bg-white dark:bg-slate-800 border-2 ${card.highlight ? 'border-emerald-500 shadow-xl shadow-emerald-500/10' : 'border-slate-100 dark:border-slate-700'} flex flex-col`}
                            >
                                {card.highlight && (
                                    <div className="absolute -top-3 left-1/2 -translate-x-1/2 px-3 py-1 bg-emerald-500 text-white text-[10px] md:text-xs font-bold uppercase tracking-widest rounded-full shadow-md">
                                        Best Value
                                    </div>
                                )}

                                <div className="mb-4 md:mb-6 flex items-center justify-between">
                                    <div className={`p-2 md:p-3 rounded-xl md:rounded-2xl ${card.highlight ? 'bg-emerald-100 dark:bg-emerald-900/30' : 'bg-slate-100 dark:bg-slate-700/50'}`}>
                                        <card.logo className={`w-6 h-6 md:w-8 md:h-8 ${card.textColor}`} />
                                    </div>
                                    <h3 className="text-lg md:text-xl font-bold text-slate-900 dark:text-white">{card.name}</h3>
                                </div>

                                <div className="mb-6 md:mb-8">
                                    <span className="text-3xl md:text-4xl font-extrabold text-slate-900 dark:text-white">{card.price}</span>
                                    <span className="block text-xs md:text-sm text-slate-500 dark:text-slate-400 mt-1">{card.priceDetail}</span>
                                </div>

                                <ul className="space-y-3 md:space-y-4 mb-6 md:mb-8 flex-1">
                                    {card.features.map((feat, j) => (
                                        <li key={j} className="flex items-center gap-2 md:gap-3 text-xs md:text-sm font-medium text-slate-600 dark:text-slate-300">
                                            {card.highlight ? (
                                                <CheckCircle2 className="w-4 h-4 md:w-5 md:h-5 text-emerald-500 flex-shrink-0" />
                                            ) : (
                                                <XCircle className="w-4 h-4 md:w-5 md:h-5 text-slate-400 flex-shrink-0" />
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
                                        : 'bg-slate-100 dark:bg-slate-700 hover:bg-slate-200 dark:hover:bg-slate-600 text-slate-600 dark:text-slate-300'
                                    }`}
                                >
                                    {card.highlight ? 'Start Free Trial' : (card.name.includes("Vercel") ? 'View Comparison' : 'View Pricing')}
                                </Link>
                            </motion.div>
                        ))}
                    </div>
                </div>
            </section>

            {/* EXPANDED FEATURES GRID */}
            <section className="py-16 md:py-32 px-4 sm:px-6 bg-slate-50 dark:bg-slate-900/50">
                <div className="max-w-7xl mx-auto">
                    <motion.div
                        className="text-center mb-12 md:mb-20"
                        initial={{ opacity: 0, y: 20 }}
                        whileInView={{ opacity: 1, y: 0 }}
                        viewport={{ once: true }}
                    >
                        <div className="inline-flex items-center gap-2 px-3 py-1 bg-violet-100 dark:bg-violet-900/30 text-violet-700 dark:text-violet-300 text-xs md:text-sm font-medium rounded-full mb-4 md:mb-6">
                            <Sparkles className="w-3 h-3 md:w-3.5 md:h-3.5" />
                            Everything Included
                        </div>
                        <h2 className="text-3xl md:text-4xl lg:text-5xl font-bold mb-4 md:mb-6 text-slate-900 dark:text-white tracking-tight">
                            More Than Just Deployments
                        </h2>
                        <p className="text-base md:text-xl text-slate-600 dark:text-slate-400 max-w-2xl mx-auto">
                            Comprehensive tooling spanning across application lifecycles, database management, high-availability replication, and AI inference.
                        </p>
                    </motion.div>

                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 md:gap-8">
                        {features.concat(expandedFeatures).map((feature, i) => (
                            <motion.div
                                key={feature.title}
                                initial={{ opacity: 0, y: 20 }}
                                whileInView={{ opacity: 1, y: 0 }}
                                viewport={{ once: true }}
                                transition={{ delay: Math.min(i * 0.05, 0.5) }}
                                className="group p-6 md:p-8 rounded-2xl md:rounded-3xl bg-white dark:bg-slate-800 border border-slate-100 dark:border-slate-700/50 hover:border-emerald-500/50 hover:shadow-lg hover:shadow-emerald-500/5 transition-all duration-300"
                            >
                                <div className={`inline-flex p-3 md:p-4 rounded-xl md:rounded-2xl ${feature.bg} mb-4 md:mb-6 group-hover:rotate-6 transition-transform duration-300`}>
                                    <feature.icon className={`w-5 h-5 md:w-6 md:h-6 ${feature.color}`} />
                                </div>
                                <h3 className="text-lg md:text-xl font-bold mb-2 md:mb-3 text-slate-900 dark:text-white">{feature.title}</h3>
                                <p className="text-sm md:text-base text-slate-600 dark:text-slate-400 leading-relaxed">{feature.description}</p>
                            </motion.div>
                        ))}
                    </div>
                </div>
            </section>

            {/* DEEP DIVE: THE INTELLIGENT CLOUD */}
            <section className="py-16 md:py-32 bg-slate-950 text-white overflow-hidden relative">
                <div className="absolute inset-0 bg-gradient-to-b from-slate-900 to-slate-950" />
                <div className="relative max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="flex flex-col lg:flex-row items-center gap-12 lg:gap-20">
                        <div className="lg:w-1/2 order-2 lg:order-1">
                            <div className="grid grid-cols-2 gap-4">
                                <div className="space-y-4">
                                    <div className="bg-slate-800/50 p-6 rounded-2xl border border-slate-700">
                                        <Database className="w-8 h-8 text-blue-400 mb-4" />
                                        <h4 className="font-bold mb-2">Automated PGVector</h4>
                                        <p className="text-sm text-slate-400">Embeddings databases ready out-of-the-box for RAG templates like Khoj.</p>
                                    </div>
                                    <div className="bg-slate-800/50 p-6 rounded-2xl border border-slate-700">
                                        <BrainCircuit className="w-8 h-8 text-pink-400 mb-4" />
                                        <h4 className="font-bold mb-2">LiteLLM AI Router</h4>
                                        <p className="text-sm text-slate-400">Connect local Ollama nodes with OpenAI fallback seamlessly.</p>
                                    </div>
                                </div>
                                <div className="space-y-4 pt-8">
                                    <div className="bg-slate-800/50 p-6 rounded-2xl border border-slate-700">
                                        <Bot className="w-8 h-8 text-emerald-400 mb-4" />
                                        <h4 className="font-bold mb-2">Ollama GPU Native</h4>
                                        <p className="text-sm text-slate-400">Schedule Llama3, DeepSeek-R1, and Mistral on your bare-metal machines.</p>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div className="lg:w-1/2 order-1 lg:order-2">
                            <div className="inline-flex items-center gap-2 px-3 py-1 bg-emerald-900/30 text-emerald-400 text-xs md:text-sm font-medium rounded-full mb-6">
                                <Sparkles className="w-3 h-3 md:w-3.5 md:h-3.5" />
                                The Intelligent Cloud
                            </div>
                            <h2 className="text-3xl md:text-4xl lg:text-5xl font-extrabold mb-6 tracking-tight">AI Infrastructure,<br/>Self-Hosted.</h2>
                            <p className="text-base md:text-lg text-slate-400 mb-8 leading-relaxed">
                                Don&apos;t just host web apps. CloudNeuron brings the power of dedicated AI clusters directly to your personal VPS. Deploy complex RAG pipelines, fine-tuned models, and intelligent routers with a single click. No more struggling with CUDA drivers or Docker networking for AI stacks.
                            </p>
                            <ul className="space-y-4 mb-8">
                                <li className="flex items-center gap-3 text-slate-300">
                                    <CheckCircle2 className="w-5 h-5 text-emerald-500" /> Pre-configured templates for 20+ Open-Source LLMs
                                </li>
                                <li className="flex items-center gap-3 text-slate-300">
                                    <CheckCircle2 className="w-5 h-5 text-emerald-500" /> Automatic cross-service secret injection
                                </li>
                                <li className="flex items-center gap-3 text-slate-300">
                                    <CheckCircle2 className="w-5 h-5 text-emerald-500" /> AI diagnostics for broken code deployments
                                </li>
                            </ul>
                        </div>
                    </div>
                </div>
            </section>

            {/* DEVELOPER EXPERIENCE & CLI */}
            <section className="py-16 md:py-32 bg-white dark:bg-slate-950 overflow-hidden">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="flex flex-col lg:flex-row items-center gap-12 lg:gap-20">
                        <div className="lg:w-1/2">
                            <div className="inline-flex items-center gap-2 px-3 py-1 bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 text-xs md:text-sm font-medium rounded-full mb-6 md:mb-8">
                                <Terminal className="w-3 h-3 md:w-3.5 md:h-3.5" />
                                Developer First Experience
                            </div>
                            <h2 className="text-3xl md:text-4xl lg:text-5xl font-extrabold text-slate-900 dark:text-white mb-6 md:mb-8 tracking-tight">Power at Your Fingertips</h2>
                            <p className="text-base md:text-lg text-slate-600 dark:text-slate-400 mb-8 md:mb-10 leading-relaxed">
                                Control your entire infrastructure from the command line. The <code className="bg-slate-100 dark:bg-slate-800 px-1 py-0.5 rounded font-mono text-emerald-600 dark:text-emerald-400">cloudneuron</code> CLI gives you instant access to logs, deployments, and secrets.
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

            {/* PLATFORM APPS */}
            <section className="py-16 md:py-32 bg-slate-100 dark:bg-slate-900/80 border-y border-slate-200 dark:border-slate-800">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="text-center mb-12 md:mb-20">
                        <div className="inline-flex items-center gap-2 px-3 py-1 bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 text-xs md:text-sm font-medium rounded-full mb-4">
                            <Boxes className="w-3 h-3 md:w-3.5 md:h-3.5" />
                            Platform primitives
                        </div>
                        <h2 className="text-3xl md:text-4xl lg:text-5xl font-bold mb-4 md:mb-6 text-slate-900 dark:text-white tracking-tight">Deploy Any Architecture</h2>
                        <p className="text-base md:text-xl text-slate-600 dark:text-slate-400 max-w-2xl mx-auto">
                            From simple static sites to complex AI workloads, we provide the foundational primitives you need.
                        </p>
                    </div>

                    <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
                        {platformApps.map((app, i) => (
                            <motion.div
                                key={i}
                                initial={{ opacity: 0, scale: 0.95 }}
                                whileInView={{ opacity: 1, scale: 1 }}
                                viewport={{ once: true }}
                                transition={{ delay: i * 0.1 }}
                                className="bg-white dark:bg-slate-950 p-6 md:p-8 rounded-2xl border border-slate-200 dark:border-slate-800 flex flex-col items-start hover:shadow-lg transition-shadow"
                            >
                                <div className={`p-3 rounded-xl text-white mb-6 ${app.color}`}>
                                    <app.icon className="w-6 h-6" />
                                </div>
                                <h3 className="text-xl font-bold text-slate-900 dark:text-white mb-3">{app.title}</h3>
                                <p className="text-slate-600 dark:text-slate-400 leading-relaxed">{app.description}</p>
                            </motion.div>
                        ))}
                    </div>
                </div>
            </section>

            {/* TESTIMONIALS */}
            <section className="py-16 md:py-32 bg-slate-50 dark:bg-slate-900">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="text-center mb-12 md:mb-20">
                        <h2 className="text-2xl md:text-4xl lg:text-5xl font-extrabold text-slate-900 dark:text-white mb-4 md:mb-6 tracking-tight">Trusted by Engineering Leaders</h2>
                        <p className="text-base md:text-xl text-slate-600 dark:text-slate-400 max-w-2xl mx-auto">
                            Join thousands of teams shipping faster and scaling smarter with CloudNeuron.
                        </p>
                    </div>

                    <div className="grid md:grid-cols-3 gap-6 md:gap-8">
                        {testimonials.map((testimonial, i) => (
                            <motion.div
                                key={i}
                                initial={{ opacity: 0, y: 30 }}
                                whileInView={{ opacity: 1, y: 0 }}
                                viewport={{ once: true }}
                                transition={{ delay: i * 0.2 }}
                                className="p-6 md:p-8 rounded-2xl md:rounded-3xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 shadow-sm hover:shadow-md transition-all"
                            >
                                <div className="mb-4 md:mb-6 text-emerald-500">
                                    {[1, 2, 3, 4, 5].map((s) => (
                                        <span key={s} className="inline-block text-base md:text-lg">★</span>
                                    ))}
                                </div>
                                <p className="text-base md:text-lg text-slate-700 dark:text-slate-300 mb-6 md:mb-8 leading-relaxed italic">
                                    &ldquo;{testimonial.quote}&rdquo;
                                </p>
                                <div className="flex items-center gap-3 md:gap-4">
                                    <div className="w-10 h-10 md:w-12 md:h-12 rounded-full bg-slate-200 overflow-hidden">
                                        <Image
                                            src={testimonial.image}
                                            alt={testimonial.author}
                                            width={48}
                                            height={48}
                                            className="w-full h-full object-cover"
                                            unoptimized
                                        />
                                    </div>
                                    <div>
                                        <p className="font-bold text-sm md:text-base text-slate-900 dark:text-white">{testimonial.author}</p>
                                        <p className="text-xs md:text-sm text-slate-500 dark:text-slate-400">{testimonial.role}</p>
                                    </div>
                                </div>
                            </motion.div>
                        ))}
                    </div>
                </div>
            </section>

            {/* ENTERPRISE SECURITY */}
            <section className="py-16 md:py-32 bg-white dark:bg-slate-950 overflow-hidden border-t border-slate-200 dark:border-slate-800">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="flex flex-col lg:flex-row items-center gap-12 lg:gap-20">
                        <div className="lg:w-1/2">
                            <div className="inline-flex items-center gap-2 px-3 py-1 bg-rose-100 dark:bg-rose-900/30 text-rose-700 dark:text-rose-300 text-xs md:text-sm font-medium rounded-full mb-6">
                                <Shield className="w-3 h-3 md:w-3.5 md:h-3.5" />
                                Enterprise Security
                            </div>
                            <h2 className="text-3xl md:text-4xl lg:text-5xl font-extrabold mb-6 tracking-tight text-slate-900 dark:text-white">Hardened for Production</h2>
                            <p className="text-base md:text-lg text-slate-600 dark:text-slate-400 mb-8 leading-relaxed">
                                Don&apos;t compromise on compliance. CloudNeuron wraps your Docker clusters in military-grade WireGuard VPNs, enforcing strict host-key checking and AES-CBC encrypted backups at rest.
                            </p>
                            <ul className="space-y-4 mb-8">
                                <li className="flex items-center gap-3 text-slate-700 dark:text-slate-300">
                                    <CheckCircle2 className="w-5 h-5 text-emerald-500" /> End-to-end VPN mesh across regions
                                </li>
                                <li className="flex items-center gap-3 text-slate-700 dark:text-slate-300">
                                    <CheckCircle2 className="w-5 h-5 text-emerald-500" /> Comprehensive Audit Logs for every action
                                </li>
                                <li className="flex items-center gap-3 text-slate-700 dark:text-slate-300">
                                    <CheckCircle2 className="w-5 h-5 text-emerald-500" /> Zero-Trust Addon architecture
                                </li>
                            </ul>
                        </div>
                        <div className="lg:w-1/2 w-full grid grid-cols-1 sm:grid-cols-2 gap-4">
                            {complianceStandards.map((std, i) => (
                                <motion.div
                                    key={i}
                                    whileHover={{ scale: 1.02 }}
                                    className="p-6 bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl flex flex-col items-center justify-center text-center"
                                >
                                    <std.icon className="w-8 h-8 text-emerald-500 mb-4" />
                                    <h4 className="font-bold text-slate-900 dark:text-white">{std.name}</h4>
                                </motion.div>
                            ))}
                        </div>
                    </div>
                </div>
            </section>

            {/* CTA SECTION */}
            <section className="py-20 md:py-32 bg-slate-950 text-white overflow-hidden relative border-t border-emerald-900/50">
                <div className="absolute inset-0 bg-gradient-to-br from-emerald-900/20 to-slate-900/20" />
                <div className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-emerald-500/50 to-transparent" />

                <div className="relative max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 text-center">
                    <h2 className="text-3xl md:text-5xl lg:text-6xl font-extrabold mb-6 md:mb-8 tracking-tight">Ready to Transform Your Workflow?</h2>
                    <p className="text-lg md:text-xl lg:text-2xl mb-10 md:mb-12 text-slate-400 max-w-3xl mx-auto font-light">
                        Start deploying in minutes. No credit card required. Cancel anytime.
                    </p>
                    <div className="flex flex-col sm:flex-row gap-4 md:gap-6 justify-center">
                        <Link href="/register" className="inline-flex items-center justify-center gap-2 px-8 py-4 md:px-10 md:py-5 text-base md:text-lg font-bold text-slate-950 bg-emerald-400 rounded-xl md:rounded-2xl hover:bg-emerald-300 transition-all shadow-[0_0_15px_rgba(52,211,153,0.3)] hover:shadow-[0_0_25px_rgba(52,211,153,0.5)] transform hover:-translate-y-0.5">
                            Get Started Free <ArrowRight className="w-4 h-4 md:w-5 md:h-5" />
                        </Link>
                        <Link href="/contact" className="inline-flex items-center justify-center gap-2 px-8 py-4 md:px-10 md:py-5 text-base md:text-lg font-bold text-white border border-slate-700 rounded-xl md:rounded-2xl hover:bg-slate-800 transition-all">
                            Talk to Sales
                        </Link>
                    </div>

                    <p className="mt-6 md:mt-8 text-xs md:text-sm text-slate-500">
                        Includes 14 day free trial of Pro features. No commitment.
                    </p>
                </div>
            </section>

            <Footer />
        </main>
    );
}

