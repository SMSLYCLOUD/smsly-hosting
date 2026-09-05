'use client';

import { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { ChevronDown, ChevronRight, Clock, CheckCircle2, XCircle, Loader2, Camera, RotateCcw } from 'lucide-react';
import { ecosystemApi, type EcosystemPlanSummary } from '@/lib/api';

const STATUS_CONFIG: Record<string, { icon: typeof Clock; color: string; label: string }> = {
    completed: { icon: CheckCircle2, color: 'text-emerald-500', label: 'Completed' },
    failed: { icon: XCircle, color: 'text-red-500', label: 'Failed' },
    deploying: { icon: Loader2, color: 'text-blue-500 animate-spin', label: 'Deploying' },
    review: { icon: Clock, color: 'text-amber-500', label: 'Review' },
    scanning: { icon: Loader2, color: 'text-purple-500 animate-spin', label: 'Scanning' },
};

interface PlanServiceEntry {
    name?: string;
    service_id?: string;
    deployment_id?: string;
    pre_deploy_snapshot_id?: string | null;
    status?: string;
}

interface RestoreResult {
    restored: { service_id: string; service_name: string; config_changes: number; env_var_changes: number; db_clone_restored: boolean; redeployed: boolean }[];
    skipped: { service_id: string | null; service_name: string; reason: string }[];
    errors: { service_id: string | null; service_name: string; error: string }[];
}

function PlanRestoreBlock({ planId }: { planId: string }) {
    const [services, setServices] = useState<PlanServiceEntry[] | null>(null);
    const [redeploy, setRedeploy] = useState(true);
    const [confirming, setConfirming] = useState(false);
    const [running, setRunning] = useState(false);
    const [result, setResult] = useState<RestoreResult | null>(null);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        ecosystemApi
            .getPlan(planId)
            .then((detail: any) => {
                const list = Array.isArray(detail?.services_created) ? detail.services_created : [];
                setServices(list.filter((e: any) => e && typeof e === 'object'));
            })
            .catch(() => setServices([]));
    }, [planId]);

    const withSnapshots = (services || []).filter((s) => s.pre_deploy_snapshot_id);

    const runRestore = async () => {
        setRunning(true);
        setError(null);
        try {
            const data = await ecosystemApi.restorePlanSnapshots(planId, {
                confirm: true,
                redeploy,
            });
            setResult(data as RestoreResult);
        } catch (e: any) {
            setError(e?.response?.data?.error || 'Restore failed.');
        } finally {
            setRunning(false);
            setConfirming(false);
        }
    };

    return (
        <div className="px-5 py-3 space-y-2 bg-muted/20">
            {services === null ? (
                <p className="text-xs text-muted-foreground">Loading services…</p>
            ) : services.length === 0 ? (
                <p className="text-xs text-muted-foreground">No service records on this plan.</p>
            ) : (
                <ul className="space-y-1">
                    {services.map((s, i) => (
                        <li key={s.service_id || i} className="flex items-center gap-2 text-xs">
                            <Camera size={12} className={s.pre_deploy_snapshot_id ? 'text-emerald-500' : 'text-zinc-600'} />
                            <span className="font-medium truncate">{s.name || s.service_id}</span>
                            <span className="text-muted-foreground">
                                {s.pre_deploy_snapshot_id ? 'snapshot saved' : 'new service — nothing to restore'}
                            </span>
                        </li>
                    ))}
                </ul>
            )}
            {withSnapshots.length > 0 && !result && (
                <div className="flex items-center gap-3 pt-1">
                    <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
                        <input type="checkbox" checked={redeploy} onChange={(e) => setRedeploy(e.target.checked)} />
                        Redeploy after restore
                    </label>
                    {!confirming ? (
                        <button
                            onClick={() => setConfirming(true)}
                            className="text-xs font-semibold text-amber-500 hover:underline flex items-center gap-1"
                        >
                            <RotateCcw size={12} /> Restore pre-deploy snapshots
                        </button>
                    ) : (
                        <span className="flex items-center gap-2 text-xs">
                            <span className="text-muted-foreground">Restore {withSnapshots.length} service(s)?</span>
                            <button
                                onClick={runRestore}
                                disabled={running}
                                className="font-semibold text-red-500 hover:underline disabled:opacity-50"
                            >
                                {running ? 'Restoring…' : 'Confirm'}
                            </button>
                            <button onClick={() => setConfirming(false)} className="text-muted-foreground hover:underline">
                                Cancel
                            </button>
                        </span>
                    )}
                </div>
            )}
            {error && <p className="text-xs text-red-500">{error}</p>}
            {result && (
                <div className="text-xs space-y-1 pt-1">
                    {result.restored.map((r) => (
                        <p key={r.service_id} className="text-emerald-500">
                            ✓ {r.service_name}: {r.config_changes} config + {r.env_var_changes} env changes
                            {r.db_clone_restored ? ' + DB restored' : ''}{r.redeployed ? ' · redeploying' : ''}
                        </p>
                    ))}
                    {result.errors.map((e, i) => (
                        <p key={e.service_id || i} className="text-red-500">✗ {e.service_name}: {e.error}</p>
                    ))}
                </div>
            )}
        </div>
    );
}

