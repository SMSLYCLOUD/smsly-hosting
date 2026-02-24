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
    Check,
    Server,
    Rocket,
    Lock,
    Code,
    Activity,
    Terminal,
    Boxes,
    CheckCircle2,
    XCircle,
    Users,
    BarChart3,
    Settings,
    Workflow,
    Command
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
        title: 'Zero-Config Deployment',
        subtitle: 'Framework Agnostic',
        description: 'Connect your GitHub repository and push. We detect your framework (Next.js, Django, Go, etc.) and configure the build pipeline automatically.',
        icon: GitBranch,
        color: 'from-emerald-500 to-teal-600',
    },
    {
        title: 'AI-Driven Auto-Scaling',
        subtitle: 'Predictive Intelligence',
        description: 'Our proprietary AI engine analyzes traffic patterns to scale your infrastructure up or down before spikes occur, optimizing cost and performance.',
        icon: Cpu,
        color: 'from-violet-500 to-purple-600',
    },
    {
        title: 'Multi-Cloud Orchestration',
        subtitle: 'Vendor Independence',
        description: 'Deploy workloads to AWS, GCP, Azure, or bare metal from a single unified control plane. Escape vendor lock-in forever.',
        icon: Cloud,
        color: 'from-blue-500 to-cyan-600',
    }
];

// ============================================
// DATA: FEATURES GRID
// ============================================
const features = [
    {
        icon: Zap,
        title: "Instant Rollbacks",
        description: "Something went wrong? Roll back to any previous deployment version in a single click.",
        color: "text-yellow-500",
        bg: "bg-yellow-500/10"
    },
    {
        icon: Shield,
        title: "Zero Trust Security",
        description: "Enterprise-grade security with automatic SSL, DDoS mitigation, and encrypted secrets management.",
        color: "text-emerald-500",
        bg: "bg-emerald-500/10"
    },
    {
        icon: Terminal,
        title: "Web Console",
        description: "Access a live interactive terminal directly in your browser — SSH into any running container instantly.",
        color: "text-blue-500",
        bg: "bg-blue-500/10"
    },
    {
        icon: Activity,
        title: "Real-Time Activity Feed",
        description: "Track every deployment, rollback, and event across all services in a single live dashboard view.",
        color: "text-purple-500",
        bg: "bg-purple-500/10"
    },
    {
        icon: GitBranch,
        title: "Preview Environments",
        description: "Automatically spin up isolated preview URLs for every Pull Request. Review before you merge.",
        color: "text-orange-500",
        bg: "bg-orange-500/10"
    },
    {
        icon: Database,
        title: "Managed Data Services",
        description: "Provision production-ready PostgreSQL, Redis, and MongoDB instances in seconds.",
        color: "text-cyan-500",
        bg: "bg-cyan-500/10"
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
        price: "$5/mo",
        priceDetail: "Self-hosted flat rate",
        features: ["Multi-Cloud Deployment", "Zero Vendor Lock-in", "AI Observability", "100% Open Source"],
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
        features: ["High Vendor Lock-in", "Complex IAM & VPC", "Opaque Billing", "Proprietary Runtime"],
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
        features: ["Platform Lock-in", "Expensive at Scale", "Black Box Runtime", "Open Core Only"],
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
        description: 'Deploy Docker containers with automatic health checks, zero-downtime updates, and self-healing.',
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
        description: 'Ultra-fast CDN-backed hosting for Next.js, React, Vue, and static sites with global replication.',
        icon: Globe,
        color: 'bg-blue-500',
    },
    {
        title: 'Managed Databases',
        description: 'Fully managed PostgreSQL, Redis, and MongoDB with automatic backups and point-in-time recovery.',
        icon: Database,
        color: 'bg-emerald-500',
    },
    {
        title: 'Team Collaboration',
        description: 'Role-based access control (RBAC), audit logs, and team workspaces for enterprise governance.',
        icon: Users,
        color: 'bg-amber-500',
    },
    {
        title: 'Observability Suite',
        description: 'Built-in real-time logging, metrics, and tracing without external dependencies.',
        icon: BarChart3,
        color: 'bg-cyan-500',
    }
];

