'use client';

import { motion, useScroll, useTransform, useInView, AnimatePresence } from 'framer-motion';
import { useRef, useState, useEffect, type ReactNode } from 'react';
import {
    Shield, Fingerprint, Search, Key, Boxes, Brain,
    Globe, Server, Network, GitBranch, Rocket, Zap,
    Lock, Activity, Terminal, Users, RefreshCw, Database,
    Cloud, Sparkles, Cpu, BarChart3, Workflow, Command,
    Folders, Timer, Cable, Waypoints, AppWindow, Blocks,
    Container, MessageSquare, TrendingUp, ArrowRight,
    CheckCircle2, Eye, Radio, Layers, ChevronDown,
    Wifi, Gauge, CircuitBoard, ShieldCheck, ScanLine,
    Binary, BotMessageSquare, BrainCircuit
} from 'lucide-react';

// ============================================
// CHAPTER NAVIGATION
// ============================================
const chapters = [
    { id: 'push', label: 'The Push', icon: Terminal },
    { id: 'shield', label: 'The Shield', icon: Shield },
    { id: 'ecosystem', label: 'The Ecosystem', icon: Globe },
    { id: 'scale', label: 'The Scale', icon: TrendingUp },
    { id: 'intelligence', label: 'The Mind', icon: Brain },
    { id: 'beyond', label: '& Beyond', icon: Sparkles },
];

function ChapterNav() {
    const [active, setActive] = useState('push');

    useEffect(() => {
        const observer = new IntersectionObserver(
            (entries) => {
                entries.forEach((entry) => {
                    if (entry.isIntersecting) {
                        setActive(entry.target.id);
                    }
                });
            },
            { threshold: 0.3, rootMargin: '-20% 0px -60% 0px' }
        );

        chapters.forEach(({ id }) => {
            const el = document.getElementById(id);
            if (el) observer.observe(el);
        });

        return () => observer.disconnect();
    }, []);

    return (
        <div className="fixed right-6 top-1/2 -translate-y-1/2 z-50 hidden xl:flex flex-col gap-3 items-end">
            {chapters.map(({ id, label, icon: Icon }) => (
                <a
                    key={id}
                    href={`#${id}`}
                    className={`flex items-center gap-3 group transition-all duration-300 ${
                        active === id ? 'opacity-100' : 'opacity-40 hover:opacity-70'
                    }`}
                >
                    <span className={`text-xs font-bold uppercase tracking-wider transition-all duration-300 ${
                        active === id ? 'text-emerald-500 translate-x-0' : 'text-slate-400 translate-x-2 opacity-0 group-hover:opacity-100 group-hover:translate-x-0'
                    }`}>
                        {label}
                    </span>
                    <div className={`w-10 h-10 rounded-xl flex items-center justify-center transition-all duration-300 ${
                        active === id
                            ? 'bg-emerald-500 text-white shadow-lg shadow-emerald-500/30 scale-110'
                            : 'bg-slate-800/80 text-slate-400 border border-slate-700 hover:border-emerald-500/50'
                    }`}>
                        <Icon className="w-4 h-4" />
                    </div>
                </a>
            ))}
        </div>
    );
}


// ============================================
// CHAPTER HEADER (shared)
// ============================================
function ChapterHeader({ number, title, subtitle, accent }: {
    number: string; title: ReactNode; subtitle: string; accent: string;
}) {
    const ref = useRef(null);
    const inView = useInView(ref, { once: true, margin: '-100px' });

    return (
        <motion.div
            ref={ref}
            initial={{ opacity: 0, y: 40 }}
            animate={inView ? { opacity: 1, y: 0 } : {}}
            transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
            className="text-center mb-16 md:mb-24"
        >
            <div className={`inline-flex items-center gap-2 px-4 py-1.5 rounded-full text-xs font-bold uppercase tracking-[0.2em] mb-6 ${accent}`}>
                Chapter {number}
            </div>
            <h2 className="text-4xl md:text-6xl lg:text-7xl font-extrabold text-slate-900 dark:text-white tracking-tight leading-[0.95] mb-6">
                {title}
            </h2>
            <p className="text-lg md:text-xl text-slate-500 dark:text-slate-400 max-w-2xl mx-auto leading-relaxed">
                {subtitle}
            </p>
        </motion.div>
    );
}


// ============================================
// CHAPTER 1: THE PUSH — Interactive Terminal
// ============================================
const terminalLines = [
    { type: 'input', text: 'git push origin main' },
    { type: 'system', text: '→ Grid webhook received' },
    { type: 'system', text: '→ Nixpacks detected: Next.js 14 + PostgreSQL' },
    { type: 'progress', text: 'Building image...' },
    { type: 'success', text: '✔ Image built in 23s' },
    { type: 'system', text: '→ Cosign: keyless signing via Sigstore OIDC' },
    { type: 'success', text: '✔ Image signed & verified' },
    { type: 'system', text: '→ Trivy: scanning for CVEs...' },
    { type: 'success', text: '✔ 0 critical, 0 high vulnerabilities' },
    { type: 'system', text: '→ Provisioning Kata microVM sandbox' },
    { type: 'success', text: '✔ Container isolated in dedicated kernel' },
    { type: 'system', text: '→ Injecting 12 secrets from Infisical' },
    { type: 'success', text: '✔ Secrets decrypted & injected' },
    { type: 'system', text: '→ Blue-green deployment: shifting traffic' },
    { type: 'success', text: '✔ Live at https://your-app.grid.dev' },
    { type: 'done', text: 'Deployed. Zero downtime. 38 seconds total.' },
];

