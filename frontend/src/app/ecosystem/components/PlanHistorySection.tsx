'use client';

import { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { ChevronDown, ChevronRight, Clock, CheckCircle2, XCircle, Loader2 } from 'lucide-react';
import { ecosystemApi, type EcosystemPlanSummary } from '@/lib/api';

const STATUS_CONFIG: Record<string, { icon: typeof Clock; color: string; label: string }> = {
    completed: { icon: CheckCircle2, color: 'text-emerald-500', label: 'Completed' },
    failed: { icon: XCircle, color: 'text-red-500', label: 'Failed' },
    deploying: { icon: Loader2, color: 'text-blue-500 animate-spin', label: 'Deploying' },
    review: { icon: Clock, color: 'text-amber-500', label: 'Review' },
    scanning: { icon: Loader2, color: 'text-purple-500 animate-spin', label: 'Scanning' },
};

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
    const [loading, setLoading] = useState(false);

    useEffect(() => {
        if (!expanded) return;
        setLoading(true);
        ecosystemApi
            .listPlans({ status: 'completed' })
            .then((data) => {
                setPlans(Array.isArray(data) ? data : data.results || []);
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
                                        return (
                                            <li key={plan.id} className="px-5 py-3 flex items-center justify-between gap-4 hover:bg-muted/30 transition-colors">
                                                <div className="flex items-center gap-3 min-w-0">
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
                                                </div>
                                                <span className={`text-xs font-semibold ${cfg.color}`}>{cfg.label}</span>
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
