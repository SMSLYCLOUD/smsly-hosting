'use client';

import { motion, useInView, AnimatePresence } from 'framer-motion';
import { useRef, useState, useEffect } from 'react';
import type { ReactNode } from 'react';
import {
    Shield, Fingerprint, Search, Key, Boxes, Brain,
    Globe, Server, Network, GitBranch, Rocket, Zap,
    Lock, Activity, Terminal, Users, RefreshCw, Database,
    Cloud, Sparkles, Cpu, BarChart3, Workflow, Command,
    Folders, Timer, Cable, Waypoints, AppWindow, Blocks,
    Container, MessageSquare, TrendingUp, ArrowRight,
    CheckCircle2, Layers, ShieldCheck, ArrowUpRight,
    Wifi, Gauge, CircuitBoard, BotMessageSquare, BrainCircuit,
    Eye, Radio, GitMerge, HardDrive,
    ScrollText, DollarSign, Binary
} from 'lucide-react';

// ============================================
// SPACE BACKGROUND — Stars, nebula, ONE big planet per section
// ============================================
type CosmicBody =
    | 'blackhole' | 'pulsar' | 'magnetar' | 'redgiant' | 'whitedwarf'
    | 'hotjupiter' | 'binarystar' | 'supernova' | 'magcloud' | 'icemoon'
    | 'quasar' | 'neutronstar' | 'blazar' | 'androgiant' | 'protostar'
    | 'none';

const cosmicPositions: Record<string, string> = {
    blackhole:   'top-[10%] right-[5%] w-[350px] h-[350px] md:w-[500px] md:h-[500px]',
    pulsar:      'bottom-[8%] left-[5%] w-[280px] h-[280px] md:w-[400px] md:h-[400px]',
    magnetar:    'top-[15%] left-[8%] w-[300px] h-[300px] md:w-[420px] md:h-[420px]',
    redgiant:    'bottom-[12%] right-[3%] w-[340px] h-[340px] md:w-[480px] md:h-[480px]',
    whitedwarf:  'top-[20%] right-[10%] w-[200px] h-[200px] md:w-[300px] md:h-[300px]',
    hotjupiter:  'top-[5%] left-[3%] w-[320px] h-[320px] md:w-[460px] md:h-[460px]',
    binarystar:  'bottom-[5%] right-[8%] w-[300px] h-[300px] md:w-[440px] md:h-[440px]',
    supernova:   'top-[12%] right-[12%] w-[350px] h-[350px] md:w-[500px] md:h-[500px]',
    magcloud:    'bottom-[10%] left-[5%] w-[380px] h-[380px] md:w-[520px] md:h-[520px]',
    icemoon:     'top-[18%] left-[10%] w-[240px] h-[240px] md:w-[340px] md:h-[340px]',
    quasar:      'bottom-[8%] right-[5%] w-[320px] h-[320px] md:w-[460px] md:h-[460px]',
    neutronstar: 'top-[15%] left-[12%] w-[200px] h-[200px] md:w-[300px] md:h-[300px]',
    blazar:      'bottom-[10%] left-[8%] w-[300px] h-[300px] md:w-[440px] md:h-[440px]',
    androgiant:  'top-[8%] right-[3%] w-[340px] h-[340px] md:w-[480px] md:h-[480px]',
    protostar:   'top-[12%] right-[10%] w-[300px] h-[300px] md:w-[420px] md:h-[420px]',
};

function SpaceBackground({ body = 'none', variant = 'space' }: { body?: CosmicBody; variant?: 'space' | 'galaxy' }) {
    return (
        <div className="absolute inset-0 overflow-hidden pointer-events-none">
            <div className={`absolute inset-0 ${variant === 'galaxy' ? 'galaxy-bg' : 'space-bg'}`} />
            <div className="stars-layer" />
            <div className="stars-twinkle" />

            {/* Nebula patches */}
            <div className="nebula-patch w-[500px] h-[500px] bg-indigo-600 top-[5%] left-[10%] opacity-20" style={{ animationDelay: '0s' }} />
            <div className="nebula-patch w-[400px] h-[300px] bg-purple-600 top-[40%] right-[5%] opacity-15" style={{ animationDelay: '-10s' }} />
            <div className="nebula-patch w-[600px] h-[400px] bg-cyan-600 bottom-[10%] left-[30%] opacity-10" style={{ animationDelay: '-20s' }} />

            {/* Galaxy spiral arms */}
            {variant === 'galaxy' && (
                <>
                    <div className="galaxy-arm w-[800px] h-[800px] border border-indigo-500/20 top-[10%] left-[20%]" style={{ animationDelay: '0s' }} />
                    <div className="galaxy-arm w-[600px] h-[600px] border border-purple-500/15 top-[30%] left-[35%]" style={{ animationDelay: '-20s' }} />
                    <div className="dust-lane w-[70%] top-[45%] left-[15%]" />
                    <div className="dust-lane w-[50%] top-[55%] left-[25%]" style={{ animationDelay: '-8s' }} />
                </>
            )}

            {/* Single big cosmic body per section */}
            {body !== 'none' && cosmicPositions[body] && (
                <motion.div
                    className={`cosmic-body cosmic-${body} ${cosmicPositions[body]} hidden md:block`}
                    animate={{ y: [0, -12, 0] }}
                    transition={{ duration: 16, repeat: Infinity, ease: 'easeInOut' }}
                />
            )}
        </div>
    );
}


// ============================================
// CHAPTER NAVIGATION
// ============================================
const chapters = [
    { id: 'push', label: 'The Push', icon: Terminal },
    { id: 'shield', label: 'The Shield', icon: Shield },
    { id: 'scale', label: 'The Scale', icon: TrendingUp },
    { id: 'intelligence', label: 'The Mind', icon: Brain },
    { id: 'fleet', label: 'The Fleet', icon: Network },
    { id: 'observe', label: 'The Pulse', icon: Activity },
    { id: 'data', label: 'The Data', icon: Database },
    { id: 'edge', label: 'The Edge', icon: Globe },
    { id: 'vault', label: 'The Vault', icon: Key },
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
            { threshold: 0.2, rootMargin: '-20% 0px -60% 0px' }
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
// SHARED: Chapter Header
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
            <h2 className="text-4xl md:text-6xl lg:text-7xl font-extrabold text-white tracking-tight leading-[0.95] mb-6">
                {title}
            </h2>
            <p className="text-lg md:text-xl text-slate-400 max-w-2xl mx-auto leading-relaxed">
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

            <motion.p
                initial={{ opacity: 0 }}
                animate={inView ? { opacity: 1 } : {}}
                transition={{ delay: 1.5 }}
                className="text-center mt-6 text-sm text-slate-400"
            >
                One command. Grid handles the entire pipeline: build, sign, scan, isolate, deploy.
            </motion.p>
        </motion.div>
    );
}


// ============================================
// CHAPTER 2: THE SHIELD — Stacked Security Layers
// ============================================
const securityLayers = [
    { icon: Cpu, title: 'Kata / gVisor Isolation', desc: 'Each container runs in its own kernel. Hardware-grade VM isolation with no shared kernel surface and no breakout vectors.', color: 'from-emerald-500 to-teal-500', bg: 'bg-emerald-500' },
    { icon: Fingerprint, title: 'Cosign Image Signing', desc: 'Keyless signing via Sigstore OIDC. Every image cryptographically verified before it ever touches your cluster.', color: 'from-cyan-500 to-blue-500', bg: 'bg-cyan-500' },
    { icon: Search, title: 'Trivy CVE Scanning', desc: 'Continuous vulnerability scanning at push and daily in runtime. Filesystem, container image, and IaC. All scanned.', color: 'from-violet-500 to-purple-500', bg: 'bg-violet-500' },
    { icon: Key, title: 'Registry RBAC + JWT', desc: 'Token-authenticated registry with project-scoped pull/push. Superusers get full access, teams get least-privilege.', color: 'from-amber-500 to-orange-500', bg: 'bg-amber-500' },
    { icon: Boxes, title: 'Infisical Secrets', desc: 'Encrypted secret store with versioning, RBAC, audit logs. Auto-reloads in running containers. No restarts needed.', color: 'from-pink-500 to-rose-500', bg: 'bg-pink-500' },
];

function SecurityLayers() {
    return (
        <div className="max-w-4xl mx-auto relative">
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
                                transition={{ duration: 0.7, delay: i * 0.12, ease: [0.16, 1, 0.3, 1] }}
                                className="relative pl-16 md:pl-20 group"
                            >
                                <div className={`absolute left-3 md:left-5 top-6 w-7 h-7 rounded-full ${layer.bg} flex items-center justify-center shadow-lg z-10 group-hover:scale-125 transition-transform duration-300`}>
                                    <layer.icon className="w-3.5 h-3.5 text-white" />
                                </div>

                                <div className="bg-white/70 dark:bg-slate-900/70 backdrop-blur-sm border border-slate-200/50 dark:border-slate-800/50 rounded-2xl p-6 md:p-8 hover:border-emerald-500/30 hover:shadow-xl hover:shadow-emerald-500/5 transition-all duration-500 relative overflow-hidden">

                                    <div className="flex items-start gap-5">
                                        <div className={`w-12 h-12 rounded-xl bg-gradient-to-br ${layer.color} flex items-center justify-center flex-shrink-0 group-hover:rotate-6 transition-transform duration-300`}>
                                            <layer.icon className="w-6 h-6 text-white" />
                                        </div>
                                        <div>
                                            <h3 className="text-xl md:text-2xl font-bold text-slate-900 dark:text-white mb-2">{layer.title}</h3>
                                            <p className="text-slate-600 dark:text-slate-400 leading-relaxed">{layer.desc}</p>
                                        </div>
                                    </div>

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
                    Every deployment passes through all five layers automatically. No configuration needed. Security is the default, not an afterthought.
                </p>
            </motion.div>
        </div>
    );
}


// ============================================
// CHAPTER 3: THE ECOSYSTEM — Orbit + Deployment Showcase
// ============================================
const orbitServices = [
    { icon: MessageSquare, label: 'SMS & WhatsApp', angle: 0, color: 'bg-blue-500', glow: 'shadow-blue-500/40' },
    { icon: Activity, label: 'Voice & Video', angle: 45, color: 'bg-violet-500', glow: 'shadow-violet-500/40' },
    { icon: Lock, label: 'Identity & Trust', angle: 90, color: 'bg-emerald-500', glow: 'shadow-emerald-500/40' },
    { icon: Brain, label: 'AI Engine', angle: 135, color: 'bg-indigo-500', glow: 'shadow-indigo-500/40' },
    { icon: Database, label: 'Managed DBs', angle: 180, color: 'bg-amber-500', glow: 'shadow-amber-500/40' },
    { icon: Globe, label: 'Edge Network', angle: 225, color: 'bg-cyan-500', glow: 'shadow-cyan-500/40' },
    { icon: Server, label: 'Fleet Servers', angle: 270, color: 'bg-rose-500', glow: 'shadow-rose-500/40' },
    { icon: BarChart3, label: 'Billing', angle: 315, color: 'bg-green-500', glow: 'shadow-green-500/40' },
];

