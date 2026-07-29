'use client';

import React, { Fragment } from 'react';
import Link from 'next/link';
import { motion } from 'framer-motion';
import {
    Check,
    X,
    Zap,
    Cloud,
    Shield,
    Layers,
    ArrowRight,
    CheckCircle2,
    XCircle,
    ArrowUpRight
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Footer } from '@/components/layout/Footer';

// ============================================
// DATA: BATTLE CARDS
// ============================================
const battleCards = [
    {
        name: "Grid",
        logo: Cloud,
        type: "Sovereign PaaS",
        price: "$0",
        priceDetail: "Open Source & Free",
        description: "Your infrastructure, your rules. Powered by Grid.",
        features: [
            "Self-Hosted (AWS, Hetzner, etc.)",
            "Zero Vendor Lock-in",
            "Predictive AI Auto-Scaling",
            "Persistent Volumes Included"
        ],
        highlight: true,
        color: "emerald"
    },
    {
        name: "Railway",
        logo: Layers,
        type: "Managed PaaS",
        price: "$5 + Usage",
        priceDetail: "RAM/CPU Minutes",
        description: "Great DX, but costs scale linearly with traffic.",
        features: [
            "Managed Cloud (GCP backbone)",
            "Low Vendor Lock-in",
            "Reactive Auto-Scaling",
            "Expensive Persistent Storage"
        ],
        highlight: false,
        color: "slate"
    },
    {
        name: "Vercel",
        logo: Zap,
        type: "Frontend Cloud",
        price: "$20/seat",
        priceDetail: "+ Bandwidth/Function fees",
        description: "Optimized for Next.js, restrictive for backend workloads.",
        features: [
            "AWS Wrapper (Serverless)",
            "High Vendor Lock-in",
            "Cold Starts (Serverless)",
            "No Native Persistent Storage"
        ],
        highlight: false,
        color: "slate"
    },
    {
        name: "Render",
        logo: ArrowUpRight,
        type: "Legacy PaaS",
        price: "$25+",
        priceDetail: "per standard dyno",
        description: "Sleeping dynos, extremely expensive scaling, rigid buildpacks.",
        features: [
            "Sleeping dynos",
            "Extremely expensive scaling",
            "Rigid buildpacks",
            "Slow support"
        ],
        highlight: false,
        color: "slate"
    }
];

