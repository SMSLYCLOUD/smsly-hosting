'use client';

import { useState, useEffect, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
    Server, Plus, Trash2, RefreshCw, CheckCircle2, XCircle, Loader2,
    Globe, Shield, Wifi, WifiOff, ChevronRight, Monitor, ArrowUpRight
} from 'lucide-react';
import { DashboardShell } from '@/components/layout/DashboardShell';

interface ManagedServer {
    id: string;
    name: string;
    host: string;
    api_url: string;
    ssh_port: number;
    is_primary: boolean;
    status: 'ONLINE' | 'OFFLINE' | 'UNKNOWN';
    last_health_check: string | null;
    server_version: string;
    services_count: number;
    created_at: string;
}

function getToken() {
    return typeof window !== 'undefined' ? localStorage.getItem('auth_token') : null;
}

async function apiFetch(path: string, method = 'GET', body?: object) {
    const token = getToken();
    const res = await fetch(path, {
        method,
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

const STATUS_CONFIG = {
    ONLINE: { icon: Wifi, color: 'text-emerald-500', bg: 'bg-emerald-500/10', border: 'border-emerald-500/20', label: 'Online' },
    OFFLINE: { icon: WifiOff, color: 'text-red-500', bg: 'bg-red-500/10', border: 'border-red-500/20', label: 'Offline' },
    UNKNOWN: { icon: Globe, color: 'text-zinc-500', bg: 'bg-zinc-500/10', border: 'border-zinc-500/20', label: 'Unknown' },
};

export default function ServersPage() {
    const [servers, setServers] = useState<ManagedServer[]>([]);
    const [loading, setLoading] = useState(true);
    const [showAdd, setShowAdd] = useState(false);
    const [checking, setChecking] = useState(false);

    // Form state
    const [form, setForm] = useState({
        name: '', host: '', api_url: '', api_token: '', ssh_port: 22, is_primary: false,
    });

    const fetchServers = useCallback(async () => {
        try {
            const data = await apiFetch('/api/v1/servers/');
            setServers(data.results || data);
        } catch { /* ignore */ }
        setLoading(false);
    }, []);

    useEffect(() => { fetchServers(); }, [fetchServers]);

    const addServer = async () => {
        try {
            await apiFetch('/api/v1/servers/', 'POST', form);
            setShowAdd(false);
            setForm({ name: '', host: '', api_url: '', api_token: '', ssh_port: 22, is_primary: false });
            fetchServers();
        } catch (err: any) {
            alert(`Failed: ${err.message}`);
        }
    };

    const deleteServer = async (id: string) => {
        if (!confirm('Remove this server?')) return;
        try {
            await apiFetch(`/api/v1/servers/${id}/`, 'DELETE');
            fetchServers();
        } catch { /* ignore */ }
    };

    const healthCheck = async (id: string) => {
        try {
            await apiFetch(`/api/v1/servers/${id}/health_check/`, 'POST');
            fetchServers();
        } catch { /* ignore */ }
    };

    const checkAll = async () => {
        setChecking(true);
        try {
            await apiFetch('/api/v1/servers/check_all/', 'POST');
            fetchServers();
        } catch { /* ignore */ }
        setChecking(false);
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
                                <Monitor className="text-blue-500" size={28} />
                                Server Fleet
                            </h1>
                            <p className="text-muted-foreground mt-1">
                                Manage multiple SMSLY Hosting servers from one dashboard
                            </p>
                        </div>
                        <div className="flex items-center gap-3">
                            <button
                                onClick={checkAll}
                                disabled={checking}
                                className="px-4 py-2 rounded-lg border border-border text-sm flex items-center gap-2 hover:bg-muted/50 transition-colors"
                            >
                                {checking ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                                Check All
                            </button>
                            <button
                                onClick={() => setShowAdd(!showAdd)}
                                className="px-4 py-2 rounded-lg bg-gradient-to-r from-blue-500 to-cyan-600 text-white text-sm font-semibold flex items-center gap-2 shadow-lg shadow-blue-500/25"
                            >
                                <Plus size={14} /> Add Server
                            </button>
                        </div>
                    </div>

                    {/* Add Server Form */}
                    <AnimatePresence>
                        {showAdd && (
                            <motion.div
                                initial={{ opacity: 0, height: 0 }}
                                animate={{ opacity: 1, height: 'auto' }}
                                exit={{ opacity: 0, height: 0 }}
                                className="bg-card border border-border rounded-xl p-6 space-y-4"
                            >
                                <h3 className="font-bold">Add Remote Server</h3>
                                <div className="grid grid-cols-2 gap-4">
                                    <div>
                                        <label className="text-xs font-medium text-muted-foreground">Name</label>
                                        <input
                                            value={form.name}
                                            onChange={e => setForm({ ...form, name: e.target.value })}
                                            placeholder="Production VPS"
                                            className="w-full mt-1 px-3 py-2 rounded-lg bg-background border border-border text-sm"
                                        />
                                    </div>
                                    <div>
                                        <label className="text-xs font-medium text-muted-foreground">Host (IP or domain)</label>
                                        <input
                                            value={form.host}
                                            onChange={e => setForm({ ...form, host: e.target.value })}
                                            placeholder="198.51.100.5"
                                            className="w-full mt-1 px-3 py-2 rounded-lg bg-background border border-border text-sm"
                                        />
                                    </div>
                                    <div>
                                        <label className="text-xs font-medium text-muted-foreground">API URL</label>
                                        <input
                                            value={form.api_url}
                                            onChange={e => setForm({ ...form, api_url: e.target.value })}
                                            placeholder="https://hosting.example.com"
                                            className="w-full mt-1 px-3 py-2 rounded-lg bg-background border border-border text-sm"
                                        />
                                    </div>
                                    <div>
                                        <label className="text-xs font-medium text-muted-foreground">API Token</label>
                                        <input
                                            type="password"
                                            value={form.api_token}
                                            onChange={e => setForm({ ...form, api_token: e.target.value })}
                                            placeholder="smsly_..."
                                            className="w-full mt-1 px-3 py-2 rounded-lg bg-background border border-border text-sm"
                                        />
                                    </div>
                                </div>
                                <div className="flex items-center justify-between">
                                    <label className="flex items-center gap-2 text-sm">
                                        <input
                                            type="checkbox"
                                            checked={form.is_primary}
                                            onChange={e => setForm({ ...form, is_primary: e.target.checked })}
                                            className="rounded"
                                        />
                                        Primary server
                                    </label>
                                    <div className="flex gap-2">
                                        <button onClick={() => setShowAdd(false)} className="px-4 py-2 text-sm rounded-lg border border-border hover:bg-muted/50">
                                            Cancel
                                        </button>
                                        <button onClick={addServer} className="px-4 py-2 text-sm rounded-lg bg-blue-500 text-white font-semibold">
                                            Add Server
                                        </button>
                                    </div>
                                </div>
                            </motion.div>
                        )}
                    </AnimatePresence>

                    {/* Loading */}
                    {loading && (
                        <div className="text-center py-16">
                            <Loader2 className="animate-spin mx-auto text-muted-foreground" size={24} />
                        </div>
                    )}

                    {/* Empty State */}
                    {!loading && servers.length === 0 && (
                        <div className="text-center py-16">
                            <div className="w-20 h-20 mx-auto mb-4 rounded-3xl bg-blue-500/10 border border-blue-500/20 flex items-center justify-center">
                                <Server className="text-blue-500" size={32} />
                            </div>
                            <h2 className="text-xl font-bold mb-2">No Servers Added</h2>
                            <p className="text-muted-foreground mb-6">
                                Add your first SMSLY Hosting server to control it from here.
                            </p>
                            <button
                                onClick={() => setShowAdd(true)}
                                className="px-6 py-2.5 rounded-xl bg-blue-500 text-white font-semibold"
                            >
                                <Plus size={16} className="inline mr-2" /> Add Server
                            </button>
                        </div>
                    )}

                    {/* Server Cards */}
                    {!loading && servers.length > 0 && (
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            {servers.map((server, idx) => {
                                const sc = STATUS_CONFIG[server.status] || STATUS_CONFIG.UNKNOWN;
                                const StatusIcon = sc.icon;
                                return (
                                    <motion.div
                                        key={server.id}
                                        initial={{ opacity: 0, y: 20 }}
                                        animate={{ opacity: 1, y: 0 }}
                                        transition={{ delay: idx * 0.05 }}
                                        className={`bg-card border rounded-xl p-5 space-y-4 ${sc.border} hover:shadow-lg transition-shadow`}
                                    >
                                        {/* Header */}
                                        <div className="flex items-center justify-between">
                                            <div className="flex items-center gap-3">
                                                <div className={`w-10 h-10 rounded-lg ${sc.bg} flex items-center justify-center`}>
                                                    <StatusIcon className={sc.color} size={18} />
                                                </div>
                                                <div>
                                                    <h3 className="font-bold flex items-center gap-2">
                                                        {server.name}
                                                        {server.is_primary && (
                                                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-500 font-bold uppercase">
                                                                Primary
                                                            </span>
                                                        )}
                                                    </h3>
                                                    <p className="text-xs text-muted-foreground">{server.host}</p>
                                                </div>
                                            </div>
                                            <span className={`text-xs font-bold ${sc.color}`}>{sc.label}</span>
                                        </div>

                                        {/* Stats */}
                                        <div className="grid grid-cols-3 gap-3 text-center">
                                            <div className="bg-muted/30 rounded-lg p-2">
                                                <p className="text-lg font-bold">{server.services_count}</p>
                                                <p className="text-[10px] text-muted-foreground uppercase">Services</p>
                                            </div>
                                            <div className="bg-muted/30 rounded-lg p-2">
                                                <p className="text-lg font-bold">{server.ssh_port}</p>
                                                <p className="text-[10px] text-muted-foreground uppercase">SSH Port</p>
                                            </div>
                                            <div className="bg-muted/30 rounded-lg p-2">
                                                <p className="text-xs font-medium text-muted-foreground mt-1">
                                                    {server.last_health_check
                                                        ? new Date(server.last_health_check).toLocaleTimeString()
                                                        : 'Never'}
                                                </p>
                                                <p className="text-[10px] text-muted-foreground uppercase">Last Check</p>
                                            </div>
                                        </div>

                                        {/* Actions */}
                                        <div className="flex items-center justify-between pt-2 border-t border-border">
                                            <div className="flex items-center gap-2">
                                                <button
                                                    onClick={() => healthCheck(server.id)}
                                                    className="text-xs px-2.5 py-1.5 rounded-lg border border-border hover:bg-muted/50 flex items-center gap-1.5"
                                                >
                                                    <RefreshCw size={12} /> Check
                                                </button>
                                                <a
                                                    href={server.api_url}
                                                    target="_blank"
                                                    rel="noopener noreferrer"
                                                    className="text-xs px-2.5 py-1.5 rounded-lg border border-border hover:bg-muted/50 flex items-center gap-1.5"
                                                >
                                                    <ArrowUpRight size={12} /> Open
                                                </a>
                                            </div>
                                            <button
                                                onClick={() => deleteServer(server.id)}
                                                className="text-xs px-2.5 py-1.5 rounded-lg text-red-500 hover:bg-red-500/10 flex items-center gap-1.5"
                                            >
                                                <Trash2 size={12} /> Remove
                                            </button>
                                        </div>
                                    </motion.div>
                                );
                            })}
                        </div>
                    )}
                </motion.div>
            </div>
        </DashboardShell>
    );
}
