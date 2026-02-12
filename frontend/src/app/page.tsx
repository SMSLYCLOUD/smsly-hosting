'use client';

import Link from 'next/link';
import { Navbar } from '@/components/layout/Navbar';
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
    Workflow
} from 'lucide-react';
import { motion } from 'framer-motion';

// ============================================
// DATA: GLOBAL STATS
// ============================================
const globalStats = [
    { label: 'Uptime SLA', value: '99.99%', icon: Activity, color: 'text-emerald-500' },
    { label: 'Deploy Speed', value: '< 30s', icon: Zap, color: 'text-amber-500' },
    { label: 'Global Regions', value: '12+', icon: Globe, color: 'text-blue-500' },
    { label: 'Apps Deployed', value: '10K+', icon: Rocket, color: 'text-violet-500' }
];

// ============================================
// DATA: CORE FEATURES
// ============================================
const coreFeatures = [
    {
        title: 'Git Push Deploy',
        subtitle: 'Zero Config',
        description: 'Connect your repository and deploy. We auto-detect frameworks and configure everything.',
        icon: GitBranch,
        color: 'from-emerald-500 to-teal-600',
    },
    {
        title: 'AI Auto-Scaling',
        subtitle: 'Predictive',
        description: 'Our AI engine predicts traffic spikes and scales your infrastructure before you need it.',
        icon: Cpu,
        color: 'from-violet-500 to-purple-600',
    },
    {
        title: 'Multi-Cloud',
        subtitle: 'Vendor Agnostic',
        description: 'Deploy to AWS, GCP, Azure, or your own infrastructure from a single dashboard.',
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
        title: "One-Click Deploy",
        description: "Connect your repo and deploy. Auto-detection of frameworks, zero configuration needed.",
        color: "text-yellow-500",
        bg: "bg-yellow-500/10"
    },
    {
        icon: Shield,
        title: "Enterprise Security",
        description: "SOC2 compliant with automatic SSL, DDoS protection, and secret management.",
        color: "text-emerald-500",
        bg: "bg-emerald-500/10"
    },
    {
        icon: Globe,
        title: "Multi-Cloud",
        description: "Deploy to AWS, Azure, GCP, or your own infrastructure from one dashboard.",
        color: "text-blue-500",
        bg: "bg-blue-500/10"
    },
    {
        icon: Cpu,
        title: "AI Auto-Scaling",
        description: "Intelligent resource management that predicts traffic and scales automatically.",
        color: "text-purple-500",
        bg: "bg-purple-500/10"
    },
    {
        icon: GitBranch,
        title: "Preview Deploys",
        description: "Every pull request gets its own environment for testing and review.",
        color: "text-orange-500",
        bg: "bg-orange-500/10"
    },
    {
        icon: Database,
        title: "Managed Databases",
        description: "PostgreSQL, Redis, MongoDB - provisioned in seconds with automatic backups.",
        color: "text-cyan-500",
        bg: "bg-cyan-500/10"
    }
];

// ============================================
// DATA: COMPARISON
// ============================================
const comparisonFeatures = [
    { feature: 'Deployment Time', legacy: '5-10 minutes', sovereign: 'Under 30 seconds' },
    { feature: 'Auto-Scaling', legacy: 'Manual configuration', sovereign: 'AI-Powered Predictive' },
    { feature: 'Multi-Cloud', legacy: 'Vendor lock-in', sovereign: 'True portability' },
    { feature: 'CI/CD', legacy: 'External setup required', sovereign: 'Built-in pipelines' },
    { feature: 'Pricing', legacy: 'Hidden costs', sovereign: 'Transparent per-use' }
];