// ============================================
// DATA: FEATURE COMPARISON
// ============================================
const comparisonRows = [
    {
        category: "Core Platform",
        items: [
            { name: "Deployment Target", cn: "Any VPS / Cloud / Bare Metal", rw: "Managed GCP", vc: "Managed AWS Lambda", rn: "Managed AWS/GCP" },
            { name: "Compute Model", cn: "Long-running Containers", rw: "Containers", vc: "Serverless Functions", rn: "Containers / Dynos" },
            { name: "Vendor Lock-in", cn: "Zero (Standard Docker)", rw: "Low", vc: "High (Proprietary APIs)", rn: "High (Proprietary YAML)" },
            { name: "Multi-Cloud Support", cn: true, rw: false, vc: false, rn: false },
        ]
    },
    {
        category: "Pricing & Limits",
        items: [
            { name: "Pricing Model", cn: "Flat Rate (You pay provider directly)", rw: "Usage-based markup", vc: "Per-seat + Usage + Bandwidth", rn: "Per Service Tier + Usage" },
            { name: "Bandwidth Cost", cn: "Included (TB free usually)", rw: "$0.10/GB after limit", vc: "$0.15/GB (Enterprise only)", rn: "$0.10/GB after limit" },
            { name: "Execution Time Limit", cn: "Unlimited", rw: "Unlimited", vc: "10s - 60s (Plan dependent)", rn: "Unlimited (unless sleeping)" },
            { name: "Seat Pricing", cn: "Unlimited Users", rw: "Unlimited", vc: "$20/user/month", rn: "$19/user/month" },
        ]
    },
    {
        category: "Features",
        items: [
            { name: "AI Auto-Scaling", cn: "Predictive (AI-Driven)", rw: "Reactive", vc: "Reactive (Serverless)", rn: "Reactive / Expensive" },
            { name: "Persistent Storage", cn: "Native Volumes (Zero Cost)", rw: "Volumes (Beta / $$$)", vc: "3rd Party Integrations Only", rn: "Volumes ($$$)" },
            { name: "Private Networking", cn: "Included (WireGuard Mesh)", rw: "Included", vc: "Enterprise Only", rn: "Included (VPC)" },
            { name: "Docker Support", cn: "Native (Dockerfile / Compose)", rw: "Native", vc: "Limited (Next.js focused)", rn: "Native" },
            { name: "Serverless Functions (FaaS)", cn: "Monaco Editor + Node/Python", rw: false, vc: "Serverless Functions (JS/TS)", rn: false },
            { name: "Container Terminal", cn: "Web SSH into containers", rw: false, vc: false, rn: false },
        ]
    },
    {
        category: "Databases & State",
        items: [
            { name: "Managed Postgres", cn: "Included (Patroni HA)", rw: "Included (No HA)", vc: "Vercel Postgres ($)", rn: "Render Postgres ($$$)" },
            { name: "Vector Search (pgvector)", cn: "Included by default", rw: "Manual setup", vc: "3rd party integrations", rn: "Manual setup" },
            { name: "Redis / In-Memory", cn: "Included", rw: "Included", vc: "Vercel KV ($)", rn: "Render Redis ($$)" },
            { name: "S3-Compatible Backups", cn: "S3, R2, MinIO (Built-in)", rw: false, vc: false, rn: false },
        ]
    },
    {
        category: "Networking & Security",
        items: [
            { name: "VPN Mesh", cn: "WireGuard Node-to-Node", rw: false, vc: "Enterprise Only", rn: "VPC Only" },
            { name: "Custom Domains & SSL", cn: "Unlimited (Caddy Proxy)", rw: "Included", vc: "Included", rn: "Included" },
            { name: "DDoS Protection", cn: "Cloudflare Compatible", rw: "Included", vc: "Included", rn: "Included" },
        ]
    },
    {
        category: "AI & LLM Workloads",
        items: [
            { name: "Ollama / Local LLMs", cn: "Native Blueprints (1-Click)", rw: "Manual Setup", vc: false, rn: "Manual Setup" },
            { name: "GPU Support", cn: "Any provider (AWS/Hetzner)", rw: false, vc: false, rn: false },
            { name: "AI Request Routing", cn: "Included (LiteLLM Senate)", rw: false, vc: "Vercel AI SDK (Code level)", rn: false },
        ]
    },
    {
        category: "Deployment & DX",
        items: [
            { name: "Git Push to Deploy", cn: true, rw: true, vc: true, rn: true },
            { name: "Multi-Git Providers", cn: "GitHub, GitLab, Bitbucket", rw: "GitHub Only", vc: "GitHub, GitLab, Bitbucket", rn: "GitHub Only" },
            { name: "Nixpacks Build Support", cn: "Auto-detect any language", rw: false, vc: false, rn: false },
            { name: "One-Click Blueprints", cn: "100+ Apps & Addons", rw: "Templates Available", vc: "Templates Available", rn: "Blueprints Available" },
            { name: "CLI Tooling", cn: "grid CLI", rw: "railway CLI", vc: "vercel CLI", rn: "render CLI" },
        ]
    }
];

