'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { Button } from '@/components/ui/button';
import { useToast } from '@/components/ui/use-toast';
import {
    Database, RefreshCw, Loader2, Crown, Server, AlertTriangle,
    ArrowRightLeft, RotateCcw, Zap, Eye, CheckCircle2, ExternalLink
} from 'lucide-react';
import api, { databaseReplicasApi, DatabaseReplica } from '@/lib/api';
import { AddReplicaCard, ReplicaRow } from '@/components/settings/DatabaseReplicasTab';

interface MeshPeer {
    id: string;
    server?: { name: string };
    wg_address: string;
    is_active: boolean;
    is_local: boolean;
}

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
    local_replicas?: {
        name: string;
        host: string;
        port: number;
        status: string;
        lag_seconds: number | null;
        last_checked_at: string | null;
    }[];
}

interface MeshNetwork {
    id: string;
    name: string;
    subnet: string;
    peer_count: number;
    replication_status?: 'DISABLED' | 'DEPLOYING' | 'ACTIVE' | 'FAILED';
    replication_last_error?: string;
    replication_updated_at?: string | null;
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
    const [replPassword, setReplPassword] = useState('');
    const [oneClicking, setOneClicking] = useState(false);
    const [syncing, setSyncing] = useState(false);
    const [disabling, setDisabling] = useState(false);

    // Scale out states
    const [availablePeers, setAvailablePeers] = useState<MeshPeer[]>([]);
    const [connectState, setConnectState] = useState<Record<string, { status: 'idle' | 'testing' | 'awaiting_approval' | 'connecting' | 'connected' }>>({});

    // External database replicas
    const [externalReplicas, setExternalReplicas] = useState<DatabaseReplica[]>([]);
    const [showExternalForm, setShowExternalForm] = useState(false);

    // Local HA status (independent of mesh)
    const [localHealth, setLocalHealth] = useState<{ primary: { name: string; status: string } | null; local_replicas: { name: string; host: string; port: number; status: string; lag_seconds: number | null }[] } | null>(null);

    // Redis HA status (independent of mesh)
    const [redisHealth, setRedisHealth] = useState<{
        primary: { name: string; status: string; role: string | null; connected_slaves: number } | null;
        replica: { name: string; status: string; role: string | null; master_link_status: string | null; lag_seconds: number | null } | null;
        sentinels: { name: string; status: string; ip: string; port: number }[];
    } | null>(null);

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

            // Fetch mesh peers to find available nodes
            const meshRes = await api.get(`/mesh/${selectedMesh}/`);
            const peers: MeshPeer[] = meshRes.data.peers || [];

            // Filter peers that are active and not in the health nodes list
            const inClusterIps = new Set(res.data.nodes.map((n: any) => n.wg_address));
            const available = peers.filter(p => p.is_active && !inClusterIps.has(p.wg_address));

            setAvailablePeers(available);