function TypewriterTerminal() {
    const ref = useRef(null);
    const inView = useInView(ref, { once: true, margin: '-150px' });
    const [visibleLines, setVisibleLines] = useState(0);

    useEffect(() => {
        if (!inView) return;
        let line = 0;
        const interval = setInterval(() => {
            line++;
            setVisibleLines(line);
            if (line >= terminalLines.length) clearInterval(interval);
        }, 280);
        return () => clearInterval(interval);
    }, [inView]);

    return (
        <motion.div
            ref={ref}
            initial={{ opacity: 0, y: 60 }}
            animate={inView ? { opacity: 1, y: 0 } : {}}
            transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1] }}
            className="max-w-3xl mx-auto"
        >
            <div className="bg-[#0a0e1a] rounded-2xl md:rounded-3xl overflow-hidden border border-slate-800/80 shadow-2xl shadow-emerald-500/5">
                {/* Window chrome */}
                <div className="flex items-center justify-between px-5 py-3.5 border-b border-slate-800/80 bg-slate-900/50">
                    <div className="flex gap-2">
                        <div className="w-3 h-3 rounded-full bg-red-500/70" />
                        <div className="w-3 h-3 rounded-full bg-amber-500/70" />
                        <div className="w-3 h-3 rounded-full bg-emerald-500/70" />
                    </div>
                    <div className="text-xs font-mono text-slate-500 flex items-center gap-2">
                        <Terminal className="w-3.5 h-3.5" />
                        grid-cli — deploy
                    </div>
                    <div className="w-16" />
                </div>

                {/* Terminal body */}
                <div className="p-5 md:p-8 font-mono text-sm md:text-base leading-relaxed min-h-[400px] md:min-h-[500px] overflow-hidden">
                    {terminalLines.slice(0, visibleLines).map((line, i) => (
                        <motion.div
                            key={i}
                            initial={{ opacity: 0, x: -10 }}
                            animate={{ opacity: 1, x: 0 }}
                            transition={{ duration: 0.25 }}
                            className={`mb-2 ${
                                line.type === 'input' ? 'text-white' :
                                line.type === 'success' ? 'text-emerald-400' :
                                line.type === 'done' ? 'text-emerald-300 font-bold text-base md:text-lg mt-4 pt-4 border-t border-slate-800' :
                                line.type === 'progress' ? 'text-amber-400' :
                                'text-slate-500'
                            }`}
                        >
                            {line.type === 'input' && (
                                <span className="text-emerald-500 mr-2">❯</span>
                            )}
                            {line.type === 'progress' && (
                                <span className="inline-block w-16 h-1.5 bg-slate-800 rounded-full mr-3 align-middle overflow-hidden">
                                    <motion.span
                                        className="block h-full bg-amber-400 rounded-full"
                                        animate={{ width: ['0%', '100%'] }}
                                        transition={{ duration: 1.5, ease: 'easeInOut' }}
                                    />
                                </span>
                            )}
                            {line.text}
                            {i === visibleLines - 1 && line.type !== 'done' && (
                                <span className="animate-pulse ml-1 text-emerald-400">▊</span>
                            )}
                        </motion.div>
                    ))}
                    {visibleLines >= terminalLines.length && (
                        <motion.div
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            transition={{ delay: 0.5 }}
                            className="mt-6 flex items-center gap-2 text-slate-600"
                        >
                            <span className="text-emerald-500">❯</span>
                            <span className="animate-pulse">▊</span>
                        </motion.div>
                    )}
                </div>
            </div>

            {/* Caption */}
            <motion.p
                initial={{ opacity: 0 }}
                animate={inView ? { opacity: 1 } : {}}
                transition={{ delay: 1.5 }}
                className="text-center mt-6 text-sm text-slate-400"
            >
                One command. Grid handles the entire pipeline — build, sign, scan, isolate, deploy.
            </motion.p>
        </motion.div>
    );
}


