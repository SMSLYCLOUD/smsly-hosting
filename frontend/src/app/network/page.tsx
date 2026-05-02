'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { Button } from '@/components/ui/button';
import { useToast } from '@/components/ui/use-toast';
import {
    Network, Plus, Trash2, RefreshCw, Loader2, Shield,
    Wifi, WifiOff, Globe, ChevronDown, Zap, ArrowRight
} from 'lucide-react';
import api from '@/lib/api';

interface Peer {
    id: string;
    server: string | null;
    server_name: string;
    server_host: string;
    public_key: string;
    wg_address: string;
    endpoint: string;
    is_active: boolean;
    is_local: boolean;
    latency_ms: number | null;
    last_handshake: string | null;
}

interface MeshNetwork {
    id: string;
    name: string;
    subnet: string;
    listen_port: number;
    interface_name: string;
    is_active: boolean;
    mesh_status?: 'UNKNOWN' | 'DEPLOYING' | 'ACTIVE' | 'FAILED';
    mesh_last_error?: string;
    mesh_last_deployed_at?: string | null;
    peers: Peer[];
    peer_count: number;
}

interface Server {
    id: string;
    name: string;
    host: string;
    status: string;
}

export default function NetworkPage() {
    const { toast } = useToast();
    const [meshes, setMeshes] = useState<MeshNetwork[]>([]);
    const [servers, setServers] = useState<Server[]>([]);
    const [loading, setLoading] = useState(true);
    const [creating, setCreating] = useState(false);
    const [oneClicking, setOneClicking] = useState(false);
    const [deploying, setDeploying] = useState<string | null>(null);
    const [checkingHealth, setCheckingHealth] = useState<string | null>(null);
    const [addingPeer, setAddingPeer] = useState<string | null>(null);
    const [showCreateForm, setShowCreateForm] = useState(false);
    const [newMeshName, setNewMeshName] = useState('production');
    const [newMeshSubnet, setNewMeshSubnet] = useState('10.100.0.0/24');
    const [selectedServerId, setSelectedServerId] = useState('');

    const fetchData = useCallback(async () => {
        try {
            const [meshRes, serverRes] = await Promise.all([
                api.get('/mesh/'),
                api.get('/servers/'),
            ]);
            setMeshes(Array.isArray(meshRes.data) ? meshRes.data : meshRes.data.results || []);
            setServers(Array.isArray(serverRes.data) ? serverRes.data : serverRes.data.results || []);
        } catch (err) {
            console.error('Failed to load mesh data:', err);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        fetchData();
        const interval = setInterval(fetchData, 15000);
        return () => clearInterval(interval);
    }, [fetchData]);

    const createMesh = async () => {
        setCreating(true);
        try {
            await api.post('/mesh/', {
                name: newMeshName,
                subnet: newMeshSubnet,
            });
            toast({ title: 'Mesh Created', description: `Created mesh "${newMeshName}" with subnet ${newMeshSubnet}` });
            setShowCreateForm(false);
            fetchData();
        } catch (err: any) {
            toast({ title: 'Error', description: err?.response?.data?.detail || 'Failed to create mesh', variant: 'destructive' });
        } finally {
            setCreating(false);
        }
    };

    const oneClickMesh = async () => {
        setOneClicking(true);
        try {
            let meshId: string | null = meshes[0]?.id || null;
            if (!meshId) {
                const res = await api.post('/mesh/', {
                    name: 'auto-mesh',
                    subnet: '10.10.0.0/24',
                });
                meshId = res.data?.id || res.data?.pk || null;
            }
            if (!meshId) throw new Error('Could not determine mesh id');

            // fetch latest servers and mesh to avoid stale state
            const serverRes = await api.get('/servers/');
            const serversList: Server[] = Array.isArray(serverRes.data) ? serverRes.data : serverRes.data.results || [];
            const meshRes = await api.get(`/mesh/${meshId}/`);
            const existingPeers: string[] = (meshRes.data?.peers || []).map((p: any) => p.server).filter(Boolean);

            for (const srv of serversList) {
                if (existingPeers.includes(srv.id)) continue;
                await api.post(`/mesh/${meshId}/add-peer/`, { server_id: srv.id });
            }
            toast({ title: 'Mesh Ready', description: 'Created/updated mesh and added all servers.' });
            fetchData();
        } catch (err: any) {
            toast({ title: 'Error', description: err?.response?.data?.error || err?.message || 'One-click mesh failed', variant: 'destructive' });
        } finally {
            setOneClicking(false);
        }
    };

    const addPeer = async (meshId: string) => {
        if (!selectedServerId) return;
        setAddingPeer(meshId);
        try {
            await api.post(`/mesh/${meshId}/add-peer/`, { server_id: selectedServerId });
            toast({ title: 'Peer Added', description: 'Server added to mesh. WireGuard configs deployed to all peers.' });
            setSelectedServerId('');
            fetchData();
        } catch (err: any) {
            toast({ title: 'Error', description: err?.response?.data?.error || 'Failed to add peer', variant: 'destructive' });
        } finally {
            setAddingPeer(null);
        }
    };

    const removePeer = async (meshId: string, peerId: string) => {
        if (!confirm('Remove this peer from the mesh? WireGuard will be torn down on the target server.')) return;
        try {
            await api.post(`/mesh/${meshId}/remove-peer/`, { peer_id: peerId });
            toast({ title: 'Peer Removed', description: 'Peer removed and configs updated.' });
            fetchData();
        } catch (err: any) {
            toast({ title: 'Error', description: err?.response?.data?.error || 'Failed to remove peer', variant: 'destructive' });
        }
    };

    const deployMesh = async (meshId: string) => {
        setDeploying(meshId);
        try {
            await api.post(`/mesh/${meshId}/deploy/`);
            toast({ title: 'Deployed', description: 'WireGuard configs deployed to all peers.' });
            fetchData();
        } catch (err: any) {
            toast({ title: 'Error', description: err?.response?.data?.error || 'Deploy failed', variant: 'destructive' });
        } finally {
            setDeploying(null);
        }
    };

    const checkHealth = async (meshId: string) => {
        setCheckingHealth(meshId);
        try {
            const res = await api.get(`/mesh/${meshId}/health/`);
            toast({ title: 'Health Check', description: `Checked ${res.data?.peers?.length || 0} peers` });
            fetchData();
        } catch (err: any) {
            toast({ title: 'Error', description: 'Health check failed', variant: 'destructive' });
        } finally {
            setCheckingHealth(null);
        }
    };

    const deleteMesh = async (meshId: string) => {
        if (!confirm('Delete this mesh network? WireGuard will be torn down on all peers.')) return;
        try {
            await api.delete(`/mesh/${meshId}/`);
            toast({ title: 'Mesh Deleted' });
            fetchData();
        } catch (err: any) {
            toast({ title: 'Error', description: 'Failed to delete mesh', variant: 'destructive' });
        }
    };

    // Determine which servers AREN'T already in a mesh
    const getAvailableServers = (mesh: MeshNetwork) => {
        const peerServerIds = new Set(mesh.peers.map(p => p.server).filter(Boolean));
        return servers.filter(s => !peerServerIds.has(s.id));
    };

    return (
        <DashboardShell>
            <div className="flex-1 p-8 relative z-10">
                <div className="max-w-5xl mx-auto space-y-8">
                    {/* Header */}
                    <div className="flex items-center justify-between">
                        <div>
                            <h1 className="text-3xl font-bold tracking-tight flex items-center gap-3">
                                <Network className="text-purple-500" size={28} />
                                VPN Mesh Network
                            </h1>
                            <p className="text-muted-foreground mt-1">
                                Encrypted WireGuard tunnels between your servers
                            </p>
                        </div>
                        <div className="flex gap-2">
                            <Button
                                variant="outline"
                                onClick={oneClickMesh}
                                disabled={oneClicking || loading}
                            >
                                {oneClicking ? <Loader2 size={14} className="animate-spin mr-2" /> : <Zap size={14} className="mr-2" />}
                                One-click Mesh
                            </Button>
                            <Button
                                onClick={() => setShowCreateForm(!showCreateForm)}
                                className="bg-gradient-to-r from-purple-500 to-indigo-600 text-white shadow-lg shadow-purple-500/25"
                            >
                                <Plus size={14} className="mr-2" />
                                Create Mesh
                            </Button>
                        </div>
                    </div>

                    {/* Create Form */}
                    {showCreateForm && (
                        <div className="bg-card border border-border rounded-xl p-6 space-y-4">
                            <h2 className="text-lg font-semibold flex items-center gap-2">
                                <Shield className="text-purple-500" size={18} />
                                New Mesh Network
                            </h2>
                            <div className="grid grid-cols-2 gap-4">
                                <div>
                                    <label className="text-xs font-medium text-muted-foreground">Name</label>
                                    <input
                                        value={newMeshName}
                                        onChange={e => setNewMeshName(e.target.value)}
                                        placeholder="production"
                                        className="w-full mt-1 px-3 py-2 rounded-lg bg-background border border-border text-sm"
                                    />
                                </div>
                                <div>
                                    <label className="text-xs font-medium text-muted-foreground">Subnet</label>
                                    <input
                                        value={newMeshSubnet}
                                        onChange={e => setNewMeshSubnet(e.target.value)}
                                        placeholder="10.100.0.0/24"
                                        className="w-full mt-1 px-3 py-2 rounded-lg bg-background border border-border text-sm"
                                    />
                                </div>
                            </div>
                            <div className="flex gap-2 justify-end">
                                <Button variant="outline" onClick={() => setShowCreateForm(false)}>Cancel</Button>
                                <Button onClick={createMesh} disabled={creating || !newMeshName}>
                                    {creating ? <Loader2 size={14} className="animate-spin mr-2" /> : <Zap size={14} className="mr-2" />}
                                    Create
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

                    {/* Empty State */}
                    {!loading && meshes.length === 0 && !showCreateForm && (
                        <div className="text-center py-16">
                            <div className="w-20 h-20 mx-auto mb-4 rounded-3xl bg-purple-500/10 border border-purple-500/20 flex items-center justify-center">
                                <Network className="text-purple-500" size={32} />
                            </div>
                            <h2 className="text-xl font-bold mb-2">No Mesh Networks</h2>
                            <p className="text-muted-foreground mb-6 max-w-md mx-auto">
                                Create a WireGuard VPN mesh to securely connect your servers
                                with encrypted tunnels.
                            </p>
                            <Button
                                onClick={() => setShowCreateForm(true)}
                                className="bg-gradient-to-r from-purple-500 to-indigo-600 text-white"
                            >
                                <Plus size={16} className="mr-2" /> Create Mesh Network
                            </Button>
                        </div>
                    )}

                    {/* Mesh Cards */}
                    {meshes.map(mesh => {
                        const available = getAvailableServers(mesh);
                        return (
                            <div key={mesh.id} className="bg-card border border-border rounded-xl overflow-hidden">
                                {/* Mesh Header */}
                                <div className="px-6 py-4 border-b border-border flex items-center justify-between">
                                    <div className="flex items-center gap-3">
                                        <div className="w-10 h-10 rounded-lg bg-purple-500/10 flex items-center justify-center">
                                            <Shield className="text-purple-500" size={18} />
                                        </div>
                                        <div>
                                            <h3 className="font-bold text-lg">{mesh.name}</h3>
                                            <div className="flex items-center gap-3 text-xs text-muted-foreground">
                                                <span>{mesh.subnet}</span>
                                                <span>•</span>
                                                <span>Port {mesh.listen_port}</span>
                                                <span>•</span>
                                                <span>{mesh.peer_count} peers</span>
                                            </div>
                                            {mesh.mesh_last_error && (
                                                <p className="mt-1 text-xs text-red-500">{mesh.mesh_last_error}</p>
                                            )}
                                        </div>
                                    </div>
                                    <div className="flex items-center gap-2">
                                        <span className={`text-xs px-2.5 py-1 rounded-full font-bold uppercase ${
                                            mesh.mesh_status === 'ACTIVE'
                                                ? 'bg-emerald-500/10 text-emerald-500'
                                                : mesh.mesh_status === 'FAILED'
                                                  ? 'bg-red-500/10 text-red-500'
                                                  : mesh.mesh_status === 'DEPLOYING'
                                                    ? 'bg-blue-500/10 text-blue-500'
                                                    : 'bg-zinc-500/10 text-zinc-500'
                                        }`}>
                                            {mesh.mesh_status || 'UNKNOWN'}
                                        </span>
                                        <Button
                                            variant="outline" size="sm"
                                            onClick={() => checkHealth(mesh.id)}
                                            disabled={checkingHealth === mesh.id}
                                        >
                                            {checkingHealth === mesh.id
                                                ? <Loader2 size={14} className="animate-spin mr-1" />
                                                : <RefreshCw size={14} className="mr-1" />}
                                            Health
                                        </Button>
                                        <Button
                                            variant="outline" size="sm"
                                            onClick={() => deployMesh(mesh.id)}
                                            disabled={deploying === mesh.id}
                                        >
                                            {deploying === mesh.id
                                                ? <Loader2 size={14} className="animate-spin mr-1" />
                                                : <Zap size={14} className="mr-1" />}
                                            Deploy All
                                        </Button>
                                        <Button
                                            variant="ghost" size="sm"
                                            className="text-red-500 hover:bg-red-500/10"
                                            onClick={() => deleteMesh(mesh.id)}
                                        >
                                            <Trash2 size={14} />
                                        </Button>
                                    </div>
                                </div>

                                {/* Peer Topology Visualization */}
                                <div className="p-6">
                                    {mesh.peers.length === 0 ? (
                                        <p className="text-sm text-muted-foreground text-center py-4">
                                            No peers yet. Add your first server below.
                                        </p>
                                    ) : (
                                        <div className="flex flex-wrap gap-4 justify-center mb-6">
                                            {mesh.peers.map((peer, idx) => (
                                                <React.Fragment key={peer.id}>
                                                    {idx > 0 && (
                                                        <div className="flex items-center text-muted-foreground">
                                                            <div className={`h-px w-8 ${peer.latency_ms !== null ? 'bg-emerald-500' : 'bg-red-500/50'}`} />
                                                            <ArrowRight size={14} className={peer.latency_ms !== null ? 'text-emerald-500' : 'text-red-500/50'} />
                                                        </div>
                                                    )}
                                                    <div className={`relative p-4 rounded-xl border-2 min-w-[160px] text-center transition-all ${
                                                        peer.latency_ms !== null
                                                            ? 'border-emerald-500/30 bg-emerald-500/5'
                                                            : 'border-zinc-700 bg-zinc-900/50'
                                                    }`}>
                                                        {peer.is_local && (
                                                            <span className="absolute -top-2 left-1/2 -translate-x-1/2 text-[10px] px-2 py-0.5 rounded bg-purple-500/20 text-purple-400 font-bold uppercase">
                                                                This Server
                                                            </span>
                                                        )}
                                                        <div className="flex items-center justify-center mb-2">
                                                            {peer.latency_ms !== null ? (
                                                                <Wifi className="text-emerald-500" size={20} />
                                                            ) : (
                                                                <WifiOff className="text-zinc-500" size={20} />
                                                            )}
                                                        </div>
                                                        <p className="font-bold text-sm">{peer.server_name || 'Local'}</p>
                                                        <p className="text-xs text-muted-foreground font-mono">{peer.wg_address}</p>
                                                        {peer.endpoint && (
                                                            <p className="text-[10px] text-muted-foreground mt-1">{peer.endpoint}</p>
                                                        )}
                                                        {peer.latency_ms !== null && (
                                                            <p className="text-xs text-emerald-500 font-bold mt-1">
                                                                {peer.latency_ms.toFixed(1)} ms
                                                            </p>
                                                        )}
                                                        {!peer.is_local && (
                                                            <button
                                                                onClick={() => removePeer(mesh.id, peer.id)}
                                                                className="absolute -top-2 -right-2 w-5 h-5 bg-red-500/20 hover:bg-red-500/40 rounded-full flex items-center justify-center text-red-500 text-xs transition-colors"
                                                                title="Remove peer"
                                                            >
                                                                ×
                                                            </button>
                                                        )}
                                                    </div>
                                                </React.Fragment>
                                            ))}
                                        </div>
                                    )}

                                    {/* Add Peer */}
                                    {available.length > 0 && (
                                        <div className="border-t border-border pt-4 flex items-center gap-3">
                                            <Globe size={14} className="text-muted-foreground" />
                                            <select
                                                value={selectedServerId}
                                                onChange={e => setSelectedServerId(e.target.value)}
                                                className="flex-1 px-3 py-2 rounded-lg bg-background border border-border text-sm"
                                            >
                                                <option value="">Select a server to add...</option>
                                                {available.map(s => (
                                                    <option key={s.id} value={s.id}>
                                                        {s.name} ({s.host})
                                                    </option>
                                                ))}
                                            </select>
                                            <Button
                                                size="sm"
                                                onClick={() => addPeer(mesh.id)}
                                                disabled={!selectedServerId || addingPeer === mesh.id}
                                            >
                                                {addingPeer === mesh.id
                                                    ? <Loader2 size={14} className="animate-spin mr-1" />
                                                    : <Plus size={14} className="mr-1" />}
                                                Add to Mesh
                                            </Button>
                                        </div>
                                    )}
                                    {available.length === 0 && servers.length > 0 && mesh.peers.length > 0 && (
                                        <p className="text-xs text-muted-foreground text-center pt-2">
                                            All servers are already in this mesh.
                                        </p>
                                    )}
                                </div>
                            </div>
                        );
                    })}
                </div>
            </div>
        </DashboardShell>
    );
}