// ============================================
// DATA: TESTIMONIALS
// ============================================
const testimonials = [
    {
        quote: "We migrated from Heroku and cut our deployment time by 90%. The AI scaling paid for itself in the first week.",
        author: "Sarah Chen",
        role: "CTO, TechFlow",
        image: "https://ui-avatars.com/api/?name=Sarah+Chen&background=6366f1&color=fff"
    },
    {
        quote: "The multi-cloud support means we're never locked in. We can optimize costs across providers effortlessly.",
        author: "Michael Torres",
        role: "VP Engineering, DataScale",
        image: "https://ui-avatars.com/api/?name=Michael+Torres&background=10b981&color=fff"
    },
    {
        quote: "Preview environments for every PR changed how our team collaborates. QA happens before merge now.",
        author: "Emily Ross",
        role: "Lead Developer, StartupXYZ",
        image: "https://ui-avatars.com/api/?name=Emily+Ross&background=f59e0b&color=fff"
    }
];

// ============================================
// DATA: COMPLIANCE
// ============================================
const complianceStandards = [
    { name: 'SOC 2 Type II', icon: CheckCircle2 },
    { name: 'GDPR Ready', icon: Globe },
    { name: 'ISO 27001', icon: Shield },
    { name: 'HIPAA Eligible', icon: Activity }
];

// ============================================
// DATA: PLATFORM APPS
// ============================================
const platformApps = [
    {
        title: 'Container Orchestration',
        description: 'Deploy Docker containers with automatic health checks and zero-downtime updates.',
        icon: Boxes,
        color: 'bg-rose-500',
    },
    {
        title: 'Serverless Functions',
        description: 'Run code without managing servers. Pay only for execution time.',
        icon: Workflow,
        color: 'bg-violet-500',
    },
    {
        title: 'Static Sites',
        description: 'Ultra-fast CDN-backed hosting for Next.js, React, Vue, and static sites.',
        icon: Globe,
        color: 'bg-blue-500',
    },
    {
        title: 'Managed Databases',
        description: 'PostgreSQL, Redis, MongoDB with automatic backups and scaling.',
        icon: Database,
        color: 'bg-emerald-500',
    },
    {
        title: 'Team Management',
        description: 'Role-based access control and audit logs for enterprise teams.',
        icon: Users,
        color: 'bg-amber-500',
    },
    {
        title: 'Real-time Analytics',
        description: 'Monitor deployments, track errors, and analyze performance metrics.',
        icon: BarChart3,
        color: 'bg-cyan-500',
    }
];

const fadeInUp = {
    initial: { opacity: 0, y: 20 },
    animate: { opacity: 1, y: 0 },
    transition: { duration: 0.5 }
};

const stagger = {
    animate: {
        transition: {
            staggerChildren: 0.1
        }
    }
};

