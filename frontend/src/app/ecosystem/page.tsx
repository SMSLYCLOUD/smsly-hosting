'use client';

import { useState, useEffect, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
    Scan, Rocket, CheckCircle2, XCircle, AlertCircle, Loader2,
    Server, Database, Globe, GitBranch, Zap, ArrowRight, RefreshCw, Sparkles
} from 'lucide-react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import Link from 'next/link';

// Types
interface ServicePlan {
    repo: string;
    name: string;
    stack: string;
    languages?: string[];
    port: number;
    build: string;
    addons: string[];
    env_vars: Record<string, string>;
    depends_on?: string[];
    deploy_order: number;
    skip?: boolean;
}

interface Addon {
    type: string;
    shared_by: string[];
}

interface DeployPlan {
    services: ServicePlan[];
    addons: Addon[];
    deploy_sequence: string[];
    ai_provider: string;
    total_repos_scanned?: number;
    deployable_repos?: number;
    message?: string;
}

interface DeployResult {
    repo: string;
    name: string;
    service_id?: string;
    deployment_id?: string;
    status: string;
    stack?: string;
    port?: number;
    error?: string;
}

// Stack colors/icons
const STACK_COLORS: Record<string, string> = {
    django: 'text-green-400 bg-green-500/10 border-green-500/20',
    python: 'text-yellow-400 bg-yellow-500/10 border-yellow-500/20',
    nextjs: 'text-white bg-zinc-500/10 border-zinc-500/20',
    node: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20',
    rust: 'text-orange-400 bg-orange-500/10 border-orange-500/20',
    go: 'text-cyan-400 bg-cyan-500/10 border-cyan-500/20',
    java: 'text-red-400 bg-red-500/10 border-red-500/20',
    ruby: 'text-red-400 bg-red-500/10 border-red-500/20',
    php: 'text-violet-400 bg-violet-500/10 border-violet-500/20',
    unknown: 'text-zinc-400 bg-zinc-500/10 border-zinc-500/20',
};

function getToken() {
    return typeof window !== 'undefined' ? localStorage.getItem('auth_token') : null;
}