// ============================================
// CHAPTER 2: THE SHIELD — Stacked Security Layers
// ============================================
const securityLayers = [
    {
        icon: Cpu,
        title: 'Kata / gVisor Isolation',
        desc: 'Each container runs in its own kernel. Hardware-grade VM isolation — no shared kernel surface, no breakout vectors.',
        color: 'from-emerald-500 to-teal-500',
        bg: 'bg-emerald-500',
    },
    {
        icon: Fingerprint,
        title: 'Cosign Image Signing',
        desc: 'Keyless signing via Sigstore OIDC. Every image cryptographically verified before it ever touches your cluster.',
        color: 'from-cyan-500 to-blue-500',
        bg: 'bg-cyan-500',
    },
    {
        icon: Search,
        title: 'Trivy CVE Scanning',
        desc: 'Continuous vulnerability scanning at push and daily in runtime. Filesystem, container image, and IaC — all scanned.',
        color: 'from-violet-500 to-purple-500',
        bg: 'bg-violet-500',
    },
    {
        icon: Key,
        title: 'Registry RBAC + JWT',
        desc: 'Token-authenticated registry with project-scoped pull/push. Superusers get full access, teams get least-privilege.',
        color: 'from-amber-500 to-orange-500',
        bg: 'bg-amber-500',
    },
    {
        icon: Boxes,
        title: 'Infisical Secrets',
        desc: 'Encrypted secret store with versioning, RBAC, audit logs. Auto-reloads in running containers — no restarts needed.',
        color: 'from-pink-500 to-rose-500',
        bg: 'bg-pink-500',
    },
];

function SecurityLayers() {
    const containerRef = useRef(null);
    const { scrollYProgress } = useScroll({
        target: containerRef,
        offset: ['start end', 'end start'],
    });

    return (
        <div ref={containerRef} className="max-w-4xl mx-auto relative">
            {/* Vertical line connector */}
            <div className="absolute left-6 md:left-8 top-0 bottom-0 w-px bg-gradient-to-b from-transparent via-emerald-500/30 to-transparent" />

            <div className="space-y-6 md:space-y-8">
                {securityLayers.map((layer, i) => {
                    const LayerCard = () => {
                        const ref = useRef(null);
                        const inView = useInView(ref, { once: true, margin: '-80px' });

                        return (
                            <motion.div
                                ref={ref}
                                initial={{ opacity: 0, x: -40, y: 20 }}
                                animate={inView ? { opacity: 1, x: 0, y: 0 } : {}}
                                transition={{
                                    duration: 0.7,
                                    delay: i * 0.12,
                                    ease: [0.16, 1, 0.3, 1],
                                }}
                                className="relative pl-16 md:pl-20 group"
                            >
                                {/* Node on the line */}
                                <div className={`absolute left-3 md:left-5 top-6 w-7 h-7 rounded-full ${layer.bg} flex items-center justify-center shadow-lg z-10 group-hover:scale-125 transition-transform duration-300`}>
                                    <layer.icon className="w-3.5 h-3.5 text-white" />
                                </div>

                                {/* Card */}
                                <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl p-6 md:p-8 hover:border-emerald-500/30 hover:shadow-xl hover:shadow-emerald-500/5 transition-all duration-500 relative overflow-hidden">
                                    {/* Gradient accent */}
                                    <div className={`absolute top-0 left-0 w-full h-1 bg-gradient-to-r ${layer.color} opacity-0 group-hover:opacity-100 transition-opacity duration-500`} />

                                    <div className="flex items-start gap-5">
                                        <div className={`w-12 h-12 rounded-xl bg-gradient-to-br ${layer.color} flex items-center justify-center flex-shrink-0 group-hover:rotate-6 transition-transform duration-300`}>
                                            <layer.icon className="w-6 h-6 text-white" />
                                        </div>
                                        <div>
                                            <h3 className="text-xl md:text-2xl font-bold text-slate-900 dark:text-white mb-2">
                                                {layer.title}
                                            </h3>
                                            <p className="text-slate-600 dark:text-slate-400 leading-relaxed">
                                                {layer.desc}
                                            </p>
                                        </div>
                                    </div>

                                    {/* Layer number */}
                                    <div className="absolute top-4 right-4 text-6xl font-black text-slate-100 dark:text-slate-800/50 select-none">
                                        {String(i + 1).padStart(2, '0')}
                                    </div>
                                </div>
                            </motion.div>
                        );
                    };

                    return <LayerCard key={i} />;
                })}
            </div>

            {/* Summary */}
            <motion.div
                initial={{ opacity: 0, y: 20 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true }}
                transition={{ delay: 0.8 }}
                className="mt-12 ml-16 md:ml-20 p-6 bg-emerald-50 dark:bg-emerald-950/20 border border-emerald-200 dark:border-emerald-800/30 rounded-2xl"
            >
                <div className="flex items-center gap-3 mb-2">
                    <ShieldCheck className="w-5 h-5 text-emerald-600 dark:text-emerald-400" />
                    <span className="font-bold text-emerald-800 dark:text-emerald-300">5 layers deep. Zero trust by default.</span>
                </div>
                <p className="text-sm text-emerald-700 dark:text-emerald-400/80">
                    Every deployment passes through all five layers automatically. No configuration needed — security is the default, not an afterthought.
                </p>
            </motion.div>
        </div>
    );
}


