'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { servicesApi, Deployment } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { GitCommit, RotateCcw, Clock, CheckCircle2, XCircle, Loader2, ChevronDown, ChevronRight, Rocket, Brain, Timer } from 'lucide-react';
import { toast } from '@/components/ui/use-toast';
import { formatDistanceToNow } from 'date-fns';

export function DeploymentsTab({ serviceId }: { serviceId: string }) {
    const [deployments, setDeployments] = useState<Deployment[]>([]);
    const [loading, setLoading] = useState(true);
    const [rollingBackId, setRollingBackId] = useState<string | null>(null);
    const [expandedId, setExpandedId] = useState<string | null>(null);
    const [expandedDetails, setExpandedDetails] = useState<Record<string, any>>({});
    const [redeploying, setRedeploying] = useState(false);

    const loadDeployments = useCallback(async () => {
        try {
            const data = await servicesApi.getDeployments(serviceId);
            setDeployments(data);
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    }, [serviceId]);

    useEffect(() => {
        void loadDeployments();
        const interval = setInterval(loadDeployments, 5000);
        return () => clearInterval(interval);
    }, [loadDeployments]);

    const handleRedeploy = async () => {
        try {
            setRedeploying(true);
            await servicesApi.deploy(serviceId);
            toast({ title: "Redeployment triggered", description: "A new deployment has started." });
            setTimeout(() => { void loadDeployments(); setRedeploying(false); }, 2000);
        } catch (err) {
            console.error(err);
            toast({ title: "Redeploy failed", variant: "destructive" });
            setRedeploying(false);
        }
    };

    const handleRollback = async (deployment: Deployment) => {
        if (!confirm(`Rollback to commit ${deployment.commit_hash.substring(0, 7)}? This will trigger a new deployment.`)) return;

        try {
            setRollingBackId(deployment.id);
            await servicesApi.rollback(deployment.id);
            toast({ title: "Rollback initiated", description: "A new deployment has started." });
            setTimeout(() => { void loadDeployments(); }, 2000);
        } catch (err) {
            console.error(err);
            toast({ title: "Rollback failed", variant: "destructive" });
        } finally {
            setRollingBackId(null);
        }
    };

    const toggleExpand = async (d: Deployment) => {
        if (expandedId === d.id) {
            setExpandedId(null);
            return;
        }
        setExpandedId(d.id);
        // Fetch full details if not cached
        if (!expandedDetails[d.id]) {
            try {
                const details = await servicesApi.getDeployment(d.id);
                setExpandedDetails(prev => ({ ...prev, [d.id]: details }));
            } catch (err) {
                console.error(err);
            }
        }
    };

    if (loading) return <div className="p-4 text-center">Loading deployments...</div>;

    const getStatusIcon = (status: string) => {
        switch (status) {
            case 'ACTIVE': return <CheckCircle2 className="w-5 h-5 text-emerald-500" />;
            case 'FAILED': return <XCircle className="w-5 h-5 text-red-500" />;
            case 'BUILDING':
            case 'DEPLOYING': return <Loader2 className="w-5 h-5 text-blue-500 animate-spin" />;
            default: return <Clock className="w-5 h-5 text-muted-foreground" />;
        }
    };

    const getStatusColor = (status: string) => {
        switch (status) {
            case 'ACTIVE': return 'bg-emerald-500/10 text-emerald-500 border-emerald-500/30';
            case 'FAILED': return 'bg-red-500/10 text-red-500 border-red-500/30';
            case 'BUILDING':
            case 'DEPLOYING': return 'bg-blue-500/10 text-blue-500 border-blue-500/30';
            default: return 'bg-muted text-muted-foreground border-border';
        }
    };

    return (
        <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4">
            <Card className="border-border shadow-md overflow-hidden">
                <div className="p-6 border-b border-border flex justify-between items-center">
                    <div>
                        <h3 className="font-bold text-lg">Deployment History</h3>
                        <p className="text-sm text-muted-foreground">
                            Click a deployment to see details. Rollback or redeploy as needed.
                        </p>
                    </div>
                    <div className="flex gap-2">
                        <Button variant="outline" size="sm" onClick={loadDeployments}>
                            Refresh
                        </Button>
                        <Button
                            size="sm"
                            onClick={handleRedeploy}
                            disabled={redeploying}
                            className="gap-2"
                        >
                            {redeploying ? <Loader2 className="w-4 h-4 animate-spin" /> : <Rocket className="w-4 h-4" />}
                            {redeploying ? 'Deploying...' : 'Redeploy'}
                        </Button>
                    </div>
                </div>

                <div className="divide-y divide-border">
                    {deployments.length === 0 ? (
                        <div className="p-8 text-center text-muted-foreground">
                            No deployments found.
                        </div>
                    ) : (
                        deployments.map((d) => {
                            const isExpanded = expandedId === d.id;
                            const details = expandedDetails[d.id] || d;
                            return (
                                <div key={d.id}>
                                    {/* Clickable Row */}
                                    <div
                                        className="p-4 flex items-center justify-between hover:bg-muted/30 transition-colors cursor-pointer"
                                        onClick={() => toggleExpand(d)}
                                    >
                                        <div className="flex items-center gap-4">
                                            <div className="bg-muted p-2 rounded-full">
                                                {getStatusIcon(d.status)}
                                            </div>
                                            <div>
                                                <div className="flex items-center gap-2">
                                                    <span className={`text-sm font-bold uppercase px-2 py-0.5 rounded text-[10px] border ${getStatusColor(d.status)}`}>
                                                        {d.status}
                                                    </span>
                                                    <span className="text-xs text-muted-foreground">
                                                        {d.created_at ? formatDistanceToNow(new Date(d.created_at), { addSuffix: true }) : 'Unknown time'}
                                                    </span>
                                                </div>
                                                <div className="flex items-center gap-2 mt-1">
                                                    <GitCommit className="w-4 h-4 text-muted-foreground" />
                                                    <span className="font-mono text-sm">{d.commit_hash.substring(0, 7)}</span>
                                                    <span className="text-sm text-muted-foreground truncate max-w-[200px] md:max-w-[400px]">
                                                        {d.commit_message || 'No commit message'}
                                                    </span>
                                                </div>
                                            </div>
                                        </div>

                                        <div className="flex items-center gap-2">
                                            {['ACTIVE', 'FAILED', 'CANCELLED'].includes(d.status) && (
                                                <Button
                                                    variant="ghost"
                                                    size="sm"
                                                    className="text-muted-foreground hover:text-foreground"
                                                    onClick={(e) => { e.stopPropagation(); handleRollback(d); }}
                                                    disabled={!!rollingBackId}
                                                >
                                                    {rollingBackId === d.id ? (
                                                        <Loader2 className="w-4 h-4 animate-spin" />
                                                    ) : (
                                                        <><RotateCcw className="w-4 h-4 mr-2" /> Rollback</>
                                                    )}
                                                </Button>
                                            )}
                                            {isExpanded ? <ChevronDown className="w-4 h-4 text-muted-foreground" /> : <ChevronRight className="w-4 h-4 text-muted-foreground" />}
                                        </div>
                                    </div>

                                    {/* Expanded Details */}
                                    {isExpanded && (
                                        <div className="px-6 pb-6 bg-muted/10 border-t border-border animate-in fade-in slide-in-from-top-2 duration-200">
                                            <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mt-4">
                                                <div className="bg-card border border-border rounded-lg p-4">
                                                    <div className="flex items-center gap-2 text-xs text-muted-foreground mb-1">
                                                        <Timer className="w-3 h-3" /> Duration
                                                    </div>
                                                    <p className="font-bold text-lg">
                                                        {details.duration_seconds ? `${details.duration_seconds.toFixed(1)}s` : '—'}
                                                    </p>
                                                </div>
                                                <div className="bg-card border border-border rounded-lg p-4">
                                                    <div className="flex items-center gap-2 text-xs text-muted-foreground mb-1">
                                                        <GitCommit className="w-3 h-3" /> Commit
                                                    </div>
                                                    <p className="font-mono text-sm font-bold">{details.commit_hash?.substring(0, 12)}</p>
                                                </div>
                                                <div className="bg-card border border-border rounded-lg p-4">
                                                    <div className="flex items-center gap-2 text-xs text-muted-foreground mb-1">
                                                        <Clock className="w-3 h-3" /> Created
                                                    </div>
                                                    <p className="text-sm font-bold">
                                                        {details.created_at ? new Date(details.created_at).toLocaleString() : '—'}
                                                    </p>
                                                </div>
                                            </div>

                                            {/* Build Logs */}
                                            {details.build_logs && (
                                                <div className="mt-4">
                                                    <h4 className="text-sm font-bold mb-2 text-muted-foreground">Build Logs</h4>
                                                    <div className="bg-slate-950 text-emerald-400 font-mono text-xs p-4 rounded-lg max-h-64 overflow-y-auto whitespace-pre-wrap">
                                                        {details.build_logs}
                                                    </div>
                                                </div>
                                            )}

                                            {/* AI Diagnosis */}
                                            {details.ai_diagnosis && (
                                                <div className="mt-4">
                                                    <h4 className="text-sm font-bold mb-2 flex items-center gap-2 text-muted-foreground">
                                                        <Brain className="w-4 h-4 text-purple-500" /> AI Diagnosis
                                                    </h4>
                                                    <div className="bg-purple-500/5 border border-purple-500/20 text-sm p-4 rounded-lg whitespace-pre-wrap">
                                                        {details.ai_diagnosis}
                                                    </div>
                                                </div>
                                            )}

                                            {/* Error details for failed */}
                                            {details.error_message && (
                                                <div className="mt-4">
                                                    <h4 className="text-sm font-bold mb-2 text-red-500">Error</h4>
                                                    <div className="bg-red-500/5 border border-red-500/20 text-sm p-4 rounded-lg text-red-400 font-mono whitespace-pre-wrap">
                                                        {details.error_message}
                                                    </div>
                                                </div>
                                            )}
                                        </div>
                                    )}
                                </div>
                            );
                        })
                    )}
                </div>
            </Card>
        </div>
    );
}