export default function Home() {
    return (
        <main className="min-h-screen relative overflow-x-hidden">


            {/* HERO SECTION */}
            <section className="relative pt-32 pb-24 lg:pt-48 lg:pb-32 overflow-hidden" style={{ background: 'linear-gradient(to bottom, #87CEEB, #56CCF2 30%, #B8E8F5 60%, #ffffff)' }}>
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
                        className="text-5xl md:text-7xl lg:text-8xl font-extrabold text-slate-900 dark:text-white tracking-tight leading-none mb-8"
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
                        className="mt-6 text-xl md:text-2xl text-slate-600 dark:text-slate-300 max-w-3xl mx-auto leading-relaxed font-light"
                    >
                        Experience <strong>zero-config deployments</strong>, AI-driven auto-scaling, and true multi-cloud freedom.
                        Built for engineering teams who demand control without the complexity.
                    </motion.p>

                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.3, duration: 0.5 }}
                        className="mt-12 flex flex-col sm:flex-row gap-4 justify-center items-center"
                    >
                        <Link href="/register" className="group relative inline-flex items-center justify-center gap-2 px-8 py-4 text-lg font-bold text-white bg-gradient-to-r from-emerald-600 to-teal-600 rounded-2xl hover:from-emerald-500 hover:to-teal-500 transition-all shadow-xl shadow-emerald-500/20 hover:shadow-emerald-500/40 hover:-translate-y-1">
                            Start Deploying Free <ArrowRight className="w-5 h-5 group-hover:translate-x-1 transition-transform" />
                        </Link>
                        <Link href="/docs" className="inline-flex items-center justify-center gap-2 px-8 py-4 text-lg font-bold text-slate-700 dark:text-slate-200 bg-white/50 dark:bg-slate-800/50 backdrop-blur-md border border-slate-200 dark:border-slate-700 rounded-2xl hover:bg-white dark:hover:bg-slate-700 transition-all hover:-translate-y-1">
                            Read Documentation
                        </Link>
                    </motion.div>

                    <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        transition={{ delay: 0.5 }}
                        className="mt-16 flex flex-wrap justify-center gap-x-8 gap-y-3 text-sm font-semibold text-slate-500 dark:text-slate-400"
                    >
                        <span className="flex items-center gap-2"><CheckCircle2 className="w-4 h-4 text-emerald-500" /> No credit card required</span>
                        <span className="flex items-center gap-2"><CheckCircle2 className="w-4 h-4 text-emerald-500" /> $100 free credit</span>
                        <span className="flex items-center gap-2"><CheckCircle2 className="w-4 h-4 text-emerald-500" /> SOC2 Type II Compliant</span>
                    </motion.div>
                </div>

                {/* Floating Elements (Decorations) */}
                <div className="absolute bottom-20 left-10 hidden xl:block pointer-events-none">
                    <motion.div
                        animate={{ y: [0, -15, 0], rotate: [0, 5, 0] }}
                        transition={{ duration: 4, repeat: Infinity, ease: "easeInOut" }}
                        className="p-4 rounded-2xl bg-white/90 dark:bg-slate-800/90 backdrop-blur-xl border border-slate-200 dark:border-slate-700 shadow-2xl"
                    >
                        <GitBranch className="w-10 h-10 text-emerald-600 dark:text-emerald-400" />
                        <div className="mt-2 text-xs font-mono font-bold text-slate-500">git push origin main</div>
                    </motion.div>
                </div>
                <div className="absolute top-40 right-20 hidden xl:block pointer-events-none">
                    <motion.div
                        animate={{ y: [0, 20, 0], rotate: [0, -5, 0] }}
                        transition={{ duration: 5, repeat: Infinity, ease: "easeInOut", delay: 1 }}
                        className="p-4 rounded-2xl bg-white/90 dark:bg-slate-800/90 backdrop-blur-xl border border-slate-200 dark:border-slate-700 shadow-2xl"
                    >
                        <Cpu className="w-10 h-10 text-violet-500" />
                        <div className="mt-2 text-xs font-mono font-bold text-slate-500">Auto-Scaling: ON</div>
                    </motion.div>
                </div>
            </section>

            {/* GLOBAL STATISTICS BAR */}
            <section className="bg-white dark:bg-slate-900 border-y border-slate-200 dark:border-slate-800 relative z-20">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="grid grid-cols-2 md:grid-cols-4 divide-y md:divide-y-0 md:divide-x divide-slate-100 dark:divide-slate-800">
                        {globalStats.map((stat, i) => (
                            <div key={i} className="py-10 md:px-6 flex flex-col items-center justify-center text-center hover:bg-slate-50 dark:hover:bg-slate-800/50 transition-colors group">
                                <div className="flex items-center gap-2 mb-3 transform group-hover:scale-110 transition-transform duration-300">
                                    <stat.icon className={`w-6 h-6 ${stat.color}`} />
                                    <span className="text-xs font-bold text-slate-400 uppercase tracking-widest">{stat.label}</span>
                                </div>
                                <span className="text-4xl font-extrabold text-slate-900 dark:text-white font-mono tracking-tight">{stat.value}</span>
                            </div>
                        ))}
                    </div>
                </div>
            </section>

            {/* CORE PILLARS */}
            <section className="py-32 bg-slate-950 text-white overflow-hidden relative">
                <div className="absolute inset-0 opacity-10">
                    <div className="absolute inset-0 bg-[linear-gradient(to_right,#8882_1px,transparent_1px),linear-gradient(to_bottom,#8882_1px,transparent_1px)] bg-[size:48px_48px]" />
                </div>

                <div className="relative max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="text-center mb-20">
                        <span className="text-emerald-400 font-bold tracking-wider uppercase text-sm mb-4 block">Why CloudNeuron?</span>
                        <h2 className="text-4xl md:text-5xl font-extrabold mb-6 tracking-tight">Built for the Modern Stack</h2>
                        <p className="text-xl text-slate-400 max-w-3xl mx-auto leading-relaxed">
                            We&apos;ve reimagined cloud deployment to be <span className="text-white font-semibold">intelligent, automated, and effortless</span>.
                        </p>
                    </div>

                    <div className="grid lg:grid-cols-3 gap-10">
                        {coreFeatures.map((feature, i) => (
                            <motion.div
                                key={i}
                                initial={{ opacity: 0, y: 30 }}
                                whileInView={{ opacity: 1, y: 0 }}
                                viewport={{ once: true }}
                                transition={{ delay: i * 0.2 }}
                                className="relative p-10 rounded-[2.5rem] bg-slate-900/50 border border-slate-800 hover:border-emerald-500/30 transition-all group overflow-hidden"
                            >
                                <div className="absolute inset-0 bg-gradient-to-br from-emerald-500/5 to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-500" />

                                <div className={`w-16 h-16 rounded-2xl bg-gradient-to-br ${feature.color} flex items-center justify-center mb-8 shadow-lg relative z-10 group-hover:scale-110 transition-transform duration-300`}>
                                    <feature.icon className="w-8 h-8 text-white" />
                                </div>

                                <h3 className="text-2xl font-bold text-white mb-3 relative z-10">{feature.title}</h3>
                                <p className="text-emerald-400 font-mono text-xs mb-5 uppercase tracking-widest relative z-10">{feature.subtitle}</p>
                                <p className="text-slate-400 leading-relaxed relative z-10">
                                    {feature.description}
                                </p>
                            </motion.div>
                        ))}
                    </div>
                </div>
            </section>

            {/* BATTLE CARDS COMPARISON */}
            <section className="py-24 bg-slate-50 dark:bg-slate-900 border-b border-slate-200 dark:border-slate-800">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="text-center mb-16">
                        <h2 className="text-3xl md:text-5xl font-extrabold text-slate-900 dark:text-white mb-6">Stop Paying the &quot;Cloud Tax&quot;</h2>
                        <p className="text-xl text-slate-600 dark:text-slate-400 max-w-2xl mx-auto">
                            CloudNeuron runs on <strong>your infrastructure</strong>, saving you up to 90% on compute costs compared to managed services.
                        </p>
                    </div>

                    <div className="grid md:grid-cols-3 gap-8">
                        {battleCards.map((card, i) => (
                            <motion.div
                                key={i}
                                initial={{ opacity: 0, y: 20 }}
                                whileInView={{ opacity: 1, y: 0 }}
                                viewport={{ once: true }}
                                transition={{ delay: i * 0.1 }}
                                className={`relative p-8 rounded-3xl bg-white dark:bg-slate-800 border-2 ${card.highlight ? 'border-emerald-500 shadow-2xl shadow-emerald-500/10' : 'border-slate-100 dark:border-slate-700'} flex flex-col`}
                            >
                                {card.highlight && (
                                    <div className="absolute -top-4 left-1/2 -translate-x-1/2 px-4 py-1 bg-emerald-500 text-white text-xs font-bold uppercase tracking-widest rounded-full shadow-lg">
                                        Best Value
                                    </div>
                                )}

                                <div className="mb-6 flex items-center justify-between">
                                    <div className={`p-3 rounded-2xl ${card.highlight ? 'bg-emerald-100 dark:bg-emerald-900/30' : 'bg-slate-100 dark:bg-slate-700/50'}`}>
                                        <card.logo className={`w-8 h-8 ${card.textColor}`} />
                                    </div>
                                    <h3 className="text-xl font-bold text-slate-900 dark:text-white">{card.name}</h3>
                                </div>

                                <div className="mb-8">
                                    <span className="text-4xl font-extrabold text-slate-900 dark:text-white">{card.price}</span>
                                    <span className="block text-sm text-slate-500 dark:text-slate-400 mt-1">{card.priceDetail}</span>
                                </div>

                                <ul className="space-y-4 mb-8 flex-1">
                                    {card.features.map((feat, j) => (
                                        <li key={j} className="flex items-center gap-3 text-sm font-medium text-slate-600 dark:text-slate-300">
                                            {card.highlight ? (
                                                <CheckCircle2 className="w-5 h-5 text-emerald-500 flex-shrink-0" />
                                            ) : (
                                                <XCircle className="w-5 h-5 text-slate-400 flex-shrink-0" />
                                            )}
                                            {feat}
                                        </li>
                                    ))}
                                </ul>

                                <Link
                                    href={card.highlight ? "/register" : (card.name.includes("Vercel") ? "/compare" : "/pricing")}
                                    className={`w-full py-3 rounded-xl font-bold text-center transition-all ${
                                        card.highlight
                                        ? 'bg-emerald-500 hover:bg-emerald-600 text-white shadow-lg hover:shadow-emerald-500/20'
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

            {/* FEATURES GRID */}
            <section className="py-32 px-6 bg-slate-50 dark:bg-slate-900/50">
                <div className="max-w-7xl mx-auto">
                    <motion.div
                        className="text-center mb-20"
                        initial={{ opacity: 0, y: 20 }}
                        whileInView={{ opacity: 1, y: 0 }}
                        viewport={{ once: true }}
                    >
                        <div className="inline-flex items-center gap-2 px-3 py-1 bg-violet-100 dark:bg-violet-900/30 text-violet-700 dark:text-violet-300 text-sm font-medium rounded-full mb-6">
                            <Sparkles className="w-3.5 h-3.5" />
                            Everything Included
                        </div>
                        <h2 className="text-4xl md:text-5xl font-bold mb-6 text-slate-900 dark:text-white tracking-tight">
                            Everything You Need to Ship
                        </h2>
                        <p className="text-xl text-slate-600 dark:text-slate-400 max-w-2xl mx-auto">
                            Comprehensive tools for the entire application lifecycle.
                        </p>
                    </motion.div>

                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8">
                        {features.map((feature, i) => (
                            <motion.div
                                key={feature.title}
                                initial={{ opacity: 0, y: 20 }}
                                whileInView={{ opacity: 1, y: 0 }}
                                viewport={{ once: true }}
                                transition={{ delay: i * 0.1 }}
                                className="group p-8 rounded-3xl bg-white dark:bg-slate-800 border border-slate-100 dark:border-slate-700/50 hover:border-emerald-500/50 hover:shadow-xl hover:shadow-emerald-500/5 transition-all duration-300"
                            >
                                <div className={`inline-flex p-4 rounded-2xl ${feature.bg} mb-6 group-hover:rotate-6 transition-transform duration-300`}>
                                    <feature.icon className={`w-6 h-6 ${feature.color}`} />
                                </div>
                                <h3 className="text-xl font-bold mb-3 text-slate-900 dark:text-white">{feature.title}</h3>
                                <p className="text-slate-600 dark:text-slate-400 leading-relaxed">{feature.description}</p>
                            </motion.div>
                        ))}
                    </div>
                </div>
            </section>

            {/* DEVELOPER EXPERIENCE & CLI */}
            <section className="py-32 bg-white dark:bg-slate-950 overflow-hidden">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="flex flex-col lg:flex-row items-center gap-20">
                        <div className="lg:w-1/2">
                            <div className="inline-flex items-center gap-2 px-3 py-1 bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 text-sm font-medium rounded-full mb-8">
                                <Terminal className="w-3.5 h-3.5" />
                                Developer First Experience
                            </div>
                            <h2 className="text-4xl md:text-5xl font-extrabold text-slate-900 dark:text-white mb-8 tracking-tight">Power at Your Fingertips</h2>
                            <p className="text-lg text-slate-600 dark:text-slate-400 mb-10 leading-relaxed">
                                Control your entire infrastructure from the command line. The <code className="bg-slate-100 dark:bg-slate-800 px-1 py-0.5 rounded font-mono text-emerald-600 dark:text-emerald-400">smsly</code> CLI gives you instant access to logs, deployments, and secrets.
                            </p>

                            <div className="flex flex-col gap-6">
                                <div className="flex items-start gap-5 group">
                                    <div className="p-3 bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300 rounded-xl group-hover:scale-110 transition-transform"><Zap className="w-6 h-6" /></div>
                                    <div>
                                        <h4 className="font-bold text-lg text-slate-900 dark:text-white mb-1">Instant Deployments</h4>
                                        <p className="text-slate-500 dark:text-slate-400">Push to git or use the CLI to deploy in seconds.</p>
                                    </div>
                                </div>
                                <div className="flex items-start gap-5 group">
                                    <div className="p-3 bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 rounded-xl group-hover:scale-110 transition-transform"><Command className="w-6 h-6" /></div>
                                    <div>
                                        <h4 className="font-bold text-lg text-slate-900 dark:text-white mb-1">Full Control</h4>
                                        <p className="text-slate-500 dark:text-slate-400">Manage environment variables, domains, and certificates.</p>
                                    </div>
                                </div>
                                <div className="flex items-start gap-5 group">
                                    <div className="p-3 bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300 rounded-xl group-hover:scale-110 transition-transform"><Lock className="w-6 h-6" /></div>
                                    <div>
                                        <h4 className="font-bold text-lg text-slate-900 dark:text-white mb-1">Secure by Default</h4>
                                        <p className="text-slate-500 dark:text-slate-400">Automatic SSL, encrypted secrets, and isolated builds.</p>
                                    </div>
                                </div>
                            </div>
                        </div>

                        <div className="lg:w-1/2 w-full">
                            <div className="bg-[#0f172a] rounded-2xl shadow-2xl overflow-hidden border border-slate-800 ring-1 ring-white/10 relative">
                                {/* Window Controls */}
                                <div className="flex items-center justify-between px-4 py-3 border-b border-slate-800 bg-slate-900">
                                    <div className="flex gap-2">
                                        <div className="w-3 h-3 rounded-full bg-red-500/80" />
                                        <div className="w-3 h-3 rounded-full bg-amber-500/80" />
                                        <div className="w-3 h-3 rounded-full bg-emerald-500/80" />
                                    </div>
                                    <div className="text-xs font-mono text-slate-500 flex items-center gap-1">
                                        <Terminal className="w-3 h-3" />
                                        user@devbox:~
                                    </div>
                                    <div className="w-10"></div>
                                </div>

                                {/* Terminal Content */}
                                <div className="p-6 font-mono text-sm overflow-x-auto leading-relaxed h-[400px]">
                                    <div className="text-slate-300">
                                        <span className="text-emerald-400">➜</span> <span className="text-blue-400">~</span> <span className="text-slate-400">smsly login</span>
                                        <br />
                                        <span className="text-emerald-500">✔</span> Authenticated as <span className="text-white font-bold">team@cloudneuron.io</span>
                                        <br /><br />
                                        <span className="text-emerald-400">➜</span> <span className="text-blue-400">~/my-app</span> <span className="text-slate-400">smsly init</span>
                                        <br />
                                        <span className="text-slate-500">Detecting framework...</span>
                                        <br />
                                        <span className="text-emerald-500">✔</span> Detected <span className="text-white font-bold">Next.js 14</span>
                                        <br />
                                        <span className="text-emerald-500">✔</span> Created <span className="text-white">smsly.yaml</span>
                                        <br /><br />
                                        <span className="text-emerald-400">➜</span> <span className="text-blue-400">~/my-app</span> <span className="text-slate-400">smsly deploy --prod</span>
                                        <br />
                                        <span className="text-slate-500">Building application...</span>
                                        <div className="w-full bg-slate-800 h-1 mt-2 mb-2 rounded-full overflow-hidden">
                                            <div className="bg-emerald-500 h-full w-3/4 animate-pulse"></div>
                                        </div>
                                        <span className="text-emerald-500">✔</span> Build completed in 23s
                                        <br />
                                        <span className="text-emerald-500">✔</span> Deployment active
                                        <br />
                                        <span className="text-emerald-500">✔</span> Available at: <a href="https://smsly.app" target="_blank" rel="noopener noreferrer" className="text-blue-400 underline hover:text-blue-300">https://my-app.smsly.app</a>
                                        <br /><br />
                                        <span className="text-emerald-400">➜</span> <span className="text-blue-400">~/my-app</span> <span className="animate-pulse">_</span>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </section>

            {/* TESTIMONIALS */}
            <section className="py-32 bg-slate-50 dark:bg-slate-900">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="text-center mb-20">
                        <h2 className="text-3xl md:text-5xl font-extrabold text-slate-900 dark:text-white mb-6 tracking-tight">Trusted by Engineering Leaders</h2>
                        <p className="text-xl text-slate-600 dark:text-slate-400 max-w-2xl mx-auto">
                            Join thousands of teams shipping faster and scaling smarter with CloudNeuron.
                        </p>
                    </div>

                    <div className="grid md:grid-cols-3 gap-8">
                        {testimonials.map((testimonial, i) => (
                            <motion.div
                                key={i}
                                initial={{ opacity: 0, y: 30 }}
                                whileInView={{ opacity: 1, y: 0 }}
                                viewport={{ once: true }}
                                transition={{ delay: i * 0.2 }}
                                className="p-8 rounded-3xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 shadow-sm hover:shadow-lg transition-all"
                            >
                                <div className="mb-6 text-emerald-500">
                                    {[1, 2, 3, 4, 5].map((s) => (
                                        <span key={s} className="inline-block text-lg">★</span>
                                    ))}
                                </div>
                                <p className="text-lg text-slate-700 dark:text-slate-300 mb-8 leading-relaxed italic">
                                    &ldquo;{testimonial.quote}&rdquo;
                                </p>
                                <div className="flex items-center gap-4">
                                    <div className="w-12 h-12 rounded-full bg-slate-200 overflow-hidden">
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
                                        <p className="font-bold text-slate-900 dark:text-white">{testimonial.author}</p>
                                        <p className="text-sm text-slate-500 dark:text-slate-400">{testimonial.role}</p>
                                    </div>
                                </div>
                            </motion.div>
                        ))}
                    </div>
                </div>
            </section>

            {/* CTA SECTION */}
            <section className="py-32 bg-slate-950 text-white overflow-hidden relative">
                <div className="absolute inset-0 bg-gradient-to-br from-emerald-900/20 to-slate-900/20" />
                <div className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-emerald-500/50 to-transparent" />

                <div className="relative max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 text-center">
                    <h2 className="text-4xl md:text-6xl font-extrabold mb-8 tracking-tight">Ready to Transform Your Workflow?</h2>
                    <p className="text-xl md:text-2xl mb-12 text-slate-400 max-w-3xl mx-auto font-light">
                        Start deploying in minutes. No credit card required. Cancel anytime.
                    </p>
                    <div className="flex flex-col sm:flex-row gap-6 justify-center">
                        <Link href="/register" className="inline-flex items-center justify-center gap-2 px-10 py-5 text-lg font-bold text-slate-950 bg-emerald-400 rounded-2xl hover:bg-emerald-300 transition-all shadow-[0_0_20px_rgba(52,211,153,0.3)] hover:shadow-[0_0_30px_rgba(52,211,153,0.5)] transform hover:-translate-y-1">
                            Get Started Free <ArrowRight className="w-5 h-5" />
                        </Link>
                        <Link href="/contact" className="inline-flex items-center justify-center gap-2 px-10 py-5 text-lg font-bold text-white border border-slate-700 rounded-2xl hover:bg-slate-800 transition-all">
                            Talk to Sales
                        </Link>
                    </div>

                    <p className="mt-8 text-sm text-slate-500">
                        Includes 14-day free trial of Pro features. No commitment.
                    </p>
                </div>
            </section>

            <Footer />
        </main>
    );
}
