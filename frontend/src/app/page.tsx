'use client';

import Link from 'next/link';
import Image from 'next/image';

import { Button } from '@/components/ui/button';
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
    Bot,
    MessageSquare,
    Fingerprint,
    TrendingUp,
    Brain,
    Container,
    Folders,
    Timer,
    Wifi,
    Waypoints,
    AppWindow,
    Cable,
    GanttChartSquare
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
// DATA: FEATURES GRID
// ============================================
const features = [
    {
        icon: GitBranch,
        title: "Deployment Previews",
        description: "Spin up isolated, ephemeral environments for every pull request with auto-injected secrets and storage.",
        color: "text-blue-500",
        bg: "bg-blue-500/10"
    },
    {
        icon: Database,
        title: "Database Cloning",
        description: "Zero-copy PostgreSQL template cloning provides instant staging data for previews without the wait.",
        color: "text-purple-500",
        bg: "bg-purple-500/10"
    },
    {
        icon: Bot,
        title: "Multi-Provider AI Engine",
        description: "17 AI providers with Senate Committee deliberation. Auto-diagnose failures, recommend fixes, and apply remediation.",
        color: "text-violet-500",
        bg: "bg-violet-500/10"
    },
    {
        icon: Container,
        title: "Addon Marketplace",
        description: "35+ managed data services — PostgreSQL, Redis, MongoDB, Kafka, Elasticsearch, MinIO, Qdrant, and more — one-click provision.",
        color: "text-orange-500",
        bg: "bg-orange-500/10"
    },
    {
        icon: Workflow,
        title: "Blueprints & AI Clusters",
        description: "One-click deployment for GPU-accelerated LLMs like Ollama, DeepSeek, and custom private data stacks.",
        color: "text-amber-500",
        bg: "bg-amber-500/10"
    },
    {
        icon: Server,
        title: "Managed Fleet Servers",
        description: "Connect, provision, and orchestrate multiple VPS nodes. Auto health-check, token exchange, and cluster management.",
        color: "text-indigo-500",
        bg: "bg-indigo-500/10"
    },
    {
        icon: Waypoints,
        title: "Dev Tunnels & Subdomains",
        description: "Expose local dev servers via public URLs with request inspection, replay, and persistent subdomain reservations.",
        color: "text-rose-500",
        bg: "bg-rose-500/10"
    },
    {
        icon: Globe,
        title: "Global Edge Routing",
        description: "Automated Let's Encrypt SSL and Caddy proxying routes traffic instantly to your global container mesh.",
        color: "text-teal-500",
        bg: "bg-teal-500/10"
    },
    {
        icon: Activity,
        title: "Observability & Mesh",
        description: "Traefik metrics and WireGuard VPN stats feed real-time health insights and autoscale decisions.",
        color: "text-cyan-500",
        bg: "bg-cyan-500/10"
    },
    {
        icon: BrainCircuit,
        title: "Auto-Remediation",
        description: "Intelligent log analysis diagnoses crash loops, auto-applies fixes, creates PRs, and re-deploys without human intervention.",
        color: "text-emerald-500",
        bg: "bg-emerald-500/10"
    },
    {
        icon: GanttChartSquare,
        title: "Ecosystem Deployer",
        description: "Scan all your repos, build a dependency graph, and deploy 30+ connected microservices in dependency-aware waves.",
        color: "text-pink-500",
        bg: "bg-pink-500/10"
    },
    {
        icon: Blocks,
        title: "Multi-Git Providers",
        description: "Connect GitHub, GitLab, and Bitbucket. Deploy from any provider with unified CI/CD, auto-deploy on push, and instant rollbacks.",
        color: "text-sky-500",
        bg: "bg-sky-500/10"
    },
    {
        icon: Boxes,
        title: "Nixpacks Build Support",
        description: "Auto-detect and build any language with Nixpacks. No Dockerfile needed — Python, Node, Go, Rust, Elixir, and more just work.",
        color: "text-fuchsia-500",
        bg: "bg-fuchsia-500/10"
    },
    {
        icon: Cloud,
        title: "S3 Backup Destinations",
        description: "Back up databases, volumes, and configs to S3, Cloudflare R2, or MinIO. Automated schedules with point-in-time recovery.",
        color: "text-amber-500",
        bg: "bg-amber-500/10"
    },
    {
        icon: AppWindow,
        title: "Serverless Functions (FaaS)",
        description: "In-browser Monaco editor to write and deploy Node.js or Python functions instantly — no repo, no Dockerfile, no config.",
        color: "text-violet-500",
        bg: "bg-violet-500/10"
    },
    {
        icon: TrendingUp,
        title: "Predictive Auto-Scaling",
        description: "AI-driven scaling that predicts load spikes before they hit. Proactively provisions resources using historical patterns and real-time metrics.",
        color: "text-rose-500",
        bg: "bg-rose-500/10"
    },
    {
        icon: Terminal,
        title: "Container Terminal",
        description: "Web-based SSH into any running container. Debug, inspect logs, run migrations, and manage state without leaving the dashboard.",
        color: "text-teal-500",
        bg: "bg-teal-500/10"
    }
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
        features: ["Full Ecosystem Deployment", "AI Auto-Remediation Engine", "Multi-Git (GitHub, GitLab, Bitbucket)", "Nixpacks Any-Language Builds", "Predictive AI Auto-Scaling", "S3/R2/MinIO Backups", "Web Container Terminal", "100% Open Source"],
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
                        What are you building?
                    </motion.p>

                    <motion.h1
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.1, duration: 0.5 }}
                        className="text-4xl md:text-6xl lg:text-7xl font-extrabold text-slate-900 dark:text-white tracking-tight leading-none mb-6"
                    >
                        Deploy your entire software ecosystem <br className="hidden md:block" />
                        <span className="text-transparent bg-clip-text bg-gradient-to-r from-emerald-600 via-teal-500 to-cyan-500">
                            from one control grid.
                        </span>
                    </motion.h1>

                    <motion.p
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.2, duration: 0.5 }}
                        className="mt-6 text-lg md:text-xl text-slate-600 dark:text-slate-300 max-w-3xl mx-auto leading-relaxed font-light"
                    >
                        Grid is a free, open-source PaaS. Deploy complete software ecosystems on infrastructure you control. <br />
                        <strong>Connect your VPS and deploy connected apps, services, databases, workers, AI auto-remediation, tunnels, and multi-server clusters without DevOps pain.</strong>
                    </motion.p>

                    <motion.p
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.25, duration: 0.5 }}
                        className="mt-4 text-lg md:text-xl font-semibold text-transparent bg-clip-text bg-gradient-to-r from-violet-600 via-purple-500 to-fuchsia-500"
                    >
                        Grid eliminates the fear of microservices.
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

            {/* VIDEO DEMO SECTION */}
            <section className="relative -mt-12 md:-mt-24 z-20 px-4 sm:px-6 lg:px-8 max-w-5xl mx-auto mb-16 md:mb-24">
                <motion.div 
                    initial={{ opacity: 0, y: 40 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.6, duration: 0.7 }}
                    className="bg-slate-900 rounded-2xl md:rounded-[2rem] p-2 md:p-4 shadow-2xl border border-slate-800 relative group overflow-hidden"
                >
                    <div className="absolute inset-0 bg-gradient-to-tr from-emerald-500/20 to-blue-500/20 opacity-0 group-hover:opacity-100 transition-opacity duration-700 pointer-events-none" />
                    <div className="aspect-video bg-black rounded-xl md:rounded-[1.5rem] overflow-hidden relative flex items-center justify-center border border-slate-800">
                        {/* Placeholder for video */}
                        <video 
                            className="w-full h-full object-cover"
                            controls
                            preload="metadata"
                            poster="/images/video-placeholder.jpg" // Add your poster image to public/images/
                        >
                            {/* Replace with your actual video source */}
                            <source src="/videos/grid-demo.mp4" type="video/mp4" />
                            Your browser does not support the video tag.
                        </video>
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



            {/* BUILT BY SMSLYCLOUD */}
            <section className="py-20 md:py-32 bg-white dark:bg-slate-950 overflow-hidden border-t border-slate-200 dark:border-slate-800">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="flex flex-col lg:flex-row items-center gap-12 lg:gap-20">
                        <div className="lg:w-1/2">
                            <motion.div
                                initial={{ opacity: 0, y: 20 }}
                                whileInView={{ opacity: 1, y: 0 }}
                                viewport={{ once: true }}
                            >
                                <div className="inline-flex items-center gap-2 px-3 py-1 bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 text-xs md:text-sm font-medium rounded-full mb-6">
                                    <Rocket className="w-3 h-3 md:w-3.5 md:h-3.5" />
                                    Secured by SMSLYCLOUD
                                </div>
                                <h2 className="text-3xl md:text-4xl lg:text-5xl font-extrabold text-slate-900 dark:text-white mb-6 tracking-tight leading-tight">
                                    Grid is secured by the SMSLYCLOUD infrastructure trust layer.
                                </h2>
                                <p className="text-base md:text-lg text-slate-600 dark:text-slate-400 mb-8 leading-relaxed">
                                    Grid comes from SMSLYCLOUD, an infrastructure trust ecosystem building the tools modern internet businesses need to communicate, verify, deploy, and grow.
                                </p>
                                <p className="text-sm md:text-base text-slate-500 dark:text-slate-500 mb-8 leading-relaxed italic">
                                    &quot;Grid is one product in a wider SMSLYCLOUD mission: make serious internet infrastructure easier, safer, and more accessible.&quot;
                                </p>
                                <Link 
                                    href="https://smsly.cloud" 
                                    target="_blank" 
                                    rel="noopener noreferrer"
                                    className="inline-flex items-center gap-2 px-6 py-3 bg-emerald-50 dark:bg-emerald-950/20 text-emerald-600 dark:text-emerald-400 font-bold rounded-xl border border-emerald-100 dark:border-emerald-800/50 hover:bg-emerald-100 dark:hover:bg-emerald-900/30 transition-all mb-10"
                                >
                                    Explore SMSLYCLOUD <ArrowUpRight className="w-4 h-4" />
                                </Link>
                            </motion.div>
                            
                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
                                {smslycloudPillars.map((pillar, i) => (
                                    <motion.div
                                        key={i}
                                        initial={{ opacity: 0, x: -20 }}
                                        whileInView={{ opacity: 1, x: 0 }}
                                        viewport={{ once: true }}
                                        transition={{ delay: i * 0.1 }}
                                        className="flex items-start gap-3 group"
                                    >
                                        <div className={`p-2 rounded-lg ${pillar.bg} ${pillar.color} group-hover:scale-110 transition-transform`}>
                                            <pillar.icon className="w-5 h-5" />
                                        </div>
                                        <div>
                                            <h4 className="font-bold text-sm md:text-base text-slate-900 dark:text-white mb-1">{pillar.title}</h4>
                                            <p className="text-xs md:text-sm text-slate-500 dark:text-slate-400">{pillar.description}</p>
                                        </div>
                                    </motion.div>
                                ))}
                            </div>
                        </div>

                        <div className="lg:w-1/2 relative">
                            {/* Visual element representing the ecosystem - using existing styles */}
                            <motion.div
                                initial={{ opacity: 0, scale: 0.95 }}
                                whileInView={{ opacity: 1, scale: 1 }}
                                viewport={{ once: true }}
                                className="relative p-1 bg-gradient-to-br from-emerald-500/20 via-teal-500/20 to-blue-500/20 rounded-[2.5rem] overflow-hidden"
                            >
                                <div className="bg-white dark:bg-slate-900 rounded-[2.3rem] p-8 md:p-12 relative overflow-hidden">
                                    <div className="absolute top-0 right-0 p-8 opacity-10">
                                        <Globe className="w-64 h-64 text-slate-400" />
                                    </div>
                                    
                                    <div className="relative z-10">
                                        <div className="text-emerald-500 font-bold tracking-widest uppercase text-xs mb-4">Infrastructure Ecosystem</div>
                                        <h3 className="text-2xl md:text-3xl font-extrabold text-slate-900 dark:text-white mb-6 leading-tight">Built for founders, scaled for the internet.</h3>
                                        
                                        <div className="space-y-6">
                                            <div className="flex items-center gap-4 p-4 bg-slate-50 dark:bg-slate-800/50 rounded-2xl border border-slate-100 dark:border-slate-800 transform hover:translate-x-2 transition-transform">
                                                <div className="w-10 h-10 rounded-full bg-blue-500 flex items-center justify-center text-white shadow-lg shadow-blue-500/20">
                                                    <MessageSquare className="w-5 h-5" />
                                                </div>
                                                <div>
                                                    <div className="font-bold text-sm text-slate-900 dark:text-white">Communication APIs</div>
                                                    <div className="text-[10px] text-slate-500 uppercase font-bold tracking-wider">Messaging & OTP</div>
                                                </div>
                                            </div>
                                            
                                            <div className="flex items-center gap-4 p-4 bg-slate-50 dark:bg-slate-800/50 rounded-2xl border border-slate-100 dark:border-slate-800 translate-x-4 md:translate-x-8 transform hover:translate-x-10 transition-transform">
                                                <div className="w-10 h-10 rounded-full bg-emerald-500 flex items-center justify-center text-white shadow-lg shadow-emerald-500/20">
                                                    <Fingerprint className="w-5 h-5" />
                                                </div>
                                                <div>
                                                    <div className="font-bold text-sm text-slate-900 dark:text-white">Identity & Trust</div>
                                                    <div className="text-[10px] text-slate-500 uppercase font-bold tracking-wider">Security & Verification</div>
                                                </div>
                                            </div>

                                            <div className="flex items-center gap-4 p-4 bg-slate-50 dark:bg-slate-800/50 rounded-2xl border border-slate-100 dark:border-slate-800 transform hover:translate-x-2 transition-transform">
                                                <div className="w-10 h-10 rounded-full bg-indigo-500 flex items-center justify-center text-white shadow-lg shadow-indigo-500/20">
                                                    <Cloud className="w-5 h-5" />
                                                </div>
                                                <div>
                                                    <div className="font-bold text-sm text-slate-900 dark:text-white">Deployment</div>
                                                    <div className="text-[10px] text-slate-500 uppercase font-bold tracking-wider">Grid by CloudNeuron</div>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </motion.div>
                        </div>
                    </div>
                </div>
            </section>
 
             {/* THE TRUST LAYER ECOSYSTEM GRID */}
             <section className="py-20 md:py-32 bg-slate-50 dark:bg-slate-900 border-t border-slate-200 dark:border-slate-800">
                 <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                     <div className="text-center mb-16 md:mb-20">
                         <h2 className="text-3xl md:text-5xl font-extrabold text-slate-900 dark:text-white mb-6 tracking-tight">
                             The Trust Layer for the Internet
                         </h2>
                         <p className="text-base md:text-xl text-slate-600 dark:text-slate-400 max-w-3xl mx-auto leading-relaxed">
                             SMSLYCLOUD provides the essential infrastructure for modern internet businesses — 
                             from zero-trust security to real-time deepfake detection and global communications.
                         </p>
                     </div>
 
                     <div className="grid md:grid-cols-2 lg:grid-cols-4 gap-6 md:gap-8">
                         {ecosystemServices.map((cat, i) => (
                             <motion.div
                                 key={i}
                                 initial={{ opacity: 0, y: 20 }}
                                 whileInView={{ opacity: 1, y: 0 }}
                                 viewport={{ once: true }}
                                 transition={{ delay: i * 0.1 }}
                                 className="flex flex-col bg-white dark:bg-slate-800 p-8 rounded-[2rem] border border-slate-200 dark:border-slate-700 shadow-sm hover:shadow-xl hover:shadow-emerald-500/5 transition-all group"
                             >
                                 <div className={`w-12 h-12 rounded-xl ${cat.bg} ${cat.color} flex items-center justify-center mb-6 group-hover:scale-110 transition-transform`}>
                                     <cat.icon className="w-6 h-6" />
                                 </div>
                                 <h3 className="text-xl font-bold text-slate-900 dark:text-white mb-6">{cat.category}</h3>
                                 <ul className="space-y-6 flex-1">
                                     {cat.services.map((svc, j) => (
                                         <li key={j} className="relative pl-4 border-l-2 border-slate-100 dark:border-slate-700 hover:border-emerald-500 transition-colors">
                                             <h4 className="font-bold text-sm text-slate-900 dark:text-white mb-1">{svc.name}</h4>
                                             <p className="text-xs text-slate-500 dark:text-slate-400 leading-normal">{svc.desc}</p>
                                         </li>
                                     ))}
                                 </ul>
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
                                    {card.highlight ? 'Install Grid' : (card.name.includes("Vercel") ? 'View Comparison' : 'View Pricing')}
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
                            Deploy Complete Ecosystems
                        </h2>
                        <p className="text-base md:text-xl text-slate-600 dark:text-slate-400 max-w-2xl mx-auto">
                            Grid handles frontends, APIs, databases, workers, queues, SSL, backups, AI diagnostics, tunnels, and multi-server nodes — all working together as one connected system.
                        </p>
                    </motion.div>

                    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4 md:gap-6">
                        {features.map((feature, i) => (
                            <motion.div
                                key={feature.title}
                                initial={{ opacity: 0, y: 20 }}
                                whileInView={{ opacity: 1, y: 0 }}
                                viewport={{ once: true }}
                                transition={{ delay: Math.min(i * 0.03, 0.4) }}
                                className="group p-5 md:p-6 rounded-2xl bg-white dark:bg-slate-800 border border-slate-100 dark:border-slate-700/50 hover:border-emerald-500/50 hover:shadow-lg hover:shadow-emerald-500/5 transition-all duration-300"
                            >
                                <div className={`inline-flex p-2.5 md:p-3 rounded-xl ${feature.bg} mb-3 md:mb-4 group-hover:rotate-6 transition-transform duration-300`}>
                                    <feature.icon className={`w-4 h-4 md:w-5 md:h-5 ${feature.color}`} />
                                </div>
                                <h3 className="text-sm md:text-base font-bold mb-1.5 md:mb-2 text-slate-900 dark:text-white">{feature.title}</h3>
                                <p className="text-xs md:text-sm text-slate-600 dark:text-slate-400 leading-relaxed">{feature.description}</p>
                            </motion.div>
                        ))}
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
                                Control your entire infrastructure from the command line. The <code className="bg-slate-100 dark:bg-slate-800 px-1 py-0.5 rounded font-mono text-emerald-600 dark:text-emerald-400">grid</code> CLI gives you instant access to logs, deployments, and secrets.
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
                                Don&apos;t compromise on compliance. Grid wraps your Docker clusters in military-grade WireGuard VPNs, powered by Grid&apos;s orchestration engine.
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