function OrbitDiagram() {
    const ref = useRef(null);
    const inView = useInView(ref, { once: true, margin: '-100px' });
    const [hovered, setHovered] = useState<number | null>(null);

    const radius = 38;
    const nodePositions = orbitServices.map((svc) => {
        const angle = (svc.angle * Math.PI) / 180;
        return {
            x: 50 + radius * Math.cos(angle),
            y: 50 + radius * Math.sin(angle),
        };
    });

    return (
        <motion.div
            ref={ref}
            initial={{ opacity: 0, scale: 0.9 }}
            animate={inView ? { opacity: 1, scale: 1 } : {}}
            transition={{ duration: 1, ease: [0.16, 1, 0.3, 1] }}
            className="relative max-w-3xl mx-auto aspect-square mb-8"
        >
            {/* Animated SVG connection lines */}
            <svg className="absolute inset-0 w-full h-full pointer-events-none z-0" viewBox="0 0 100 100">
                {nodePositions.map((pos, i) => (
                    <motion.line
                        key={i}
                        x1="50"
                        y1="50"
                        x2={pos.x}
                        y2={pos.y}
                        stroke="url(#lineGrad)"
                        strokeWidth="0.3"
                        strokeDasharray="2 2"
                        initial={{ pathLength: 0, opacity: 0 }}
                        animate={inView ? { pathLength: 1, opacity: 1 } : {}}
                        transition={{ delay: 0.8 + i * 0.08, duration: 0.8, ease: 'easeOut' }}
                    />
                ))}
                {/* Orbit ring connections */}
                {nodePositions.map((pos, i) => {
                    const next = nodePositions[(i + 1) % nodePositions.length];
                    return (
                        <motion.line
                            key={`ring-${i}`}
                            x1={pos.x}
                            y1={pos.y}
                            x2={next.x}
                            y2={next.y}
                            stroke="url(#ringGrad)"
                            strokeWidth="0.15"
                            initial={{ pathLength: 0, opacity: 0 }}
                            animate={inView ? { pathLength: 1, opacity: 0.5 } : {}}
                            transition={{ delay: 1.2 + i * 0.06, duration: 0.6 }}
                        />
                    );
                })}
                <defs>
                    <linearGradient id="lineGrad" x1="0%" y1="0%" x2="100%" y2="0%">
                        <stop offset="0%" stopColor="#10b981" stopOpacity="0.6" />
                        <stop offset="100%" stopColor="#06b6d4" stopOpacity="0.3" />
                    </linearGradient>
                    <linearGradient id="ringGrad" x1="0%" y1="0%" x2="100%" y2="0%">
                        <stop offset="0%" stopColor="#64748b" stopOpacity="0.4" />
                        <stop offset="100%" stopColor="#64748b" stopOpacity="0.1" />
                    </linearGradient>
                </defs>
            </svg>

            {/* Orbital rings */}
            {[1, 1.6, 2.2].map((scale, i) => (
                <motion.div
                    key={i}
                    initial={{ opacity: 0, scale: 0.5 }}
                    animate={inView ? { opacity: 1, scale: 1 } : {}}
                    transition={{ delay: 0.3 + i * 0.15, duration: 0.8 }}
                    className="absolute inset-0 border border-slate-200 dark:border-slate-800/60 rounded-full"
                    style={{ transform: `scale(${scale / 2.2})` }}
                />
            ))}

            {/* Center — Grid */}
            <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-10">
                <motion.div
                    animate={{ scale: [1, 1.06, 1] }}
                    transition={{ duration: 4, repeat: Infinity, ease: 'easeInOut' }}
                    className="w-32 h-32 md:w-40 md:h-40 rounded-full bg-gradient-to-br from-emerald-500 to-teal-600 flex items-center justify-center shadow-2xl shadow-emerald-500/40 relative"
                >
                    {/* Pulse ring */}
                    <div className="absolute inset-0 rounded-full bg-emerald-400/20 animate-ping" style={{ animationDuration: '3s' }} />
                    <div className="text-center relative z-10">
                        <Cloud className="w-10 h-10 md:w-12 md:h-12 text-white mx-auto mb-1" />
                        <span className="text-white font-bold text-sm md:text-base">Grid</span>
                    </div>
                </motion.div>
            </div>

            {/* Orbiting nodes */}
            {orbitServices.map((svc, i) => {
                const pos = nodePositions[i];
                return (
                    <motion.div
                        key={i}
                        initial={{ opacity: 0, scale: 0 }}
                        animate={inView ? { opacity: 1, scale: 1 } : {}}
                        transition={{ delay: 0.6 + i * 0.1, duration: 0.5, type: 'spring', stiffness: 200 }}
                        className="absolute group cursor-pointer z-10"
                        style={{ left: `${pos.x}%`, top: `${pos.y}%`, transform: 'translate(-50%, -50%)' }}
                        onMouseEnter={() => setHovered(i)}
                        onMouseLeave={() => setHovered(null)}
                    >
                        <div className={`relative w-16 h-16 md:w-20 md:h-20 rounded-2xl ${svc.color} flex items-center justify-center shadow-lg ${svc.glow} group-hover:scale-125 group-hover:shadow-2xl transition-all duration-300`}>
                            <svc.icon className="w-7 h-7 md:w-8 md:h-8 text-white" />
                        </div>

                        <AnimatePresence>
                            {hovered === i && (
                                <motion.div
                                    initial={{ opacity: 0, y: 8, scale: 0.9 }}
                                    animate={{ opacity: 1, y: 0, scale: 1 }}
                                    exit={{ opacity: 0, y: 8, scale: 0.9 }}
                                    className="absolute top-full mt-3 left-1/2 -translate-x-1/2 whitespace-nowrap bg-slate-900 dark:bg-white text-white dark:text-slate-900 text-sm font-bold px-4 py-2 rounded-xl shadow-xl z-20"
                                >
                                    {svc.label}
                                    <div className="absolute -top-1 left-1/2 -translate-x-1/2 w-2 h-2 bg-slate-900 dark:bg-white rotate-45" />
                                </motion.div>
                            )}
                        </AnimatePresence>
                    </motion.div>
                );
            })}
        </motion.div>
    );
}

/* Deployment Features — accurate architecture visual */
const deploymentCapabilities = [
    {
        icon: Workflow,
        title: 'Blueprints & Clusters',
        desc: 'JSON-based deployment templates. Deploy addons first, then services in dependency order. Env var placeholders auto-resolve from provisioned addons.',
        stat: 'One-click deploy',
        gradient: 'from-emerald-500 to-teal-500',
    },
    {
        icon: AppWindow,
        title: 'Serverless FaaS',
        desc: 'Submit raw Node.js or Python code. Provisioner generates HTTP wrapper with SSRF guard, Dockerfile, and deploys as a standard container.',
        stat: 'Node.js / Python',
        gradient: 'from-blue-500 to-cyan-500',
    },
    {
        icon: BrainCircuit,
        title: 'AI Ecosystem Scanner',
        desc: 'Point at your GitHub org. AI scans repos, detects stacks, maps dependencies, provisions addons, and generates a full EcosystemPlan automatically.',
        stat: 'AI-powered',
        gradient: 'from-indigo-500 to-violet-500',
    },
    {
        icon: Server,
        title: 'Managed Fleet Servers',
        desc: 'SSH provisioning, hardware fingerprint attestation, auto-registration. Node selector load-balances by services count across the fleet.',
        stat: 'Auto-provision',
        gradient: 'from-cyan-500 to-blue-500',
    },
];

/* The real ecosystem deployment pipeline */
const pipelineStages = [
    { label: 'Scan', detail: 'AI analyzes repos', icon: Search, color: 'bg-indigo-500' },
    { label: 'Plan', detail: 'EcosystemPlan generated', icon: Layers, color: 'bg-violet-500' },
    { label: 'Provision', detail: 'Addons created', icon: Database, color: 'bg-amber-500' },
    { label: 'Build', detail: 'Wave-based builds', icon: Cpu, color: 'bg-blue-500' },
    { label: 'Deploy', detail: 'Dependency order', icon: Rocket, color: 'bg-emerald-500' },
];

/* Addon categories for the visual */
const addonCategories = [
    { label: 'PostgreSQL', category: 'Relational', color: 'bg-blue-500' },
    { label: 'MongoDB', category: 'Document', color: 'bg-green-500' },
    { label: 'Redis', category: 'Key-Value', color: 'bg-red-500' },
    { label: 'Kafka', category: 'Streaming', color: 'bg-slate-400' },
    { label: 'Qdrant', category: 'Vector DB', color: 'bg-purple-500' },
    { label: 'Elasticsearch', category: 'Search', color: 'bg-yellow-500' },
    { label: 'Neo4j', category: 'Graph', color: 'bg-cyan-500' },
    { label: 'MinIO', category: 'Storage', color: 'bg-rose-500' },
];