function formatDate(iso: string): string {
    return new Date(iso).toLocaleDateString(undefined, {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
    });
}

export function PlanHistorySection() {
    const [plans, setPlans] = useState<EcosystemPlanSummary[]>([]);
    const [expanded, setExpanded] = useState(false);
    const [openPlanId, setOpenPlanId] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);

    useEffect(() => {
        if (!expanded) return;
        setLoading(true);
        Promise.all([
            ecosystemApi.listPlans({ status: 'completed' }).catch(() => []),
            ecosystemApi.listPlans({ status: 'failed' }).catch(() => []),
        ])
            .then(([completed, failed]) => {
                const norm = (d: any): EcosystemPlanSummary[] =>
                    Array.isArray(d) ? d : d.results || [];
                const merged = [...norm(completed), ...norm(failed)];
                merged.sort((a, b) => +new Date(b.created_at) - +new Date(a.created_at));
                setPlans(merged);
            })
            .catch(() => setPlans([]))
            .finally(() => setLoading(false));
    }, [expanded]);

    return (
        <div className="border border-border rounded-xl overflow-hidden">
            <button
                onClick={() => setExpanded(!expanded)}
                className="w-full flex items-center justify-between px-5 py-3 text-sm font-semibold text-muted-foreground hover:bg-muted/50 transition-colors"
            >
                <span className="flex items-center gap-2">
                    <Clock size={14} />
                    Past Deployments
                </span>
                {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            </button>
            <AnimatePresence>
                {expanded && (
                    <motion.div
                        initial={{ height: 0 }}
                        animate={{ height: 'auto' }}
                        exit={{ height: 0 }}
                        className="overflow-hidden"
                    >
                        <div className="border-t border-border">
                            {loading ? (
                                <div className="px-5 py-6 text-center text-sm text-muted-foreground">
                                    Loading...
                                </div>
                            ) : plans.length === 0 ? (
                                <div className="px-5 py-6 text-center text-sm text-muted-foreground">
                                    No completed deployments yet.
                                </div>
                            ) : (
                                <ul className="divide-y divide-border">
                                    {plans.map((plan) => {
                                        const cfg = STATUS_CONFIG[plan.status] || STATUS_CONFIG.review;
                                        const Icon = cfg.icon;
                                        const open = openPlanId === plan.id;
                                        const restorable = plan.status === 'failed' || plan.status === 'completed';
                                        return (
                                            <li key={plan.id}>
                                                <div className="px-5 py-3 flex items-center justify-between gap-4 hover:bg-muted/30 transition-colors">
                                                    <button
                                                        onClick={() => setOpenPlanId(open ? null : plan.id)}
                                                        className="flex items-center gap-3 min-w-0 text-left"
                                                    >
                                                        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                                                        <Icon size={14} className={cfg.color} />
                                                        <div className="min-w-0">
                                                            <p className="text-sm font-medium truncate">
                                                                {plan.selected_repos?.length || 0} repos
                                                                {plan.ai_provider ? ` · ${plan.ai_provider}` : ''}
                                                            </p>
                                                            <p className="text-xs text-muted-foreground">
                                                                {formatDate(plan.created_at)}
                                                                {plan.completed_at ? ` — completed ${formatDate(plan.completed_at)}` : ''}
                                                            </p>
                                                        </div>
                                                    </button>
                                                    <span className={`text-xs font-semibold ${cfg.color}`}>{cfg.label}</span>
                                                </div>
                                                {open && restorable && <PlanRestoreBlock planId={plan.id} />}
                                            </li>
                                        );
                                    })}
                                </ul>
                            )}
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    );
}
