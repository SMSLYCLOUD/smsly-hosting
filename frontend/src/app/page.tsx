'use client';

import Link from 'next/link';
import { Navbar } from '@/components/layout/Navbar';
import { Button } from '@/components/ui/button';
import {
    ArrowRight,
    Layout,
    PlusCircle,
    Zap,
    Shield,
    Globe,
    Cpu,
    GitBranch,
    Database,
    Cloud,
    Sparkles,
    Check
} from 'lucide-react';
import { motion } from 'framer-motion';

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
        <main className="min-h-screen bg-background flex flex-col overflow-hidden">
            <Navbar />

            {/* Hero Section */}
            <section className="relative flex-1 flex flex-col items-center justify-center px-6 py-20 lg:py-32">
                {/* Background Effects */}
                <div className="absolute inset-0 -z-10">
                    <div className="absolute inset-0 bg-gradient-to-br from-primary/5 via-background to-accent/5" />
                    <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-primary/20 rounded-full blur-3xl animate-pulse-slow" />
                    <div className="absolute bottom-1/4 right-1/4 w-96 h-96 bg-cyan-500/20 rounded-full blur-3xl animate-pulse-slow" style={{ animationDelay: '1s' }} />
                    <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[800px] h-[800px] bg-gradient-radial from-primary/5 to-transparent" />
                </div>

                {/* Grid Pattern */}
                <div className="absolute inset-0 -z-10 bg-[linear-gradient(to_right,#8882_1px,transparent_1px),linear-gradient(to_bottom,#8882_1px,transparent_1px)] bg-[size:64px_64px]" />

                <motion.div
                    className="max-w-5xl text-center space-y-8"
                    initial="initial"
                    animate="animate"
                    variants={stagger}
                >
                    {/* Badge */}
                    <motion.div variants={fadeInUp} className="inline-flex items-center gap-2 px-4 py-2 rounded-full bg-primary/10 border border-primary/20 text-primary text-sm font-medium">
                        <Sparkles size={16} />
                        AI-Powered Multi-Cloud PaaS
                    </motion.div>

                    {/* Headline */}
                    <motion.h1
                        variants={fadeInUp}
                        className="text-5xl md:text-7xl font-bold tracking-tight"
                    >
                        Deploy Anything,{' '}
                        <span className="bg-clip-text text-transparent bg-gradient-to-r from-primary via-cyan-500 to-emerald-500 animate-gradient bg-[length:200%_auto]">
                            Anywhere
                        </span>
                    </motion.h1>

                    <motion.p
                        variants={fadeInUp}
                        className="text-xl md:text-2xl text-muted-foreground max-w-3xl mx-auto leading-relaxed"
                    >
                        The intelligent platform that builds, deploys, and scales your applications
                        across any cloud provider with zero configuration.
                    </motion.p>

                    {/* CTA Buttons */}
                    <motion.div variants={fadeInUp} className="flex flex-col sm:flex-row items-center justify-center gap-4 pt-4">
                        <Link href="/new">
                            <Button size="lg" className="h-14 px-8 text-lg bg-gradient-to-r from-primary via-primary to-cyan-500 hover:opacity-90 shadow-2xl shadow-primary/30 transition-all hover:scale-105 group">
                                Start Deploying
                                <ArrowRight className="ml-2 group-hover:translate-x-1 transition-transform" />
                            </Button>
                        </Link>
                        <Link href="/services">
                            <Button variant="outline" size="lg" className="h-14 px-8 text-lg border-2 hover:bg-muted/50">
                                View Demo
                            </Button>
                        </Link>
                    </motion.div>

                    {/* Trust Badges */}
                    <motion.div variants={fadeInUp} className="flex items-center justify-center gap-6 pt-8 text-muted-foreground">
                        <div className="flex items-center gap-2">
                            <Check className="w-5 h-5 text-emerald-500" />
                            <span className="text-sm">No credit card</span>
                        </div>
                        <div className="flex items-center gap-2">
                            <Check className="w-5 h-5 text-emerald-500" />
                            <span className="text-sm">Free tier forever</span>
                        </div>
                        <div className="flex items-center gap-2">
                            <Check className="w-5 h-5 text-emerald-500" />
                            <span className="text-sm">5 min setup</span>
                        </div>
                    </motion.div>
                </motion.div>

                {/* Floating Elements */}
                <div className="absolute bottom-10 left-10 hidden lg:block">
                    <motion.div
                        animate={{ y: [0, -10, 0] }}
                        transition={{ duration: 3, repeat: Infinity, ease: "easeInOut" }}
                        className="p-4 rounded-2xl bg-card/50 backdrop-blur-xl border border-border shadow-xl"
                    >
                        <GitBranch className="w-8 h-8 text-primary" />
                    </motion.div>
                </div>
                <div className="absolute top-32 right-20 hidden lg:block">
                    <motion.div
                        animate={{ y: [0, 10, 0] }}
                        transition={{ duration: 4, repeat: Infinity, ease: "easeInOut", delay: 0.5 }}
                        className="p-4 rounded-2xl bg-card/50 backdrop-blur-xl border border-border shadow-xl"
                    >
                        <Database className="w-8 h-8 text-cyan-500" />
                    </motion.div>
                </div>
                <div className="absolute bottom-32 right-32 hidden lg:block">
                    <motion.div
                        animate={{ y: [0, -8, 0] }}
                        transition={{ duration: 3.5, repeat: Infinity, ease: "easeInOut", delay: 1 }}
                        className="p-4 rounded-2xl bg-card/50 backdrop-blur-xl border border-border shadow-xl"
                    >
                        <Cloud className="w-8 h-8 text-emerald-500" />
                    </motion.div>
                </div>
            </section>

            {/* Features Section */}
            <section className="py-24 px-6 bg-muted/30">
                <div className="max-w-6xl mx-auto">
                    <motion.div
                        className="text-center mb-16"
                        initial={{ opacity: 0, y: 20 }}
                        whileInView={{ opacity: 1, y: 0 }}
                        viewport={{ once: true }}
                    >
                        <h2 className="text-3xl md:text-4xl font-bold mb-4">
                            Everything You Need to Ship Fast
                        </h2>
                        <p className="text-xl text-muted-foreground">
                            From git push to production in seconds, not hours.
                        </p>
                    </motion.div>

                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                        {[
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
                        ].map((feature, i) => (
                            <motion.div
                                key={feature.title}
                                initial={{ opacity: 0, y: 20 }}
                                whileInView={{ opacity: 1, y: 0 }}
                                viewport={{ once: true }}
                                transition={{ delay: i * 0.1 }}
                                className="group p-6 rounded-2xl bg-card/50 backdrop-blur-xl border border-border hover:border-primary/50 hover:shadow-xl hover:shadow-primary/5 transition-all duration-300"
                            >
                                <div className={`inline-flex p-3 rounded-xl ${feature.bg} mb-4 group-hover:scale-110 transition-transform`}>
                                    <feature.icon className={`w-6 h-6 ${feature.color}`} />
                                </div>
                                <h3 className="text-xl font-semibold mb-2">{feature.title}</h3>
                                <p className="text-muted-foreground">{feature.description}</p>
                            </motion.div>
                        ))}
                    </div>
                </div>
            </section>

            {/* Quick Actions Section */}
            <section className="py-24 px-6">
                <div className="max-w-4xl mx-auto">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                        <Link href="/services" className="group">
                            <motion.div
                                whileHover={{ scale: 1.02 }}
                                className="h-full p-8 rounded-2xl bg-gradient-to-br from-card via-card to-primary/5 border border-border hover:border-primary/50 transition-all shadow-lg hover:shadow-xl"
                            >
                                <div className="flex items-center justify-between mb-6">
                                    <div className="p-4 bg-primary/10 rounded-xl text-primary">
                                        <Layout size={28} />
                                    </div>
                                    <ArrowRight className="text-muted-foreground group-hover:translate-x-2 group-hover:text-primary transition-all" />
                                </div>
                                <h2 className="text-2xl font-bold mb-3">View Services</h2>
                                <p className="text-muted-foreground">Monitor your active deployments, check health status, and manage configurations.</p>
                            </motion.div>
                        </Link>

                        <Link href="/new" className="group">
                            <motion.div
                                whileHover={{ scale: 1.02 }}
                                className="h-full p-8 rounded-2xl bg-gradient-to-br from-card via-card to-emerald-500/5 border border-border hover:border-emerald-500/50 transition-all shadow-lg hover:shadow-xl"
                            >
                                <div className="flex items-center justify-between mb-6">
                                    <div className="p-4 bg-emerald-500/10 rounded-xl text-emerald-500">
                                        <PlusCircle size={28} />
                                    </div>
                                    <ArrowRight className="text-muted-foreground group-hover:translate-x-2 group-hover:text-emerald-500 transition-all" />
                                </div>
                                <h2 className="text-2xl font-bold mb-3">Deploy New</h2>
                                <p className="text-muted-foreground">Deploy from GitHub, Docker, or choose from our marketplace of templates.</p>
                            </motion.div>
                        </Link>
                    </div>
                </div>
            </section>

            {/* Footer */}
            <footer className="py-8 px-6 border-t border-border bg-muted/20">
                <div className="max-w-6xl mx-auto flex flex-col md:flex-row items-center justify-between gap-4">
                    <div className="text-muted-foreground text-sm">
                        © 2024 SMSly Hosting. Built for developers.
                    </div>
                    <div className="flex items-center gap-6 text-sm text-muted-foreground">
                        <Link href="/docs" className="hover:text-foreground transition-colors">Docs</Link>
                        <Link href="/pricing" className="hover:text-foreground transition-colors">Pricing</Link>
                        <Link href="/status" className="hover:text-foreground transition-colors">Status</Link>
                        <Link href="/support" className="hover:text-foreground transition-colors">Support</Link>
                    </div>
                </div>
            </footer>
        </main>
    );
}