// ============================================
// CHAPTER 3: THE ECOSYSTEM — Orbit Diagram
// ============================================
const orbitServices = [
    { icon: MessageSquare, label: 'SMS & WhatsApp', angle: 0, distance: 1, color: 'bg-blue-500' },
    { icon: Activity, label: 'Voice & Video', angle: 45, distance: 1, color: 'bg-violet-500' },
    { icon: Lock, label: 'Identity & Trust', angle: 90, distance: 1, color: 'bg-emerald-500' },
    { icon: Brain, label: 'AI Engine', angle: 135, distance: 1, color: 'bg-fuchsia-500' },
    { icon: Database, label: 'Managed DBs', angle: 180, distance: 1, color: 'bg-amber-500' },
    { icon: Globe, label: 'Edge Network', angle: 225, distance: 1, color: 'bg-cyan-500' },
    { icon: Server, label: 'Fleet Servers', angle: 270, distance: 1, color: 'bg-rose-500' },
    { icon: BarChart3, label: 'Billing', angle: 315, distance: 1, color: 'bg-green-500' },
];

function OrbitDiagram() {
    const ref = useRef(null);
    const inView = useInView(ref, { once: true, margin: '-100px' });
    const [hovered, setHovered] = useState<number | null>(null);

    return (
        <motion.div
            ref={ref}
            initial={{ opacity: 0, scale: 0.9 }}
            animate={inView ? { opacity: 1, scale: 1 } : {}}
            transition={{ duration: 1, ease: [0.16, 1, 0.3, 1] }}
            className="relative max-w-2xl mx-auto aspect-square"
        >
            {/* Orbital rings */}
            {[1, 1.6, 2.2].map((scale, i) => (
                <motion.div
                    key={i}
                    initial={{ opacity: 0, scale: 0.5 }}
                    animate={inView ? { opacity: 1, scale: 1 } : {}}
                    transition={{ delay: 0.3 + i * 0.15, duration: 0.8 }}
                    className="absolute inset-0 border border-slate-200 dark:border-slate-800 rounded-full"
                    style={{
                        transform: `scale(${scale / 2.2})`,
                    }}
                />
            ))}

            {/* Center — Grid */}
            <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-10">
                <motion.div
                    animate={{ scale: [1, 1.05, 1] }}
                    transition={{ duration: 4, repeat: Infinity, ease: 'easeInOut' }}
                    className="w-28 h-28 md:w-36 md:h-36 rounded-full bg-gradient-to-br from-emerald-500 to-teal-600 flex items-center justify-center shadow-2xl shadow-emerald-500/30"
                >
                    <div className="text-center">
                        <Cloud className="w-8 h-8 md:w-10 md:h-10 text-white mx-auto mb-1" />
                        <span className="text-white font-bold text-xs md:text-sm">Grid</span>
                    </div>
                </motion.div>
            </div>

            {/* Orbiting services */}
            {orbitServices.map((svc, i) => {
                const radius = svc.distance === 1 ? 38 : svc.distance === 2 ? 55 : 72;
                const angle = (svc.angle * Math.PI) / 180;
                const x = 50 + radius * Math.cos(angle);
                const y = 50 + radius * Math.sin(angle);

                return (
                    <motion.div
                        key={i}
                        initial={{ opacity: 0, scale: 0 }}
                        animate={inView ? { opacity: 1, scale: 1 } : {}}
                        transition={{
                            delay: 0.6 + i * 0.1,
                            duration: 0.5,
                            type: 'spring',
                            stiffness: 200,
                        }}
                        className="absolute group cursor-pointer"
                        style={{
                            left: `${x}%`,
                            top: `${y}%`,
                            transform: 'translate(-50%, -50%)',
                        }}
                        onMouseEnter={() => setHovered(i)}
                        onMouseLeave={() => setHovered(null)}
                    >
                        {/* Connection line */}
                        <svg className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 pointer-events-none" style={{ width: '200%', height: '200%' }}>
                            <line
                                x1="50%"
                                y1="50%"
                                x2={`${50 - (x - 50) * 2}%`}
                                y2={`${50 - (y - 50) * 2}%`}
                                stroke="currentColor"
                                strokeWidth="1"
                                className="text-slate-200 dark:text-slate-700 group-hover:text-emerald-400 transition-colors"
                                strokeDasharray="4 4"
                            />
                        </svg>

                        <div className={`relative w-14 h-14 md:w-16 md:h-16 rounded-2xl ${svc.color} flex items-center justify-center shadow-lg group-hover:scale-125 group-hover:shadow-xl transition-all duration-300`}>
                            <svc.icon className="w-6 h-6 md:w-7 md:h-7 text-white" />
                        </div>

                        {/* Label tooltip */}
                        <AnimatePresence>
                            {hovered === i && (
                                <motion.div
                                    initial={{ opacity: 0, y: 8, scale: 0.9 }}
                                    animate={{ opacity: 1, y: 0, scale: 1 }}
                                    exit={{ opacity: 0, y: 8, scale: 0.9 }}
                                    className="absolute top-full mt-2 left-1/2 -translate-x-1/2 whitespace-nowrap bg-slate-900 dark:bg-white text-white dark:text-slate-900 text-xs font-bold px-3 py-1.5 rounded-lg shadow-xl z-20"
                                >
                                    {svc.label}
                                </motion.div>
                            )}
                        </AnimatePresence>
                    </motion.div>
                );
            })}
        </motion.div>
    );
}