function EcosystemArchitectureVisual() {
    const ref = useRef(null);
    const inView = useInView(ref, { once: true, margin: '-60px' });
    const [activeStage, setActiveStage] = useState(0);

    useEffect(() => {
        if (!inView) return;
        let stage = 0;
        const interval = setInterval(() => {
            stage++;
            setActiveStage(stage);
            if (stage >= pipelineStages.length - 1) clearInterval(interval);
        }, 800);
        return () => clearInterval(interval);
    }, [inView]);

    return (
        <div ref={ref} className="relative">
            {/* Dark showcase container */}
            <div className="rounded-3xl bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 p-6 md:p-10 lg:p-14 overflow-hidden relative border border-slate-700/50">
                {/* Ambient glow */}
                <div className="absolute -top-32 -right-32 w-96 h-96 bg-emerald-500/8 blur-[120px] rounded-full pointer-events-none" />
                <div className="absolute -bottom-32 -left-32 w-96 h-96 bg-blue-500/8 blur-[120px] rounded-full pointer-events-none" />

                <div className="relative z-10">
                    {/* Header */}
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={inView ? { opacity: 1, y: 0 } : {}}
                        transition={{ duration: 0.6 }}
                        className="text-center mb-10 md:mb-14"
                    >
                        <span className="inline-flex items-center gap-2 px-4 py-1.5 bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-xs font-bold uppercase tracking-[0.2em] rounded-full mb-4">
                            <Sparkles className="w-3.5 h-3.5" />
                            Ecosystem Deployment
                        </span>
                        <h3 className="text-3xl md:text-4xl font-extrabold text-white">
                            Deploy more than apps.<br />
                            <span className="text-transparent bg-clip-text bg-gradient-to-r from-emerald-400 to-cyan-400">Deploy entire ecosystems.</span>
                        </h3>
                        <p className="text-slate-400 mt-4 max-w-xl mx-auto">
                            Point Grid at your GitHub org. AI scans your repos, maps dependencies, provisions databases, and deploys everything in the right order. In waves.
                        </p>
                    </motion.div>

                    {/* === PIPELINE FLOW === */}
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={inView ? { opacity: 1, y: 0 } : {}}
                        transition={{ delay: 0.2, duration: 0.6 }}
                        className="mb-10"
                    >
                        <div className="flex items-center justify-between gap-2 md:gap-3 overflow-x-auto pb-2">
                            {pipelineStages.map((stage, i) => (
                                <div key={i} className="flex items-center gap-2 md:gap-3 flex-shrink-0">
                                    <div className={`flex items-center gap-2 px-3 md:px-4 py-2 md:py-2.5 rounded-xl border transition-all duration-500 ${
                                        i <= activeStage
                                            ? 'bg-slate-800 border-emerald-500/40 shadow-lg shadow-emerald-500/10'
                                            : 'bg-slate-900 border-slate-700/50 opacity-40'
                                    }`}>
                                        <div className={`w-7 h-7 md:w-8 md:h-8 rounded-lg ${stage.color} flex items-center justify-center`}>
                                            <stage.icon className="w-4 h-4 text-white" />
                                        </div>
                                        <div className="hidden sm:block">
                                            <div className="text-xs font-bold text-white">{stage.label}</div>
                                            <div className="text-[10px] text-slate-400">{stage.detail}</div>
                                        </div>
                                    </div>
                                    {i < pipelineStages.length - 1 && (
                                        <div className={`w-6 md:w-10 h-px transition-colors duration-500 ${
                                            i < activeStage ? 'bg-emerald-500/60' : 'bg-slate-700'
                                        }`} />
                                    )}
                                </div>
                            ))}
                        </div>
                    </motion.div>

                    {/* === MAIN VISUAL: Dependency Graph + Addon Provisioning === */}
                    <div className="grid lg:grid-cols-5 gap-6 md:gap-8">

                        {/* LEFT: Source repos */}
                        <motion.div
                            initial={{ opacity: 0, x: -30 }}
                            animate={inView ? { opacity: 1, x: 0 } : {}}
                            transition={{ delay: 0.3, duration: 0.6 }}
                            className="lg:col-span-2"
                        >
                            <div className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-3 flex items-center gap-2">
                                <GitBranch className="w-3.5 h-3.5" />
                                Source Repos
                            </div>

                            <div className="space-y-2">
                                {[
                                    { name: 'web-frontend', stack: 'Next.js 14', deps: ['api-server', 'postgres'], color: 'border-blue-500/30' },
                                    { name: 'api-server', stack: 'Django + DRF', deps: ['postgres', 'redis', 'kafka'], color: 'border-emerald-500/30' },
                                    { name: 'worker-ml', stack: 'Python 3.12', deps: ['postgres', 'qdrant', 'redis'], color: 'border-violet-500/30' },
                                    { name: 'edge-proxy', stack: 'Go 1.22', deps: ['redis'], color: 'border-amber-500/30' },
                                    { name: 'cron-scheduler', stack: 'Node.js 20', deps: ['postgres', 'redis'], color: 'border-cyan-500/30' },
                                ].map((repo, i) => (
                                    <motion.div
                                        key={i}
                                        initial={{ opacity: 0, x: -20 }}
                                        animate={inView ? { opacity: 1, x: 0 } : {}}
                                        transition={{ delay: 0.5 + i * 0.1, duration: 0.4 }}
                                        className={`bg-slate-800/60 border ${repo.color} rounded-xl p-3 md:p-4 group hover:bg-slate-800/80 transition-all`}
                                    >
                                        <div className="flex items-center justify-between mb-1.5">
                                            <span className="font-mono text-sm font-bold text-white">{repo.name}</span>
                                            <span className="text-[10px] px-2 py-0.5 bg-slate-700 text-slate-300 rounded-full">{repo.stack}</span>
                                        </div>
                                        <div className="flex items-center gap-1.5 flex-wrap">
                                            <span className="text-[10px] text-slate-500">depends on:</span>
                                            {repo.deps.map((dep, j) => (
                                                <span key={j} className="text-[10px] font-mono text-emerald-400/80 px-1.5 py-0.5 bg-emerald-500/10 rounded">
                                                    {dep}
                                                </span>
                                            ))}
                                        </div>
                                    </motion.div>
                                ))}
                            </div>

                            {/* AI analysis badge */}
                            <motion.div
                                initial={{ opacity: 0 }}
                                animate={inView ? { opacity: 1 } : {}}
                                transition={{ delay: 1.2 }}
                                className="mt-4 flex items-center gap-2 px-3 py-2 bg-indigo-500/10 border border-indigo-500/20 rounded-lg"
                            >
                                <BrainCircuit className="w-4 h-4 text-indigo-400" />
                                <span className="text-xs text-indigo-300 font-medium">AI detected 5 services, 6 addon dependencies, optimal deploy order</span>
                            </motion.div>
                        </motion.div>

                        {/* CENTER: Arrow + Wave indicator */}
                        <motion.div
                            initial={{ opacity: 0, scale: 0.8 }}
                            animate={inView ? { opacity: 1, scale: 1 } : {}}
                            transition={{ delay: 0.6, duration: 0.5 }}
                            className="hidden lg:flex flex-col items-center justify-center gap-4"
                        >
                            <div className="w-px h-16 bg-gradient-to-b from-transparent via-emerald-500/40 to-transparent" />
                            <div className="w-10 h-10 rounded-full bg-emerald-500/20 border border-emerald-500/40 flex items-center justify-center">
                                <ArrowRight className="w-5 h-5 text-emerald-400" />
                            </div>
                            <div className="text-[10px] text-slate-500 font-bold uppercase tracking-wider text-center">
                                Wave<br />Deploy
                            </div>
                            <div className="w-10 h-10 rounded-full bg-emerald-500/20 border border-emerald-500/40 flex items-center justify-center">
                                <ArrowRight className="w-5 h-5 text-emerald-400" />
                            </div>
                            <div className="w-px h-16 bg-gradient-to-b from-transparent via-emerald-500/40 to-transparent" />
                        </motion.div>

                        {/* RIGHT: Addons + Deployed services */}
                        <motion.div
                            initial={{ opacity: 0, x: 30 }}
                            animate={inView ? { opacity: 1, x: 0 } : {}}
                            transition={{ delay: 0.4, duration: 0.6 }}
                            className="lg:col-span-2"
                        >
                            {/* Addons provisioned */}
                            <div className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-3 flex items-center gap-2">
                                <Database className="w-3.5 h-3.5" />
                                Auto-Provisioned Addons
                            </div>

                            <div className="grid grid-cols-2 gap-2 mb-6">
                                {addonCategories.map((addon, i) => (
                                    <motion.div
                                        key={i}
                                        initial={{ opacity: 0, scale: 0.9 }}
                                        animate={inView ? { opacity: 1, scale: 1 } : {}}
                                        transition={{ delay: 0.7 + i * 0.06, duration: 0.3 }}
                                        className="flex items-center gap-2 px-3 py-2 bg-slate-800/60 border border-slate-700/40 rounded-lg hover:border-slate-600/60 transition-colors"
                                    >
                                        <div className={`w-2 h-2 rounded-full ${addon.color}`} />
                                        <div>
                                            <div className="text-xs font-bold text-white">{addon.label}</div>
                                            <div className="text-[10px] text-slate-500">{addon.category}</div>
                                        </div>
                                    </motion.div>
                                ))}
                            </div>

                            {/* Deployed services */}
                            <div className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-3 flex items-center gap-2">
                                <Rocket className="w-3.5 h-3.5" />
                                Deployed Services
                            </div>

                            <div className="space-y-2">
                                {[
                                    { name: 'web-frontend', status: 'ACTIVE', port: '3000', wave: 2 },
                                    { name: 'api-server', status: 'ACTIVE', port: '8000', wave: 1 },
                                    { name: 'worker-ml', status: 'ACTIVE', port: '8080', wave: 1 },
                                    { name: 'edge-proxy', status: 'ACTIVE', port: '4000', wave: 2 },
                                    { name: 'cron-scheduler', status: 'ACTIVE', port: '5000', wave: 2 },
                                ].map((svc, i) => (
                                    <motion.div
                                        key={i}
                                        initial={{ opacity: 0, x: 20 }}
                                        animate={inView ? { opacity: 1, x: 0 } : {}}
                                        transition={{ delay: 1.0 + i * 0.08, duration: 0.4 }}
                                        className="flex items-center justify-between px-3 py-2 bg-slate-800/60 border border-emerald-500/20 rounded-lg"
                                    >
                                        <div className="flex items-center gap-2">
                                            <div className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
                                            <span className="font-mono text-xs font-bold text-white">{svc.name}</span>
                                        </div>
                                        <div className="flex items-center gap-3">
                                            <span className="text-[10px] text-slate-500">:{svc.port}</span>
                                            <span className="text-[10px] px-1.5 py-0.5 bg-emerald-500/20 text-emerald-400 rounded-full font-bold">
                                                {svc.status}
                                            </span>
                                        </div>
                                    </motion.div>
                                ))}
                            </div>

                            {/* Env resolution note */}
                            <motion.div
                                initial={{ opacity: 0 }}
                                animate={inView ? { opacity: 1 } : {}}
                                transition={{ delay: 1.5 }}
                                className="mt-4 flex items-center gap-2 px-3 py-2 bg-amber-500/10 border border-amber-500/20 rounded-lg"
                            >
                                <Key className="w-4 h-4 text-amber-400" />
                                <span className="text-xs text-amber-300 font-medium">Cross-service env vars auto-resolved: DATABASE_URL, REDIS_URL, KAFKA_BROKERS injected</span>
                            </motion.div>
                        </motion.div>
                    </div>

                    {/* === CAPABILITY CARDS === */}
                    <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4 mt-10 md:mt-14">
                        {deploymentCapabilities.map((feat, i) => (
                            <motion.div
                                key={i}
                                initial={{ opacity: 0, y: 20 }}
                                animate={inView ? { opacity: 1, y: 0 } : {}}
                                transition={{ delay: 0.8 + i * 0.1, duration: 0.5 }}
                                className="group bg-slate-800/40 backdrop-blur-sm border border-slate-700/50 rounded-2xl p-5 hover:border-slate-600/80 transition-all duration-500 relative overflow-hidden"
                            >
                                <div className={`absolute -top-10 -right-10 w-24 h-24 bg-gradient-to-bl ${feat.gradient} opacity-[0.07] group-hover:opacity-[0.15] rounded-full blur-2xl transition-opacity duration-500 pointer-events-none`} />
                                <div className="relative z-10">
                                    <div className={`w-10 h-10 rounded-lg bg-gradient-to-br ${feat.gradient} flex items-center justify-center mb-3 group-hover:scale-110 transition-transform duration-300`}>
                                        <feat.icon className="w-5 h-5 text-white" />
                                    </div>
                                    <h4 className="text-sm font-bold text-white mb-1.5">{feat.title}</h4>
                                    <p className="text-xs text-slate-400 leading-relaxed mb-3">{feat.desc}</p>
                                    <div className="inline-flex items-center gap-1.5 px-2.5 py-1 bg-slate-700/50 rounded-full">
                                        <Zap className="w-3 h-3 text-emerald-400" />
                                        <span className="text-[10px] font-bold text-slate-300">{feat.stat}</span>
                                    </div>
                                </div>
                            </motion.div>
                        ))}
                    </div>
                </div>
            </div>
        </div>
    );
}


