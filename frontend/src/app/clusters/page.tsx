'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { useToast } from '@/components/ui/use-toast';
import { useConfirm } from '@/components/ui/confirm-dialog';
import { clustersApi, ClusterState, ClusterHeartbeat } from '@/lib/api';
import { Loader2, RefreshCw, Crown, Zap, ChevronDown, ChevronRight } from 'lucide-react';

function stateColor(state: string) {
    switch ((state || '').toUpperCase()) {
        case 'LEADER':
        case 'HEALTHY':
        case 'STABLE': return 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30';
        case 'ELECTING':
        case 'CANDIDATE':
        case 'DEGRADED': return 'bg-amber-500/15 text-amber-400 border-amber-500/30';
        case 'PARTITIONED':
        case 'DOWN':
        case 'FAILED': return 'bg-red-500/15 text-red-400 border-red-500/30';
        default: return 'bg-zinc-500/15 text-zinc-400 border-zinc-500/30';
    }
}

export default function ClustersPage() {
    const { toast } = useToast();
    const confirm = useConfirm();
    const [clusters, setClusters] = useState<ClusterState[]>([]);
    const [loading, setLoading] = useState(true);
    const [expanded, setExpanded] = useState<string | null>(null);
    const [heartbeats, setHeartbeats] = useState<Record<string, ClusterHeartbeat[]>>({});
    const [electing, setElecting] = useState<string | null>(null);

    const fetchClusters = useCallback(async (silent = false) => {
        try {
            if (!silent) setLoading(true);
            const list = await clustersApi.list();
            setClusters(list);
        } catch (err: any) {
            if (!silent) {
                toast({
                    title: 'Failed to load clusters',
                    description: err?.response?.status === 403 ? 'Admin access required.' : err?.message,
                    variant: 'destructive',
                });
            }
        } finally {
            setLoading(false);
        }
    }, [toast]);

    useEffect(() => {
        fetchClusters();
        const t = setInterval(() => fetchClusters(true), 30000);
        return () => clearInterval(t);
    }, [fetchClusters]);

    const toggleExpand = async (id: string) => {
        if (expanded === id) {
            setExpanded(null);
            return;
        }
        setExpanded(id);
        if (!heartbeats[id]) {
            try {
                const logs = await clustersApi.heartbeats(id);
                setHeartbeats((prev) => ({ ...prev, [id]: logs }));
            } catch {
                // Heartbeats are optional detail; list already loaded.
            }
        }
    };

    const handleForceElection = async (id: string) => {
        if (!await confirm({
            title: 'Force leader election?',
            message: 'This starts a new election term. Brief control-plane unavailability may occur. Continue?',
            confirmText: 'Force Election',
            variant: 'destructive',
        })) return;
        setElecting(id);
        try {
            const res = await clustersApi.forceElection(id);
            toast({ title: 'Election started', description: res.message || `New term: ${res.term ?? 'pending'}.` });
            fetchClusters(true);
        } catch (err: any) {
            toast({ title: 'Failed to force election', description: err?.response?.data?.error || err?.message, variant: 'destructive' });
        } finally {
            setElecting(null);
        }
    };

    return (
        <DashboardShell>
            <div className="container mx-auto max-w-6xl px-4 py-8 sm:px-6 lg:py-10 space-y-6">
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                    <div>
                        <h1 className="text-2xl font-bold tracking-tight">Leader-Elected Clusters</h1>
                        <p className="text-sm text-muted-foreground mt-1">
                            Raft-style control-plane state per mesh: leader, term, quorum and heartbeat health.
                        </p>
                    </div>
                    <Button variant="outline" size="sm" onClick={() => fetchClusters()}>
                        <RefreshCw className="w-3.5 h-3.5 mr-1.5" /> Refresh
                    </Button>
                </div>

                {loading ? (
                    <div className="flex items-center justify-center py-16">
                        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
                    </div>
                ) : clusters.length === 0 ? (
                    <Card>
                        <CardContent className="p-12 text-center text-muted-foreground">
                            No clusters found. Clusters appear once a mesh network is deployed.
                        </CardContent>
                    </Card>
                ) : (
                    clusters.map((cluster) => {
                        const isOpen = expanded === cluster.id;
                        const logs = heartbeats[cluster.id] || [];
                        return (
                            <Card key={cluster.id} className="border-border/60">
                                <CardHeader className="py-3 px-4">
                                    <div className="flex items-center justify-between gap-3 flex-wrap">
                                        <div className="flex items-center gap-2 min-w-0">
                                            <button onClick={() => toggleExpand(cluster.id)} className="text-muted-foreground hover:text-foreground">
                                                {isOpen ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                                            </button>
                                            <Crown className="w-4 h-4 text-amber-500 shrink-0" />
                                            <CardTitle className="text-sm font-mono truncate">
                                                {cluster.leader_name || 'no leader'}
                                            </CardTitle>
                                            <Badge variant="outline" className={`text-[10px] ${stateColor(cluster.state)}`}>
                                                {cluster.state || 'UNKNOWN'}
                                            </Badge>
                                        </div>
                                        <div className="flex items-center gap-2">
                                            <span className="text-xs text-muted-foreground font-mono">
                                                term {cluster.term} · {cluster.peer_count} peer{cluster.peer_count === 1 ? '' : 's'}
                                            </span>
                                            <Button
                                                variant="outline"
                                                size="sm"
                                                disabled={electing === cluster.id}
                                                onClick={() => handleForceElection(cluster.id)}
                                            >
                                                {electing === cluster.id
                                                    ? <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" />
                                                    : <Zap className="w-3.5 h-3.5 mr-1.5" />}
                                                Force Election
                                            </Button>
                                        </div>
                                    </div>
                                    <CardDescription className="text-xs font-mono mt-1">
                                        {cluster.id.slice(0, 8)}
                                        {cluster.last_heartbeat ? ` · last heartbeat ${new Date(cluster.last_heartbeat).toLocaleString()}` : ' · no heartbeat yet'}
                                        {cluster.min_quorum ? ` · quorum ${cluster.min_quorum}` : ''}
                                    </CardDescription>
                                </CardHeader>
                                {isOpen && (
                                    <CardContent className="p-4 border-t border-border/40">
                                        <p className="text-xs font-bold uppercase tracking-wider text-muted-foreground mb-2">
                                            Recent Heartbeats ({logs.length})
                                        </p>
                                        {logs.length === 0 ? (
                                            <p className="text-xs text-muted-foreground">No heartbeat logs yet.</p>
                                        ) : (
                                            <div className="space-y-1.5">
                                                {logs.slice(0, 20).map((log) => (
                                                    <div key={log.id} className="flex items-center justify-between gap-3 text-xs font-mono p-2 rounded-lg border border-border/40 bg-card">
                                                        <span className="truncate">{log.source_name} → {log.target_name}</span>
                                                        <span className="text-muted-foreground shrink-0">
                                                            term {log.term}
                                                            {log.latency_ms !== null && log.latency_ms !== undefined ? ` · ${log.latency_ms}ms` : ''}
                                                        </span>
                                                        <Badge variant={log.success ? 'default' : 'destructive'} className="text-[10px] shrink-0">
                                                            {log.success ? 'OK' : 'FAIL'}
                                                        </Badge>
                                                    </div>
                                                ))}
                                            </div>
                                        )}
                                        {logs.length > 0 && logs[0] && !logs[0].success && logs[0].error_message && (
                                            <p className="mt-2 text-xs text-red-400 font-mono">{logs[0].error_message}</p>
                                        )}
                                    </CardContent>
                                )}
                            </Card>
                        );
                    })
                )}
            </div>
        </DashboardShell>
    );
}