// ============================================
// CHAPTER 4: THE SCALE — Horizontal Timeline
// ============================================
const scaleStages = [
    {
        icon: Terminal,
        title: 'git push',
        subtitle: 'Your laptop',
        desc: 'Write code. Push to git. Grid handles everything else.',
        stat: '1 command',
        color: 'bg-slate-500',
    },
    {
        icon: Server,
        title: 'First VPS',
        subtitle: 'Single server',
        desc: 'Grid provisions containers, databases, SSL, and monitoring on your server.',
        stat: '< 45s deploy',
        color: 'bg-emerald-500',
    },
    {
        icon: Database,
        title: 'Managed Data',
        subtitle: '35+ addons',
        desc: 'PostgreSQL, Redis, MongoDB, Kafka — one-click provisioning with automated backups.',
        stat: '35+ services',
        color: 'bg-amber-500',
    },
    {
        icon: Network,
        title: 'Fleet Mesh',
        subtitle: 'Multi-server',
        desc: 'WireGuard VPN mesh connects servers. Raft leader election. Federated updates.',
        stat: 'Encrypted mesh',
        color: 'bg-blue-500',
    },
    {
        icon: Globe,
        title: 'Global Edge',
        subtitle: '24+ locations',
        desc: 'Route traffic to the nearest edge. Lite edge agents. Custom SSL everywhere.',
        stat: '24+ PoPs',
        color: 'bg-violet-500',
    },
    {
        icon: Brain,
        title: 'AI Autopilot',
        subtitle: 'Predictive scaling',
        desc: 'AI predicts load spikes, auto-remediates crashes, and optimizes resource allocation.',
        stat: '99.99% uptime',
        color: 'bg-fuchsia-500',
    },
];