// ============================================
// CHAPTER 4: THE SCALE — Horizontal Timeline
// ============================================
const scaleStages = [
    { icon: Terminal, title: 'git push', subtitle: 'Your laptop', desc: 'Write code. Push to git. Grid handles everything else.', stat: '1 command', color: 'bg-slate-500' },
    { icon: Server, title: 'First VPS', subtitle: 'Single server', desc: 'Grid provisions containers, databases, SSL, and monitoring on your server.', stat: '< 45s deploy', color: 'bg-emerald-500' },
    { icon: Database, title: 'Managed Data', subtitle: '35+ addons', desc: 'PostgreSQL, Redis, MongoDB, Kafka. One-click provisioning with automated backups.', stat: '35+ services', color: 'bg-amber-500' },
    { icon: RefreshCw, title: 'HA Replication', subtitle: 'Streaming replication', desc: 'PostgreSQL streaming replicas via Patroni. Redis Sentinel auto-failover. Sub-second recovery.', stat: '< 30s failover', color: 'bg-blue-500' },
    { icon: Network, title: 'Multi-Region', subtitle: 'Distributed', desc: 'Deploy across regions with WireGuard mesh networking. Data locality and latency-aware placement.', stat: '24+ regions', color: 'bg-violet-500' },
    { icon: Brain, title: 'AI Autopilot', subtitle: 'Predictive scaling', desc: 'AI predicts load spikes, auto-remediates crashes, and optimizes resource allocation.', stat: '99.99% uptime', color: 'bg-indigo-500' },
];

function ScaleTimeline() {
    const containerRef = useRef(null);
    const inView = useInView(containerRef, { once: true, margin: '-100px' });

    return (
        <div ref={containerRef}>
            <motion.div
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
                            {i < scaleStages.length - 1 && (
                                <div className="hidden lg:block absolute top-8 -right-3 z-10">
                                    <ArrowRight className="w-6 h-6 text-slate-300 dark:text-slate-700" />
                                </div>
                            )}

                            <div className="bg-white/70 dark:bg-slate-900/70 backdrop-blur-sm border border-slate-200/50 dark:border-slate-800/50 rounded-2xl p-6 hover:border-emerald-500/30 hover:shadow-xl hover:shadow-emerald-500/5 transition-all duration-500 h-full relative overflow-hidden">
                                <div className="absolute top-3 right-3 text-5xl font-black text-slate-100 dark:text-slate-800/40 select-none leading-none">
                                    {i + 1}
                                </div>

                                <div className={`w-12 h-12 rounded-xl ${stage.color} flex items-center justify-center mb-4 group-hover:scale-110 transition-transform duration-300`}>
                                    <stage.icon className="w-6 h-6 text-white" />
                                </div>

                                <h3 className="text-lg font-bold text-slate-900 dark:text-white mb-1">{stage.title}</h3>
                                <p className="text-xs font-semibold text-emerald-600 dark:text-emerald-400 uppercase tracking-wider mb-3">{stage.subtitle}</p>
                                <p className="text-sm text-slate-600 dark:text-slate-400 leading-relaxed mb-4">{stage.desc}</p>

                                <div className="inline-flex items-center gap-1.5 px-3 py-1 bg-slate-100 dark:bg-slate-800 rounded-full">
                                    <Zap className="w-3 h-3 text-emerald-500" />
                                    <span className="text-xs font-bold text-slate-700 dark:text-slate-300">{stage.stat}</span>
                                </div>
                            </div>
                        </motion.div>
                    ))}
                </div>
            </motion.div>

            <div className="flex lg:hidden items-center justify-center gap-2 mt-4 text-slate-400 text-sm">
                <span>Scroll to explore</span>
                <ArrowRight className="w-4 h-4" />
            </div>
        </div>
    );
}


// ============================================
// CHAPTER 5: THE MIND — AI Showcase (clean blue/indigo)
// ============================================
const aiCapabilities = [
    { icon: BrainCircuit, title: 'Multi-Provider AI Engine', desc: '17 AI providers with Senate Committee deliberation. Multiple models debate and reach consensus on complex decisions.', tag: 'Deliberation' },
    { icon: BotMessageSquare, title: 'AI Auto-Remediation', desc: 'Intelligent log analysis diagnoses crash loops, auto-applies fixes, and re-deploys without human intervention.', tag: 'Self-Healing' },
    { icon: Gauge, title: 'Predictive Auto-Scaling', desc: 'AI-driven scaling that predicts load spikes before they happen. Not reactive. Predictive.', tag: 'Forecasting' },
    { icon: Binary, title: 'Code Intelligence', desc: 'Automatic codebase scanning with deep AI analysis. Skeleton extraction, dependency graphs, deployment plan verification.', tag: 'Deep Analysis' },
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
                        initial={{ opacity: 0, y: 30 }}
                        animate={inView ? { opacity: 1, y: 0 } : {}}
                        transition={{ delay: i * 0.15, duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
                        className="group"
                    >
                        <div className="bg-white/70 dark:bg-slate-900/70 backdrop-blur-sm border border-slate-200/50 dark:border-slate-800/50 rounded-2xl p-8 hover:border-indigo-500/30 hover:shadow-xl hover:shadow-indigo-500/5 transition-all duration-500 h-full relative overflow-hidden">
                            <div className="absolute -top-20 -right-20 w-40 h-40 bg-indigo-500/5 dark:bg-indigo-500/10 rounded-full blur-3xl group-hover:bg-indigo-500/15 transition-colors duration-700" />

                            <div className="relative z-10">
                                <div className="flex items-center justify-between mb-6">
                                    <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-indigo-500 to-blue-600 flex items-center justify-center group-hover:scale-110 group-hover:rotate-6 transition-transform duration-300">
                                        <cap.icon className="w-7 h-7 text-white" />
                                    </div>
                                    <span className="px-3 py-1 bg-indigo-100 dark:bg-indigo-900/30 text-indigo-700 dark:text-indigo-300 text-xs font-bold rounded-full uppercase tracking-wider">
                                        {cap.tag}
                                    </span>
                                </div>

                                <h3 className="text-xl md:text-2xl font-bold text-slate-900 dark:text-white mb-3">{cap.title}</h3>
                                <p className="text-slate-600 dark:text-slate-400 leading-relaxed">{cap.desc}</p>
                            </div>
                        </div>
                    </motion.div>
                ))}
            </div>

            <motion.div
                initial={{ opacity: 0, y: 20 }}
                animate={inView ? { opacity: 1, y: 0 } : {}}
                transition={{ delay: 0.8, duration: 0.6 }}
                className="mt-8 bg-gradient-to-r from-indigo-500/10 via-blue-500/10 to-cyan-500/10 dark:from-indigo-500/5 dark:via-blue-500/5 dark:to-cyan-500/5 border border-indigo-200 dark:border-indigo-800/30 rounded-2xl p-6 flex flex-col md:flex-row items-center gap-6"
            >
                <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-indigo-500 to-blue-600 flex items-center justify-center flex-shrink-0">
                    <BrainCircuit className="w-8 h-8 text-white" />
                </div>
                <div>
                    <h4 className="text-lg font-bold text-slate-900 dark:text-white mb-1">Not just AI. A committee of AI.</h4>
                    <p className="text-slate-600 dark:text-slate-400 text-sm">
                        Grid&apos;s AI engine doesn&apos;t rely on a single model. Multiple providers deliberate on decisions, like a Senate Committee for your infrastructure. Consensus-driven, not single-point-of-failure.
                    </p>
                </div>
            </motion.div>
        </div>
    );
}