export default function Home() {
    return (
        <main className="min-h-screen relative">
            <Navbar />

            <section className="relative pt-32 pb-24 overflow-hidden" style={{ background: 'linear-gradient(to bottom, #87CEEB, #56CCF2 30%, #B8E8F5 60%, #ffffff)' }}>
                {/* Dark mode override */}
                <div className="absolute inset-0 bg-gradient-to-b from-slate-900 via-slate-800 to-slate-950 dark:block hidden" />
                {/* Cloud Animation */}
                <div className="absolute inset-0 z-0">
                    <CloudHeroAnimation />
                </div>

                <div className="relative max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 text-center z-10">
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ duration: 0.5 }}
                        className="inline-flex items-center gap-2 px-4 py-2 bg-emerald-50 dark:bg-emerald-950/50 border border-emerald-100 dark:border-emerald-800 rounded-full mb-8 shadow-sm"
                    >
                        <Sparkles className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
                        <span className="text-sm font-semibold text-emerald-800 dark:text-emerald-300">Intelligent Cloud Infrastructure</span>
                    </motion.div>

                    <motion.h1
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.1, duration: 0.5 }}
                        className="text-5xl md:text-7xl font-extrabold text-slate-900 dark:text-white tracking-tight leading-tight mb-6"
                    >
                        Deploy Anything, <br className="hidden md:block" />
                        <span className="text-transparent bg-clip-text bg-gradient-to-r from-emerald-600 via-green-500 to-teal-500">
                            Anywhere.
                        </span>
                    </motion.h1>

                    <motion.p
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.2, duration: 0.5 }}
                        className="mt-6 text-xl text-slate-600 dark:text-slate-400 max-w-3xl mx-auto leading-relaxed"
                    >
                        The intelligent platform that builds, deploys, and scales your applications
                        across any cloud provider with <strong>zero configuration</strong>.
                    </motion.p>

                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.3, duration: 0.5 }}
                        className="mt-10 flex flex-col sm:flex-row gap-4 justify-center"
                    >
                        <Link href="/register" className="inline-flex items-center justify-center gap-2 px-8 py-4 text-lg font-bold text-white bg-gradient-to-r from-emerald-600 to-green-600 rounded-xl hover:from-emerald-700 hover:to-green-700 transition-all shadow-lg shadow-emerald-200 dark:shadow-emerald-900/30">
                            Start Deploying <ArrowRight className="w-5 h-5" />
                        </Link>
                        <Link href="/services" className="inline-flex items-center justify-center gap-2 px-8 py-4 text-lg font-bold text-slate-700 dark:text-slate-200 bg-white dark:bg-slate-800 border-2 border-slate-200 dark:border-slate-700 rounded-xl hover:bg-slate-50 dark:hover:bg-slate-700 transition-all">
                            View Demo
                        </Link>
                    </motion.div>

                    <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        transition={{ delay: 0.5 }}
                        className="mt-16 pt-8 border-t border-slate-100 dark:border-slate-800 flex flex-wrap justify-center gap-x-12 gap-y-4 text-sm font-semibold text-slate-500 dark:text-slate-400"
                    >
                        <span className="flex items-center gap-2"><Check className="w-4 h-4 text-emerald-500" /> No credit card</span>
                        <span className="flex items-center gap-2"><Check className="w-4 h-4 text-emerald-500" /> Free tier forever</span>
                        <span className="flex items-center gap-2"><Check className="w-4 h-4 text-emerald-500" /> 5 min setup</span>
                        <span className="flex items-center gap-2"><Check className="w-4 h-4 text-emerald-500" /> SOC2 Compliant</span>
                    </motion.div>
                </div>

                {/* Floating Elements */}
                <div className="absolute bottom-10 left-10 hidden lg:block">
                    <motion.div
                        animate={{ y: [0, -10, 0] }}
                        transition={{ duration: 3, repeat: Infinity, ease: "easeInOut" }}
                        className="p-4 rounded-2xl bg-white/80 dark:bg-slate-800/80 backdrop-blur-xl border border-slate-200 dark:border-slate-700 shadow-xl"
                    >
                        <GitBranch className="w-8 h-8 text-emerald-600 dark:text-emerald-400" />
                    </motion.div>
                </div>
                <div className="absolute top-32 right-20 hidden lg:block">
                    <motion.div
                        animate={{ y: [0, 10, 0] }}
                        transition={{ duration: 4, repeat: Infinity, ease: "easeInOut", delay: 0.5 }}
                        className="p-4 rounded-2xl bg-white/80 dark:bg-slate-800/80 backdrop-blur-xl border border-slate-200 dark:border-slate-700 shadow-xl"
                    >
                        <Database className="w-8 h-8 text-cyan-500" />
                    </motion.div>
                </div>
                <div className="absolute bottom-32 right-32 hidden lg:block">
                    <motion.div
                        animate={{ y: [0, -8, 0] }}
                        transition={{ duration: 3.5, repeat: Infinity, ease: "easeInOut", delay: 1 }}
                        className="p-4 rounded-2xl bg-white/80 dark:bg-slate-800/80 backdrop-blur-xl border border-slate-200 dark:border-slate-700 shadow-xl"
                    >
                        <Cloud className="w-8 h-8 text-emerald-500" />
                    </motion.div>
                </div>
            </section>

            {/* GLOBAL STATISTICS BAR */}
            <section className="bg-slate-50 dark:bg-slate-900 border-y border-slate-200 dark:border-slate-800">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="grid grid-cols-2 md:grid-cols-4 divide-y md:divide-y-0 md:divide-x divide-slate-200 dark:divide-slate-800">
                        {globalStats.map((stat, i) => (
                            <div key={i} className="py-8 md:px-6 flex flex-col items-center justify-center text-center hover:bg-white dark:hover:bg-slate-800 transition-colors">
                                <div className="flex items-center gap-2 mb-2">
                                    <stat.icon className={`w-5 h-5 ${stat.color}`} />
                                    <span className="text-sm font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wide">{stat.label}</span>
                                </div>
                                <span className="text-3xl font-extrabold text-slate-900 dark:text-white">{stat.value}</span>
                            </div>
                        ))}
                    </div>
                </div>
            </section>

            {/* CORE PILLARS */}
            <section className="py-24 bg-slate-900 text-white overflow-hidden relative">
                <div className="absolute inset-0 opacity-20">
                    <div className="absolute inset-0 bg-[linear-gradient(to_right,#8882_1px,transparent_1px),linear-gradient(to_bottom,#8882_1px,transparent_1px)] bg-[size:64px_64px]" />
                </div>

                <div className="relative max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="text-center mb-16">
                        <h2 className="text-3xl md:text-5xl font-extrabold mb-6">Built for Modern Development</h2>
                        <p className="text-xl text-slate-400 max-w-3xl mx-auto leading-relaxed">
                            Not just another PaaS. We&apos;ve reimagined <span className="text-emerald-400 font-semibold">cloud deployment</span> from the ground up.
                        </p>
                    </div>

                    <div className="grid lg:grid-cols-3 gap-8">
                        {coreFeatures.map((feature, i) => (
                            <motion.div
                                key={i}
                                initial={{ opacity: 0, y: 30 }}
                                whileInView={{ opacity: 1, y: 0 }}
                                viewport={{ once: true }}
                                transition={{ delay: i * 0.2 }}
                                className="relative p-8 rounded-3xl bg-slate-800 border border-slate-700 hover:border-emerald-500/50 transition-all group h-full"
                            >
                                <div className={`w-14 h-14 rounded-2xl bg-gradient-to-br ${feature.color} flex items-center justify-center mb-6 shadow-lg relative overflow-hidden`}>
                                    <feature.icon className="w-7 h-7 text-white relative z-10" />
                                    <div className="absolute inset-0 bg-white/20 animate-ping opacity-0 group-hover:opacity-30 rounded-2xl transition-opacity duration-700" />
                                </div>
                                <h3 className="text-2xl font-bold text-white mb-2">{feature.title}</h3>
                                <p className="text-emerald-400 font-medium text-sm mb-4 uppercase tracking-wider">{feature.subtitle}</p>
                                <p className="text-slate-400 leading-relaxed">
                                    {feature.description}
                                </p>
                            </motion.div>
                        ))}
                    </div>
                </div>
            </section>

            {/* COMPLIANCE STRIP */}
            <section className="bg-slate-900 border-t border-slate-800 py-12">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="flex flex-wrap justify-center md:justify-between items-center gap-8">
                        <span className="text-slate-400 font-semibold uppercase tracking-wider text-sm">Enterprise Compliance:</span>
                        <div className="flex flex-wrap justify-center gap-8 md:gap-16">
                            {complianceStandards.map((std, i) => (
                                <div key={i} className="flex items-center gap-2 text-slate-300 opacity-60 hover:opacity-100 transition-opacity">
                                    <std.icon className="w-5 h-5 text-emerald-500" />
                                    <span className="font-bold text-lg">{std.name}</span>
                                </div>
                            ))}
                        </div>
                    </div>
                </div>
            </section>

            {/* FEATURES GRID */}
            <section className="py-24 px-6 bg-slate-50 dark:bg-slate-900/50">
                <div className="max-w-6xl mx-auto">
                    <motion.div
                        className="text-center mb-16"
                        initial={{ opacity: 0, y: 20 }}
                        whileInView={{ opacity: 1, y: 0 }}
                        viewport={{ once: true }}
                    >
                        <div className="inline-flex items-center gap-2 px-3 py-1 bg-emerald-100 dark:bg-emerald-900/50 text-emerald-700 dark:text-emerald-300 text-sm font-medium rounded-full mb-4">
                            <Zap className="w-3.5 h-3.5" />
                            Powerful Features
                        </div>
                        <h2 className="text-3xl md:text-4xl font-bold mb-4 text-slate-900 dark:text-white">
                            Everything You Need to Ship Fast
                        </h2>
                        <p className="text-xl text-slate-600 dark:text-slate-400">
                            From git push to production in seconds, not hours.
                        </p>
                    </motion.div>

                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                        {features.map((feature, i) => (
                            <motion.div
                                key={feature.title}
                                initial={{ opacity: 0, y: 20 }}
                                whileInView={{ opacity: 1, y: 0 }}
                                viewport={{ once: true }}
                                transition={{ delay: i * 0.1 }}
                                className="group p-6 rounded-2xl bg-white dark:bg-slate-800/50 backdrop-blur-xl border border-slate-200 dark:border-slate-700 hover:border-emerald-500/50 hover:shadow-xl hover:shadow-emerald-500/5 transition-all duration-300"
                            >
                                <div className={`inline-flex p-3 rounded-xl ${feature.bg} mb-4 group-hover:scale-110 transition-transform`}>
                                    <feature.icon className={`w-6 h-6 ${feature.color}`} />
                                </div>
                                <h3 className="text-xl font-semibold mb-2 text-slate-900 dark:text-white">{feature.title}</h3>
                                <p className="text-slate-600 dark:text-slate-400">{feature.description}</p>
                            </motion.div>
                        ))}
                    </div>
                </div>
            </section>

            {/* DEVELOPER EXPERIENCE */}
            <section className="py-24 bg-white dark:bg-slate-950">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="flex flex-col lg:flex-row items-center gap-16">
                        <div className="lg:w-1/2">
                            <div className="inline-flex items-center gap-2 px-3 py-1 bg-violet-100 dark:bg-violet-900/50 text-violet-700 dark:text-violet-300 text-sm font-medium rounded-full mb-6">
                                <Terminal className="w-3.5 h-3.5" />
                                Developer First
                            </div>
                            <h2 className="text-4xl font-extrabold text-slate-900 dark:text-white mb-6">Deploy in 3 Lines</h2>
                            <p className="text-lg text-slate-600 dark:text-slate-400 mb-8 leading-relaxed">
                                No YAML configs. No Kubernetes expertise. Just connect your repo and deploy.
                                We handle the rest automatically.
                            </p>

                            <div className="flex flex-col gap-4">
                                <div className="flex items-start gap-4">
                                    <div className="p-2 bg-emerald-100 dark:bg-emerald-900/50 text-emerald-700 dark:text-emerald-300 rounded-lg"><Zap className="w-5 h-5" /></div>
                                    <div>
                                        <h4 className="font-bold text-slate-900 dark:text-white">Instant Builds</h4>
                                        <p className="text-sm text-slate-500 dark:text-slate-400">Parallel build system with caching for sub-minute deploys.</p>
                                    </div>
                                </div>
                                <div className="flex items-start gap-4">
                                    <div className="p-2 bg-blue-100 dark:bg-blue-900/50 text-blue-700 dark:text-blue-300 rounded-lg"><Code className="w-5 h-5" /></div>
                                    <div>
                                        <h4 className="font-bold text-slate-900 dark:text-white">Auto-Detection</h4>
                                        <p className="text-sm text-slate-500 dark:text-slate-400">Detects Node.js, Python, Go, Rust, and 20+ frameworks.</p>
                                    </div>
                                </div>
                                <div className="flex items-start gap-4">
                                    <div className="p-2 bg-amber-100 dark:bg-amber-900/50 text-amber-700 dark:text-amber-300 rounded-lg"><Lock className="w-5 h-5" /></div>
                                    <div>
                                        <h4 className="font-bold text-slate-900 dark:text-white">Secret Management</h4>
                                        <p className="text-sm text-slate-500 dark:text-slate-400">Encrypted env vars with per-environment isolation.</p>
                                    </div>
                                </div>
                            </div>
                        </div>

                        <div className="lg:w-1/2 w-full">
                            <div className="bg-slate-900 rounded-2xl shadow-2xl overflow-hidden border border-slate-800">
                                <div className="flex items-center justify-between px-4 py-3 border-b border-slate-800 bg-slate-900/50">
                                    <div className="flex gap-2">
                                        <div className="w-3 h-3 rounded-full bg-red-500" />
                                        <div className="w-3 h-3 rounded-full bg-amber-500" />
                                        <div className="w-3 h-3 rounded-full bg-green-500" />
                                    </div>
                                    <div className="text-xs font-mono text-slate-500">terminal</div>
                                </div>

                                <div className="p-6 font-mono text-sm overflow-x-auto">
                                    <pre className="text-slate-300">
                                        {`$ smsly login
✓ Authenticated as team@example.com

$ smsly init
✓ Detected: Next.js 14 application
✓ Created smsly.yaml

$ smsly deploy
⠋ Building...
✓ Build completed in 23s
✓ Deployed to `}<span className="text-emerald-400">https://app.smsly.cloud</span>{`

