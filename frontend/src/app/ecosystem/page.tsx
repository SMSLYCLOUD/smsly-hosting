'use client';

// TODO: Migrate this page to React Query (TanStack Query). This 1530-line page
// manages topology data, scan results, bulk env updates, and service deployment
// plans via useEffect+useState. React Query's useQuery/useMutation would handle
// cache management, background refetching, and optimistic updates cleanly.

import { useState, useEffect, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
    Scan, Rocket, CheckCircle2, XCircle, AlertCircle, Loader2, Plus,
    Server, Database, Globe, GitBranch, Zap, ArrowRight, RefreshCw, Sparkles,
    Code, CheckCircle, AlertTriangle, Variable, Terminal, Download
} from 'lucide-react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { ecosystemApi } from '@/lib/api';
import Link from 'next/link';
import { TopologyCanvas } from './components/TopologyCanvas';
import { BulkEnvDialog } from './components/BulkEnvDialog';
import { CachedScanCard } from './components/CachedScanCard';
import { PlanHistorySection } from './components/PlanHistorySection';

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
    server_id?: string;
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
    server?: string;
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

type Step = 'idle' | 'selection' | 'scanning' | 'review' | 'deploying' | 'done';

interface SharedAddonConfig {
    [addonType: string]: { shared: boolean; shared_by?: string[] };
}

// SECURITY: Use sessionStorage, not localStorage. The deployment plan
// contains environment variable values that may include secrets.
// sessionStorage is cleared when the tab closes, limiting exposure.
function saveState(key: string, value: any) {
    try { sessionStorage.setItem(`ecosystem:${key}`, JSON.stringify(value)); } catch {}
}

function loadState<T>(key: string, fallback: T): T {
    try {
        const stored = sessionStorage.getItem(`ecosystem:${key}`);
        return stored ? JSON.parse(stored) : fallback;
    } catch { return fallback; }
}

function clearState() {
    ['step', 'plan', 'planId', 'scanTaskId', 'deployTaskId', 'selectedRepos', 'aiProvider', 'useSharedAddons', 'cancelOthersOnFailure', 'sharedAddonConfig'].forEach(
        key => sessionStorage.removeItem(`ecosystem:${key}`)
    );
}