// ============================================
// SERVICE TOPOLOGY — Dependency graph visualization
// ============================================
function TopologyVisualization() {
    const ref = useRef(null);
    const inView = useInView(ref, { once: true, margin: '-80px' });

    const services = [
        { id: 'api', x: 300, y: 80, label: 'API Gateway', color: '#22d3ee', type: 'service' },
        { id: 'web', x: 140, y: 80, label: 'Web Frontend', color: '#60a5fa', type: 'service' },
        { id: 'worker', x: 460, y: 80, label: 'Worker', color: '#a78bfa', type: 'service' },
        { id: 'cron', x: 460, y: 200, label: 'Cron Jobs', color: '#fbbf24', type: 'service' },
    ];

    const addons = [
        { id: 'pg', x: 200, y: 240, label: 'PostgreSQL', color: '#34d399', type: 'addon' },
        { id: 'redis', x: 340, y: 240, label: 'Redis', color: '#f87171', type: 'addon' },
        { id: 's3', x: 480, y: 280, label: 'S3 Storage', color: '#fb923c', type: 'addon' },
    ];

    const edges = [
        { from: 'web', to: 'api', label: 'HTTP' },
        { from: 'api', to: 'pg', label: 'DATABASE_URL' },
        { from: 'api', to: 'redis', label: 'REDIS_URL' },
        { from: 'worker', to: 'pg', label: 'DATABASE_URL' },
        { from: 'worker', to: 'redis', label: 'CELERY_BROKER' },
        { from: 'cron', to: 'api', label: 'INTERNAL_URL' },
        { from: 'worker', to: 's3', label: 'S3_BUCKET' },
    ];

    const getNode = (id: string) => [...services, ...addons].find(n => n.id === id)!;

    return (
        <div ref={ref} className="max-w-6xl mx-auto space-y-12">
            {/* Main dependency graph */}
            <motion.div
                initial={{ opacity: 0, y: 30 }}
                animate={inView ? { opacity: 1, y: 0 } : {}}
                transition={{ duration: 0.7 }}
                className="bg-slate-900/40 backdrop-blur-sm border border-slate-700/30 rounded-2xl p-6 md:p-8"
            >
                <div className="flex items-center gap-3 mb-6">
                    <div className="w-10 h-10 rounded-lg bg-cyan-500/20 flex items-center justify-center">
                        <Waypoints className="w-5 h-5 text-cyan-400" />
                    </div>
                    <div>
                        <h3 className="text-lg font-bold text-white">Service Dependency Graph</h3>
                        <p className="text-sm text-slate-400">Auto-discovered from environment variables. Grid maps how your services talk to each other.</p>
                    </div>
                </div>

                <svg viewBox="0 0 600 340" fill="none" className="w-full">
                    {/* Connection lines */}
                    {edges.map((edge, i) => {
                        const from = getNode(edge.from);
                        const to = getNode(edge.to);
                        const midX = (from.x + to.x) / 2;
                        const midY = (from.y + to.y) / 2;
                        return (
                            <motion.g key={i}
                                initial={{ opacity: 0 }}
                                animate={inView ? { opacity: 1 } : {}}
                                transition={{ delay: 0.4 + i * 0.08 }}
                            >
                                <motion.path
                                    d={`M ${from.x} ${from.y} Q ${midX + 20} ${midY - 20} ${to.x} ${to.y}`}
                                    stroke="rgba(148,163,184,0.2)"
                                    strokeWidth="1.5"
                                    strokeDasharray="4 3"
                                    fill="none"
                                    initial={{ pathLength: 0 }}
                                    animate={inView ? { pathLength: 1 } : {}}
                                    transition={{ duration: 0.8, delay: 0.5 + i * 0.1 }}
                                />
                                {/* Env var label */}
                                <rect x={midX - 30} y={midY - 24} width="60" height="16" rx="8"
                                    fill="rgba(30,41,59,0.8)" stroke="rgba(100,116,139,0.2)" strokeWidth="0.5"
                                />
                                <text x={midX} y={midY - 13} textAnchor="middle"
                                    className="fill-slate-500 text-[7px] font-mono"
                                >{edge.label}</text>
                            </motion.g>
                        );
                    })}

                    {/* Service nodes */}
                    {services.map((node, i) => (
                        <motion.g key={node.id}
                            initial={{ opacity: 0, scale: 0 }}
                            animate={inView ? { opacity: 1, scale: 1 } : {}}
                            transition={{ duration: 0.5, delay: 0.6 + i * 0.12 }}
                        >
                            <circle cx={node.x} cy={node.y} r="32" fill="none"
                                stroke={node.color} strokeWidth="1" opacity="0.3"
                            />
                            <circle cx={node.x} cy={node.y} r="22"
                                fill={`${node.color}20`}
                            />
                            <circle cx={node.x} cy={node.y} r="10" fill={node.color} />
                            <text x={node.x} y={node.y + 46} textAnchor="middle"
                                className="fill-slate-300 text-[10px] font-medium"
                            >{node.label}</text>
                            <rect x={node.x - 20} y={node.y + 52} width="40" height="14" rx="7"
                                fill="rgba(148,163,184,0.1)"
                            />
                            <text x={node.x} y={node.y + 62} textAnchor="middle"
                                className="fill-cyan-400 text-[8px]"
                            >SERVICE</text>
                        </motion.g>
                    ))}

                    {/* Addon nodes (different shape: rounded rect) */}
                    {addons.map((node, i) => (
                        <motion.g key={node.id}
                            initial={{ opacity: 0, scale: 0 }}
                            animate={inView ? { opacity: 1, scale: 1 } : {}}
                            transition={{ duration: 0.5, delay: 1 + i * 0.12 }}
                        >
                            <rect x={node.x - 30} y={node.y - 20} width="60" height="40" rx="10"
                                fill="none" stroke={node.color} strokeWidth="1" opacity="0.3"
                            />
                            <rect x={node.x - 22} y={node.y - 14} width="44" height="28" rx="8"
                                fill={`${node.color}15`}
                            />
                            <circle cx={node.x} cy={node.y} r="6" fill={node.color} />
                            <text x={node.x} y={node.y + 34} textAnchor="middle"
                                className="fill-slate-300 text-[10px] font-medium"
                            >{node.label}</text>
                            <rect x={node.x - 18} y={node.y + 40} width="36" height="14" rx="7"
                                fill="rgba(148,163,184,0.1)"
                            />
                            <text x={node.x} y={node.y + 50} textAnchor="middle"
                                className="fill-amber-400/70 text-[8px]"
                            >ADDON</text>
                        </motion.g>
                    ))}

                    {/* Animated data pulses along edges */}
                    <motion.circle r="3" fill="#22d3ee" cx="0" cy="0"
                        initial={{ x: 140, y: 80 }}
                        animate={{
                            x: [140, 220, 300],
                            y: [80, 60, 80],
                        }}
                        transition={{ duration: 3, repeat: Infinity, ease: 'linear' }}
                    />
                    <motion.circle r="2.5" fill="#a78bfa" cx="0" cy="0"
                        initial={{ x: 460, y: 80 }}
                        animate={{
                            x: [460, 380, 300],
                            y: [80, 160, 240],
                        }}
                        transition={{ duration: 3.5, repeat: Infinity, ease: 'linear', delay: 0.8 }}
                    />
                    <motion.circle r="2" fill="#fbbf24" cx="0" cy="0"
                        initial={{ x: 460, y: 200 }}
                        animate={{
                            x: [460, 380, 300],
                            y: [200, 140, 80],
                        }}
                        transition={{ duration: 2.8, repeat: Infinity, ease: 'linear', delay: 1.5 }}
                    />
                </svg>
            </motion.div>

            {/* How topology discovery works */}
            <div className="grid md:grid-cols-3 gap-6">
                {[
                    {
                        icon: Search,
                        title: 'Env Var Scanning',
                        desc: 'Grid scans every service environment variable. DATABASE_URL, REDIS_URL, _BROKER, _HOST keys are parsed to discover connections automatically.',
                        color: 'text-cyan-400',
                        bg: 'bg-cyan-500/10',
                    },
                    {
                        icon: Waypoints,
                        title: 'Dependency Mapping',
                        desc: 'Services, addons, volumes, domains, cron jobs, and tunnels are all mapped into a single dependency graph with live health status.',
                        color: 'text-violet-400',
                        bg: 'bg-violet-500/10',
                    },
                    {
                        icon: Activity,
                        title: 'Live Health Checks',
                        desc: 'Every node in the topology graph is health-checked in real time. TCP probes, HTTP checks, and connection pool status all visible at a glance.',
                        color: 'text-emerald-400',
                        bg: 'bg-emerald-500/10',
                    },
                ].map((item, i) => (
                    <motion.div
                        key={i}
                        initial={{ opacity: 0, y: 20 }}
                        animate={inView ? { opacity: 1, y: 0 } : {}}
                        transition={{ delay: 0.3 + i * 0.12 }}
                        className="bg-slate-900/40 backdrop-blur-sm border border-slate-700/30 rounded-xl p-5"
                    >
                        <div className={`w-10 h-10 rounded-lg ${item.bg} flex items-center justify-center mb-4`}>
                            <item.icon className={`w-5 h-5 ${item.color}`} />
                        </div>
                        <h4 className="text-sm font-bold text-white mb-2">{item.title}</h4>
                        <p className="text-xs text-slate-400 leading-relaxed">{item.desc}</p>
                    </motion.div>
                ))}
            </div>
        </div>
    );
}


// ============================================
// CHAPTER 6: THE FLEET — Mesh & Orchestration
// ============================================
const fleetFeatures = [
    { icon: Network, title: 'WireGuard VPN Mesh', desc: 'Encrypted mesh network across all managed servers. Auto-allocated IPs, encrypted-at-rest keys, per-peer latency tracking. Multiple named meshes.', color: 'text-blue-500', bg: 'bg-blue-500/10' },
    { icon: Server, title: 'Raft Leader Election', desc: 'Automatic leader election across your server fleet. Term tracking, heartbeat monitoring, quorum requirements, full vote history.', color: 'text-violet-500', bg: 'bg-violet-500/10' },
    { icon: RefreshCw, title: 'Self-Healing Orchestration', desc: 'Automatic failure classification: Docker daemon down, disk full, OOM, container crashed. Escalates to AI after 5 attempts with auto-remediation.', color: 'text-emerald-500', bg: 'bg-emerald-500/10' },
    { icon: Waypoints, title: 'Server Migration', desc: '9-state cross-server transfer engine. DNS cutover, rollback with deadline, progress tracking, estimated downtime.', color: 'text-amber-500', bg: 'bg-amber-500/10' },
];

function FleetShowcase() {
    const ref = useRef(null);
    const inView = useInView(ref, { once: true, margin: '-100px' });

    return (
        <div ref={ref} className="max-w-5xl mx-auto">
            {/* Visual: mesh network illustration */}
            <motion.div
                initial={{ opacity: 0, scale: 0.95 }}
                animate={inView ? { opacity: 1, scale: 1 } : {}}
                transition={{ duration: 0.8 }}
                className="mb-16 relative max-w-md mx-auto"
            >
                <svg viewBox="0 0 400 200" fill="none" className="w-full">
                    {/* Connection lines */}
                    <line x1="80" y1="60" x2="200" y2="40" stroke="currentColor" strokeWidth="1.5" className="text-blue-400/40" strokeDasharray="6 4" />
                    <line x1="200" y1="40" x2="320" y2="60" stroke="currentColor" strokeWidth="1.5" className="text-blue-400/40" strokeDasharray="6 4" />
                    <line x1="80" y1="60" x2="140" y2="150" stroke="currentColor" strokeWidth="1.5" className="text-blue-400/40" strokeDasharray="6 4" />
                    <line x1="200" y1="40" x2="200" y2="140" stroke="currentColor" strokeWidth="1.5" className="text-blue-400/40" strokeDasharray="6 4" />
                    <line x1="320" y1="60" x2="260" y2="150" stroke="currentColor" strokeWidth="1.5" className="text-blue-400/40" strokeDasharray="6 4" />
                    <line x1="140" y1="150" x2="200" y2="140" stroke="currentColor" strokeWidth="1.5" className="text-blue-400/40" strokeDasharray="6 4" />
                    <line x1="200" y1="140" x2="260" y2="150" stroke="currentColor" strokeWidth="1.5" className="text-blue-400/40" strokeDasharray="6 4" />
                    <line x1="80" y1="60" x2="200" y2="140" stroke="currentColor" strokeWidth="1" className="text-cyan-400/20" strokeDasharray="4 6" />
                    <line x1="320" y1="60" x2="140" y2="150" stroke="currentColor" strokeWidth="1" className="text-cyan-400/20" strokeDasharray="4 6" />

                    {/* Nodes */}
                    {[
                        { cx: 80, cy: 60, label: 'US-East', leader: false },
                        { cx: 200, cy: 40, label: 'EU-West', leader: true },
                        { cx: 320, cy: 60, label: 'AP-South', leader: false },
                        { cx: 140, cy: 150, label: 'US-West', leader: false },
                        { cx: 200, cy: 140, label: 'EU-Central', leader: false },
                        { cx: 260, cy: 150, label: 'AP-East', leader: false },
                    ].map((node, i) => (
                        <g key={i}>
                            <circle cx={node.cx} cy={node.cy} r={node.leader ? 22 : 18} className={node.leader ? 'fill-emerald-500' : 'fill-slate-700 dark:fill-slate-600'} />
                            {node.leader && (
                                <circle cx={node.cx} cy={node.cy} r="28" className="fill-none stroke-emerald-400/30" strokeWidth="2">
                                    <animate attributeName="r" from="22" to="35" dur="2s" repeatCount="indefinite" />
                                    <animate attributeName="opacity" from="0.6" to="0" dur="2s" repeatCount="indefinite" />
                                </circle>
                            )}
                            <text x={node.cx} y={node.cy + 1} textAnchor="middle" dominantBaseline="middle" className="fill-white text-[10px] font-bold">{node.label.split('-')[0]}</text>
                            <text x={node.cx} y={node.cy + 35} textAnchor="middle" className="fill-slate-400 dark:fill-slate-500 text-[10px] font-medium">{node.label}</text>
                            {node.leader && (
                                <text x={node.cx} y={node.cy - 30} textAnchor="middle" className="fill-emerald-400 text-[9px] font-bold uppercase tracking-wider">Leader</text>
                            )}
                        </g>
                    ))}
                </svg>
            </motion.div>

            <div className="grid md:grid-cols-2 gap-6">
                {fleetFeatures.map((feat, i) => (
                    <motion.div
                        key={i}
                        initial={{ opacity: 0, y: 20 }}
                        animate={inView ? { opacity: 1, y: 0 } : {}}
                        transition={{ delay: 0.3 + i * 0.1, duration: 0.6 }}
                        className="group bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 p-8 rounded-2xl hover:shadow-xl hover:border-blue-500/30 transition-all relative overflow-hidden"
                    >
                        <div className="absolute top-0 right-0 w-32 h-32 bg-blue-500/5 dark:bg-blue-500/10 blur-[50px] rounded-full group-hover:bg-blue-500/20 transition-colors pointer-events-none" />
                        <div className={`w-12 h-12 ${feat.bg} ${feat.color} rounded-xl flex items-center justify-center mb-6 group-hover:scale-110 transition-transform shadow-sm relative z-10`}>
                            <feat.icon className="w-6 h-6" />
                        </div>
                        <h4 className="text-xl font-bold text-slate-900 dark:text-white mb-3 relative z-10">{feat.title}</h4>
                        <p className="text-slate-600 dark:text-slate-400 leading-relaxed relative z-10">{feat.desc}</p>
                    </motion.div>
                ))}
            </div>
        </div>
    );
}