🚀 Your app is live!`}
                                    </pre>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </section>

            {/* PLATFORM APPLICATIONS */}
            <section className="py-24 bg-slate-50 dark:bg-slate-900">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="text-center mb-16">
                        <h2 className="text-3xl md:text-4xl font-extrabold text-slate-900 dark:text-white mb-4">One Platform, Infinite Possibilities</h2>
                        <p className="text-lg text-slate-600 dark:text-slate-400 max-w-2xl mx-auto">
                            Everything you need to build, deploy, and scale modern applications.
                        </p>
                    </div>

                    <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-8">
                        {platformApps.map((app, i) => (
                            <motion.div
                                key={i}
                                initial={{ opacity: 0, y: 30, scale: 0.95 }}
                                whileInView={{ opacity: 1, y: 0, scale: 1 }}
                                viewport={{ once: true }}
                                transition={{ delay: i * 0.12, duration: 0.6, type: "spring", stiffness: 100 }}
                                whileHover={{ y: -8, scale: 1.03 }}
                                className="bg-white dark:bg-slate-800 rounded-2xl p-6 shadow-sm border border-slate-200 dark:border-slate-700 hover:border-emerald-300 dark:hover:border-emerald-600 transition-all group cursor-pointer h-full"
                            >
                                <motion.div
                                    className={`w-12 h-12 rounded-xl ${app.color} flex items-center justify-center mb-6 shadow-lg`}
                                    whileHover={{ scale: 1.15, rotate: 5 }}
                                    transition={{ type: "spring", stiffness: 300 }}
                                >
                                    <app.icon className="w-6 h-6 text-white" />
                                </motion.div>
                                <h3 className="text-xl font-bold text-slate-900 dark:text-white mb-3">{app.title}</h3>
                                <p className="text-slate-600 dark:text-slate-400 leading-relaxed">{app.description}</p>
                            </motion.div>
                        ))}
                    </div>
                </div>
            </section>

            {/* TESTIMONIALS */}
            <section className="py-24 bg-white dark:bg-slate-950">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="text-center mb-16">
                        <h2 className="text-3xl md:text-4xl font-extrabold text-slate-900 dark:text-white mb-4">Loved by Developers</h2>
                        <p className="text-lg text-slate-600 dark:text-slate-400">
                            Join thousands of teams shipping faster with CloudNeuron.
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
                                className="p-8 rounded-2xl bg-slate-50 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700"
                            >
                                <p className="text-lg text-slate-700 dark:text-slate-300 mb-6 leading-relaxed italic">
                                    &ldquo;{testimonial.quote}&rdquo;
                                </p>
                                <div className="flex items-center gap-4">
                                    <img
                                        src={testimonial.image}
                                        alt={testimonial.author}
                                        className="w-12 h-12 rounded-full"
                                    />
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

            {/* COMPARISON TABLE */}
            <section className="py-24 bg-slate-900 text-white">
                <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="text-center mb-16">
                        <h2 className="text-3xl md:text-4xl font-extrabold mb-4">The CloudNeuron Advantage</h2>
                        <p className="text-lg text-slate-400 max-w-2xl mx-auto">
                            Why modern teams are switching from legacy hosting providers.
                        </p>
                    </div>

                    <div className="overflow-hidden rounded-2xl border border-slate-700 shadow-2xl">
                        <table className="w-full text-left border-collapse">
                            <thead>
                                <tr className="bg-slate-800 border-b border-slate-700">
                                    <th className="px-6 py-4 text-sm font-semibold text-slate-300 uppercase tracking-wider">Feature</th>
                                    <th className="px-6 py-4 text-sm font-semibold text-slate-400 uppercase tracking-wider">Traditional PaaS</th>
                                    <th className="px-6 py-4 text-sm font-semibold text-emerald-400 uppercase tracking-wider">CloudNeuron</th>
                                </tr>
                            </thead>
                            <tbody>
                                {comparisonFeatures.map((row, i) => (
                                    <tr key={i} className="border-b border-slate-800 hover:bg-slate-800/50 transition-colors">
                                        <td className="px-6 py-5 text-slate-200 font-medium">{row.feature}</td>
                                        <td className="px-6 py-5">
                                            <span className="flex items-center gap-2 text-slate-400">
                                                <XCircle className="w-4 h-4 text-red-400" />
                                                {row.legacy}
                                            </span>
                                        </td>
                                        <td className="px-6 py-5">
                                            <span className="flex items-center gap-2 text-emerald-400 font-semibold">
                                                <CheckCircle2 className="w-4 h-4" />
                                                {row.sovereign}
                                            </span>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            </section>

            {/* CTA SECTION */}
            <section className="py-24 bg-gradient-to-r from-emerald-600 via-green-600 to-teal-600 text-white">
                <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 text-center">
                    <h2 className="text-4xl md:text-5xl font-extrabold mb-6">Ready to Deploy?</h2>
                    <p className="text-xl mb-10 text-white/80 max-w-2xl mx-auto">
                        Join thousands of developers shipping faster with CloudNeuron.
                        Start free, scale infinitely.
                    </p>
                    <div className="flex flex-col sm:flex-row gap-4 justify-center">
                        <Link href="/register" className="inline-flex items-center justify-center gap-2 px-8 py-4 text-lg font-bold text-emerald-600 bg-white rounded-xl hover:bg-slate-100 transition-all shadow-lg">
                            Get Started Free <ArrowRight className="w-5 h-5" />
                        </Link>
                        <Link href="/contact" className="inline-flex items-center justify-center gap-2 px-8 py-4 text-lg font-bold text-white border-2 border-white/30 rounded-xl hover:bg-white/10 transition-all">
                            Talk to Sales
                        </Link>
                    </div>
                </div>
            </section>

            {/* FOOTER */}
            <footer className="py-12 px-6 border-t border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-950">
                <div className="max-w-6xl mx-auto flex flex-col md:flex-row items-center justify-between gap-6">
                    <div className="flex items-center gap-3">
                        <div className="w-8 h-8 bg-emerald-600 rounded-lg flex items-center justify-center">
                            <Cloud className="w-4 h-4 text-white" />
                        </div>
                        <span className="font-bold text-slate-900 dark:text-white">CloudNeuron</span>
                    </div>
                    <div className="text-slate-500 dark:text-slate-400 text-sm">
                        © 2026 CloudNeuron. Built for developers.
                    </div>
                    <div className="flex items-center gap-6 text-sm text-slate-500 dark:text-slate-400">
                        <Link href="/docs" className="hover:text-slate-900 dark:hover:text-white transition-colors">Docs</Link>
                        <Link href="/pricing" className="hover:text-slate-900 dark:hover:text-white transition-colors">Pricing</Link>
                        <Link href="/status" className="hover:text-slate-900 dark:hover:text-white transition-colors">Status</Link>
                        <Link href="/support" className="hover:text-slate-900 dark:hover:text-white transition-colors">Support</Link>
                    </div>
                </div>
            </footer>
        </main>
    );
}
