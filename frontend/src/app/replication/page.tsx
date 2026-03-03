'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { Button } from '@/components/ui/button';
import { useToast } from '@/components/ui/use-toast';
import {
    Database, RefreshCw, Loader2, Crown, Server, AlertTriangle,
    ArrowRightLeft, RotateCcw, Zap, Eye
} from 'lucide-react';
import api from '@/lib/api';

interface ReplicationNode {
    name: string;
    wg_address: string;
    server: string;
    role?: string;
    state?: string;
    timeline?: number;
    lag_bytes?: number;
    status: string;
    pg_version?: string;
}

interface ReplicationHealth {
    nodes: ReplicationNode[];
    primary: ReplicationNode | null;
    replicas: ReplicationNode[];
}

interface MeshNetwork {
    id: string;
    name: string;
    subnet: string;
    peer_count: number;
}

export default function ReplicationPage() {
    const { toast } = useToast();
    const [meshes, setMeshes] = useState<MeshNetwork[]>([]);
    const [selectedMesh, setSelectedMesh] = useState<string>('');
    const [health, setHealth] = useState<ReplicationHealth | null>(null);
    const [loading, setLoading] = useState(true);
    const [checking, setChecking] = useState(false);
    const [deploying, setDeploying] = useState(false);
    const [showDeployForm, setShowDeployForm] = useState(false);
    const [dbPassword, setDbPassword] = useState('');
    const [adminPassword, setAdminPassword] = useState('');
    const [replPassword, setReplPassword] = useState('repl_pass');

    const fetchMeshes = useCallback(async () => {
        try {
            const res = await api.get('/mesh/');
            const data = Array.isArray(res.data) ? res.data : res.data.results || [];
            setMeshes(data);
            if (data.length > 0 && !selectedMesh) {
                setSelectedMesh(data[0].id);
            }
        } catch (err) {
            console.error('Failed to load meshes:', err);
        } finally {
            setLoading(false);
        }
    }, [selectedMesh]);

    const checkHealth = useCallback(async () => {
        if (!selectedMesh) return;
        setChecking(true);
        try {
            const res = await api.get(`/replication/health/${selectedMesh}/`);
            setHealth(res.data);
        } catch (err: any) {
            if (err?.response?.status !== 404) {
                toast({ title: 'Error', description: 'Health check failed', variant: 'destructive' });
            }
            setHealth(null);
        } finally {
            setChecking(false);
        }
    }, [selectedMesh, toast]);

    useEffect(() => { fetchMeshes(); }, [fetchMeshes]);
    useEffect(() => { if (selectedMesh) checkHealth(); }, [selectedMesh, checkHealth]);

    const deployReplication = async () => {
        if (!selectedMesh || !dbPassword || !adminPassword) return;
        setDeploying(true);
        try {
            await api.post('/replication/deploy/', {
                mesh_id: selectedMesh,
                db_password: dbPassword,
                admin_password: adminPassword,
                replication_password: replPassword,
            });
            toast({ title: 'Deployment Started', description: 'Patroni cluster is being deployed to all peers.' });
            setShowDeployForm(false);
            setDbPassword(''); setAdminPassword('');
        } catch (err: any) {
            toast({ title: 'Error', description: err?.response?.data?.error || 'Deploy failed', variant: 'destructive' });
        } finally {
            setDeploying(false);
        }
    };

    const triggerFailover = async (targetWgAddress: string) => {
        if (!confirm(`Failover to ${targetWgAddress}? The current primary will be demoted.`)) return;
        try {
            await api.post('/replication/failover/', {
                mesh_id: selectedMesh,
                target_wg_address: targetWgAddress,
            });
            toast({ title: 'Failover Initiated', description: `Switching primary to ${targetWgAddress}` });
            setTimeout(checkHealth, 5000);
        } catch (err: any) {
            toast({ title: 'Error', description: err?.response?.data?.error || 'Failover failed', variant: 'destructive' });
        }
    };

    const reinitReplica = async (targetWgAddress: string) => {
        if (!confirm(`Reinitialize ${targetWgAddress}? This will rebuild it from scratch.`)) return;
        try {
            await api.post('/replication/reinitialize/', {
                mesh_id: selectedMesh,
                target_wg_address: targetWgAddress,
            });
            toast({ title: 'Reinitialize Started', description: `Rebuilding replica at ${targetWgAddress}` });
        } catch (err: any) {
            toast({ title: 'Error', description: err?.response?.data?.error || 'Reinit failed', variant: 'destructive' });
        }
    };

    const formatLag = (bytes: number | undefined) => {
        if (bytes === undefined || bytes === null) return 'N/A';
        if (bytes < 1024) return `${bytes} B`;
        if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
        return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
    };

    const lagColor = (bytes: number | undefined) => {
        if (bytes === undefined || bytes === null) return 'text-muted-foreground';
        if (bytes > 10 * 1024 * 1024) return 'text-red-500';
        if (bytes > 1024 * 1024) return 'text-amber-500';
        return 'text-emerald-500';
    };

    return (
        <DashboardShell>
            <div className="flex-1 p-8 relative z-10">
                <div className="max-w-5xl mx-auto space-y-8">
                    {/* Header */}
                    <div className="flex items-center justify-between">
                        <div>
                            <h1 className="text-3xl font-bold tracking-tight flex items-center gap-3">
                                <Database className="text-blue-500" size={28} />
                                Database Replication
                            </h1>
                            <p className="text-muted-foreground mt-1">
                                Patroni streaming replication with automatic failover
                            </p>
                        </div>
                        <div className="flex items-center gap-3">
                            <Button
                                variant="outline" size="sm"
                                onClick={checkHealth}
                                disabled={checking || !selectedMesh}
                            >
                                {checking ? <Loader2 size={14} className="animate-spin mr-1" /> : <RefreshCw size={14} className="mr-1" />}
                                Check Health
                            </Button>
                            <Button
                                onClick={() => setShowDeployForm(!showDeployForm)}
                                className="bg-gradient-to-r from-blue-500 to-cyan-600 text-white shadow-lg shadow-blue-500/25"
                            >
                                <Zap size={14} className="mr-2" />
                                Deploy Cluster
                            </Button>
                        </div>
                    </div>

                    {/* Mesh Selector */}
                    {meshes.length > 1 && (
                        <div className="flex items-center gap-3">
                            <label className="text-sm font-medium text-muted-foreground">Mesh:</label>
                            <select
                                value={selectedMesh}
                                onChange={e => setSelectedMesh(e.target.value)}
                                className="px-3 py-2 rounded-lg bg-background border border-border text-sm"
                            >
                                {meshes.map(m => (
                                    <option key={m.id} value={m.id}>{m.name} ({m.peer_count} peers)</option>
                                ))}
                            </select>
                        </div>
                    )}

                    {/* Deploy Form */}
                    {showDeployForm && (
                        <div className="bg-card border border-border rounded-xl p-6 space-y-4">
                            <h2 className="text-lg font-semibold flex items-center gap-2">
                                <Database className="text-blue-500" size={18} />
                                Deploy Patroni Cluster
                            </h2>
                            <p className="text-sm text-muted-foreground">
                                This will deploy Patroni + etcd to all peers in the selected mesh.
                                Each server will run one PostgreSQL instance with streaming replication.
                            </p>
                            <div className="grid grid-cols-3 gap-4">
                                <div>
                                    <label className="text-xs font-medium text-muted-foreground">DB Superuser Password</label>
                                    <input
                                        type="password"
                                        value={dbPassword}
                                        onChange={e => setDbPassword(e.target.value)}
                                        className="w-full mt-1 px-3 py-2 rounded-lg bg-background border border-border text-sm"
                                    />
                                </div>
                                <div>
                                    <label className="text-xs font-medium text-muted-foreground">Admin Password</label>
                                    <input
                                        type="password"
                                        value={adminPassword}
                                        onChange={e => setAdminPassword(e.target.value)}
                                        className="w-full mt-1 px-3 py-2 rounded-lg bg-background border border-border text-sm"
                                    />
                                </div>
                                <div>
                                    <label className="text-xs font-medium text-muted-foreground">Replication Password</label>
                                    <input
                                        type="password"
                                        value={replPassword}
                                        onChange={e => setReplPassword(e.target.value)}
                                        className="w-full mt-1 px-3 py-2 rounded-lg bg-background border border-border text-sm"
                                    />
                                </div>
                            </div>
                            <div className="flex gap-2 justify-end">
                                <Button variant="outline" onClick={() => setShowDeployForm(false)}>Cancel</Button>
                                <Button onClick={deployReplication} disabled={deploying || !dbPassword || !adminPassword}>
                                    {deploying ? <Loader2 size={14} className="animate-spin mr-2" /> : <Zap size={14} className="mr-2" />}
                                    Deploy
                                </Button>
                            </div>
                        </div>
                    )}

                    {/* Loading */}
                    {loading && (
                        <div className="text-center py-16">
                            <Loader2 className="animate-spin mx-auto text-muted-foreground" size={24} />
                        </div>
                    )}

                    {/* No Health Data */}
                    {!loading && !health && !showDeployForm && (
                        <div className="text-center py-16">
                            <div className="w-20 h-20 mx-auto mb-4 rounded-3xl bg-blue-500/10 border border-blue-500/20 flex items-center justify-center">
                                <Database className="text-blue-500" size={32} />
                            </div>
                            <h2 className="text-xl font-bold mb-2">No Replication Cluster</h2>
                            <p className="text-muted-foreground mb-6 max-w-md mx-auto">
                                Deploy a Patroni cluster to enable streaming replication
                                with automatic failover across your server mesh.
                            </p>
                            <Button
                                onClick={() => setShowDeployForm(true)}
                                className="bg-gradient-to-r from-blue-500 to-cyan-600 text-white"
                            >
                                <Zap size={16} className="mr-2" /> Deploy Patroni Cluster
                            </Button>
                        </div>
                    )}

                    {/* Replication Health */}
                    {health && (
                        <div className="space-y-6">
                            {/* Primary */}
                            {health.primary && (
                                <div className="bg-card border-2 border-emerald-500/30 rounded-xl p-6">
                                    <div className="flex items-center justify-between mb-4">
                                        <div className="flex items-center gap-3">
                                            <div className="w-12 h-12 rounded-lg bg-emerald-500/10 flex items-center justify-center">
                                                <Crown className="text-emerald-500" size={22} />
                                            </div>
                                            <div>
                                                <h3 className="font-bold text-lg flex items-center gap-2">
                                                    {health.primary.name}
                                                    <span className="text-[10px] px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-500 font-bold uppercase">
                                                        Primary
                                                    </span>
                                                </h3>
                                                <p className="text-xs text-muted-foreground">
                                                    {health.primary.server} • {health.primary.wg_address}
                                                </p>
                                            </div>
                                        </div>
                                        <div className="text-right">
                                            <span className="text-xs font-mono text-muted-foreground">
                                                {health.primary.state || 'running'}
                                            </span>
                                            {health.primary.pg_version && (
                                                <p className="text-[10px] text-muted-foreground">
                                                    PG {health.primary.pg_version}
                                                </p>
                                            )}
                                        </div>
                                    </div>
                                </div>
                            )}

                            {/* WAL Stream Arrow */}
                            {health.replicas.length > 0 && (
                                <div className="flex items-center justify-center gap-2 text-muted-foreground">
                                    <div className="h-px w-12 bg-blue-500/50" />
                                    <span className="text-xs">WAL Stream</span>
                                    <div className="h-px w-12 bg-blue-500/50" />
                                </div>
                            )}

                            {/* Replicas */}
                            {health.replicas.map(replica => (
                                <div
                                    key={replica.wg_address}
                                    className="bg-card border border-border rounded-xl p-6"
                                >
                                    <div className="flex items-center justify-between">
                                        <div className="flex items-center gap-3">
                                            <div className="w-12 h-12 rounded-lg bg-blue-500/10 flex items-center justify-center">
                                                <Server className="text-blue-500" size={22} />
                                            </div>
                                            <div>
                                                <h3 className="font-bold text-lg flex items-center gap-2">
                                                    {replica.name}
                                                    <span className="text-[10px] px-2 py-0.5 rounded bg-blue-500/10 text-blue-500 font-bold uppercase">
                                                        Replica
                                                    </span>
                                                </h3>
                                                <p className="text-xs text-muted-foreground">
                                                    {replica.server} • {replica.wg_address}
                                                </p>
                                            </div>
                                        </div>
                                        <div className="flex items-center gap-4">
                                            {/* Lag indicator */}
                                            <div className="text-right">
                                                <p className="text-xs text-muted-foreground">Replication Lag</p>
                                                <p className={`font-bold text-sm ${lagColor(replica.lag_bytes)}`}>
                                                    {formatLag(replica.lag_bytes)}
                                                </p>
                                            </div>
                                            {/* Actions */}
                                            <div className="flex gap-2">
                                                <Button
                                                    variant="outline" size="sm"
                                                    onClick={() => triggerFailover(replica.wg_address)}
                                                    title="Promote to primary"
                                                >
                                                    <ArrowRightLeft size={14} className="mr-1" />
                                                    Failover
                                                </Button>
                                                <Button
                                                    variant="ghost" size="sm"
                                                    onClick={() => reinitReplica(replica.wg_address)}
                                                    title="Rebuild from scratch"
                                                >
                                                    <RotateCcw size={14} />
                                                </Button>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            ))}

                            {/* Unreachable nodes */}
                            {health.nodes.filter(n => n.status.includes('UNREACHABLE')).length > 0 && (
                                <div className="bg-red-500/5 border border-red-500/20 rounded-xl p-4">
                                    <h3 className="font-bold text-sm text-red-500 flex items-center gap-2 mb-2">
                                        <AlertTriangle size={14} />
                                        Unreachable Nodes
                                    </h3>
                                    {health.nodes.filter(n => n.status.includes('UNREACHABLE')).map(node => (
                                        <p key={node.wg_address} className="text-xs text-muted-foreground">
                                            {node.name} ({node.wg_address}) — {node.status}
                                        </p>
                                    ))}
                                </div>
                            )}
                        </div>
                    )}
                </div>
            </div>
        </DashboardShell>
    );
}