export default function ComparePage() {
    return (
        <main className="min-h-screen bg-slate-50 dark:bg-slate-950">

            {/* HERO SECTION */}
            <section className="relative pt-32 pb-20 px-6 overflow-hidden bg-slate-950 text-white">
                <div className="absolute inset-0 bg-[linear-gradient(to_right,#8882_1px,transparent_1px),linear-gradient(to_bottom,#8882_1px,transparent_1px)] bg-[size:48px_48px] opacity-10" />
                <div className="absolute inset-0 bg-gradient-to-b from-transparent via-slate-950/50 to-slate-950" />

                <div className="max-w-7xl mx-auto text-center relative z-10">
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ duration: 0.5 }}
                        className="inline-flex items-center gap-2 px-4 py-1.5 bg-emerald-500/10 border border-emerald-500/20 rounded-full mb-8"
                    >
                        <Shield className="w-4 h-4 text-emerald-400" />
                        <span className="text-sm font-semibold text-emerald-400 uppercase tracking-wide">The Sovereign Alternative</span>
                    </motion.div>

                    <h1 className="text-4xl md:text-6xl lg:text-7xl font-extrabold mb-8 tracking-tight">
                        Stop Paying the <br className="hidden md:block" />
                        <span className="text-transparent bg-clip-text bg-gradient-to-r from-emerald-400 to-cyan-400">
                            &quot;Cloud Tax&quot;
                        </span>
                    </h1>

                    <p className="text-xl text-slate-400 max-w-3xl mx-auto leading-relaxed mb-10">
                        Why pay a 300% markup for managed services? <br className="hidden md:block" />
                        Grid gives you the DX of Vercel with the cost of a VPS.
                    </p>

                    <div className="flex flex-col sm:flex-row gap-4 justify-center">
                        <Link href="/register">
                            <Button size="lg" className="bg-emerald-500 hover:bg-emerald-600 text-white font-bold px-8 h-14 rounded-xl text-lg shadow-lg shadow-emerald-500/20">
                                Start Deploying Free <ArrowRight className="ml-2 w-5 h-5" />
                            </Button>
                        </Link>
                        <Link href="#comparison-table">
                            <Button variant="outline" size="lg" className="border-slate-700 text-slate-300 hover:text-white hover:bg-slate-800 h-14 rounded-xl text-lg">
                                View Full Comparison
                            </Button>
                        </Link>
                    </div>
                </div>
            </section>

            {/* BATTLE CARDS */}
            <section className="py-20 px-6 relative -mt-20 z-20">
                <div className="max-w-7xl mx-auto">
                    <div className="grid md:grid-cols-2 lg:grid-cols-4 gap-8">
                        {battleCards.map((card, i) => (
                            <motion.div
                                key={card.name}
                                initial={{ opacity: 0, y: 30 }}
                                animate={{ opacity: 1, y: 0 }}
                                transition={{ delay: i * 0.1 + 0.3 }}
                                className={`relative p-8 rounded-3xl border-2 flex flex-col ${
                                    card.highlight
                                    ? 'bg-slate-900 border-emerald-500 shadow-2xl shadow-emerald-500/10'
                                    : 'bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-800'
                                }`}
                            >
                                {card.highlight && (
                                    <div className="absolute -top-5 left-1/2 -translate-x-1/2 px-6 py-2 bg-emerald-500 text-white text-xs font-bold uppercase tracking-widest rounded-full shadow-lg">
                                        Winner
                                    </div>
                                )}

                                <div className="mb-6 flex items-center justify-between">
                                    <div className={`p-3 rounded-2xl ${card.highlight ? 'bg-emerald-900/30 text-emerald-400' : 'bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400'}`}>
                                        <card.logo className="w-8 h-8" />
                                    </div>
                                    <div className="text-right">
                                        <h3 className={`text-xl font-bold ${card.highlight ? 'text-white' : 'text-slate-900 dark:text-white'}`}>{card.name}</h3>
                                        <p className="text-xs font-medium text-slate-500 uppercase tracking-wide">{card.type}</p>
                                    </div>
                                </div>

                                <div className="mb-8">
                                    <span className={`text-4xl font-extrabold ${card.highlight ? 'text-white' : 'text-slate-900 dark:text-white'}`}>{card.price}</span>
                                    <span className="block text-sm text-slate-500 mt-1">{card.priceDetail}</span>
                                </div>

                                <p className={`text-sm mb-8 leading-relaxed ${card.highlight ? 'text-slate-400' : 'text-slate-600 dark:text-slate-400'}`}>
                                    {card.description}
                                </p>

                                <ul className="space-y-4 mb-8 flex-1">
                                    {card.features.map((feat, j) => (
                                        <li key={j} className={`flex items-start gap-3 text-sm font-medium ${card.highlight ? 'text-slate-300' : 'text-slate-600 dark:text-slate-400'}`}>
                                            {card.highlight ? (
                                                <CheckCircle2 className="w-5 h-5 text-emerald-500 flex-shrink-0 mt-0.5" />
                                            ) : (
                                                <XCircle className="w-5 h-5 text-slate-400 flex-shrink-0 mt-0.5" />
                                            )}
                                            {feat}
                                        </li>
                                    ))}
                                </ul>
                            </motion.div>
                        ))}
                    </div>
                </div>
            </section>

            {/* DETAILED COMPARISON TABLE */}
            <section id="comparison-table" className="py-24 px-6 bg-white dark:bg-slate-950">
                <div className="max-w-7xl mx-auto">
                    <div className="text-center mb-16">
                        <h2 className="text-3xl md:text-5xl font-extrabold text-slate-900 dark:text-white mb-6">Feature by Feature</h2>
                        <p className="text-xl text-slate-600 dark:text-slate-400 max-w-2xl mx-auto">
                            See exactly what you get (and what you don&apos;t) with Grid compared to managed platforms.
                        </p>
                    </div>

                    <div className="overflow-x-auto rounded-3xl border border-slate-200 dark:border-slate-800 shadow-xl bg-white dark:bg-slate-900">
                        <table className="w-full text-left border-collapse">
                            <thead>
                                <tr className="bg-slate-50 dark:bg-slate-950/50 border-b border-slate-200 dark:border-slate-800">
                                    <th className="p-6 text-sm font-bold text-slate-500 uppercase tracking-wider w-1/5">Feature</th>
                                    <th className="p-6 text-lg font-bold text-emerald-600 dark:text-emerald-400 w-1/5 bg-emerald-50/50 dark:bg-emerald-900/10 border-x border-emerald-100 dark:border-emerald-900/20">Grid</th>
                                    <th className="p-6 text-lg font-bold text-slate-700 dark:text-slate-300 w-1/5">Railway</th>
                                    <th className="p-6 text-lg font-bold text-slate-700 dark:text-slate-300 w-1/5">Vercel</th>
                                    <th className="p-6 text-lg font-bold text-slate-700 dark:text-slate-300 w-1/5">Render</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                                {comparisonRows.map((section, sIndex) => (
                                    <Fragment key={section.category}>
                                        <tr className="bg-slate-50/50 dark:bg-slate-900/50">
                                            <td colSpan={5} className="px-6 py-4 text-xs font-bold text-slate-400 uppercase tracking-widest bg-slate-100/50 dark:bg-slate-800/50">
                                                {section.category}
                                            </td>
                                        </tr>
                                        {section.items.map((row, rIndex) => (
                                            <tr key={rIndex} className="hover:bg-slate-50 dark:hover:bg-slate-800/30 transition-colors">
                                                <td className="px-6 py-5 text-sm font-semibold text-slate-900 dark:text-white border-r border-slate-100 dark:border-slate-800/50">
                                                    {row.name}
                                                </td>
                                                <td className="px-6 py-5 text-sm font-bold text-slate-900 dark:text-white bg-emerald-50/10 dark:bg-emerald-900/5 border-x border-emerald-100 dark:border-emerald-900/20">
                                                    {typeof row.cn === 'boolean' ? (
                                                        row.cn ? <Check className="w-5 h-5 text-emerald-500" /> : <X className="w-5 h-5 text-red-500" />
                                                    ) : row.cn}
                                                </td>
                                                <td className="px-6 py-5 text-sm text-slate-600 dark:text-slate-400">
                                                    {typeof row.rw === 'boolean' ? (
                                                        row.rw ? <Check className="w-5 h-5 text-emerald-500" /> : <X className="w-5 h-5 text-slate-400" />
                                                    ) : row.rw}
                                                </td>
                                                <td className="px-6 py-5 text-sm text-slate-600 dark:text-slate-400 border-r border-slate-100 dark:border-slate-800/50">
                                                    {typeof row.vc === 'boolean' ? (
                                                        row.vc ? <Check className="w-5 h-5 text-emerald-500" /> : <X className="w-5 h-5 text-slate-400" />
                                                    ) : row.vc}
                                                </td>
                                                <td className="px-6 py-5 text-sm text-slate-600 dark:text-slate-400">
                                                    {typeof row.rn === 'boolean' ? (
                                                        row.rn ? <Check className="w-5 h-5 text-emerald-500" /> : <X className="w-5 h-5 text-slate-400" />
                                                    ) : row.rn}
                                                </td>
                                            </tr>
                                        ))}
                                    </Fragment>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            </section>

            {/* MIGRATION CTA */}
            <section className="py-24 px-6 bg-slate-950 text-white relative overflow-hidden">
                <div className="absolute inset-0 bg-gradient-to-br from-emerald-900/20 to-slate-900/20" />
                <div className="max-w-4xl mx-auto text-center relative z-10">
                    <h2 className="text-3xl md:text-5xl font-extrabold mb-6">Ready to make the switch?</h2>
                    <p className="text-xl text-slate-400 mb-10 max-w-2xl mx-auto">
                        Migrating from Vercel or Railway is easier than you think. Connect your GitHub, point to your VPS, and deploy.
                    </p>
                    <div className="flex flex-col sm:flex-row gap-6 justify-center">
                        <Link href="/register">
                            <Button size="lg" className="bg-white text-slate-900 hover:bg-slate-100 font-bold px-10 h-14 rounded-xl text-lg">
                                Get Started Now
                            </Button>
                        </Link>
                        <Link href="/docs/migration">
                            <Button variant="outline" size="lg" className="border-slate-700 text-white hover:bg-slate-800 h-14 rounded-xl text-lg">
                                Read Migration Guide
                            </Button>
                        </Link>
                    </div>
                </div>
            </section>

            <Footer />
        </main>
    );
}