            // Initialize connect state for available peers
            const newState: Record<string, any> = {};
            available.forEach(p => {
                newState[p.wg_address] = connectState[p.wg_address] || { status: 'idle' };
            });
            setConnectState(newState);

        } catch (err: any) {
            if (err?.response?.status !== 404) {
                toast({ title: 'Error', description: 'Health check failed', variant: 'destructive' });
            }
            setHealth(null);
            setAvailablePeers([]);
        } finally {
            setChecking(false);
        }
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [selectedMesh, toast]);

    useEffect(() => { fetchMeshes(); }, [fetchMeshes]);
    useEffect(() => { if (selectedMesh) checkHealth(); }, [selectedMesh, checkHealth]);

    const fetchExternalReplicas = useCallback(async () => {
        try {
            const list = await databaseReplicasApi.list();
            setExternalReplicas(list);
        } catch {
            // silently ignore — non-critical
        }
    }, []);

    useEffect(() => { fetchExternalReplicas(); }, [fetchExternalReplicas]);

    const fetchLocalHealth = useCallback(async () => {
        try {
            const res = await api.get('/replication/local-health/');
            setLocalHealth(res.data);
        } catch {
            // silently ignore — local HA may not be running
        }
    }, []);

    useEffect(() => { fetchLocalHealth(); }, [fetchLocalHealth]);

    const fetchRedisHealth = useCallback(async () => {
        try {
            const res = await api.get('/replication/redis-health/');
            setRedisHealth(res.data);
        } catch {
            // silently ignore — Redis HA may not be running
        }
    }, []);

    useEffect(() => { fetchRedisHealth(); }, [fetchRedisHealth]);

    const runPreflight = async (wgAddress: string) => {
        setConnectState(prev => ({ ...prev, [wgAddress]: { status: 'testing' } }));
        try {
            await api.post('/replication/preflight/', {
                mesh_id: selectedMesh,
                target_wg_address: wgAddress
            });
            setConnectState(prev => ({ ...prev, [wgAddress]: { status: 'awaiting_approval' } }));
            toast({ title: 'Pre-flight Passed', description: `Ready to connect ${wgAddress}.` });
        } catch (err: any) {
            setConnectState(prev => ({ ...prev, [wgAddress]: { status: 'idle' } }));
            toast({
                title: 'Pre-flight Failed',
                description: err?.response?.data?.error || 'Failed to verify node readiness',
                variant: 'destructive'
            });
        }
    };

    const confirmConnect = async (wgAddress: string) => {
        // Need passwords to deploy - ideally we'd prompt for them, but for one-click simplicity
        // we'll require the user to input them or generate new ones (if using auto).
        // Since we don't store the db password in plaintext, we'll prompt the user for it if not auto-deploying.
        let dbPass = dbPassword;
        let adminPass = adminPassword;
        let replPass = replPassword;

        if (!dbPass || !adminPass) {
            dbPass = prompt('Enter DB Superuser Password to connect this replica:') || '';
            if (!dbPass) return;
            adminPass = prompt('Enter Admin Password:') || '';
            if (!adminPass) return;
            replPass = prompt('Enter Replication Password:') || '';
            if (!replPass || replPass === 'repl_pass') {
                alert('Please enter a valid, non-default replication password.');
                return;
            }
        }

        setConnectState(prev => ({ ...prev, [wgAddress]: { status: 'connecting' } }));
        try {
            await api.post('/replication/connect-replica/', {
                mesh_id: selectedMesh,
                target_wg_address: wgAddress,
                db_password: dbPass,
                admin_password: adminPass,
                replication_password: replPass
            });
            setConnectState(prev => ({ ...prev, [wgAddress]: { status: 'connected' } }));
            toast({ title: 'Replica Connected', description: `Successfully deployed replica to ${wgAddress}.` });
            setTimeout(checkHealth, 3000);
        } catch (err: any) {
            setConnectState(prev => ({ ...prev, [wgAddress]: { status: 'idle' } }));
            toast({
                title: 'Connection Failed',
                description: err?.response?.data?.error || 'Failed to connect replica',
                variant: 'destructive'
            });
        }
    };

    const deployReplication = async () => {
        if (!selectedMesh || !dbPassword || !adminPassword || !replPassword || replPassword === 'repl_pass') return;
        setDeploying(true);
        try {
            await api.post('/replication/enable/', {
                mesh_id: selectedMesh,
                db_password: dbPassword,
                admin_password: adminPassword,
                replication_password: replPassword,
            });
            toast({ title: 'Deployment Started', description: 'Patroni cluster is being deployed to all peers.' });
            setShowDeployForm(false);
            setDbPassword(''); setAdminPassword(''); setReplPassword('');
            fetchMeshes();
            setTimeout(checkHealth, 3000);
        } catch (err: any) {
            toast({ title: 'Error', description: err?.response?.data?.error || 'Deploy failed', variant: 'destructive' });
        } finally {
            setDeploying(false);
        }
    };

    const oneClickReplication = async () => {
        setOneClicking(true);
        try {
            // Ensure a mesh exists
            let meshId = selectedMesh;
            if (!meshId) {
                const res = await api.post('/mesh/', { name: 'auto-mesh', subnet: '10.10.0.0/24' }).catch(() => null);
                meshId = res?.data?.id || meshes[0]?.id || '';
                setSelectedMesh(meshId);
            }
            if (!meshId) throw new Error('No mesh available for replication');

            // Lightweight randoms for passwords (browser safe)
            const rand = (len = 24) =>
                Array.from(crypto.getRandomValues(new Uint8Array(len)))
                    .map((b) => ('0' + (b % 36).toString(36)).slice(-1))
                    .join('');

            const dbPass = rand(24);
            const adminPass = rand(16);
            const repPass = rand(20);

            setDbPassword(dbPass);
            setAdminPassword(adminPass);
            setReplPassword(repPass);

            await api.post('/replication/deploy/', {
                mesh_id: meshId,
                db_password: dbPass,
                admin_password: adminPass,
                replication_password: repPass,
            });

            toast({ title: 'Replication Started', description: 'One-click deploy kicked off for this mesh.' });
            setShowDeployForm(false);
            fetchMeshes();
            checkHealth();
        } catch (err: any) {
            toast({ title: 'Error', description: err?.response?.data?.error || err?.message || 'One-click replication failed', variant: 'destructive' });
        } finally {
            setOneClicking(false);
        }
    };

    const syncNow = async () => {
        if (!selectedMesh) return;
        setSyncing(true);
        try {
            const res = await api.post('/replication/sync-now/', { mesh_id: selectedMesh });
            setHealth(res.data.health || null);
            await fetchMeshes();
            toast({ title: 'Replication Synced', description: `Status: ${res.data.status || 'updated'}` });
        } catch (err: any) {
            toast({ title: 'Error', description: err?.response?.data?.error || 'Sync failed', variant: 'destructive' });
        } finally {
            setSyncing(false);
        }
    };

    const disableReplication = async () => {
        if (!selectedMesh) return;
        if (!confirm('Disable replication on this mesh?')) return;
        setDisabling(true);
        try {
            const res = await api.post('/replication/disable/', { mesh_id: selectedMesh });
            setHealth(null);
            await fetchMeshes();
            toast({ title: 'Replication Disabled', description: `Status: ${res.data.status}` });
        } catch (err: any) {
            toast({ title: 'Error', description: err?.response?.data?.error || 'Disable failed', variant: 'destructive' });
        } finally {
            setDisabling(false);
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
    const selectedMeshObj = meshes.find(mesh => mesh.id === selectedMesh);

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
                                Patroni streaming replication with automatic failover, or connect external read replicas
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
                                variant="outline" size="sm"
                                onClick={syncNow}
                                disabled={syncing || !selectedMesh}
                            >
                                {syncing ? <Loader2 size={14} className="animate-spin mr-1" /> : <RotateCcw size={14} className="mr-1" />}
                                Sync Now
                            </Button>
                            <Button
                                variant="outline" size="sm"
                                onClick={disableReplication}
                                disabled={disabling || !selectedMesh}
                            >
                                {disabling ? <Loader2 size={14} className="animate-spin mr-1" /> : <AlertTriangle size={14} className="mr-1" />}
                                Disable
                            </Button>
                            <Button
                                onClick={() => setShowDeployForm(!showDeployForm)}
                                className="bg-gradient-to-r from-blue-500 to-cyan-600 text-white shadow-lg shadow-blue-500/25"
                            >
                                <Zap size={14} className="mr-2" />
                                Deploy Cluster
                            </Button>
                            <Button
                                variant="outline"
                                size="sm"
                                onClick={oneClickReplication}
                                disabled={oneClicking}
                            >
                                {oneClicking ? <Loader2 size={14} className="animate-spin mr-1" /> : <ArrowRightLeft size={14} className="mr-1" />}
                                One-click Replication
                            </Button>
                            <Button
                                variant="outline"
                                size="sm"
                                onClick={() => setShowExternalForm(!showExternalForm)}
                            >
                                <ExternalLink size={14} className="mr-1" />
                                Add External Database
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

                    {selectedMeshObj && (
                        <div className="bg-card border border-border rounded-xl p-4 flex items-center justify-between gap-4">
                            <div>
                                <p className="text-sm font-semibold">{selectedMeshObj.name}</p>
                                <p className="text-xs text-muted-foreground">
                                    {selectedMeshObj.peer_count} peers · {selectedMeshObj.replication_updated_at
                                        ? `updated ${new Date(selectedMeshObj.replication_updated_at).toLocaleTimeString()}`
                                        : 'status pending'}
                                </p>
                                {selectedMeshObj.replication_last_error && (
                                    <p className="mt-1 text-xs text-red-500">{selectedMeshObj.replication_last_error}</p>
                                )}
                            </div>
                            <span className={`text-xs px-2.5 py-1 rounded-full font-bold uppercase ${
                                selectedMeshObj.replication_status === 'ACTIVE'
                                    ? 'bg-emerald-500/10 text-emerald-500'
                                    : selectedMeshObj.replication_status === 'FAILED'
                                      ? 'bg-red-500/10 text-red-500'
                                      : selectedMeshObj.replication_status === 'DEPLOYING'
                                        ? 'bg-blue-500/10 text-blue-500'
                                        : 'bg-zinc-500/10 text-zinc-500'
                            }`}>
                                {selectedMeshObj.replication_status || 'DISABLED'}
                            </span>
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
                                <Button onClick={deployReplication} disabled={deploying || !dbPassword || !adminPassword || !replPassword || replPassword === 'repl_pass'}>
                                    {deploying ? <Loader2 size={14} className="animate-spin mr-2" /> : <Zap size={14} className="mr-2" />}
                                    Deploy
                                </Button>
                            </div>
                        </div>
                    )}

                    {/* External Database Form */}
                    {showExternalForm && (
                        <div className="border border-purple-500/20 rounded-xl p-1">
                            <AddReplicaCard
                                defaultOpen
                                onAdded={(r) => {
                                    setExternalReplicas((cur) => [...cur, r]);
                                    setShowExternalForm(false);
                                }}
                            />
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
                            <div className="flex items-center justify-center gap-3">
                                <Button
                                    onClick={() => setShowDeployForm(true)}
                                    className="bg-gradient-to-r from-blue-500 to-cyan-600 text-white"
                                >
                                    <Zap size={16} className="mr-2" /> Deploy Patroni Cluster
                                </Button>
                                <Button
                                    variant="outline"
                                    onClick={() => setShowExternalForm(true)}
                                >
                                    <ExternalLink size={16} className="mr-2" /> Add External Database
                                </Button>
                            </div>
                        </div>
                    )}

                    {/* Local HA Status — always shown when available, independent of mesh */}
                    {localHealth && (localHealth.primary || localHealth.local_replicas.length > 0) && (
                        <div className="space-y-4 mt-6">
                            <h2 className="text-lg font-bold flex items-center gap-2">
                                <Server className="text-emerald-500" size={20} />
                                Local HA Stack
                            </h2>
                            <p className="text-sm text-muted-foreground -mt-2">
                                PostgreSQL primary and replica running on this host.
                            </p>

                            {/* Primary */}
                            {localHealth.primary && (
                                <div className="bg-card border-2 border-emerald-500/30 rounded-xl p-5">
                                    <div className="flex items-center justify-between">
                                        <div className="flex items-center gap-3">
                                            <div className="w-10 h-10 rounded-lg bg-emerald-500/10 flex items-center justify-center">
                                                <Crown className="text-emerald-500" size={20} />
                                            </div>
                                            <div>
                                                <h3 className="font-bold flex items-center gap-2">
                                                    {localHealth.primary.name}
                                                    <span className={`text-[10px] px-2 py-0.5 rounded font-bold uppercase ${
                                                        localHealth.primary.status === 'OK'
                                                            ? 'bg-emerald-500/10 text-emerald-500'
                                                            : 'bg-red-500/10 text-red-500'
                                                    }`}>
                                                        {localHealth.primary.status === 'OK' ? 'Healthy' : localHealth.primary.status}
                                                    </span>
                                                </h3>
                                                <p className="text-xs text-muted-foreground">Primary (writes)</p>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            )}

                            {/* Replicas */}
                            {localHealth.local_replicas.map((r) => (
                                <div key={r.host} className="bg-card border border-border rounded-xl p-5">
                                    <div className="flex items-center justify-between">
                                        <div className="flex items-center gap-3">
                                            <div className="w-10 h-10 rounded-lg bg-blue-500/10 flex items-center justify-center">
                                                <Server className="text-blue-500" size={20} />
                                            </div>
                                            <div>
                                                <h3 className="font-bold flex items-center gap-2">
                                                    {r.name}
                                                    <span className={`text-[10px] px-2 py-0.5 rounded font-bold uppercase ${
                                                        r.status === 'OK'
                                                            ? 'bg-emerald-500/10 text-emerald-500'
                                                            : 'bg-red-500/10 text-red-500'
                                                    }`}>
                                                        {r.status === 'OK' ? 'Healthy' : r.status}
                                                    </span>
                                                </h3>
                                                <p className="text-xs text-muted-foreground">Replica (reads) — {r.host}:{r.port}</p>
                                            </div>
                                        </div>
                                        {r.lag_seconds != null && (
                                            <div className="text-right">
                                                <p className="text-xs text-muted-foreground">Replication Lag</p>
                                                <p className="font-bold text-sm text-emerald-500">{r.lag_seconds.toFixed(2)}s</p>
                                            </div>
                                        )}
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}

                    {/* Redis HA Status — always shown when available, independent of mesh */}
                    {redisHealth && (redisHealth.primary || redisHealth.replica || redisHealth.sentinels.length > 0) && (
                        <div className="space-y-4 mt-6">
                            <h2 className="text-lg font-bold flex items-center gap-2">
                                <Zap className="text-amber-500" size={20} />
                                Redis HA Stack
                            </h2>
                            <p className="text-sm text-muted-foreground -mt-2">
                                Redis primary, replica, and 3 sentinels running on this host.
                            </p>

                            {/* Redis Primary */}
                            {redisHealth.primary && (
                                <div className="bg-card border-2 border-amber-500/30 rounded-xl p-5">
                                    <div className="flex items-center justify-between">
                                        <div className="flex items-center gap-3">
                                            <div className="w-10 h-10 rounded-lg bg-amber-500/10 flex items-center justify-center">
                                                <Crown className="text-amber-500" size={20} />
                                            </div>
                                            <div>
                                                <h3 className="font-bold flex items-center gap-2">
                                                    {redisHealth.primary.name}
                                                    <span className={`text-[10px] px-2 py-0.5 rounded font-bold uppercase ${
                                                        redisHealth.primary.status === 'OK'
                                                            ? 'bg-emerald-500/10 text-emerald-500'
                                                            : 'bg-red-500/10 text-red-500'
                                                    }`}>
                                                        {redisHealth.primary.status === 'OK' ? 'Healthy' : redisHealth.primary.status}
                                                    </span>
                                                </h3>
                                                <p className="text-xs text-muted-foreground">
                                                    Primary (writes) — {redisHealth.primary.connected_slaves} connected replica{redisHealth.primary.connected_slaves !== 1 ? 's' : ''}
                                                </p>
                                            </div>
                                        </div>
                                        {redisHealth.primary.role && (
                                            <div className="text-right">
                                                <p className="text-xs text-muted-foreground">Role</p>
                                                <p className="font-bold text-sm text-amber-500">{redisHealth.primary.role}</p>
                                            </div>
                                        )}
                                    </div>
                                </div>
                            )}

                            {/* Redis Replica */}
                            {redisHealth.replica && (
                                <div className="bg-card border border-border rounded-xl p-5">
                                    <div className="flex items-center justify-between">
                                        <div className="flex items-center gap-3">
                                            <div className="w-10 h-10 rounded-lg bg-blue-500/10 flex items-center justify-center">
                                                <Server className="text-blue-500" size={20} />
                                            </div>
                                            <div>
                                                <h3 className="font-bold flex items-center gap-2">
                                                    {redisHealth.replica.name}
                                                    <span className={`text-[10px] px-2 py-0.5 rounded font-bold uppercase ${
                                                        redisHealth.replica.status === 'OK'
                                                            ? 'bg-emerald-500/10 text-emerald-500'
                                                            : 'bg-red-500/10 text-red-500'
                                                    }`}>
                                                        {redisHealth.replica.status === 'OK' ? 'Healthy' : redisHealth.replica.status}
                                                    </span>
                                                </h3>
                                                <p className="text-xs text-muted-foreground">
                                                    Replica (reads) — master link: {redisHealth.replica.master_link_status || 'unknown'}
                                                </p>
                                            </div>
                                        </div>
                                        {redisHealth.replica.lag_seconds != null && (
                                            <div className="text-right">
                                                <p className="text-xs text-muted-foreground">Last IO</p>
                                                <p className="font-bold text-sm text-emerald-500">{redisHealth.replica.lag_seconds}s</p>
                                            </div>
                                        )}
                                    </div>
                                </div>
                            )}

                            {/* Sentinels */}
                            {redisHealth.sentinels.length > 0 && (
                                <div className="bg-card border border-border rounded-xl p-5">
                                    <div className="flex items-center gap-3 mb-3">
                                        <div className="w-10 h-10 rounded-lg bg-purple-500/10 flex items-center justify-center">
                                            <Eye className="text-purple-500" size={20} />
                                        </div>
                                        <div>
                                            <h3 className="font-bold">Sentinels</h3>
                                            <p className="text-xs text-muted-foreground">
                                                {redisHealth.sentinels.filter(s => s.status === 'OK').length}/{redisHealth.sentinels.length} healthy
                                            </p>
                                        </div>
                                    </div>
                                    <div className="grid grid-cols-3 gap-2">
                                        {redisHealth.sentinels.map((s) => (
                                            <div key={s.name} className="flex items-center gap-2 px-3 py-2 rounded-lg bg-background">
                                                <span className={`w-2 h-2 rounded-full ${s.status === 'OK' ? 'bg-emerald-500' : 'bg-red-500'}`} />
                                                <span className="text-xs font-mono">{s.name}</span>
                                                <span className={`text-[10px] px-1.5 py-0.5 rounded font-bold uppercase ${
                                                    s.status === 'OK' ? 'bg-emerald-500/10 text-emerald-500' : 'bg-red-500/10 text-red-500'
                                                }`}>
                                                    {s.status === 'OK' ? 'OK' : 'DOWN'}
                                                </span>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            )}
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

                    {/* Available Servers to Connect */}
                    {availablePeers.length > 0 && (
                        <div className="space-y-4 pt-6 border-t border-border mt-8">
                            <h2 className="text-xl font-bold flex items-center gap-2">
                                <Server className="text-blue-500" size={24} />
                                Available Servers
                            </h2>
                            <p className="text-muted-foreground text-sm">
                                These nodes are part of the mesh but are not running a Patroni replica.
                            </p>

                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                {availablePeers.map(peer => {
                                    const cState = connectState[peer.wg_address]?.status || 'idle';

                                    return (
                                        <div key={peer.wg_address} className="bg-card border border-border rounded-xl p-5 flex items-center justify-between">
                                            <div>
                                                <h3 className="font-bold">{peer.server?.name || (peer.is_local ? 'Local Server' : 'Unknown')}</h3>
                                                <p className="text-sm text-muted-foreground font-mono">{peer.wg_address}</p>

                                                {/* Status Badges */}
                                                <div className="mt-2">
                                                    {cState === 'testing' && <span className="text-xs px-2 py-0.5 rounded bg-blue-500/10 text-blue-500">Running Pre-flight...</span>}
                                                    {cState === 'awaiting_approval' && <span className="text-xs px-2 py-0.5 rounded bg-amber-500/10 text-amber-500">Pre-flight Passed</span>}
                                                    {cState === 'connecting' && <span className="text-xs px-2 py-0.5 rounded bg-purple-500/10 text-purple-500">Connecting Replica...</span>}
                                                    {cState === 'connected' && <span className="text-xs px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-500">Connected Successfully</span>}
                                                </div>
                                            </div>

                                            <div className="flex items-center gap-2">
                                                {cState === 'idle' && (
                                                    <Button variant="outline" size="sm" onClick={() => runPreflight(peer.wg_address)}>
                                                        <CheckCircle2 size={14} className="mr-2" /> Connect Replica
                                                    </Button>
                                                )}

                                                {cState === 'testing' && (
                                                    <Button variant="outline" size="sm" disabled>
                                                        <Loader2 size={14} className="animate-spin mr-2" /> Testing
                                                    </Button>
                                                )}

                                                {cState === 'awaiting_approval' && (
                                                    <>
                                                        <Button variant="ghost" size="sm" onClick={() => setConnectState(prev => ({ ...prev, [peer.wg_address]: { status: 'idle' } }))}>
                                                            Cancel
                                                        </Button>
                                                        <Button size="sm" className="bg-emerald-600 hover:bg-emerald-700 text-white" onClick={() => confirmConnect(peer.wg_address)}>
                                                            <Zap size={14} className="mr-2" /> Confirm & Connect
                                                        </Button>
                                                    </>
                                                )}

                                                {cState === 'connecting' && (
                                                    <Button size="sm" disabled className="bg-purple-600">
                                                        <Loader2 size={14} className="animate-spin mr-2" /> Connecting
                                                    </Button>
                                                )}
                                            </div>
                                        </div>
                                    );
                                })}
                            </div>
                        </div>
                    )}

                    {/* External Database Replicas */}
                    {externalReplicas.length > 0 && (
                        <div className="space-y-4 pt-6 border-t border-border mt-8">
                            <div className="flex items-center justify-between">
                                <div>
                                    <h2 className="text-xl font-bold flex items-center gap-2">
                                        <ExternalLink className="text-purple-500" size={24} />
                                        External Replicas
                                    </h2>
                                    <p className="text-muted-foreground text-sm">
                                        Read-only endpoints managed via pgcat. Writes always go to the Patroni primary.
                                    </p>
                                </div>
                                <Button
                                    variant="outline"
                                    size="sm"
                                    onClick={() => setShowExternalForm(!showExternalForm)}
                                >
                                    <Database size={14} className="mr-1" />
                                    Add
                                </Button>
                            </div>

                            <div className="space-y-3">
                                {externalReplicas.map((r) => (
                                    <ReplicaRow
                                        key={r.id}
                                        replica={r}
                                        onUpdated={(updated) =>
                                            setExternalReplicas((cur) => cur.map((x) => (x.id === updated.id ? updated : x)))
                                        }
                                        onDeleted={(id) => setExternalReplicas((cur) => cur.filter((x) => x.id !== id))}
                                    />
                                ))}
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </DashboardShell>
    );
}