// ============================================
// CHAPTER 7: THE PULSE — Observability Bento
// ============================================
const observabilityFeatures = [
    { icon: TrendingUp, title: 'Predictive Auto-Scaling', desc: 'AI-driven scaling that predicts load spikes before they happen. Scales up proactively, not reactively.', colSpan: 'md:col-span-2' },
    { icon: Activity, title: 'Fleet Health Monitor', desc: 'Cross-server health aggregation with per-node status, resource usage, and alert thresholds in one view.' },
    { icon: Cpu, title: 'Horizontal Scaling', desc: 'Scale any service horizontally across replicas with a single command or automatically via AI.' },
    { icon: RefreshCw, title: 'Disaster Recovery', desc: 'Tiered backup schedules (6h/24h/7d), cloud replication to S3/R2/MinIO, encryption key rotation, and defined RPO/RTO targets.', colSpan: 'md:col-span-2' },
    { icon: BarChart3, title: 'Metrics Dashboard', desc: 'CPU, memory, network, and disk metrics per service. Historical trends and anomaly detection.' },
    { icon: Radio, title: 'Anomaly Detection', desc: 'AI monitors metrics in real time and flags unusual patterns before they become incidents. Proactive, not reactive.' },
];

function ObservabilityBento() {
    const ref = useRef(null);
    const inView = useInView(ref, { once: true, margin: '-100px' });

    return (
        <div ref={ref} className="max-w-5xl mx-auto grid grid-cols-1 md:grid-cols-3 gap-4 auto-rows-[200px]">
            {observabilityFeatures.map((feat, i) => (
                <motion.div
                    key={i}
                    initial={{ opacity: 0, y: 20 }}
                    animate={inView ? { opacity: 1, y: 0 } : {}}
                    transition={{ delay: i * 0.1, duration: 0.6 }}
                    className={`bg-white/70 dark:bg-slate-900/70 backdrop-blur-sm border border-slate-200/50 dark:border-slate-800/50 rounded-2xl p-6 flex flex-col justify-between hover:border-emerald-500/30 transition-all overflow-hidden relative group ${feat.colSpan || ''}`}
                >
                    <div className="absolute -top-10 -right-10 w-32 h-32 bg-emerald-500/5 dark:bg-emerald-500/10 rounded-full blur-2xl group-hover:bg-emerald-500/15 transition-colors pointer-events-none" />
                    <div className="w-10 h-10 bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 rounded-lg flex items-center justify-center relative z-10 group-hover:scale-110 transition-transform">
                        <feat.icon className="w-5 h-5" />
                    </div>
                    <div className="relative z-10">
                        <h4 className="font-bold text-slate-900 dark:text-white mb-2">{feat.title}</h4>
                        <p className="text-sm text-slate-500 dark:text-slate-400 line-clamp-2">{feat.desc}</p>
                    </div>
                </motion.div>
            ))}
        </div>
    );
}


// ============================================
// CHAPTER 8: THE DATA — Services & Billing
// ============================================
const dataServices = [
    { icon: Container, title: 'Addon Marketplace', desc: '35+ managed data services. PostgreSQL, Redis, MongoDB, Kafka, Elasticsearch, and more.', color: 'text-amber-500', bg: 'bg-amber-500/10' },
    { icon: Database, title: 'HA PostgreSQL', desc: 'Patroni-managed streaming replication with automatic failover. PgCat read/write splitting for zero-downtime upgrades.', color: 'text-blue-500', bg: 'bg-blue-500/10' },
    { icon: GitBranch, title: 'Deployment Previews', desc: 'Spin up isolated, ephemeral environments for every pull request. Review changes in production-like conditions.', color: 'text-emerald-500', bg: 'bg-emerald-500/10' },
    { icon: Blocks, title: 'Multi-Git Providers', desc: 'Connect GitHub, GitLab, and Bitbucket. Auto-deploy on push. Unified webhook handling across all providers.', color: 'text-violet-500', bg: 'bg-violet-500/10' },
    { icon: Boxes, title: 'Nixpacks Build Support', desc: 'Auto-detect and build any language with Nixpacks. No Dockerfile needed for most applications.', color: 'text-cyan-500', bg: 'bg-cyan-500/10' },
    { icon: Waypoints, title: 'Dev Tunnels', desc: 'Expose local dev servers via public URLs with reserved subdomains. Test webhooks and integrations locally.', color: 'text-rose-500', bg: 'bg-rose-500/10' },
];

const billingFeatures = [
    { icon: BarChart3, title: 'Multi-Provider Billing', desc: 'Stripe, Flutterwave, and Cryptomus. Usage-based metering per CPU, RAM, storage, and addon. Revenue analytics included.', color: 'text-green-500', bg: 'bg-green-500/10' },
    { icon: Key, title: 'Offline License Validation', desc: 'RSA-signed license keys with three tiers: Community, Pro, Enterprise. Offline-capable verification.', color: 'text-indigo-500', bg: 'bg-indigo-500/10' },
    { icon: Cloud, title: '7 Cloud Storage Backends', desc: 'Cloudflare R2, Amazon S3, MinIO, Backblaze B2, DigitalOcean Spaces, Wasabi, or custom. Per-service or platform-wide.', color: 'text-sky-500', bg: 'bg-sky-500/10' },
    { icon: Brain, title: 'Code Intelligence', desc: 'Automatic codebase scanning with deep analysis. Skeleton extraction, dependency graph building, deployment plan verification.', color: 'text-purple-500', bg: 'bg-purple-500/10' },
];

