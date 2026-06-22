'use client';

import React, { useState, useEffect } from 'react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { AlertCircle, CheckCircle, Clock, RefreshCw, Terminal, RotateCcw, Play, Loader2 } from 'lucide-react';
import { useConfirm } from '@/components/ui/confirm-dialog';
import { toast } from '@/components/ui/use-toast';

interface PlatformUpdate {
    id: string;
    status: 'PENDING' | 'PULLING' | 'BACKING_UP' | 'MIGRATING' | 'RESTARTING' | 'HEALTH_CHECK' | 'COMPLETED' | 'FAILED' | 'ROLLED_BACK';
    from_version: string;
    to_version: string;
    from_commit: string;
    to_commit: string;
    progress_percent: number;
    current_step: string;
    logs: string;
    error_message: string;
    can_rollback: boolean;
    rollback_deadline: string | null;
    created_at: string;
    completed_at: string | null;
    initiated_by: string;
}

function getHeaders(): Record<string, string> {
    return { 'Content-Type': 'application/json' };
}

function apiUrl(path: string) {
    const base = typeof window !== 'undefined' ? `${window.location.origin}/api/v1` : '/api/v1';
    return `${base}${path}`;
}

export default function UpdatesPage() {
    const confirm = useConfirm();
    const [updates, setUpdates] = useState<PlatformUpdate[]>([]);
    const [loading, setLoading] = useState(true);
    const [triggering, setTriggering] = useState(false);
    const [isBackendReachable, setIsBackendReachable] = useState(true);
    const [isPolling, setIsPolling] = useState(false);
    const [expandedLogs, setExpandedLogs] = useState<string | null>(null);

    const fetchUpdates = async () => {
        try {
            const res = await fetch(apiUrl('/platform-updates/'), {
                credentials: 'include',
                headers: getHeaders(),
            });
            if (res.ok) {
                setIsBackendReachable(true);
                const data = await res.json();
                setUpdates(data);

                // Normalize status to uppercase for comparison — backend
                // returns UPPERCASE (PENDING, PULLING, etc.) but a future
                // schema change could introduce lowercase. Be defensive.
                const inProgress = data.find((u: any) =>
                    ['PENDING', 'PULLING', 'BACKING_UP', 'MIGRATING',
                     'RESTARTING', 'HEALTH_CHECK', 'ROLLING_BACK'].includes(
                        String(u.status).toUpperCase()
                    )
                );
                if (inProgress) {
                    setIsPolling(true);
                } else {
                    setIsPolling(false);
                }
            } else {
                if (isPolling && (res.status === 502 || res.status === 503)) {
                     setIsBackendReachable(false);
                }
            }
        } catch (e) {
            console.error("Failed to fetch updates:", e);
            if (isPolling) {
                setIsBackendReachable(false);
            }
        } finally {
            setLoading(false);
        }
    };


    useEffect(() => {
        fetchUpdates();
        const interval = setInterval(fetchUpdates, 5000);
        return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const handleTrigger = async () => {
        if (!await confirm({
            title: 'Update Platform?',
            message: 'This will restart the platform services. There may be brief downtime.',
            confirmText: 'Update Now'
        })) return;

        setTriggering(true);
        try {
            const res = await fetch(apiUrl('/platform-updates/trigger/'), {
                method: 'POST',
                credentials: 'include',
                headers: getHeaders(),
            });
            if (res.ok) {
                toast({ title: 'Update started' });
                fetchUpdates();
            } else {
                const data = await res.json();
                toast({ title: 'Failed to start update', description: data.error, variant: 'destructive' });
            }
        } catch (e) {
            toast({ title: 'Request failed', variant: 'destructive' });
        } finally {
            setTriggering(false);
        }
    };

    const handleRollback = async (id: string) => {
        if (!await confirm({
            title: 'Rollback Update?',
            message: 'This will revert the platform to the previous version.',
            variant: 'destructive',
            confirmText: 'Rollback'
        })) return;

        try {
            const res = await fetch(apiUrl(`/platform-updates/${id}/rollback/`), {
                method: 'POST',
                credentials: 'include',
                headers: getHeaders(),
            });
            if (res.ok) {
                toast({ title: 'Rollback started' });
                fetchUpdates();
            } else {
                const data = await res.json();
                toast({ title: 'Rollback failed', description: data.error, variant: 'destructive' });
            }
        } catch (e) {
            toast({ title: 'Request failed', variant: 'destructive' });
        }
    };

    const statusColor = (s: string) => {
        switch (s) {
            case 'COMPLETED': return 'text-emerald-500 bg-emerald-500/10 border-emerald-500/20';
            case 'FAILED': return 'text-red-500 bg-red-500/10 border-red-500/20';
            case 'ROLLED_BACK': return 'text-orange-500 bg-orange-500/10 border-orange-500/20';
            default: return 'text-blue-500 bg-blue-500/10 border-blue-500/20';
        }
    };

    const isInProgress = (s: string) =>
        ['PENDING', 'PULLING', 'BACKING_UP', 'MIGRATING', 'RESTARTING', 'HEALTH_CHECK'].includes(s);

    const activeUpdate = updates.find(u => isInProgress(u.status));

    return (
        <DashboardShell>
            <div className="container max-w-5xl mx-auto px-4 py-8">
                <div className="flex items-center justify-between mb-8">
                    <div>
                        <h1 className="text-2xl font-bold tracking-tight">Platform Updates</h1>
                        <p className="text-muted-foreground mt-1">Manage self-updates and rollbacks.</p>
                    </div>
                    <Button onClick={handleTrigger} disabled={triggering || !!activeUpdate} className="bg-primary text-primary-foreground">
                        {triggering ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Play className="w-4 h-4 mr-2" />}
                        {activeUpdate ? 'Update in Progress' : triggering ? 'Starting...' : 'Update Now'}
                    </Button>
                </div>

                {activeUpdate && (
                    <Card className="p-6 mb-8 border-blue-500/30 bg-blue-500/5 animate-in fade-in slide-in-from-top-4">
                        <div className="flex items-center gap-4 mb-4">
                            <RefreshCw className="w-6 h-6 text-blue-500 animate-spin" />
                            <div>
                                <h3 className="font-semibold text-lg text-blue-500">Update in Progress</h3>
                                <p className="text-sm text-blue-400/80">
                                    {!isBackendReachable ? "Backend restarting, waiting for reconnect..." : activeUpdate.status.replace(/_/g, ' ')}
                                </p>
                            </div>
                        </div>
                    </Card>
                )}


                <div className="space-y-4">
                    {loading ? (
                        <div className="text-center py-12 text-muted-foreground">Loading history...</div>
                    ) : updates.length === 0 ? (
                        <div className="text-center py-12 border-2 border-dashed border-border rounded-xl">
                            <CheckCircle className="w-12 h-12 mx-auto text-muted-foreground/50 mb-4" />
                            <h3 className="font-semibold">No updates found</h3>
                            <p className="text-sm text-muted-foreground">The platform hasn&apos;t been updated yet.</p>
                        </div>
                    ) : (
                        updates.map(update => (
                            <Card key={update.id} className="overflow-hidden border-border bg-card/50">
                                <div className="p-5 flex items-start gap-4">
                                    <div className={`mt-1 p-2 rounded-full border ${statusColor(update.status)}`}>
                                        {update.status === 'COMPLETED' ? <CheckCircle size={16} /> :
                                         update.status === 'FAILED' ? <AlertCircle size={16} /> :
                                         update.status === 'ROLLED_BACK' ? <RotateCcw size={16} /> :
                                         <Loader2 size={16} className="animate-spin" />}
                                    </div>
                                    <div className="flex-1 min-w-0">
                                        <div className="flex items-center gap-3 mb-1">
                                            <h4 className="font-semibold text-foreground">
                                                Update to {update.to_commit ? update.to_commit.substring(0, 7) : 'Latest'}
                                            </h4>
                                            <span className={`text-[10px] px-2 py-0.5 rounded-full font-bold uppercase tracking-wider ${statusColor(update.status)}`}>
                                                {update.status.replace('_', ' ')}
                                            </span>
                                        </div>
                                        <p className="text-xs text-muted-foreground flex items-center gap-3">
                                            <Clock size={12} />
                                            {new Date(update.created_at).toLocaleString()}
                                            {update.from_commit && (
                                                <span className="font-mono bg-muted px-1.5 rounded text-[10px]">
                                                    {update.from_commit.substring(0, 7)} → {update.to_commit ? update.to_commit.substring(0, 7) : '???'}
                                                </span>
                                            )}
                                        </p>

                                        {update.error_message && (
                                            <div className="mt-3 p-3 bg-red-500/10 border border-red-500/20 rounded-md text-red-400 text-xs font-mono break-all">
                                                {update.error_message}
                                            </div>
                                        )}
                                    </div>

                                    <div className="flex flex-col gap-2 items-end">
                                        {update.can_rollback && (
                                            <Button
                                                variant="outline"
                                                size="sm"
                                                onClick={() => handleRollback(update.id)}
                                                className="border-red-500/30 text-red-400 hover:bg-red-500/10 hover:text-red-300"
                                            >
                                                <RotateCcw className="w-3 h-3 mr-2" /> Rollback
                                            </Button>
                                        )}
                                        <Button
                                            variant="ghost"
                                            size="sm"
                                            onClick={() => setExpandedLogs(expandedLogs === update.id ? null : update.id)}
                                        >
                                            <Terminal className="w-3 h-3 mr-2" />
                                            {expandedLogs === update.id ? 'Hide Logs' : 'View Logs'}
                                        </Button>
                                    </div>
                                </div>

                                {expandedLogs === update.id && (
                                    <div className="border-t border-border bg-black/50 p-4">
                                        <pre className="font-mono text-xs text-zinc-400 whitespace-pre-wrap max-h-96 overflow-y-auto custom-scrollbar">
                                            {update.logs || 'No logs available.'}
                                        </pre>
                                    </div>
                                )}
                            </Card>
                        ))
                    )}
                </div>
            </div>
        </DashboardShell>
    );
}