async function apiPost(path: string, body?: object) {
    const res = await fetch(path, {
        method: 'POST',
        credentials: 'include',
        headers: {
            'Content-Type': 'application/json',
        },
        body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
}

async function apiGet(path: string) {
    const res = await fetch(path, {
        credentials: 'include',
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
}

export default function EcosystemPage() {
    const [step, setStep] = useState<Step>(() => loadState('step', 'idle'));
    const [plan, setPlan] = useState<DeployPlan | null>(() => loadState('plan', null));
    const [planId, setPlanId] = useState<string>(() => loadState('planId', ''));
    const [scanTaskId, setScanTaskId] = useState<string | null>(() => loadState('scanTaskId', null));
    const [deployTaskId, setDeployTaskId] = useState<string | null>(() => loadState('deployTaskId', null));
    const [deployResults, setDeployResults] = useState<DeployResult[]>([]);
    const [error, setError] = useState<string | null>(null);
    const [scanProgress, setScanProgress] = useState('Initializing scan...');
    const [scanLogs, setScanLogs] = useState<string[]>([]);
    const [expandedEnv, setExpandedEnv] = useState<number | null>(null);
    const [servers, setServers] = useState<any[]>([]);
    const [availableRepos, setAvailableRepos] = useState<any[]>([]);
    const [selectedRepos, setSelectedRepos] = useState<string[]>(() => loadState('selectedRepos', []));
    const [viewMode, setViewMode] = useState<'canvas' | 'list'>('canvas');
    const [useSharedAddons, setUseSharedAddons] = useState<boolean>(() => loadState('useSharedAddons', true));
    const [cancelOthersOnFailure, setCancelOthersOnFailure] = useState<boolean>(() => loadState('cancelOthersOnFailure', false));
    const [sharedAddonConfig, setSharedAddonConfig] = useState<SharedAddonConfig>(() => loadState('sharedAddonConfig', {}));

    const [aiProviders, setAiProviders] = useState<any[]>([]);
    const [selectedProvider, setSelectedProvider] = useState<string>(() => loadState('aiProvider', 'auto'));

    // Bulk env dialog
    const [bulkEnvOpen, setBulkEnvOpen] = useState(false);

    // Derive app list from plan for bulk env dialog
    const ecosystemApps = plan?.services.map(s => ({
        id: s.repo || s.name || `svc-${Math.random().toString(36).slice(2, 8)}`,
        name: s.name || s.repo,
        repo: s.repo,
        stack: s.stack,
    })) || [];

    // Deep scan states
    const [isDeepScanning, setIsDeepScanning] = useState(false);
    const [deepScanProgress, setDeepScanProgress] = useState('');
    const [deepScanResult, setDeepScanResult] = useState<any>(null);

    useEffect(() => {
        apiGet('/api/v1/servers/').then(data => {
            setServers(data.results || data || []);
        }).catch(() => {});

        apiGet('/api/v1/cloud/intelligence/providers/').then(data => {
            setAiProviders(data || []);
        }).catch(() => {});
    }, []);

    // Persist state to localStorage
    useEffect(() => { saveState('step', step); }, [step]);
    useEffect(() => { saveState('plan', plan); }, [plan]);
    useEffect(() => { saveState('planId', planId); }, [planId]);
    useEffect(() => { saveState('useSharedAddons', useSharedAddons); }, [useSharedAddons]);
    useEffect(() => { saveState('cancelOthersOnFailure', cancelOthersOnFailure); }, [cancelOthersOnFailure]);
    useEffect(() => { saveState('sharedAddonConfig', sharedAddonConfig); }, [sharedAddonConfig]);
    useEffect(() => { saveState('scanTaskId', scanTaskId); }, [scanTaskId]);
    useEffect(() => { saveState('deployTaskId', deployTaskId); }, [deployTaskId]);
    useEffect(() => { saveState('selectedRepos', selectedRepos); }, [selectedRepos]);
    useEffect(() => { saveState('aiProvider', selectedProvider); }, [selectedProvider]);

    // Check for active plan on mount — always runs so that returning
    // after page navigation properly resumes scanning/deploying.
    useEffect(() => {
        const checkActivePlan = async () => {
            // Don't override 'review' or 'done' — we have all data locally
            if (step === 'review' || step === 'done') return;

            try {
                const data = await ecosystemApi.getActivePlan();
                if (!data.has_active_plan) {
                    // Server has no record but our local session says we're
                    // mid-flow — the plan was likely cleaned up or failed.
                    if (step === 'scanning' || step === 'deploying') {
                        setError('Previous scan was interrupted. Please start a new scan.');
                        setStep('selection');
                    }
                    return;
                }

                setPlanId(data.plan_id);
                setSelectedRepos(data.selected_repos || []);
                setSelectedProvider(data.ai_provider || 'auto');

                if (data.status === 'scanning' && data.scan_task_id) {
                    setScanTaskId(data.scan_task_id);
                    setStep('scanning');
                    if (data.scan_progress) {
                        setScanProgress(data.scan_progress);
                        setScanLogs([data.scan_progress]);
                    }
                    pollTask(data.scan_task_id, (result: any) => {
                        if (result.error) {
                            setError(result.error);
                            setStep('selection');
                        } else {
                            setPlan(result);
                            setStep('review');
                        }
                    });
                } else if (data.status === 'review' && data.plan) {
                    setPlan(data.plan);
                    setStep('review');
                } else if (data.status === 'deploying' && data.deploy_task_id) {
                    setDeployTaskId(data.deploy_task_id);
                    setPlan(data.plan);
                    setStep('deploying');
                    pollTask(data.deploy_task_id, (result: any) => {
                        if (result.error) {
                            setError(result.error);
                            setStep('review');
                        } else {
                            setDeployResults(result.services || []);
                            setStep('done');
                        }
                    });
                }
            } catch {
                // Server unreachable — keep local state as-is
            }
        };
        checkActivePlan();
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    // Poll for scan task completion
    const pollTask = useCallback(async (taskId: string, onComplete: (result: any) => void) => {
        let retries = 0;
        const MAX_RETRIES = 900; // 900 * 2s ≈ 30 min (matches Celery soft_time_limit)
        const poll = async () => {
            try {
                const data = await ecosystemApi.getTaskStatus(taskId);
                if (data.status === 'SUCCESS' && data.result) {
                    onComplete(data.result);
                } else if (data.status === 'FAILURE') {
                    setError(data.error || data.result?.error || 'Task failed');
                    setStep('selection');
                } else {
                    retries++;
                    if (retries > MAX_RETRIES) {
                        setError(
                            'Scan is taking too long and may have been interrupted. '
                            + 'Please start a new scan.'
                        );
                        setStep('selection');
                        return;
                    }
                    // Show persisted progress (survives page navigation) if available
                    if (data.scan_progress) {
                        setScanProgress(data.scan_progress);
                        setScanLogs(prev => {
                            if (prev.length === 0 || prev[prev.length - 1] !== data.scan_progress) {
                                return [...prev, data.scan_progress];
                            }
                            return prev;
                        });
                    } else if (data.status === 'PROGRESS' || data.result?.state) {
                         const msg = data.result?.state || 'Scanning in progress...';
                         setScanProgress(msg);
                         setScanLogs(prev => {
                             if (prev[prev.length - 1] !== msg) return [...prev, msg];
                             return prev;
                         });
                    }
                    setTimeout(poll, 2000);
                }
            } catch {
                retries++;
                if (retries > MAX_RETRIES) {
                    setError(
                        'Scan is taking too long and may have been interrupted. '
                        + 'Please start a new scan.'
                    );
                    setStep('selection');
                    return;
                }
                setTimeout(poll, 3000);
            }
        };
        poll();
    }, []);

    // Poll for deep scan task
    const pollDeepScanTask = useCallback(async (taskId: string, onComplete: (result: any) => void) => {
        const poll = async () => {
            try {
                const data = await ecosystemApi.getDeepScanStatus(taskId);
                if (data.status === 'SUCCESS' && data.result) {
                    onComplete(data.result);
                } else if (data.status === 'FAILURE') {
                    setError(data.error || data.result?.error || 'Deep scan failed');
                    setIsDeepScanning(false);
                } else {
                    if (data.status === 'PROGRESS' || data.result?.state) {
                         setDeepScanProgress(data.result?.state || 'Scanning in progress...');
                    }
                    setTimeout(poll, 2000);
                }
            } catch {
                setTimeout(poll, 3000);
            }
        };
        poll();
    }, []);

    // Fetch repos for selection
    const fetchRepos = async () => {
        setStep('scanning'); // Temporary state for loading
        setError(null);
        setScanProgress('Fetching your GitHub repositories...');
        setScanLogs(['Fetching your GitHub repositories...']);
        
        try {
            const data = await apiGet('/api/v1/integrations/github/repos/?per_page=100');
            setAvailableRepos(data.repos || []);
            // Default select all
            setSelectedRepos((data.repos || []).map((r: any) => r.full_name));
            setStep('selection');
        } catch (err: any) {
            setError(err.message || 'Failed to fetch repositories');
            setStep('idle');
        }
    };

    // Start scan
    const startScan = async () => {
        setStep('scanning');
        setError(null);
        setPlan(null);
        setScanProgress('Initializing batch processing...');
        setScanLogs(['Initializing batch processing...']);

        try {
            const data = await ecosystemApi.startScan({
                ai_provider: selectedProvider,
                selected_repos: selectedRepos,
            });
            setScanTaskId(data.task_id);
            if (data.plan_id) setPlanId(data.plan_id);
            
            // Note: The UI now polls the task status. The backend can optionally provide 
            // progress updates by returning custom state, or we just rely on generic messages.
            const messages = [
                'Chunking repositories into batches...',
                'Analyzing files and detecting stacks...',
                'Evaluating dependencies...',
                'Running AI Synthesis pass...',
                'Finalizing ecosystem architecture...',
            ];
            let i = 0;
            const interval = setInterval(() => {
                if (i < messages.length) {
                    const msg = messages[i];
                    setScanProgress(msg);
                    setScanLogs(prev => {
                        if (prev[prev.length - 1] !== msg) return [...prev, msg];
                        return prev;
                    });
                    i++;
                }
            }, 5000);

            pollTask(data.task_id, (result) => {
                clearInterval(interval);
                if (result.error) {
                    setError(result.error);
                    setStep('selection');
                } else {
                    setPlan(result);
                    setStep('review');
                }
            });
        } catch (err: any) {
            setError(err.message || 'Failed to start scan');
            setStep('selection');
        }
    };

    // Deep codebase scan
    const startDeepScan = async () => {
        if (!plan) return;
        setIsDeepScanning(true);
        setError(null);
        setDeepScanResult(null);
        setDeepScanProgress('Initializing deep codebase scan...');
        
        // Convert plan to simplified repos_data structure that deep scan expects
        const reposData = plan.services.map((s: any) => ({ repo: s.repo, stack: s.stack }));

        try {
            const data = await ecosystemApi.startDeepScan({
                ai_provider: selectedProvider,
                repos_data: reposData,
                deploy_plan: plan
            });
            
            pollDeepScanTask(data.task_id, (result) => {
                setIsDeepScanning(false);
                if (result.error) {
                    setError(result.error);
                } else {
                    setDeepScanResult(result);
                }
            });
        } catch (err: any) {
            setError(err.message || 'Failed to start deep scan');
            setIsDeepScanning(false);
        }
    };

    // Deploy all
    const deployAll = async () => {
        if (!plan) return;
        setStep('deploying');
        setError(null);

        try {
            const data = await ecosystemApi.deploy({
                plan,
                plan_id: planId,
                use_shared_addons: useSharedAddons,
                cancel_others_on_failure: cancelOthersOnFailure,
                shared_addon_config: sharedAddonConfig,
            });
            setDeployTaskId(data.task_id);
            if (data.plan_id) setPlanId(data.plan_id);

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
    const updateEnvVar = (idx: number, key: string, value: string) => {
        if (!plan) return;
        const newServices = [...plan.services];
        const newEnv = { ...newServices[idx].env_vars };
        newEnv[key] = value;
        newServices[idx] = { ...newServices[idx], env_vars: newEnv };
        setPlan({ ...plan, services: newServices });
    };

    const updateServer = (idx: number, serverId: string) => {
        if (!plan) return;
        const newServices = [...plan.services];
        newServices[idx] = { ...newServices[idx], server_id: serverId };
        setPlan({ ...plan, services: newServices });
    };


    const downloadEnv = async () => {
        try {
            const blob = await ecosystemApi.downloadEnv();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'ecosystem-env.json';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        } catch (err: any) {
            setError(err.message || 'Failed to download env vars');
        }
    };

    const [syncing, setSyncing] = useState(false);
    const syncHealth = async () => {
        setSyncing(true);
        try {
            await apiPost('/api/v1/servers/health_check/');
            // Also trigger the background fix
            const data = await apiGet('/api/v1/servers/');
            setServers(data.results || data || []);
            alert('Fleet health check triggered. Nodes will update shortly.');
        } catch (err: any) {
            setError('Failed to sync health: ' + err.message);
        } finally {
            setSyncing(false);
        }
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
                            <div className="flex items-center gap-2">
                                {plan && (
                                    <>
                                        <button
                                            onClick={downloadEnv}
                                            className="text-sm text-muted-foreground hover:text-foreground flex items-center gap-1"
                                        >
                                            <Download size={14} /> Download .env.json
                                        </button>
                                        <button
                                            onClick={() => setBulkEnvOpen(true)}
                                            className="text-sm text-muted-foreground hover:text-foreground flex items-center gap-1"
                                        >
                                            <Variable size={14} /> Bulk Env Update
                                        </button>
                                    </>
                                )}
                                <button
                                    onClick={() => { clearState(); setStep('idle'); setPlan(null); setPlanId(''); setError(null); setUseSharedAddons(true); setCancelOthersOnFailure(false); setSharedAddonConfig({}); }}
                                    className="text-sm text-muted-foreground hover:text-foreground flex items-center gap-1"
                                >
                                    <RefreshCw size={14} /> Start Over
                                </button>
                            </div>
                        )}
                    </div>

                    {/* Cached Scan Card */}
                    <CachedScanCard />

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
                            <div className="max-w-md mx-auto mb-10 space-y-4">
                                <div className="flex items-center justify-between px-2">
                                    <span className="text-sm font-semibold text-muted-foreground uppercase tracking-wider">AI Intelligence Senate</span>
                                    <span className="text-[10px] text-emerald-500 font-bold bg-emerald-500/10 px-2 py-0.5 rounded-full uppercase">Layer 5 Security</span>
                                </div>
                                <div className="grid grid-cols-1 gap-2">
                                    <button
                                        onClick={() => setSelectedProvider('auto')}
                                        className={`flex items-center justify-between p-4 rounded-xl border transition-all ${selectedProvider === 'auto'
                                                ? 'bg-emerald-500/10 border-emerald-500/50 shadow-lg shadow-emerald-500/10 ring-1 ring-emerald-500/20'
                                                : 'bg-card border-border hover:border-emerald-500/30'
                                            }`}
                                    >
                                        <div className="flex items-center gap-3">
                                            <div className={`p-2 rounded-lg ${selectedProvider === 'auto' ? 'bg-emerald-500/20 text-emerald-500' : 'bg-muted text-muted-foreground'}`}>
                                                <Zap size={20} />
                                            </div>
                                            <div className="text-left">
                                                <p className="font-bold text-sm">Automated Consensus</p>
                                                <p className="text-xs text-muted-foreground">Highest accuracy: combines all available AI providers</p>
                                            </div>
                                        </div>
                                        {selectedProvider === 'auto' && <CheckCircle2 size={16} className="text-emerald-500" />}
                                    </button>

                                    {aiProviders.filter(p => p.configured).map((provider) => (
                                        <button
                                            key={provider.id}
                                            onClick={() => setSelectedProvider(provider.id)}
                                            className={`flex items-center justify-between p-4 rounded-xl border transition-all ${selectedProvider === provider.id
                                                    ? 'bg-primary/10 border-primary/50 shadow-lg shadow-primary/10 ring-1 ring-primary/20'
                                                    : 'bg-card border-border hover:border-primary/30'
                                                }`}
                                        >
                                            <div className="flex items-center gap-3">
                                                <div className={`p-2 rounded-lg ${selectedProvider === provider.id ? 'bg-primary/20 text-primary' : 'bg-muted text-muted-foreground'}`}>
                                                    <Sparkles size={20} />
                                                </div>
                                                <div className="text-left">
                                                    <p className="font-bold text-sm">{provider.name}</p>
                                                    <p className="text-xs text-muted-foreground">Model: {provider.model || 'Standard'}</p>
                                                </div>
                                            </div>
                                            {selectedProvider === provider.id && <CheckCircle2 size={16} className="text-primary" />}
                                        </button>
                                    ))}

                                    {aiProviders.filter(p => !p.configured).length > 0 && (
                                        <div className="pt-2 text-[10px] text-muted-foreground italic px-2">
                                            Missing keys? Configure OpenAI, Anthropic, or Gemini in <Link href="/settings" className="text-primary hover:underline">System Settings</Link> to unlock deeper analysis.
                                        </div>
                                    )}
                                </div>
                            </div>

                            <motion.button
                                whileHover={{ scale: 1.05 }}
                                whileTap={{ scale: 0.95 }}
                                onClick={fetchRepos}
                                className="btn-shimmer px-10 py-4 rounded-xl bg-gradient-to-r from-emerald-500 to-teal-600 text-white font-bold shadow-lg shadow-emerald-500/25 flex items-center gap-3 mx-auto text-lg"
                            >
                                <Scan size={24} />
                                Begin Ecosystem Discovery
                            </motion.button>
                        </motion.div>
                    )}

                    {/* Plan History */}
                    {step === 'idle' && (
                        <PlanHistorySection />
                    )}

                    {/* Step: Selection */}
                    {step === 'selection' && (
                        <motion.div
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            className="space-y-6"
                        >
                            <div className="text-center py-6">
                                <h2 className="text-2xl font-bold mb-2">Select Repositories</h2>
                                <p className="text-muted-foreground">
                                    Choose the repositories you want to include in this ecosystem deploy.
                                </p>
                            </div>

                            <div className="bg-card border border-border rounded-xl p-4 max-h-[60vh] overflow-y-auto">
                                <div className="flex justify-between items-center mb-4 pb-2 border-b">
                                    <span className="font-semibold">{selectedRepos.length} / {availableRepos.length} selected</span>
                                    <div className="space-x-2">
                                        <button 
                                            onClick={() => setSelectedRepos(availableRepos.map(r => r.full_name))}
                                            className="text-xs px-3 py-1 bg-muted rounded hover:bg-muted/80"
                                        >
                                            Select All
                                        </button>
                                        <button 
                                            onClick={() => setSelectedRepos([])}
                                            className="text-xs px-3 py-1 bg-muted rounded hover:bg-muted/80"
                                        >
                                            Clear
                                        </button>
                                    </div>
                                </div>
                                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                                    {availableRepos.map((repo) => (
                                        <label key={repo.full_name} className="flex items-start gap-3 p-3 border rounded-lg hover:bg-muted/30 cursor-pointer">
                                            <input 
                                                type="checkbox" 
                                                className="mt-1"
                                                checked={selectedRepos.includes(repo.full_name)}
                                                onChange={(e) => {
                                                    if (e.target.checked) {
                                                        setSelectedRepos([...selectedRepos, repo.full_name]);
                                                    } else {
                                                        setSelectedRepos(selectedRepos.filter(r => r !== repo.full_name));
                                                    }
                                                }}
                                            />
                                            <div className="overflow-hidden">
                                                <p className="font-medium truncate" title={repo.full_name}>{repo.full_name}</p>
                                                {repo.description && <p className="text-xs text-muted-foreground truncate">{repo.description}</p>}
                                                <div className="flex gap-2 mt-1">
                                                    {repo.language && <span className="text-[10px] px-1.5 rounded bg-muted">{repo.language}</span>}
                                                    {repo.private ? <span className="text-[10px] px-1.5 rounded bg-yellow-500/10 text-yellow-500">Private</span> : <span className="text-[10px] px-1.5 rounded bg-emerald-500/10 text-emerald-500">Public</span>}
                                                </div>
                                            </div>
                                        </label>
                                    ))}
                                </div>
                            </div>

                            <div className="flex justify-center pt-4">
                                <motion.button
                                    whileHover={{ scale: 1.05 }}
                                    whileTap={{ scale: 0.95 }}
                                    onClick={startScan}
                                    disabled={selectedRepos.length === 0}
                                    className="btn-shimmer px-10 py-4 rounded-xl bg-gradient-to-r from-emerald-500 to-teal-600 text-white font-bold shadow-lg shadow-emerald-500/25 flex items-center gap-3 text-lg disabled:opacity-50 disabled:cursor-not-allowed"
                                >
                                    <Scan size={22} />
                                    Analyze Selected Repos
                                    <ArrowRight size={18} />
                                </motion.button>
                            </div>
                        </motion.div>
                    )}

                    {/* Step: Scanning */}
                    {step === 'scanning' && (
                        <motion.div
                            initial={{ opacity: 0, scale: 0.95 }}
                            animate={{ opacity: 1, scale: 1 }}
                            className="max-w-2xl mx-auto space-y-6"
                        >
                            <div className="text-center">
                                <div className="w-20 h-20 mx-auto mb-4 rounded-3xl bg-gradient-to-br from-blue-500/20 to-cyan-500/10 flex items-center justify-center border border-blue-500/20">
                                    <Loader2 className="text-blue-500 animate-spin" size={32} />
                                </div>
                                <h2 className="text-2xl font-bold mb-2">Scanning Your Repositories</h2>
                                <p className="text-muted-foreground">{scanProgress}</p>
                            </div>

                            <div className="bg-zinc-950 border border-zinc-800 rounded-xl overflow-hidden font-mono text-sm shadow-2xl">
                                <div className="flex items-center gap-2 px-4 py-3 border-b border-zinc-800 bg-zinc-900/50">
                                    <Terminal size={16} className="text-zinc-500" />
                                    <span className="text-zinc-400 font-medium">System Output</span>
                                    <div className="flex gap-1.5 ml-auto">
                                        <div className="w-3 h-3 rounded-full bg-red-500/20 border border-red-500/50"></div>
                                        <div className="w-3 h-3 rounded-full bg-yellow-500/20 border border-yellow-500/50"></div>
                                        <div className="w-3 h-3 rounded-full bg-green-500/20 border border-green-500/50"></div>
                                    </div>
                                </div>
                                <div className="p-4 h-64 overflow-y-auto flex flex-col justify-end">
                                    <div className="space-y-2">
                                        <AnimatePresence initial={false}>
                                            {scanLogs.map((log, index) => (
                                                <motion.div
                                                    key={index}
                                                    initial={{ opacity: 0, x: -10 }}
                                                    animate={{ opacity: 1, x: 0 }}
                                                    className="flex items-start gap-3"
                                                >
                                                    <span className="text-zinc-600 shrink-0">
                                                        [{new Date().toLocaleTimeString([], { hour12: false })}]
                                                    </span>
                                                    <span className={`flex-1 ${index === scanLogs.length - 1 ? 'text-blue-400 animate-pulse' : 'text-zinc-300'}`}>
                                                        {log}
                                                    </span>
                                                </motion.div>
                                            ))}
                                        </AnimatePresence>
                                    </div>
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
                                <div className="bg-card border border-border p-4 rounded-xl relative overflow-hidden group">
                                    <p className="text-xs text-muted-foreground uppercase tracking-wider font-bold">AI Intelligence</p>
                                    <div className="flex items-baseline gap-2">
                                        <p className="text-2xl font-bold mt-1 text-primary">{plan.ai_provider?.split(' ')[0] || 'Heuristic'}</p>
                                        <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded uppercase ${
                                            plan.ai_provider && plan.ai_provider !== 'Heuristic' && !plan.ai_provider.includes('Mock') 
                                            ? 'bg-emerald-500/10 text-emerald-500' 
                                            : 'bg-yellow-500/10 text-yellow-500'
                                        }`}>
                                            {plan.ai_provider && plan.ai_provider !== 'Heuristic' && !plan.ai_provider.includes('Mock') ? 'Active' : 'Heuristic'}
                                        </span>
                                    </div>
                                    <p className="text-[10px] text-muted-foreground truncate mt-0.5">{plan.ai_provider || 'Local Heuristics Only'}</p>
                                    <Sparkles size={40} className="absolute -right-2 -bottom-2 text-primary/5 group-hover:text-primary/10 transition-colors" />
                                </div>
                            </div>

                            {/* Deep Scan Section */}
                            <div className="bg-card border border-border p-5 rounded-xl">
                                <div className="flex items-center justify-between mb-4">
                                    <h3 className="font-bold text-sm uppercase tracking-wider text-muted-foreground flex items-center gap-2">
                                        <Code size={14} /> Deep Codebase Verification
                                    </h3>
                                    {!isDeepScanning && !deepScanResult && (
                                        <button 
                                            onClick={startDeepScan}
                                            className="text-xs px-3 py-1.5 bg-emerald-500/10 text-emerald-500 border border-emerald-500/30 rounded-lg hover:bg-emerald-500/20 font-medium"
                                        >
                                            Verify Env Vars vs Actual Code
                                        </button>
                                    )}
                                </div>

                                {isDeepScanning && (
                                    <div className="flex flex-col items-center justify-center py-6 gap-3">
                                        <Loader2 className="animate-spin text-primary" size={24} />
                                        <p className="text-sm font-medium animate-pulse">{deepScanProgress}</p>
                                    </div>
                                )}

                                {deepScanResult && (
                                    <div className="space-y-4">
                                        <div className={`p-4 rounded-lg border ${deepScanResult.verification?.is_valid ? 'bg-emerald-500/5 border-emerald-500/30' : 'bg-red-500/5 border-red-500/30'}`}>
                                            <div className="flex items-center gap-2 mb-2">
                                                {deepScanResult.verification?.is_valid ? (
                                                    <CheckCircle size={18} className="text-emerald-500" />
                                                ) : (
                                                    <AlertTriangle size={18} className="text-red-500" />
                                                )}
                                                <h4 className={`font-bold ${deepScanResult.verification?.is_valid ? 'text-emerald-500' : 'text-red-500'}`}>
                                                    {deepScanResult.verification?.is_valid ? 'Architecture & Env Vars Verified' : 'Discrepancies Detected'}
                                                </h4>
                                            </div>
                                            
                                            {deepScanResult.verification?.missing_env_vars?.length > 0 && (
                                                <div className="mt-3">
                                                    <p className="text-sm font-semibold mb-1">Missing Environment Variables in Plan:</p>
                                                    <ul className="text-sm space-y-1 text-muted-foreground list-disc list-inside">
                                                        {deepScanResult.verification.missing_env_vars.map((missing: any, i: number) => (
                                                            <li key={i}>
                                                                <span className="font-mono text-xs">{missing.env_key}</span> ({missing.service_name}): {missing.reason}
                                                            </li>
                                                        ))}
                                                    </ul>
                                                </div>
                                            )}

                                            {deepScanResult.verification?.architectural_warnings?.length > 0 && (
                                                <div className="mt-3">
                                                    <p className="text-sm font-semibold mb-1">Architectural Warnings:</p>
                                                    <ul className="text-sm space-y-1 text-muted-foreground list-disc list-inside">
                                                        {deepScanResult.verification.architectural_warnings.map((warn: string, i: number) => (
                                                            <li key={i}>{warn}</li>
                                                        ))}
                                                    </ul>
                                                </div>
                                            )}

                                            {deepScanResult.verification?.fixed_deploy_plan && !deepScanResult.verification?.is_valid && (
                                                <div className="mt-4 pt-3 border-t border-border flex justify-end">
                                                    <button
                                                        onClick={() => {
                                                            setPlan(deepScanResult.verification.fixed_deploy_plan);
                                                            setDeepScanResult({
                                                                ...deepScanResult,
                                                                verification: {
                                                                    ...deepScanResult.verification,
                                                                    is_valid: true // mark as fixed
                                                                }
                                                            });
                                                        }}
                                                        className="px-4 py-2 bg-emerald-500 hover:bg-emerald-600 text-white text-sm font-bold rounded-lg transition-colors flex items-center gap-2 shadow-lg shadow-emerald-500/20"
                                                    >
                                                        <Sparkles size={16} />
                                                        Apply AI Fixes to Plan
                                                    </button>
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                )}
                            </div>

                            {/* View Toggle */}
                            <div className="flex justify-end">
                                <div className="bg-muted p-1 rounded-lg flex items-center gap-1 border border-border">
                                    <button 
                                        onClick={() => setViewMode('canvas')}
                                        className={`text-xs px-3 py-1.5 rounded-md transition-all ${viewMode === 'canvas' ? 'bg-background shadow font-bold text-foreground' : 'text-muted-foreground hover:text-foreground hover:bg-background/50'}`}
                                    >
                                        Topology Canvas
                                    </button>
                                    <button 
                                        onClick={() => setViewMode('list')}
                                        className={`text-xs px-3 py-1.5 rounded-md transition-all ${viewMode === 'list' ? 'bg-background shadow font-bold text-foreground' : 'text-muted-foreground hover:text-foreground hover:bg-background/50'}`}
                                    >
                                        List View
                                    </button>
                                </div>
                            </div>

                            {viewMode === 'canvas' ? (
                                <div className="space-y-3">
                                    {/* Services Topology Canvas */}
                                    <div className="flex items-center justify-between mb-2">
                                        <h3 className="font-bold text-sm uppercase tracking-wider text-muted-foreground flex items-center gap-2">
                                            <Server size={14} /> Services Topology
                                        </h3>
                                        <button
                                            onClick={syncHealth}
                                            disabled={syncing}
                                            className="text-[10px] flex items-center gap-1.5 px-2 py-1 rounded bg-muted hover:bg-muted/80 text-muted-foreground transition-colors"
                                            title="Synchronize health and tokens across all nodes"
                                        >
                                            <RefreshCw size={10} className={syncing ? 'animate-spin' : ''} />
                                            {syncing ? 'Syncing...' : 'Sync Fleet Health'}
                                        </button>
                                    </div>
                                    {/* Shared Addons + Fail-Fast (Canvas View) */}
                                    <div className="bg-card border border-border p-4 rounded-xl space-y-3">
                                        <div className="flex items-center justify-between">
                                            <div>
                                                <h3 className="font-bold text-sm flex items-center gap-2">
                                                    <Database size={14} className="text-purple-500" /> Addon Sharing
                                                </h3>
                                                <p className="text-xs text-muted-foreground mt-0.5">
                                                    {useSharedAddons ? 'Global: provisioned once, shared across services' : 'Global: each service provisions its own'}
                                                </p>
                                            </div>
                                            <button
                                                onClick={() => {
                                                    const next = !useSharedAddons;
                                                    setUseSharedAddons(next);
                                                    setSharedAddonConfig({});
                                                }}
                                                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                                                    useSharedAddons ? 'bg-emerald-500' : 'bg-muted'
                                                }`}
                                            >
                                                <span className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                                                    useSharedAddons ? 'translate-x-6' : 'translate-x-1'
                                                }`} />
                                            </button>
                                        </div>
                                        {plan.addons && plan.addons.length > 0 && (
                                            <div className="flex flex-wrap gap-1.5">
                                                {plan.addons.map((addon) => {
                                                    const cfg = sharedAddonConfig[addon.type];
                                                    const isShared = cfg !== undefined ? cfg.shared : useSharedAddons;
                                                    return (
                                                        <button
                                                            key={addon.type}
                                                            onClick={() => setSharedAddonConfig(prev => ({ ...prev, [addon.type]: { shared: !isShared } }))}
                                                            className={`text-[11px] px-2 py-1 rounded-md border transition-colors ${
                                                                isShared
                                                                    ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'
                                                                    : 'bg-yellow-500/10 border-yellow-500/30 text-yellow-400'
                                                            }`}
                                                            title={`Click to toggle ${addon.type} sharing`}
                                                        >
                                                            {addon.type}: {isShared ? 'shared' : 'individual'}
                                                        </button>
                                                    );
                                                })}
                                            </div>
                                        )}
                                        <div className="flex items-center justify-between pt-1 border-t border-border/50">
                                            <div>
                                                <h3 className="font-bold text-sm flex items-center gap-2">
                                                    <AlertTriangle size={13} className="text-amber-500" /> Fail-Fast
                                                </h3>
                                                <p className="text-[10px] text-muted-foreground">
                                                    {cancelOthersOnFailure ? 'Cancel all on any failure' : 'Continue unrelated on failure'}
                                                </p>
                                            </div>
                                            <button
                                                onClick={() => setCancelOthersOnFailure(!cancelOthersOnFailure)}
                                                className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
                                                    cancelOthersOnFailure ? 'bg-amber-500' : 'bg-muted'
                                                }`}
                                            >
                                                <span className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${
                                                    cancelOthersOnFailure ? 'translate-x-5' : 'translate-x-0.5'
                                                }`} />
                                            </button>
                                        </div>
                                    </div>
                                    <TopologyCanvas 
                                        plan={plan} 
                                        servers={servers}
                                        callbacks={{
                                            updateServer,
                                            toggleSkip,
                                            updateEnvVar,
                                            handlePasteEnv
                                        }}
                                    />
                                </div>
                            ) : (
                                <>
                                    {/* Shared Addons Toggle */}
                                    <div className="bg-card border border-border p-5 rounded-xl">
                                        <div className="flex items-center justify-between mb-3">
                                            <h3 className="font-bold text-sm uppercase tracking-wider text-muted-foreground flex items-center gap-2">
                                                <Database size={14} /> Addon Sharing
                                            </h3>
                                            <button
                                                onClick={() => {
                                                    const next = !useSharedAddons;
                                                    setUseSharedAddons(next);
                                                    // When toggling global, clear per-addon overrides
                                                    setSharedAddonConfig({});
                                                }}
                                                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                                                    useSharedAddons ? 'bg-emerald-500' : 'bg-muted'
                                                }`}
                                            >
                                                <span className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                                                    useSharedAddons ? 'translate-x-6' : 'translate-x-1'
                                                }`} />
                                            </button>
                                        </div>
                                        <p className="text-xs text-muted-foreground mb-3">
                                            {useSharedAddons
                                                ? 'Global: addons are provisioned once and shared. Override per addon below.'
                                                : 'Global: each service provisions its own addons. Override per addon below.'}
                                        </p>

                                        {/* Per-addon overrides */}
                                        {plan.addons && plan.addons.length > 0 && (
                                            <div className="space-y-2 mt-3">
                                                {plan.addons.map((addon) => {
                                                    const cfg = sharedAddonConfig[addon.type];
                                                    const isShared = cfg !== undefined ? cfg.shared : useSharedAddons;
                                                    return (
                                                        <div key={addon.type} className="flex items-center justify-between p-2.5 rounded-lg border border-border/60 bg-background/50">
                                                            <div className="flex items-center gap-2.5">
                                                                <Database size={12} className="text-purple-400" />
                                                                <span className="text-sm font-medium">{addon.type}</span>
                                                                {isShared ? (
                                                                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">shared</span>
                                                                ) : (
                                                                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-yellow-500/10 text-yellow-400 border border-yellow-500/20">individual</span>
                                                                )}
                                                                {addon.shared_by?.length > 0 && isShared && (
                                                                    <span className="text-[10px] text-muted-foreground">
                                                                        by {addon.shared_by.join(', ')}
                                                                    </span>
                                                                )}
                                                            </div>
                                                            <button
                                                                onClick={() => {
                                                                    setSharedAddonConfig(prev => ({
                                                                        ...prev,
                                                                        [addon.type]: { shared: !isShared },
                                                                    }));
                                                                }}
                                                                className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
                                                                    isShared ? 'bg-emerald-500' : 'bg-muted'
                                                                }`}
                                                            >
                                                                <span className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${
                                                                    isShared ? 'translate-x-5' : 'translate-x-0.5'
                                                                }`} />
                                                            </button>
                                                        </div>
                                                    );
                                                })}
                                            </div>
                                        )}
                                    </div>

                                    {/* Cancel Others on Failure */}
                                    <div className="bg-card border border-border p-5 rounded-xl">
                                        <div className="flex items-center justify-between">
                                            <div>
                                                <h3 className="font-bold text-sm flex items-center gap-2">
                                                    <AlertTriangle size={14} className="text-amber-500" /> Fail-Fast Mode
                                                </h3>
                                                <p className="text-xs text-muted-foreground mt-0.5">
                                                    {cancelOthersOnFailure
                                                        ? 'If any service fails, all remaining queued deployments are cancelled.'
                                                        : 'Failed services are retried; unrelated services continue deploying.'}
                                                </p>
                                            </div>
                                            <button
                                                onClick={() => setCancelOthersOnFailure(!cancelOthersOnFailure)}
                                                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                                                    cancelOthersOnFailure ? 'bg-amber-500' : 'bg-muted'
                                                }`}
                                            >
                                                <span className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                                                    cancelOthersOnFailure ? 'translate-x-6' : 'translate-x-1'
                                                }`} />
                                            </button>
                                        </div>
                                    </div>

                                    {/* Services List */}
                                    <div className="space-y-3">
                                        <div className="flex items-center justify-between mb-2">
                                            <h3 className="font-bold text-sm uppercase tracking-wider text-muted-foreground flex items-center gap-2">
                                                <Server size={14} /> Services to Deploy
                                            </h3>
                                            <button
                                                onClick={syncHealth}
                                                disabled={syncing}
                                                className="text-[10px] flex items-center gap-1.5 px-2 py-1 rounded bg-muted hover:bg-muted/80 text-muted-foreground transition-colors"
                                                title="Synchronize health and tokens across all nodes"
                                            >
                                                <RefreshCw size={10} className={syncing ? 'animate-spin' : ''} />
                                                {syncing ? 'Syncing...' : 'Sync Fleet Health'}
                                            </button>
                                        </div>

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
                                                        <div className="flex items-center gap-2">
                                                            <Server size={12} className="text-muted-foreground" />
                                                            <select
                                                                value={svc.server_id || 'local'}
                                                                onChange={(e) => updateServer(idx, e.target.value)}
                                                                className="text-[10px] bg-background border border-border rounded px-2 py-1 outline-none focus:border-primary transition-colors min-w-[120px]"
                                                            >
                                                                <option value="local">Local Server</option>
                                                                {servers.map(s => (
                                                                    <option key={s.id} value={s.id}>{s.name} ({s.host})</option>
                                                                ))}
                                                            </select>
                                                        </div>
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
                                </>
                            )}

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
                            <div className="text-center py-6">
                                <div className="w-20 h-20 mx-auto mb-4 rounded-3xl bg-emerald-500/20 flex items-center justify-center border border-emerald-500/20">
                                    <CheckCircle2 className="text-emerald-500" size={36} />
                                </div>
                                <h2 className="text-2xl font-bold mb-2">Ecosystem Deployment Queued!</h2>
                                <p className="text-muted-foreground">
                                    All services have been created and builds are running in dependency order.
                                </p>
                            </div>

                            {/* Summary Stats */}
                            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                                {[
                                    { label: 'Total Services', value: deployResults.length, color: 'text-foreground' },
                                    { label: 'Queued / Building', value: deployResults.filter(r => r.status === 'queued').length, color: 'text-emerald-500' },
                                    { label: 'Skipped', value: deployResults.filter(r => r.status === 'skipped').length, color: 'text-yellow-500' },
                                    { label: 'Failed', value: deployResults.filter(r => r.status === 'failed').length, color: 'text-red-500' },
                                ].map(stat => (
                                    <div key={stat.label} className="bg-card border border-border rounded-xl p-4 text-center">
                                        <p className={`text-2xl font-bold ${stat.color}`}>{stat.value}</p>
                                        <p className="text-xs text-muted-foreground mt-1">{stat.label}</p>
                                    </div>
                                ))}
                            </div>

                            {/* Addons created from plan */}
                            {plan?.addons && plan.addons.length > 0 && (
                                <div className="bg-card border border-border rounded-xl p-4">
                                    <h3 className="font-bold text-sm uppercase tracking-wider text-muted-foreground flex items-center gap-2 mb-3">
                                        <Database size={14} /> Addons Provisioned
                                    </h3>
                                    <div className="flex flex-wrap gap-2">
                                        {plan.addons.map((a, i) => (
                                            <span key={i} className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-purple-500/10 text-purple-400 border border-purple-500/20">
                                                <Database size={12} />
                                                {a.type}
                                                {a.shared_by?.length > 0 && (
                                                    <span className="opacity-60">shared by {a.shared_by.length}</span>
                                                )}
                                            </span>
                                        ))}
                                    </div>
                                </div>
                            )}

                            {/* Results grouped */}
                            {deployResults.length > 0 && (
                                <div>
                                    <h3 className="font-bold text-sm uppercase tracking-wider text-muted-foreground flex items-center gap-2 mb-3">
                                        <Server size={14} /> Service Deployment Context
                                    </h3>
                                    <div className="space-y-2">
                                        {deployResults.map((r, idx) => (
                                            <div
                                                key={r.repo}
                                                className="bg-card border border-border p-4 rounded-xl flex items-center justify-between"
                                            >
                                                <div className="flex items-center gap-3 min-w-0">
                                                    {r.status === 'queued' ? (
                                                        <CheckCircle2 className="text-emerald-500 shrink-0" size={18} />
                                                    ) : r.status === 'skipped' ? (
                                                        <AlertCircle className="text-yellow-500 shrink-0" size={18} />
                                                    ) : (
                                                        <XCircle className="text-red-500 shrink-0" size={18} />
                                                    )}
                                                    <div className="min-w-0">
                                                        <p className="font-medium truncate">{r.name || r.repo}</p>
                                                        <p className="text-xs text-muted-foreground flex flex-wrap gap-x-2">
                                                            {r.stack && <span className="inline-flex items-center gap-1"><Code size={10} />{r.stack}</span>}
                                                            {r.port && <span>port {r.port}</span>}
                                                            {r.server && <span className="inline-flex items-center gap-1"><Server size={10} />{r.server}</span>}
                                                            <span className={`capitalize ${r.status === 'failed' ? 'text-red-400' : r.status === 'skipped' ? 'text-yellow-400' : 'text-emerald-400'}`}>
                                                                {r.status}
                                                            </span>
                                                        </p>
                                                        {r.error && (
                                                            <p className="text-xs text-red-400 mt-1 truncate">{r.error}</p>
                                                        )}
                                                    </div>
                                                </div>
                                                <div className="flex gap-3 items-center shrink-0 ml-3">
                                                    {r.status === 'failed' && r.deployment_id && (
                                                        <button
                                                            onClick={() => handleRetry(r.deployment_id!, idx)}
                                                            className="text-xs flex items-center gap-1 text-primary bg-primary/10 hover:bg-primary/20 px-2 py-1 rounded"
                                                        >
                                                            <RefreshCw size={12} /> Retry
                                                        </button>
                                                    )}
                                                    {r.service_id && (
                                                        <Link
                                                            href={`/services/${r.service_id}`}
                                                            className="text-xs text-primary hover:underline flex items-center gap-1"
                                                        >
                                                            <Globe size={12} /> Details →
                                                        </Link>
                                                    )}
                                                    {r.deployment_id && (
                                                        <Link
                                                            href={`/deployments`}
                                                            className="text-xs text-muted-foreground hover:text-foreground"
                                                        >
                                                            Build Logs
                                                        </Link>
                                                    )}
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            )}

                            <div className="flex flex-wrap justify-center gap-3 pt-4">
                                <Link
                                    href="/services"
                                    className="px-6 py-2.5 rounded-xl border border-border hover:border-foreground/20 text-foreground font-semibold transition-colors"
                                >
                                    View All Services
                                </Link>
                                <Link
                                    href="/deployments"
                                    className="px-6 py-2.5 rounded-xl bg-gradient-to-r from-emerald-500 to-teal-600 text-white font-semibold shadow-lg shadow-emerald-500/25"
                                >
                                    View Deployments
                                </Link>
                                <button
                                    onClick={downloadEnv}
                                    className="px-6 py-2.5 rounded-xl border border-border hover:border-foreground/20 text-muted-foreground hover:text-foreground font-semibold transition-colors flex items-center gap-2"
                                >
                                    <Download size={16} /> Download .env.json
                                </button>
                                <button
                                    onClick={() => {
                                        setStep('selection');
                                        setError(null);
                                        fetchRepos();
                                    }}
                                    className="px-6 py-2.5 rounded-xl border border-primary/30 text-primary hover:bg-primary/10 font-semibold transition-colors flex items-center gap-2"
                                >
                                    <Plus size={16} /> Add More Repos
                                </button>
                                <button
                                    onClick={() => { clearState(); setStep('idle'); setDeployResults([]); setError(null); setUseSharedAddons(true); setCancelOthersOnFailure(false); setSharedAddonConfig({}); }}
                                    className="px-6 py-2.5 rounded-xl border border-border hover:border-foreground/20 text-muted-foreground font-semibold transition-colors"
                                >
                                    Deploy Another
                                </button>
                            </div>
                        </motion.div>
                    )}
                </motion.div>
            </div>

            {/* Bulk Env Update Dialog */}
            <BulkEnvDialog
                open={bulkEnvOpen}
                onOpenChange={setBulkEnvOpen}
                apps={ecosystemApps}
            />
        </DashboardShell>
    );
}