async function apiPost(path: string, body?: object) {
    const token = getToken();
    const res = await fetch(path, {
        method: 'POST',
        credentials: 'include',
        headers: {
            'Content-Type': 'application/json',
            ...(token ? { Authorization: `Token ${token}` } : {}),
        },
        body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
}

async function apiGet(path: string) {
    const token = getToken();
    const res = await fetch(path, {
        credentials: 'include',
        headers: {
            ...(token ? { Authorization: `Token ${token}` } : {}),
        },
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
}

export default function EcosystemPage() {
    const [step, setStep] = useState<'idle' | 'scanning' | 'review' | 'deploying' | 'done'>('idle');
    const [plan, setPlan] = useState<DeployPlan | null>(null);
    const [scanTaskId, setScanTaskId] = useState<string | null>(null);
    const [deployTaskId, setDeployTaskId] = useState<string | null>(null);
    const [deployResults, setDeployResults] = useState<DeployResult[]>([]);
    const [error, setError] = useState<string | null>(null);
    const [scanProgress, setScanProgress] = useState('Initializing scan...');
    const [expandedEnv, setExpandedEnv] = useState<number | null>(null);

    // Poll for scan task completion
    const pollTask = useCallback(async (taskId: string, onComplete: (result: any) => void) => {
        const poll = async () => {
            try {
                const data = await apiGet(`/api/v1/cloud/ecosystem/task_status/?task_id=${taskId}`);
                if (data.status === 'SUCCESS' && data.result) {
                    onComplete(data.result);
                } else if (data.status === 'FAILURE') {
                    setError(data.error || 'Task failed');
                    setStep('idle');
                } else {
                    // Still running
                    setTimeout(poll, 2000);
                }
            } catch {
                setTimeout(poll, 3000);
            }
        };
        poll();
    }, []);

    // Start scan
    const startScan = async () => {
        setStep('scanning');
        setError(null);
        setPlan(null);
        setScanProgress('Connecting to GitHub...');

        try {
            const data = await apiPost('/api/v1/cloud/ecosystem/scan/');
            setScanTaskId(data.task_id);
            setScanProgress('Scanning repositories...');

            // Animate progress messages
            const messages = [
                'Fetching repository list...',
                'Analyzing file structures...',
                'Detecting tech stacks...',
                'Building dependency graph...',
                'Generating deploy plan...',
            ];
            let i = 0;
            const interval = setInterval(() => {
                if (i < messages.length) {
                    setScanProgress(messages[i]);
                    i++;
                }
            }, 4000);

            pollTask(data.task_id, (result) => {
                clearInterval(interval);
                if (result.error) {
                    setError(result.error);
                    setStep('idle');
                } else {
                    setPlan(result);
                    setStep('review');
                }
            });
        } catch (err: any) {
            setError(err.message || 'Failed to start scan');
            setStep('idle');
        }
    };

    // Deploy all
    const deployAll = async () => {
        if (!plan) return;
        setStep('deploying');
        setError(null);

        try {
            const data = await apiPost('/api/v1/cloud/ecosystem/deploy/', { plan });
            setDeployTaskId(data.task_id);

            pollTask(data.task_id, (result) => {
                if (result.error) {
                    setError(result.error);
                    setStep('review');
                } else {
                    setDeployResults(result.services || []);
                    setStep('done');
                }
            });
        } catch (err: any) {
            setError(err.message || 'Failed to start deployment');
            setStep('review');
        }
    };

    // Toggle skip on a service
    const toggleSkip = (index: number) => {
        if (!plan) return;
        const updated = { ...plan };
        updated.services = [...updated.services];
        updated.services[index] = {
            ...updated.services[index],
            skip: !updated.services[index].skip,
        };
        setPlan(updated);
    };

    // Update env var
    const updateEnvVar = (serviceIndex: number, key: string, value: string) => {
        if (!plan) return;
        const updated = { ...plan };
        updated.services = [...updated.services];
        const newEnv = { ...updated.services[serviceIndex].env_vars };
        newEnv[key] = value;
        updated.services[serviceIndex] = {
            ...updated.services[serviceIndex],
            env_vars: newEnv,
        };
        setPlan(updated);
    };

    // Handle paste .env
    const handlePasteEnv = async (serviceIndex: number) => {
        try {
            const text = await navigator.clipboard.readText();
            const lines = text.split('\n');
            const newEnvVars: Record<string, string> = {};

            lines.forEach((line) => {
                const trimmed = line.trim();
                if (trimmed && !trimmed.startsWith('#')) {
                    const match = trimmed.match(/^([^=]+)=(.*)$/);
                    if (match) {
                        const [, key, val] = match;
                        // Remove surrounding quotes if present
                        let cleanVal = val.trim();
                        if (cleanVal.startsWith('"') && cleanVal.endsWith('"')) {
                            cleanVal = cleanVal.slice(1, -1);
                        } else if (cleanVal.startsWith("'") && cleanVal.endsWith("'")) {
                            cleanVal = cleanVal.slice(1, -1);
                        }
                        newEnvVars[key.trim()] = cleanVal;
                    }
                }
            });

            if (Object.keys(newEnvVars).length > 0 && plan) {
                const updated = { ...plan };
                updated.services = [...updated.services];
                const existingEnv = updated.services[serviceIndex].env_vars || {};
                updated.services[serviceIndex] = {
                    ...updated.services[serviceIndex],
                    env_vars: { ...existingEnv, ...newEnvVars },
                };
                setPlan(updated);
            }
        } catch (err) {
            console.error('Failed to read clipboard', err);
        }
    };

    // Handle individual retry
    const handleRetry = async (deploymentId: string, repoIndex: number) => {
        try {
            const data = await apiPost(`/api/v1/cloud/deployments/${deploymentId}/retry/`);
            if (data) {
                const updatedResults = [...deployResults];
                updatedResults[repoIndex].status = 'queued';
                setDeployResults(updatedResults);
            }
        } catch (err: any) {
            setError(err.message || 'Failed to retry deployment');
        }
    };

    return (
        <DashboardShell>
            <div className="flex-1 p-8 relative z-10">
                <motion.div
                    className="max-w-5xl mx-auto space-y-8"
                    initial={{ opacity: 0, y: 20 }}
                    animate={{ opacity: 1, y: 0 }}
                >
                    {/* Header */}
                    <div className="flex items-center justify-between">
                        <div>
                            <h1 className="text-3xl font-bold tracking-tight flex items-center gap-3">
                                <Sparkles className="text-emerald-500" size={28} />
                                Ecosystem Deploy
                            </h1>
                            <p className="text-muted-foreground mt-1">
                                Zero-config AI deployment — scan your GitHub, deploy everything
                            </p>
                        </div>
                        {step !== 'idle' && step !== 'scanning' && (
                            <button
                                onClick={() => { setStep('idle'); setPlan(null); setError(null); }}
                                className="text-sm text-muted-foreground hover:text-foreground flex items-center gap-1"
                            >
                                <RefreshCw size={14} /> Start Over
                            </button>
                        )}
                    </div>

                    {/* Error Banner */}
                    <AnimatePresence>
                        {error && (
                            <motion.div
                                initial={{ opacity: 0, height: 0 }}
                                animate={{ opacity: 1, height: 'auto' }}
                                exit={{ opacity: 0, height: 0 }}
                                className="p-4 rounded-xl bg-red-500/10 border border-red-500/30 flex items-center gap-3 text-red-500"
                            >
                                <AlertCircle size={18} />
                                <span className="text-sm">{error}</span>
                            </motion.div>
                        )}
                    </AnimatePresence>

                    {/* Step: Idle */}
                    {step === 'idle' && (
                        <motion.div
                            initial={{ opacity: 0, scale: 0.95 }}
                            animate={{ opacity: 1, scale: 1 }}
                            className="text-center py-16"
                        >
                            <div className="w-24 h-24 mx-auto mb-6 rounded-3xl bg-gradient-to-br from-emerald-500/20 to-teal-500/10 flex items-center justify-center border border-emerald-500/20">
                                <Globe className="text-emerald-500" size={40} />
                            </div>
                            <h2 className="text-2xl font-bold mb-3">Deploy Your Entire Ecosystem</h2>
                            <p className="text-muted-foreground max-w-lg mx-auto mb-8">
                                Connect your GitHub and we&apos;ll scan <strong>all your repos</strong>,
                                detect stacks, map dependencies, and deploy everything
                                to your server — <strong>zero configuration needed</strong>.
                            </p>
                            <motion.button
                                whileHover={{ scale: 1.05 }}
                                whileTap={{ scale: 0.95 }}
                                onClick={startScan}
                                className="btn-shimmer px-8 py-3.5 rounded-xl bg-gradient-to-r from-emerald-500 to-teal-600 text-white font-semibold shadow-lg shadow-emerald-500/25 flex items-center gap-3 mx-auto text-lg"
                            >
                                <Scan size={22} />
                                Scan My GitHub
                            </motion.button>
                        </motion.div>
                    )}

                    {/* Step: Scanning */}
                    {step === 'scanning' && (
                        <motion.div
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            className="text-center py-16"
                        >
                            <div className="w-24 h-24 mx-auto mb-6 rounded-3xl bg-gradient-to-br from-blue-500/20 to-cyan-500/10 flex items-center justify-center border border-blue-500/20">
                                <Loader2 className="text-blue-500 animate-spin" size={40} />
                            </div>
                            <h2 className="text-2xl font-bold mb-3">Scanning Your Repositories</h2>
                            <p className="text-muted-foreground">{scanProgress}</p>
                            <div className="mt-6 flex justify-center">
                                <div className="h-1 w-64 bg-muted rounded-full overflow-hidden">
                                    <motion.div
                                        className="h-full bg-gradient-to-r from-blue-500 to-cyan-500 rounded-full"
                                        initial={{ width: '5%' }}
                                        animate={{ width: '85%' }}
                                        transition={{ duration: 30, ease: 'linear' }}
                                    />
                                </div>
                            </div>
                        </motion.div>
                    )}

                    {/* Step: Review */}
                    {step === 'review' && plan && (
                        <motion.div
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            className="space-y-6"
                        >
                            {/* Stats */}
                            <div className="grid grid-cols-3 gap-4">
                                <div className="bg-card border border-border p-4 rounded-xl">
                                    <p className="text-xs text-muted-foreground uppercase tracking-wider font-bold">Repos Scanned</p>
                                    <p className="text-2xl font-bold mt-1">{plan.total_repos_scanned || '—'}</p>
                                </div>
                                <div className="bg-card border border-border p-4 rounded-xl">
                                    <p className="text-xs text-muted-foreground uppercase tracking-wider font-bold">Deployable</p>
                                    <p className="text-2xl font-bold mt-1 text-emerald-500">{plan.services?.length || 0}</p>
                                </div>
                                <div className="bg-card border border-border p-4 rounded-xl">
                                    <p className="text-xs text-muted-foreground uppercase tracking-wider font-bold">AI Provider</p>
                                    <p className="text-2xl font-bold mt-1 text-primary">{plan.ai_provider?.split(' ')[0] || '—'}</p>
                                </div>
                            </div>

                            {/* Addons */}
                            {plan.addons && plan.addons.length > 0 && (
                                <div className="bg-card border border-border p-5 rounded-xl">
                                    <h3 className="font-bold text-sm uppercase tracking-wider text-muted-foreground mb-3 flex items-center gap-2">
                                        <Database size={14} /> Shared Addons (Auto-Provisioned)
                                    </h3>
                                    <div className="flex flex-wrap gap-2">
                                        {plan.addons.map((addon) => (
                                            <span key={addon.type} className="px-3 py-1.5 rounded-lg bg-purple-500/10 border border-purple-500/20 text-purple-400 text-sm font-medium">
                                                {addon.type} → {addon.shared_by.join(', ')}
                                            </span>
                                        ))}
                                    </div>
                                </div>
                            )}

                            {/* Services List */}
                            <div className="space-y-3">
                                <h3 className="font-bold text-sm uppercase tracking-wider text-muted-foreground flex items-center gap-2">
                                    <Server size={14} /> Services to Deploy
                                </h3>
                                {plan.services
                                    .sort((a, b) => a.deploy_order - b.deploy_order)
                                    .map((svc, idx) => (
                                        <div key={svc.repo}>
                                        <motion.div
                                            initial={{ opacity: 0, x: -20 }}
                                            animate={{ opacity: 1, x: 0 }}
                                            transition={{ delay: idx * 0.05 }}
                                            className={`bg-card border rounded-xl p-4 flex items-center justify-between transition-all ${svc.skip
                                                    ? 'border-border/50 opacity-50'
                                                    : 'border-border hover:border-emerald-500/30'
                                                }`}
                                        >
                                            <div className="flex items-center gap-4">
                                                <div className="text-xs text-muted-foreground font-mono w-6 text-center">
                                                    #{svc.deploy_order}
                                                </div>
                                                <div>
                                                    <p className="font-semibold flex items-center gap-2">
                                                        <GitBranch size={14} className="text-muted-foreground" />
                                                        {svc.repo}
                                                    </p>
                                                    <div className="flex items-center gap-2 mt-1 flex-wrap">
                                                        {(svc.languages && svc.languages.length > 0 ? svc.languages : [svc.stack]).map((lang) => (
                                                            <span key={lang} className={`text-xs px-2 py-0.5 rounded-md border font-medium ${STACK_COLORS[lang] || STACK_COLORS.unknown}`}>
                                                                {lang}
                                                            </span>
                                                        ))}
                                                        <span className="text-xs text-muted-foreground">
                                                            :{svc.port}
                                                        </span>
                                                        <span className="text-xs text-muted-foreground">
                                                            {svc.build}
                                                        </span>
                                                        {svc.addons?.map((a) => (
                                                            <span key={a} className="text-xs px-1.5 py-0.5 rounded bg-purple-500/10 text-purple-400">
                                                                {a}
                                                            </span>
                                                        ))}
                                                        {Object.keys(svc.env_vars || {}).length > 0 && (
                                                            <button
                                                                onClick={() => setExpandedEnv(expandedEnv === idx ? null : idx)}
                                                                className="text-xs text-primary hover:underline"
                                                            >
                                                                {Object.keys(svc.env_vars).length} env vars {expandedEnv === idx ? '▲' : '▼'}
                                                            </button>
                                                        )}
                                                    </div>
                                                </div>
                                            </div>
                                            <div className="flex flex-col gap-2 items-end">
                                                <button
                                                    onClick={() => toggleSkip(idx)}
                                                    className={`text-xs px-3 py-1.5 rounded-lg border transition-colors ${svc.skip
                                                            ? 'border-border text-muted-foreground hover:text-foreground'
                                                            : 'border-emerald-500/30 text-emerald-500 bg-emerald-500/10'
                                                        }`}
                                                >
                                                    {svc.skip ? 'Skipped' : 'Include'}
                                                </button>
                                            </div>
                                        </motion.div>
                                        {expandedEnv === idx && !svc.skip && (
                                            <motion.div
                                                initial={{ opacity: 0, height: 0 }}
                                                animate={{ opacity: 1, height: 'auto' }}
                                                className="bg-card border border-t-0 rounded-b-xl p-4 -mt-2 space-y-3"
                                            >
                                                <div className="flex items-center justify-between mb-2">
                                                    <h4 className="text-sm font-semibold text-muted-foreground">Environment Variables</h4>
                                                    <button
                                                        onClick={() => handlePasteEnv(idx)}
                                                        className="text-xs text-primary bg-primary/10 px-2 py-1 rounded hover:bg-primary/20"
                                                    >
                                                        Paste .env
                                                    </button>
                                                </div>
                                                {Object.entries(svc.env_vars || {}).map(([key, value]) => (
                                                    <div key={key} className="flex gap-2 items-center">
                                                        <input
                                                            type="text"
                                                            value={key}
                                                            disabled
                                                            className="text-xs font-mono bg-muted border border-border rounded px-2 py-1.5 w-1/3"
                                                        />
                                                        <input
                                                            type="text"
                                                            value={value}
                                                            onChange={(e) => updateEnvVar(idx, key, e.target.value)}
                                                            className="text-xs font-mono bg-background border border-border rounded px-2 py-1.5 flex-1"
                                                            placeholder="Empty value"
                                                        />
                                                    </div>
                                                ))}
                                            </motion.div>
                                        )}
                                        </div>
                                    ))}
                            </div>

                            {/* Deploy Button */}
                            <div className="flex justify-center pt-4">
                                <motion.button
                                    whileHover={{ scale: 1.05 }}
                                    whileTap={{ scale: 0.95 }}
                                    onClick={deployAll}
                                    className="btn-shimmer px-10 py-4 rounded-xl bg-gradient-to-r from-emerald-500 to-teal-600 text-white font-bold shadow-lg shadow-emerald-500/25 flex items-center gap-3 text-lg"
                                >
                                    <Rocket size={22} />
                                    Deploy {plan.services.filter(s => !s.skip).length} Services
                                    <ArrowRight size={18} />
                                </motion.button>
                            </div>
                        </motion.div>
                    )}

                    {/* Step: Deploying */}
                    {step === 'deploying' && (
                        <motion.div
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            className="text-center py-16"
                        >
                            <div className="w-24 h-24 mx-auto mb-6 rounded-3xl bg-gradient-to-br from-emerald-500/20 to-teal-500/10 flex items-center justify-center border border-emerald-500/20">
                                <Rocket className="text-emerald-500 animate-bounce" size={40} />
                            </div>
                            <h2 className="text-2xl font-bold mb-3">Deploying Your Ecosystem</h2>
                            <p className="text-muted-foreground">
                                Creating services and queuing builds in dependency order...
                            </p>
                            <div className="mt-4">
                                <Loader2 className="animate-spin text-emerald-500 mx-auto" size={24} />
                            </div>
                        </motion.div>
                    )}

                    {/* Step: Done */}
                    {step === 'done' && (
                        <motion.div
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            className="space-y-6"
                        >
                            <div className="text-center py-8">
                                <div className="w-20 h-20 mx-auto mb-4 rounded-3xl bg-emerald-500/20 flex items-center justify-center border border-emerald-500/20">
                                    <CheckCircle2 className="text-emerald-500" size={36} />
                                </div>
                                <h2 className="text-2xl font-bold mb-2">Ecosystem Deployment Queued!</h2>
                                <p className="text-muted-foreground">
                                    All services have been created and builds are running.
                                </p>
                            </div>

                            {/* Results */}
                            <div className="space-y-2">
                                {deployResults.map((r, idx) => (
                                    <div
                                        key={r.repo}
                                        className="bg-card border border-border p-4 rounded-xl flex items-center justify-between"
                                    >
                                        <div className="flex items-center gap-3">
                                            {r.status === 'queued' ? (
                                                <CheckCircle2 className="text-emerald-500" size={18} />
                                            ) : r.status === 'skipped' ? (
                                                <AlertCircle className="text-yellow-500" size={18} />
                                            ) : (
                                                <XCircle className="text-red-500" size={18} />
                                            )}
                                            <div>
                                                <p className="font-medium">{r.name || r.repo}</p>
                                                <p className="text-xs text-muted-foreground">
                                                    {r.stack && `${r.stack} · `}
                                                    {r.port && `port ${r.port} · `}
                                                    {r.status}
                                                </p>
                                            </div>
                                        </div>
                                        <div className="flex gap-3 items-center">
                                            {r.status === 'failed' && r.deployment_id && (
                                                <button
                                                    onClick={() => handleRetry(r.deployment_id!, idx)}
                                                    className="text-xs flex items-center gap-1 text-primary bg-primary/10 hover:bg-primary/20 px-2 py-1 rounded"
                                                >
                                                    <RefreshCw size={12} /> Retry
                                                </button>
                                            )}
                                            {r.deployment_id && (
                                                <Link
                                                    href={`/services/${r.service_id}?tab=logs`}
                                                    className="text-xs text-primary hover:underline"
                                                >
                                                    View Logs →
                                                </Link>
                                            )}
                                        </div>
                                    </div>
                                ))}
                            </div>

                            <div className="flex justify-center gap-4 pt-4">
                                <Link
                                    href="/services"
                                    className="px-6 py-2.5 rounded-xl border border-border hover:border-foreground/20 text-foreground font-semibold transition-colors"
                                >
                                    View Services
                                </Link>
                                <Link
                                    href="/deployments"
                                    className="px-6 py-2.5 rounded-xl bg-gradient-to-r from-emerald-500 to-teal-600 text-white font-semibold shadow-lg shadow-emerald-500/25"
                                >
                                    View Deployments
                                </Link>
                            </div>
                        </motion.div>
                    )}
                </motion.div>
            </div>
        </DashboardShell>
    );
}