function DataAndBilling() {
    const ref = useRef(null);
    const inView = useInView(ref, { once: true, margin: '-100px' });

    return (
        <div ref={ref} className="max-w-5xl mx-auto">
            {/* Data Services */}
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6 mb-16">
                {dataServices.map((feat, i) => (
                    <motion.div
                        key={i}
                        initial={{ opacity: 0, y: 20 }}
                        animate={inView ? { opacity: 1, y: 0 } : {}}
                        transition={{ delay: i * 0.08, duration: 0.6 }}
                        className="group bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 p-6 rounded-2xl hover:shadow-xl hover:border-emerald-500/30 transition-all relative overflow-hidden"
                    >
                        <div className="absolute top-0 right-0 w-24 h-24 bg-emerald-500/5 dark:bg-emerald-500/10 blur-[40px] rounded-full group-hover:bg-emerald-500/15 transition-colors pointer-events-none" />
                        <div className={`w-12 h-12 ${feat.bg} ${feat.color} rounded-xl flex items-center justify-center mb-4 group-hover:scale-110 transition-transform relative z-10`}>
                            <feat.icon className="w-6 h-6" />
                        </div>
                        <h4 className="text-lg font-bold text-slate-900 dark:text-white mb-2 relative z-10">{feat.title}</h4>
                        <p className="text-sm text-slate-600 dark:text-slate-400 leading-relaxed relative z-10">{feat.desc}</p>
                    </motion.div>
                ))}
            </div>

            {/* Divider */}
            <div className="flex items-center gap-4 mb-16">
                <div className="flex-1 h-px bg-slate-200 dark:bg-slate-800" />
                <div className="flex items-center gap-2 px-4 py-2 bg-slate-100 dark:bg-slate-800 rounded-full">
                    <DollarSign className="w-4 h-4 text-emerald-500" />
                    <span className="text-xs font-bold text-slate-500 dark:text-slate-400 uppercase tracking-wider">Billing & Intelligence</span>
                </div>
                <div className="flex-1 h-px bg-slate-200 dark:bg-slate-800" />
            </div>

            {/* Billing */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                {billingFeatures.map((feat, i) => (
                    <motion.div
                        key={i}
                        initial={{ opacity: 0, y: 20 }}
                        animate={inView ? { opacity: 1, y: 0 } : {}}
                        transition={{ delay: 0.5 + i * 0.1, duration: 0.6 }}
                        className="group bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 p-6 rounded-2xl hover:shadow-xl hover:border-emerald-500/30 transition-all relative overflow-hidden"
                    >
                        <div className="absolute top-0 right-0 w-24 h-24 bg-emerald-500/5 dark:bg-emerald-500/10 blur-[40px] rounded-full group-hover:bg-emerald-500/15 transition-colors pointer-events-none" />
                        <div className={`w-12 h-12 ${feat.bg} ${feat.color} rounded-xl flex items-center justify-center mb-4 group-hover:scale-110 transition-transform relative z-10`}>
                            <feat.icon className="w-6 h-6" />
                        </div>
                        <h4 className="text-lg font-bold text-slate-900 dark:text-white mb-2 relative z-10">{feat.title}</h4>
                        <p className="text-sm text-slate-600 dark:text-slate-400 leading-relaxed relative z-10">{feat.desc}</p>
                    </motion.div>
                ))}
            </div>
        </div>
    );
}


// ============================================
// CHAPTER 9: & BEYOND — Feature Constellation
// ============================================
const constellationFeatures = [
    { icon: Globe, label: 'Global Edge', group: 'edge' },
    { icon: Network, label: 'Edge Agents', group: 'edge' },
    { icon: Lock, label: 'Custom SSL', group: 'edge' },
    { icon: Shield, label: 'Safe Deploy', group: 'edge' },
    { icon: Fingerprint, label: 'Device Trust', group: 'edge' },
    { icon: Folders, label: 'File Browser', group: 'util' },
    { icon: Timer, label: 'Cron Jobs', group: 'util' },
    { icon: Users, label: 'Teams & RBAC', group: 'util' },
    { icon: RefreshCw, label: 'Blue-Green', group: 'util' },
    { icon: Cable, label: 'Cloud Targets', group: 'util' },
    { icon: Sparkles, label: 'Self-Updates', group: 'util' },
    { icon: Command, label: 'Audit Log', group: 'util' },
    { icon: Boxes, label: 'Nixpacks', group: 'build' },
    { icon: GitBranch, label: 'Multi-Git', group: 'build' },
    { icon: Waypoints, label: 'Dev Tunnels', group: 'build' },
    { icon: Terminal, label: 'Web Terminal', group: 'build' },
];

const groupColors: Record<string, string> = {
    edge: 'hover:bg-rose-500/10 hover:border-rose-500/40',
    util: 'hover:bg-slate-500/10 hover:border-slate-500/40',
    build: 'hover:bg-emerald-500/10 hover:border-emerald-500/40',
};

const groupIconColors: Record<string, string> = {
    edge: 'group-hover:text-rose-500',
    util: 'group-hover:text-slate-500',
    build: 'group-hover:text-emerald-500',
};

// ============================================
// CHAPTER 8: THE EDGE — Global Network Visualization
// ============================================
const edgeLocations = [
    { name: 'US East', x: 22, y: 38 },
    { name: 'US West', x: 10, y: 40 },
    { name: 'EU West', x: 48, y: 30 },
    { name: 'EU Central', x: 52, y: 28 },
    { name: 'AP South', x: 70, y: 50 },
    { name: 'AP East', x: 80, y: 42 },
    { name: 'SA East', x: 28, y: 65 },
    { name: 'AF South', x: 52, y: 58 },
    { name: 'ME Central', x: 60, y: 42 },
    { name: 'OC Southeast', x: 82, y: 65 },
];

const edgeFeatures = [
    { icon: Globe, title: 'Global Edge Routing', desc: '24+ edge locations route traffic to the nearest PoP. Sub-50ms latency worldwide.', color: 'text-rose-400', bg: 'bg-rose-500/10' },
    { icon: Network, title: 'Lite Edge Agents', desc: 'Lightweight nodes connect to the master\'s DB and Redis. Minimal footprint, full orchestration.', color: 'text-blue-400', bg: 'bg-blue-500/10' },
    { icon: Lock, title: 'Custom SSL Manager', desc: 'Auto-provisioned Let\'s Encrypt certificates. Custom cert upload. Wildcard support.', color: 'text-emerald-400', bg: 'bg-emerald-500/10' },
    { icon: Shield, title: 'Safe Deploy & Approvals', desc: 'Manual approval gates for production. Preview environments per branch. Migration risk classification.', color: 'text-amber-400', bg: 'bg-amber-500/10' },
    { icon: Waypoints, title: 'Secure Tunneling', desc: 'Expose local services via encrypted tunnels with reserved subdomains and public URLs. Real-time connection stats.', color: 'text-violet-400', bg: 'bg-violet-500/10' },
    { icon: Key, title: 'API Tokens & CLI', desc: 'Scoped API tokens with RBAC. Full CLI access to logs, deployments, secrets, and fleet management.', color: 'text-cyan-400', bg: 'bg-cyan-500/10' },
];

function EdgeVisualization() {
    const ref = useRef(null);
    const inView = useInView(ref, { once: true, margin: '-80px' });
    const [activeLocation, setActiveLocation] = useState<number | null>(null);

    return (
        <div ref={ref} className="max-w-6xl mx-auto">
            {/* World map with edge locations */}
            <motion.div
                initial={{ opacity: 0, scale: 0.95 }}
                animate={inView ? { opacity: 1, scale: 1 } : {}}
                transition={{ duration: 0.8 }}
                className="relative mb-14 rounded-2xl overflow-hidden bg-slate-900/50 border border-slate-700/40 p-4 md:p-8"
            >
                <svg viewBox="0 0 100 80" className="w-full h-auto" fill="none">
                    {/* Simplified world outline */}
                    <path d="M12 35 Q15 28 22 30 Q28 25 30 28 Q35 22 40 25 Q42 20 48 22 Q52 18 55 22 Q58 20 62 24 Q65 22 68 26 Q72 24 75 28 Q78 30 80 32 Q82 28 85 32 Q82 38 80 42 Q78 48 75 50 Q72 55 68 58 Q65 62 60 60 Q55 65 50 62 Q45 68 40 65 Q35 70 30 65 Q25 60 22 55 Q18 50 15 45 Q12 42 12 35Z"
                        className="fill-slate-800/60 stroke-slate-600/40" strokeWidth="0.3" />

                    {/* Connection lines from edge locations to a central hub */}
                    {edgeLocations.map((loc, i) => (
                        <motion.line
                            key={`line-${i}`}
                            x1={loc.x}
                            y1={loc.y}
                            x2="50"
                            y2="42"
                            stroke="url(#edgeGrad)"
                            strokeWidth="0.2"
                            strokeDasharray="1.5 1.5"
                            initial={{ pathLength: 0, opacity: 0 }}
                            animate={inView ? { pathLength: 1, opacity: 0.6 } : {}}
                            transition={{ delay: 0.3 + i * 0.08, duration: 0.6 }}
                        />
                    ))}

                    {/* Edge location dots */}
                    {edgeLocations.map((loc, i) => (
                        <g key={i}>
                            <motion.circle
                                cx={loc.x}
                                cy={loc.y}
                                r="1.5"
                                className="fill-rose-400"
                                initial={{ scale: 0, opacity: 0 }}
                                animate={inView ? { scale: 1, opacity: 1 } : {}}
                                transition={{ delay: 0.5 + i * 0.06, type: 'spring', stiffness: 300 }}
                                onMouseEnter={() => setActiveLocation(i)}
                                onMouseLeave={() => setActiveLocation(null)}
                                style={{ cursor: 'pointer' }}
                            />
                            {/* Pulse ring */}
                            <motion.circle
                                cx={loc.x}
                                cy={loc.y}
                                r="1.5"
                                className="fill-none stroke-rose-400/40"
                                strokeWidth="0.3"
                                initial={{ scale: 1, opacity: 0 }}
                                animate={inView ? { scale: [1, 2.5], opacity: [0.6, 0] } : {}}
                                transition={{ delay: 1 + i * 0.1, duration: 2, repeat: Infinity, repeatDelay: 3 }}
                            />
                        </g>
                    ))}

                    {/* Central hub */}
                    <motion.circle
                        cx="50"
                        cy="42"
                        r="3"
                        className="fill-emerald-500"
                        initial={{ scale: 0 }}
                        animate={inView ? { scale: 1 } : {}}
                        transition={{ delay: 0.8, type: 'spring' }}
                    />
                    <motion.circle
                        cx="50"
                        cy="42"
                        r="3"
                        className="fill-none stroke-emerald-400/30"
                        strokeWidth="0.3"
                        animate={{ scale: [1, 3], opacity: [0.5, 0] }}
                        transition={{ duration: 3, repeat: Infinity }}
                    />

                    <defs>
                        <linearGradient id="edgeGrad" x1="0%" y1="0%" x2="100%" y2="0%">
                            <stop offset="0%" stopColor="#f43f5e" stopOpacity="0.5" />
                            <stop offset="100%" stopColor="#10b981" stopOpacity="0.3" />
                        </linearGradient>
                    </defs>
                </svg>

                {/* Location label on hover */}
                <AnimatePresence>
                    {activeLocation !== null && (
                        <motion.div
                            initial={{ opacity: 0, y: 5 }}
                            animate={{ opacity: 1, y: 0 }}
                            exit={{ opacity: 0, y: 5 }}
                            className="absolute bottom-4 left-1/2 -translate-x-1/2 bg-slate-800 border border-slate-700 text-white text-sm font-bold px-4 py-2 rounded-lg shadow-xl"
                        >
                            {edgeLocations[activeLocation].name}
                        </motion.div>
                    )}
                </AnimatePresence>

                {/* Stats overlay */}
                <div className="absolute top-4 right-4 md:top-6 md:right-6 flex flex-col gap-2">
                    {[
                        { label: 'Edge Locations', value: '24+' },
                        { label: 'Avg Latency', value: '<50ms' },
                        { label: 'Uptime', value: '99.99%' },
                    ].map((stat, i) => (
                        <motion.div
                            key={i}
                            initial={{ opacity: 0, x: 20 }}
                            animate={inView ? { opacity: 1, x: 0 } : {}}
                            transition={{ delay: 1.2 + i * 0.1 }}
                            className="bg-slate-800/80 backdrop-blur-sm border border-slate-700/50 rounded-lg px-3 py-1.5 text-right"
                        >
                            <div className="text-[10px] text-slate-500 font-medium">{stat.label}</div>
                            <div className="text-sm font-bold text-white">{stat.value}</div>
                        </motion.div>
                    ))}
                </div>
            </motion.div>

            {/* Edge feature cards */}
            <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
                {edgeFeatures.map((feat, i) => (
                    <motion.div
                        key={i}
                        initial={{ opacity: 0, y: 20 }}
                        animate={inView ? { opacity: 1, y: 0 } : {}}
                        transition={{ delay: 0.6 + i * 0.08, duration: 0.5 }}
                        className="group bg-slate-900/40 backdrop-blur-sm border border-slate-700/30 rounded-xl p-5 hover:border-slate-600/80 transition-all relative overflow-hidden"
                    >
                        <div className="absolute -top-8 -right-8 w-24 h-24 bg-rose-500/5 rounded-full blur-2xl group-hover:bg-rose-500/10 transition-colors pointer-events-none" />
                        <div className="relative z-10">
                            <div className={`w-10 h-10 rounded-lg ${feat.bg} ${feat.color} flex items-center justify-center mb-3 group-hover:scale-110 transition-transform`}>
                                <feat.icon className="w-5 h-5" />
                            </div>
                            <h4 className="text-sm font-bold text-white mb-1.5">{feat.title}</h4>
                            <p className="text-xs text-slate-400 leading-relaxed">{feat.desc}</p>
                        </div>
                    </motion.div>
                ))}
            </div>
        </div>
    );
}


// ============================================
// CHAPTER 9: THE VAULT — Billing, Licensing, Storage
// ============================================
const vaultFeatures = [
    {
        icon: BarChart3,
        title: 'Multi-Provider Billing',
        desc: 'Stripe, Flutterwave, and Cryptomus integration. Usage-based metering per CPU, RAM, storage, and addon. Revenue analytics and infrastructure cost tracking built in.',
        stat: 'Stripe + Flutterwave + Crypto',
        gradient: 'from-green-500 to-emerald-500',
    },
    {
        icon: Key,
        title: 'Offline License Validation',
        desc: 'RSA-signed license keys with three tiers: Community, Pro, Enterprise. Feature limits, expiration tracking, and offline-capable verification. No internet required.',
        stat: 'RSA-signed, offline-ready',
        gradient: 'from-indigo-500 to-violet-500',
    },
    {
        icon: Cloud,
        title: '7 Cloud Storage Backends',
        desc: 'Cloudflare R2, Amazon S3, MinIO, Backblaze B2, DigitalOcean Spaces, Wasabi, or custom endpoint. Per-service or platform-wide backup offloading.',
        stat: 'R2 · S3 · MinIO · B2 · DO · Wasabi',
        gradient: 'from-sky-500 to-blue-500',
    },
    {
        icon: Brain,
        title: 'Code Intelligence',
        desc: 'Automatic codebase scanner with deep AI analysis. Skeleton extraction, dependency graph building, deployment plan verification. AI auto-injects missing env vars.',
        stat: 'AI-powered analysis',
        gradient: 'from-purple-500 to-fuchsia-500',
    },
    {
        icon: RefreshCw,
        title: 'Backup Scheduling',
        desc: 'Automated backup schedules per service and per server. Snapshot schedules for point-in-time recovery. Encrypted at rest with managed keys.',
        stat: 'Automated + encrypted',
        gradient: 'from-amber-500 to-orange-500',
    },
    {
        icon: DollarSign,
        title: 'Revenue Analytics',
        desc: 'Track infrastructure costs against user revenue. Per-project cost breakdown. Margin analysis and optimization recommendations from the AI engine.',
        stat: 'Cost vs revenue tracking',
        gradient: 'from-teal-500 to-cyan-500',
    },
];

function VaultShowcase() {
    const ref = useRef(null);
    const inView = useInView(ref, { once: true, margin: '-80px' });

    return (
        <div ref={ref} className="max-w-5xl mx-auto">
            {/* Visual: vault door illustration */}
            <motion.div
                initial={{ opacity: 0, scale: 0.95 }}
                animate={inView ? { opacity: 1, scale: 1 } : {}}
                transition={{ duration: 0.8 }}
                className="relative mb-14 max-w-md mx-auto"
            >
                <svg viewBox="0 0 200 200" className="w-full h-auto" fill="none">
                    {/* Outer ring */}
                    <motion.circle
                        cx="100" cy="100" r="85"
                        className="stroke-amber-500/30 fill-none"
                        strokeWidth="2"
                        initial={{ pathLength: 0 }}
                        animate={inView ? { pathLength: 1 } : {}}
                        transition={{ duration: 1.5, ease: 'easeOut' }}
                    />
                    {/* Inner ring */}
                    <motion.circle
                        cx="100" cy="100" r="65"
                        className="stroke-amber-400/20 fill-none"
                        strokeWidth="1.5"
                        initial={{ pathLength: 0 }}
                        animate={inView ? { pathLength: 1 } : {}}
                        transition={{ delay: 0.3, duration: 1.2, ease: 'easeOut' }}
                    />
                    {/* Lock mechanism lines */}
                    {[0, 45, 90, 135, 180, 225, 270, 315].map((angle, i) => {
                        const rad = (angle * Math.PI) / 180;
                        const x1 = 100 + 45 * Math.cos(rad);
                        const y1 = 100 + 45 * Math.sin(rad);
                        const x2 = 100 + 60 * Math.cos(rad);
                        const y2 = 100 + 60 * Math.sin(rad);
                        return (
                            <motion.line
                                key={i}
                                x1={x1} y1={y1} x2={x2} y2={y2}
                                className="stroke-amber-400/40"
                                strokeWidth="2"
                                strokeLinecap="round"
                                initial={{ opacity: 0 }}
                                animate={inView ? { opacity: 1 } : {}}
                                transition={{ delay: 0.8 + i * 0.05 }}
                            />
                        );
                    })}
                    {/* Center lock */}
                    <motion.circle
                        cx="100" cy="100" r="20"
                        className="fill-amber-500/20 stroke-amber-400/50"
                        strokeWidth="2"
                        initial={{ scale: 0 }}
                        animate={inView ? { scale: 1 } : {}}
                        transition={{ delay: 1, type: 'spring', stiffness: 200 }}
                    />
                    {/* Dollar sign */}
                    <motion.text
                        x="100" y="107"
                        textAnchor="middle"
                        className="fill-amber-400 text-2xl font-bold"
                        initial={{ opacity: 0 }}
                        animate={inView ? { opacity: 1 } : {}}
                        transition={{ delay: 1.2 }}
                    >
                        $
                    </motion.text>
                    {/* Spinning highlight */}
                    <motion.circle
                        cx="100" cy="100" r="85"
                        className="fill-none stroke-amber-400/10"
                        strokeWidth="8"
                        strokeDasharray="30 200"
                        animate={{ rotate: 360 }}
                        transition={{ duration: 20, repeat: Infinity, ease: 'linear' }}
                        style={{ transformOrigin: '100px 100px' }}
                    />
                </svg>
            </motion.div>

            {/* Feature grid */}
            <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
                {vaultFeatures.map((feat, i) => (
                    <motion.div
                        key={i}
                        initial={{ opacity: 0, y: 20 }}
                        animate={inView ? { opacity: 1, y: 0 } : {}}
                        transition={{ delay: 0.4 + i * 0.08, duration: 0.5 }}
                        className="group bg-slate-900/40 backdrop-blur-sm border border-slate-700/30 rounded-xl p-5 hover:border-amber-500/30 transition-all relative overflow-hidden"
                    >
                        <div className={`absolute -top-8 -right-8 w-24 h-24 bg-gradient-to-bl ${feat.gradient} opacity-[0.06] group-hover:opacity-[0.12] rounded-full blur-2xl transition-opacity pointer-events-none`} />
                        <div className="relative z-10">
                            <div className={`w-10 h-10 rounded-lg bg-gradient-to-br ${feat.gradient} flex items-center justify-center mb-3 group-hover:scale-110 transition-transform`}>
                                <feat.icon className="w-5 h-5 text-white" />
                            </div>
                            <h4 className="text-sm font-bold text-white mb-1.5">{feat.title}</h4>
                            <p className="text-xs text-slate-400 leading-relaxed mb-3">{feat.desc}</p>
                            <div className="inline-flex items-center gap-1.5 px-2.5 py-1 bg-slate-800/80 rounded-full">
                                <Zap className="w-3 h-3 text-amber-400" />
                                <span className="text-[10px] font-bold text-slate-300">{feat.stat}</span>
                            </div>
                        </div>
                    </motion.div>
                ))}
            </div>
        </div>
    );
}


// ============================================
// CHAPTER 10: & BEYOND — Feature Constellation
// ============================================

function FeatureConstellation() {
    const ref = useRef(null);
    const inView = useInView(ref, { once: true, margin: '-80px' });

    return (
        <div ref={ref} className="max-w-4xl mx-auto">
            <motion.div
                initial={{ opacity: 0 }}
                animate={inView ? { opacity: 1 } : {}}
                transition={{ duration: 0.8 }}
                className="grid grid-cols-2 sm:grid-cols-4 gap-3"
            >
                {constellationFeatures.map((feat, i) => (
                    <motion.div
                        key={i}
                        initial={{ opacity: 0, scale: 0.8 }}
                        animate={inView ? { opacity: 1, scale: 1 } : {}}
                        transition={{ delay: i * 0.03, duration: 0.4, type: 'spring', stiffness: 300 }}
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
            {/* CHAPTER 1: THE PUSH — Black Hole */}
            <section id="push" className="relative py-24 md:py-40 scroll-mt-20 overflow-hidden">
                <SpaceBackground body="blackhole" />
                <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <ChapterHeader
                        number="01"
                        title={<>One push.<br />Everything deploys.</>}
                        subtitle="Stop wrestling with Dockerfiles, CI pipelines, and infrastructure configs. Push your code. Grid detects your stack, builds it, secures it, and ships it."
                        accent="bg-emerald-500/20 text-emerald-300"
                    />
                    <TypewriterTerminal />

                    <div className="mt-20 md:mt-32 max-w-6xl mx-auto">
                        <EcosystemArchitectureVisual />
                    </div>
                </div>
            </section>

            {/* CHAPTER 2: THE SHIELD — Pulsar */}
            <section id="shield" className="relative py-24 md:py-40 scroll-mt-20 overflow-hidden">
                <SpaceBackground body="pulsar" />
                <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <ChapterHeader
                        number="02"
                        title={<>Security isn&apos;t optional.<br />It&apos;s the foundation.</>}
                        subtitle="Five layers of defense wrap every deployment. No configuration, no checkboxes. Just hardened infrastructure by default."
                        accent="bg-rose-500/20 text-rose-300"
                    />
                    <SecurityLayers />
                </div>
            </section>

            {/* CHAPTER 3: THE SCALE — Red Supergiant */}
            <section id="scale" className="relative py-24 md:py-40 scroll-mt-20 overflow-hidden">
                <SpaceBackground body="redgiant" />
                <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <ChapterHeader
                        number="03"
                        title={<>From one server<br />to a global fleet.</>}
                        subtitle="Start with a single VPS. Scale to a multi-region, AI-managed fleet. Add servers, distribute workloads, and let Grid handle the rest."
                        accent="bg-purple-500/20 text-purple-300"
                    />
                    <ScaleTimeline />
                </div>
            </section>

            {/* CHAPTER 4: THE MIND — Magnetar */}
            <section id="intelligence" className="relative py-24 md:py-40 scroll-mt-20 overflow-hidden">
                <SpaceBackground body="magnetar" />
                <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <ChapterHeader
                        number="04"
                        title={<>Your infrastructure<br />thinks for itself.</>}
                        subtitle="Grid doesn't just run your code. It understands it. AI-powered diagnostics, predictive scaling, and self-healing deployments."
                        accent="bg-indigo-500/20 text-indigo-300"
                    />
                    <AIShowcase />
                </div>
            </section>

            {/* CHAPTER 5: THE FLEET — Binary Star */}
            <section id="fleet" className="relative py-24 md:py-40 scroll-mt-20 overflow-hidden">
                <SpaceBackground body="binarystar" />
                <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <ChapterHeader
                        number="05"
                        title={<>Dozens of servers.<br />One brain.</>}
                        subtitle="Orchestrate your entire fleet as a single unit. Self-healing orchestration, 4-stage rolling updates, automatic failover, and zero-downtime migration."
                        accent="bg-blue-500/20 text-blue-300"
                    />
                    <FleetShowcase />
                </div>
            </section>

            {/* CHAPTER 6: THE TOPOLOGY — Quasar */}
            <section id="topology" className="relative py-24 md:py-40 scroll-mt-20 overflow-hidden">
                <SpaceBackground body="quasar" />
                <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <ChapterHeader
                        number="06"
                        title={<>Your infrastructure.<br />Mapped. Connected. Alive.</>}
                        subtitle="Grid auto-discovers how your services connect. Environment variables, addon links, internal URLs, all mapped into a live dependency graph."
                        accent="bg-cyan-500/20 text-cyan-300"
                    />
                    <TopologyVisualization />
                </div>
            </section>

            {/* CHAPTER 7: THE PULSE — White Dwarf */}
            <section id="observe" className="relative py-24 md:py-40 scroll-mt-20 overflow-hidden">
                <SpaceBackground body="whitedwarf" />
                <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <ChapterHeader
                        number="07"
                        title={<>See everything.<br />Miss nothing.</>}
                        subtitle="Full observability across your entire infrastructure. Real-time logs, metrics dashboards, disaster recovery, and AI-driven anomaly detection."
                        accent="bg-teal-500/20 text-teal-300"
                    />
                    <ObservabilityBento />
                </div>
            </section>

            {/* CHAPTER 8: THE DATA — Hot Jupiter (exoplanet) */}
            <section id="data" className="relative py-24 md:py-40 scroll-mt-20 overflow-hidden">
                <SpaceBackground body="hotjupiter" />
                <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <ChapterHeader
                        number="08"
                        title={<>Your data layer.<br />Handled.</>}
                        subtitle="35+ managed data services with HA streaming replication, deployment previews, multi-git support, and secure tunneling. All built in."
                        accent="bg-sky-500/20 text-sky-300"
                    />
                    <DataAndBilling />
                </div>
            </section>

            {/* CHAPTER 9: THE EDGE — Supernova Remnant */}
            <section id="edge" className="relative py-24 md:py-40 scroll-mt-20 overflow-hidden">
                <SpaceBackground body="supernova" />
                <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <ChapterHeader
                        number="09"
                        title={<>Global reach.<br />Local speed.</>}
                        subtitle="24+ edge locations, custom SSL, device attestation, safe deploy approvals, and API tokens. Your infrastructure spans the globe."
                        accent="bg-orange-500/20 text-orange-300"
                    />
                    <EdgeVisualization />
                </div>
            </section>

            {/* CHAPTER 10: THE VAULT — Magenta Nebula Cloud */}
            <section id="vault" className="relative py-24 md:py-40 scroll-mt-20 overflow-hidden">
                <SpaceBackground body="magcloud" />
                <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <ChapterHeader
                        number="10"
                        title={<>Protect. Recover.<br />Scale.</>}
                        subtitle="Multi-provider billing, RSA-signed offline licenses, 7 cloud storage backends, AI code intelligence, and automated backup scheduling."
                        accent="bg-fuchsia-500/20 text-fuchsia-300"
                    />
                    <VaultShowcase />
                </div>
            </section>

            {/* CHAPTER 11: & BEYOND — Ice Exomoon */}
            <section id="beyond" className="relative py-24 md:py-40 scroll-mt-20 overflow-hidden">
                <SpaceBackground body="icemoon" />
                <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <ChapterHeader
                        number="11"
                        title={<>And there&apos;s always<br />more to explore.</>}
                        subtitle="Build tools, utilities, developer experience features, and community — all included, all free."
                        accent="bg-slate-500/20 text-slate-300"
                    />
                    <FeatureConstellation />
                </div>
            </section>
        </>
    );
}