function ScaleTimeline() {
    const scrollRef = useRef<HTMLDivElement>(null);
    const containerRef = useRef(null);
    const inView = useInView(containerRef, { once: true, margin: '-100px' });

    return (
        <div ref={containerRef}>
            <motion.div
                ref={scrollRef}
                initial={{ opacity: 0 }}
                animate={inView ? { opacity: 1 } : {}}
                transition={{ duration: 0.8 }}
                className="overflow-x-auto pb-8 scrollbar-hide -mx-4 px-4 md:mx-0 md:px-0"
            >
                <div className="flex gap-4 md:gap-6 min-w-max md:min-w-0 md:grid md:grid-cols-3 lg:grid-cols-6">
                    {scaleStages.map((stage, i) => (
                        <motion.div
                            key={i}
                            initial={{ opacity: 0, y: 40 }}
                            animate={inView ? { opacity: 1, y: 0 } : {}}
                            transition={{ delay: 0.2 + i * 0.1, duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
                            className="relative w-64 md:w-auto flex-shrink-0 group"
                        >
                            {/* Connector arrow (not on last) */}
                            {i < scaleStages.length - 1 && (
                                <div className="hidden lg:block absolute top-8 -right-3 z-10">
                                    <ArrowRight className="w-6 h-6 text-slate-300 dark:text-slate-700" />
                                </div>
                            )}

                            <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl p-6 hover:border-emerald-500/30 hover:shadow-xl hover:shadow-emerald-500/5 transition-all duration-500 h-full relative overflow-hidden">
                                {/* Step number */}
                                <div className="absolute top-3 right-3 text-5xl font-black text-slate-100 dark:text-slate-800/40 select-none leading-none">
                                    {i + 1}
                                </div>

                                <div className={`w-12 h-12 rounded-xl ${stage.color} flex items-center justify-center mb-4 group-hover:scale-110 transition-transform duration-300`}>
                                    <stage.icon className="w-6 h-6 text-white" />
                                </div>

                                <h3 className="text-lg font-bold text-slate-900 dark:text-white mb-1">
                                    {stage.title}
                                </h3>
                                <p className="text-xs font-semibold text-emerald-600 dark:text-emerald-400 uppercase tracking-wider mb-3">
                                    {stage.subtitle}
                                </p>
                                <p className="text-sm text-slate-600 dark:text-slate-400 leading-relaxed mb-4">
                                    {stage.desc}
                                </p>

                                <div className="inline-flex items-center gap-1.5 px-3 py-1 bg-slate-100 dark:bg-slate-800 rounded-full">
                                    <Zap className="w-3 h-3 text-emerald-500" />
                                    <span className="text-xs font-bold text-slate-700 dark:text-slate-300">{stage.stat}</span>
                                </div>
                            </div>
                        </motion.div>
                    ))}
                </div>
            </motion.div>

            {/* Mobile scroll hint */}
            <div className="flex lg:hidden items-center justify-center gap-2 mt-4 text-slate-400 text-sm">
                <span>Scroll to explore</span>
                <ArrowRight className="w-4 h-4" />
            </div>
        </div>
    );
}


// ============================================
// CHAPTER 5: THE INTELLIGENCE — AI Showcase
// ============================================
const aiCapabilities = [
    {
        icon: BrainCircuit,
        title: 'Multi-Provider AI Engine',
        desc: '17 AI providers with Senate Committee deliberation — multiple models debate and reach consensus on complex decisions.',
        tag: 'Deliberation',
    },
    {
        icon: BotMessageSquare,
        title: 'AI Auto-Remediation',
        desc: 'Intelligent log analysis diagnoses crash loops, auto-applies fixes, and re-deploys without human intervention.',
        tag: 'Self-Healing',
    },
    {
        icon: Gauge,
        title: 'Predictive Auto-Scaling',
        desc: 'AI-driven scaling that predicts load spikes before they happen. Not reactive — predictive.',
        tag: 'Forecasting',
    },
    {
        icon: Binary,
        title: 'Code Intelligence',
        desc: 'Automatic codebase scanning with deep AI analysis. Skeleton extraction, dependency graphs, deployment plan verification.',
        tag: 'Deep Analysis',
    },
];

function AIShowcase() {
    const ref = useRef(null);
    const inView = useInView(ref, { once: true, margin: '-100px' });

    return (
        <div ref={ref} className="max-w-5xl mx-auto">
            <div className="grid md:grid-cols-2 gap-6">
                {aiCapabilities.map((cap, i) => (
                    <motion.div
                        key={i}
                        initial={{ opacity: 0, y: 30, rotateX: 10 }}
                        animate={inView ? { opacity: 1, y: 0, rotateX: 0 } : {}}
                        transition={{ delay: i * 0.15, duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
                        className="group relative"
                    >
                        <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl p-8 hover:border-fuchsia-500/30 hover:shadow-xl hover:shadow-fuchsia-500/5 transition-all duration-500 h-full relative overflow-hidden">
                            {/* Animated background gradient */}
                            <div className="absolute -top-20 -right-20 w-40 h-40 bg-fuchsia-500/5 dark:bg-fuchsia-500/10 rounded-full blur-3xl group-hover:bg-fuchsia-500/15 transition-colors duration-700" />

                            <div className="relative z-10">
                                <div className="flex items-center justify-between mb-6">
                                    <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-fuchsia-500 to-purple-600 flex items-center justify-center group-hover:scale-110 group-hover:rotate-6 transition-transform duration-300">
                                        <cap.icon className="w-7 h-7 text-white" />
                                    </div>
                                    <span className="px-3 py-1 bg-fuchsia-100 dark:bg-fuchsia-900/30 text-fuchsia-700 dark:text-fuchsia-300 text-xs font-bold rounded-full uppercase tracking-wider">
                                        {cap.tag}
                                    </span>
                                </div>

                                <h3 className="text-xl md:text-2xl font-bold text-slate-900 dark:text-white mb-3">
                                    {cap.title}
                                </h3>
                                <p className="text-slate-600 dark:text-slate-400 leading-relaxed">
                                    {cap.desc}
                                </p>
                            </div>
                        </div>
                    </motion.div>
                ))}
            </div>

            {/* AI Engine highlight bar */}
            <motion.div
                initial={{ opacity: 0, y: 20 }}
                animate={inView ? { opacity: 1, y: 0 } : {}}
                transition={{ delay: 0.8, duration: 0.6 }}
                className="mt-8 bg-gradient-to-r from-fuchsia-500/10 via-purple-500/10 to-violet-500/10 dark:from-fuchsia-500/5 dark:via-purple-500/5 dark:to-violet-500/5 border border-fuchsia-200 dark:border-fuchsia-800/30 rounded-2xl p-6 flex flex-col md:flex-row items-center gap-6"
            >
                <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-fuchsia-500 to-purple-600 flex items-center justify-center flex-shrink-0">
                    <BrainCircuit className="w-8 h-8 text-white" />
                </div>
                <div>
                    <h4 className="text-lg font-bold text-slate-900 dark:text-white mb-1">Not just AI — a committee of AI</h4>
                    <p className="text-slate-600 dark:text-slate-400 text-sm">
                        Grid&apos;s AI engine doesn&apos;t rely on a single model. Multiple providers deliberate on decisions — like a Senate Committee for your infrastructure. Consensus-driven, not single-point-of-failure.
                    </p>
                </div>
            </motion.div>
        </div>
    );
}


// ============================================
// CHAPTER 6: & BEYOND — Feature Constellation
// ============================================
const constellationFeatures = [
    { icon: Container, label: 'Addon Marketplace', group: 'data' },
    { icon: Database, label: 'DB Cloning', group: 'data' },
    { icon: GitBranch, label: 'Preview Envs', group: 'devops' },
    { icon: Blocks, label: 'Multi-Git', group: 'devops' },
    { icon: Boxes, label: 'Nixpacks', group: 'devops' },
    { icon: Waypoints, label: 'Dev Tunnels', group: 'devops' },
    { icon: TrendingUp, label: 'Auto-Scaling', group: 'observe' },
    { icon: Activity, label: 'Observability', group: 'observe' },
    { icon: Cpu, label: 'H-Scale', group: 'observe' },
    { icon: BarChart3, label: 'Metrics', group: 'observe' },
    { icon: Network, label: 'VPN Mesh', group: 'fleet' },
    { icon: Server, label: 'Raft Election', group: 'fleet' },
    { icon: RefreshCw, label: 'Fleet Updates', group: 'fleet' },
    { icon: Waypoints, label: 'Migration', group: 'fleet' },
    { icon: BarChart3, label: 'Billing', group: 'business' },
    { icon: Key, label: 'Licenses', group: 'business' },
    { icon: Cloud, label: '7 Storage Backends', group: 'business' },
    { icon: Brain, label: 'Code Intel', group: 'business' },
    { icon: Folders, label: 'File Browser', group: 'util' },
    { icon: Timer, label: 'Cron Jobs', group: 'util' },
    { icon: Users, label: 'Teams & RBAC', group: 'util' },
    { icon: RefreshCw, label: 'Blue-Green', group: 'util' },
    { icon: Cable, label: 'Cloud Targets', group: 'util' },
    { icon: Sparkles, label: 'Self-Updates', group: 'util' },
    { icon: Command, label: 'Audit Log', group: 'util' },
    { icon: Workflow, label: 'Blueprints', group: 'eco' },
    { icon: AppWindow, label: 'Serverless FaaS', group: 'eco' },
    { icon: Globe, label: 'Edge Routing', group: 'edge' },
    { icon: Network, label: 'Edge Agents', group: 'edge' },
    { icon: Lock, label: 'Custom SSL', group: 'edge' },
    { icon: Shield, label: 'Safe Deploy', group: 'edge' },
    { icon: Fingerprint, label: 'Device Trust', group: 'edge' },
];

const groupColors: Record<string, string> = {
    data: 'hover:bg-amber-500/10 hover:border-amber-500/40',
    devops: 'hover:bg-blue-500/10 hover:border-blue-500/40',
    observe: 'hover:bg-violet-500/10 hover:border-violet-500/40',
    fleet: 'hover:bg-cyan-500/10 hover:border-cyan-500/40',
    business: 'hover:bg-green-500/10 hover:border-green-500/40',
    util: 'hover:bg-slate-500/10 hover:border-slate-500/40',
    eco: 'hover:bg-emerald-500/10 hover:border-emerald-500/40',
    edge: 'hover:bg-rose-500/10 hover:border-rose-500/40',
};

const groupIconColors: Record<string, string> = {
    data: 'group-hover:text-amber-500',
    devops: 'group-hover:text-blue-500',
    observe: 'group-hover:text-violet-500',
    fleet: 'group-hover:text-cyan-500',
    business: 'group-hover:text-green-500',
    util: 'group-hover:text-slate-500',
    eco: 'group-hover:text-emerald-500',
    edge: 'group-hover:text-rose-500',
};

function FeatureConstellation() {
    const ref = useRef(null);
    const inView = useInView(ref, { once: true, margin: '-80px' });

    return (
        <div ref={ref} className="max-w-6xl mx-auto">
            <motion.div
                initial={{ opacity: 0 }}
                animate={inView ? { opacity: 1 } : {}}
                transition={{ duration: 0.8 }}
                className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-3"
            >
                {constellationFeatures.map((feat, i) => (
                    <motion.div
                        key={i}
                        initial={{ opacity: 0, scale: 0.8 }}
                        animate={inView ? { opacity: 1, scale: 1 } : {}}
                        transition={{
                            delay: i * 0.03,
                            duration: 0.4,
                            type: 'spring',
                            stiffness: 300,
                        }}
                        className={`group flex flex-col items-center gap-2.5 p-4 rounded-xl bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 cursor-default transition-all duration-300 ${groupColors[feat.group] || ''}`}
                    >
                        <feat.icon className={`w-5 h-5 text-slate-400 transition-colors duration-300 ${groupIconColors[feat.group] || ''}`} />
                        <span className="text-xs font-semibold text-slate-600 dark:text-slate-400 text-center leading-tight">
                            {feat.label}
                        </span>
                    </motion.div>
                ))}
            </motion.div>
        </div>
    );
}


// ============================================
// MAIN EXPORT
// ============================================
export default function StoryTellingSection() {
    return (
        <>
            <ChapterNav />

            {/* CHAPTER 1: THE PUSH */}
            <section id="push" className="py-24 md:py-40 bg-slate-50 dark:bg-slate-950 scroll-mt-20">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <ChapterHeader
                        number="01"
                        title={<>One push.<br />Everything deploys.</>}
                        subtitle="Stop wrestling with Dockerfiles, CI pipelines, and infrastructure configs. Push your code — Grid detects your stack, builds it, secures it, and ships it."
                        accent="bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300"
                    />
                    <TypewriterTerminal />
                </div>
            </section>

            {/* CHAPTER 2: THE SHIELD */}
            <section id="shield" className="py-24 md:py-40 bg-white dark:bg-slate-950 scroll-mt-20">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <ChapterHeader
                        number="02"
                        title={<>Security isn't optional.<br />It's the foundation.</>}
                        subtitle="Five layers of defense wrap every deployment. No configuration, no checkboxes — just hardened infrastructure by default."
                        accent="bg-rose-100 dark:bg-rose-900/30 text-rose-700 dark:text-rose-300"
                    />
                    <SecurityLayers />
                </div>
            </section>

            {/* CHAPTER 3: THE ECOSYSTEM */}
            <section id="ecosystem" className="py-24 md:py-40 bg-slate-50 dark:bg-slate-950 scroll-mt-20">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <ChapterHeader
                        number="03"
                        title={<>One grid.<br />An entire ecosystem.</>}
                        subtitle="Grid isn't just a deployment tool — it's the center of a full infrastructure universe. Communication, identity, billing, AI, edge networking — all connected."
                        accent="bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300"
                    />
                    <OrbitDiagram />

                    {/* Ecosystem cards below orbit */}
                    <motion.div
                        initial={{ opacity: 0, y: 30 }}
                        whileInView={{ opacity: 1, y: 0 }}
                        viewport={{ once: true }}
                        transition={{ delay: 0.5 }}
                        className="max-w-5xl mx-auto mt-16 md:mt-24 grid md:grid-cols-3 gap-6"
                    >
                        {[
                            { icon: MessageSquare, title: 'Communication', desc: 'SMS, WhatsApp, Voice, Email — global messaging infrastructure built in.', color: 'text-blue-500', bg: 'bg-blue-500/10' },
                            { icon: Shield, title: 'Identity & Trust', desc: 'SilentOTP, verification, deepfake detection, zero-trust routing.', color: 'text-emerald-500', bg: 'bg-emerald-500/10' },
                            { icon: TrendingUp, title: 'Growth Automation', desc: 'AI-assisted marketing, publishing, leads, and analytics with Ignite.', color: 'text-amber-500', bg: 'bg-amber-500/10' },
                        ].map((pillar, i) => (
                            <motion.div
                                key={i}
                                initial={{ opacity: 0, y: 20 }}
                                whileInView={{ opacity: 1, y: 0 }}
                                viewport={{ once: true }}
                                transition={{ delay: 0.7 + i * 0.1 }}
                                className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl p-6 hover:border-emerald-500/30 transition-all"
                            >
                                <div className={`w-12 h-12 rounded-xl ${pillar.bg} ${pillar.color} flex items-center justify-center mb-4`}>
                                    <pillar.icon className="w-6 h-6" />
                                </div>
                                <h4 className="text-lg font-bold text-slate-900 dark:text-white mb-2">{pillar.title}</h4>
                                <p className="text-sm text-slate-600 dark:text-slate-400">{pillar.desc}</p>
                            </motion.div>
                        ))}
                    </motion.div>
                </div>
            </section>

            {/* CHAPTER 4: THE SCALE */}
            <section id="scale" className="py-24 md:py-40 bg-white dark:bg-slate-950 scroll-mt-20">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <ChapterHeader
                        number="04"
                        title={<>From one server<br />to a global fleet.</>}
                        subtitle="Start with a single VPS. Scale to a multi-region, AI-managed fleet with encrypted mesh networking — without changing a single line of code."
                        accent="bg-violet-100 dark:bg-violet-900/30 text-violet-700 dark:text-violet-300"
                    />
                    <ScaleTimeline />
                </div>
            </section>

            {/* CHAPTER 5: THE MIND */}
            <section id="intelligence" className="py-24 md:py-40 bg-slate-50 dark:bg-slate-950 scroll-mt-20">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <ChapterHeader
                        number="05"
                        title={<>Your infrastructure<br />thinks for itself.</>}
                        subtitle="Grid doesn't just run your code — it understands it. AI-powered diagnostics, predictive scaling, and self-healing deployments."
                        accent="bg-fuchsia-100 dark:bg-fuchsia-900/30 text-fuchsia-700 dark:text-fuchsia-300"
                    />
                    <AIShowcase />
                </div>
            </section>

            {/* CHAPTER 6: & BEYOND */}
            <section id="beyond" className="py-24 md:py-40 bg-white dark:bg-slate-950 scroll-mt-20">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <ChapterHeader
                        number="06"
                        title={<>And there's always<br />more to explore.</>}
                        subtitle="32+ features across data services, DevOps, observability, fleet management, billing, edge networking, and utilities — all included, all free."
                        accent="bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300"
                    />
                    <FeatureConstellation />
                </div>
            </section>
        </>
    );
}
