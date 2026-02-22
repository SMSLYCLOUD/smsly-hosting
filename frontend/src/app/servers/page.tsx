'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { useRouter } from 'next/navigation';
import { motion, AnimatePresence } from 'framer-motion';
import {
    Server, Plus, Trash2, RefreshCw, CheckCircle2, XCircle, Loader2,
    Globe, Shield, Wifi, WifiOff, ChevronRight, Monitor, ArrowUpRight,
    Terminal, Key, Lock, Zap, Link2
} from 'lucide-react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { toast } from '@/components/ui/use-toast';
import { useConfirm } from '@/components/ui/confirm-dialog';

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
    provision_status: 'NONE' | 'PENDING' | 'PROVISIONING' | 'DONE' | 'FAILED';
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
    if (res.status === 204) return {};
    return res.json();
}

const STATUS_CONFIG = {
    ONLINE: { icon: Wifi, color: 'text-emerald-500', bg: 'bg-emerald-500/10', border: 'border-emerald-500/20', label: 'Online' },
    OFFLINE: { icon: WifiOff, color: 'text-red-500', bg: 'bg-red-500/10', border: 'border-red-500/20', label: 'Offline' },
    UNKNOWN: { icon: Globe, color: 'text-zinc-500', bg: 'bg-zinc-500/10', border: 'border-zinc-500/20', label: 'Unknown' },
};

const PROVISION_STATUS_CONFIG: Record<string, { color: string; label: string; animate?: boolean }> = {
    NONE: { color: 'text-zinc-500', label: 'Not provisioned' },
    PENDING: { color: 'text-amber-500', label: 'Pending...', animate: true },
    PROVISIONING: { color: 'text-blue-500', label: 'Installing...', animate: true },
    DONE: { color: 'text-emerald-500', label: 'Provisioned' },
    FAILED: { color: 'text-red-500', label: 'Failed' },
};

export default function ServersPage() {
    const router = useRouter();
    const confirm = useConfirm();
    const [servers, setServers] = useState<ManagedServer[]>([]);
    const [loading, setLoading] = useState(true);
    const [showAdd, setShowAdd] = useState(false);
    const [checking, setChecking] = useState(false);
    const [addMode, setAddMode] = useState<'connect' | 'provision'>('provision');
    const [submitting, setSubmitting] = useState(false);

    // Provision log viewer
    const [viewingLogs, setViewingLogs] = useState<string | null>(null);
    const [provisionLogs, setProvisionLogs] = useState('');
    const [provisionStatus, setProvisionStatus] = useState('');
    const logRef = useRef<HTMLPreElement>(null);

    // Connect form
    const [connectForm, setConnectForm] = useState({
        name: '', host: '', api_url: '', api_token: '', gateway_secret: '', ssh_port: 22, is_primary: false,
    });

    // Provision form
    const [provisionForm, setProvisionForm] = useState({
        name: '', host: '', ssh_port: 22, ssh_user: 'root',
        ssh_auth_method: 'password' as 'password' | 'key',
        ssh_password: '', ssh_key: '', is_primary: false,
    });

    const fetchServers = useCallback(async () => {
        try {
            const data = await apiFetch('/api/v1/servers/');
            setServers(data.results || data);
        } catch { /* ignore */ }
        setLoading(false);
    }, []);

    useEffect(() => {
        fetchServers();
        const interval = setInterval(fetchServers, 10000);
        return () => clearInterval(interval);
    }, [fetchServers]);

    // Poll provision logs when viewing
    useEffect(() => {
        if (!viewingLogs) return;
        let active = true;
        const poll = async () => {
            while (active) {
                try {
                    const data = await apiFetch(`/api/v1/servers/${viewingLogs}/provision-logs/`);
                    if (!active) break;
                    setProvisionLogs(data.provision_logs || '');
                    setProvisionStatus(data.provision_status || '');
                    // Auto-scroll
                    if (logRef.current) {
                        logRef.current.scrollTop = logRef.current.scrollHeight;
                    }
                    // Stop polling if done or failed
                    if (data.provision_status === 'DONE' || data.provision_status === 'FAILED') {
                        fetchServers();
                        break;
                    }
                } catch { break; }
                await new Promise(r => setTimeout(r, 2000));
            }
        };
        poll();
        return () => { active = false; };
    }, [viewingLogs, fetchServers]);

    const addServerConnect = async () => {
        setSubmitting(true);
        try {
            await apiFetch('/api/v1/servers/', 'POST', connectForm);
            setShowAdd(false);
            setConnectForm({ name: '', host: '', api_url: '', api_token: '', gateway_secret: '', ssh_port: 22, is_primary: false });
            fetchServers();
        } catch (err: any) {
            toast({ title: 'Failed to connect server', description: err.message, variant: 'destructive' });
        }
        setSubmitting(false);
    };

    const addServerProvision = async () => {
        setSubmitting(true);
        try {
            const result = await apiFetch('/api/v1/servers/provision/', 'POST', provisionForm);
            setShowAdd(false);
            setProvisionForm({
                name: '', host: '', ssh_port: 22, ssh_user: 'root',
                ssh_auth_method: 'password', ssh_password: '', ssh_key: '', is_primary: false,
            });
            fetchServers();
            // Auto-open provision logs
            if (result.id) {
                setViewingLogs(result.id);
                setProvisionLogs('');
                setProvisionStatus('PENDING');
            }
        } catch (err: any) {
            toast({ title: 'Failed to provision server', description: err.message, variant: 'destructive' });
        }
        setSubmitting(false);
    };

    const deleteServer = async (id: string) => {
        if (!await confirm({ title: 'Remove server?', message: 'Are you sure you want to remove this server?', variant: 'destructive', confirmText: 'Remove' })) return;
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
                                Manage multiple CloudNeuron servers from one dashboard
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
                                className="bg-card border border-border rounded-xl p-6 space-y-5"
                            >
                                {/* Mode Tabs */}
                                <div className="flex items-center gap-1 p-1 bg-muted/50 rounded-lg w-fit">
                                    <button
                                        onClick={() => setAddMode('provision')}
                                        className={`px-4 py-2 rounded-md text-sm font-medium flex items-center gap-2 transition-all ${
                                            addMode === 'provision'
                                                ? 'bg-gradient-to-r from-blue-500 to-cyan-600 text-white shadow-md'
                                                : 'text-muted-foreground hover:text-foreground'
                                        }`}
                                    >
                                        <Zap size={14} />
                                        Provision New
                                    </button>
                                    <button
                                        onClick={() => setAddMode('connect')}
                                        className={`px-4 py-2 rounded-md text-sm font-medium flex items-center gap-2 transition-all ${
                                            addMode === 'connect'
                                                ? 'bg-gradient-to-r from-blue-500 to-cyan-600 text-white shadow-md'
                                                : 'text-muted-foreground hover:text-foreground'
                                        }`}
                                    >
                                        <Link2 size={14} />
                                        Connect Existing
                                    </button>
                                </div>

                                {addMode === 'provision' ? (
                                    <>
                                        <div>
                                            <p className="text-sm text-muted-foreground mb-4">
                                                Enter your VPS SSH credentials. CloudNeuron will be automatically installed and configured.
                                            </p>
                                        </div>
                                        <div className="grid grid-cols-2 gap-4">
                                            <div>
                                                <label className="text-xs font-medium text-muted-foreground">Server Name</label>
                                                <input
                                                    value={provisionForm.name}
                                                    onChange={e => setProvisionForm({ ...provisionForm, name: e.target.value })}
                                                    placeholder="Production VPS"
                                                    className="w-full mt-1 px-3 py-2 rounded-lg bg-background border border-border text-sm"
                                                />
                                            </div>
                                            <div>
                                                <label className="text-xs font-medium text-muted-foreground">Host / IP Address</label>
                                                <input
                                                    value={provisionForm.host}
                                                    onChange={e => setProvisionForm({ ...provisionForm, host: e.target.value })}
                                                    placeholder="198.51.100.5"
                                                    className="w-full mt-1 px-3 py-2 rounded-lg bg-background border border-border text-sm"
                                                />
                                            </div>
                                            <div>
                                                <label className="text-xs font-medium text-muted-foreground">SSH User</label>
                                                <input
                                                    value={provisionForm.ssh_user}
                                                    onChange={e => setProvisionForm({ ...provisionForm, ssh_user: e.target.value })}
                                                    placeholder="root"
                                                    className="w-full mt-1 px-3 py-2 rounded-lg bg-background border border-border text-sm"
                                                />
                                            </div>
                                            <div>
                                                <label className="text-xs font-medium text-muted-foreground">SSH Port</label>
                                                <input
                                                    type="number"
                                                    value={provisionForm.ssh_port}
                                                    onChange={e => setProvisionForm({ ...provisionForm, ssh_port: parseInt(e.target.value) || 22 })}
                                                    className="w-full mt-1 px-3 py-2 rounded-lg bg-background border border-border text-sm"
                                                />
                                            </div>
                                        </div>

                                        {/* Auth Method Toggle */}
                                        <div>
                                            <label className="text-xs font-medium text-muted-foreground block mb-2">Authentication</label>
                                            <div className="flex items-center gap-2 mb-3">
                                                <button
                                                    onClick={() => setProvisionForm({ ...provisionForm, ssh_auth_method: 'password' })}
                                                    className={`px-3 py-1.5 rounded-md text-xs font-medium flex items-center gap-1.5 transition-all ${
                                                        provisionForm.ssh_auth_method === 'password'
                                                            ? 'bg-blue-500/10 text-blue-500 border border-blue-500/30'
                                                            : 'border border-border text-muted-foreground hover:text-foreground'
                                                    }`}
                                                >
                                                    <Lock size={12} /> Password
                                                </button>
                                                <button
                                                    onClick={() => setProvisionForm({ ...provisionForm, ssh_auth_method: 'key' })}
                                                    className={`px-3 py-1.5 rounded-md text-xs font-medium flex items-center gap-1.5 transition-all ${
                                                        provisionForm.ssh_auth_method === 'key'
                                                            ? 'bg-blue-500/10 text-blue-500 border border-blue-500/30'
                                                            : 'border border-border text-muted-foreground hover:text-foreground'
                                                    }`}
                                                >
                                                    <Key size={12} /> SSH Key
                                                </button>
                                            </div>

                                            {provisionForm.ssh_auth_method === 'password' ? (
                                                <input
                                                    type="password"
                                                    value={provisionForm.ssh_password}
                                                    onChange={e => setProvisionForm({ ...provisionForm, ssh_password: e.target.value })}
                                                    placeholder="SSH password"
                                                    className="w-full px-3 py-2 rounded-lg bg-background border border-border text-sm"
                                                />
                                            ) : (
                                                <textarea
                                                    value={provisionForm.ssh_key}
                                                    onChange={e => setProvisionForm({ ...provisionForm, ssh_key: e.target.value })}
                                                    placeholder="-----BEGIN OPENSSH PRIVATE KEY-----&#10;...&#10;-----END OPENSSH PRIVATE KEY-----"
                                                    rows={4}
                                                    className="w-full px-3 py-2 rounded-lg bg-background border border-border font-mono text-xs"
                                                />
                                            )}
                                        </div>

                                        <div className="flex items-center justify-between">
                                            <label className="flex items-center gap-2 text-sm">
                                                <input
                                                    type="checkbox"
                                                    checked={provisionForm.is_primary}
                                                    onChange={e => setProvisionForm({ ...provisionForm, is_primary: e.target.checked })}
                                                    className="rounded"
                                                />
                                                Primary server
                                            </label>
                                            <div className="flex gap-2">
                                                <button onClick={() => setShowAdd(false)} className="px-4 py-2 text-sm rounded-lg border border-border hover:bg-muted/50">
                                                    Cancel
                                                </button>
                                                <button
                                                    onClick={addServerProvision}
                                                    disabled={submitting || !provisionForm.name || !provisionForm.host}
                                                    className="px-4 py-2 text-sm rounded-lg bg-gradient-to-r from-blue-500 to-cyan-600 text-white font-semibold flex items-center gap-2 disabled:opacity-50"
                                                >
                                                    {submitting ? <Loader2 size={14} className="animate-spin" /> : <Zap size={14} />}
                                                    Provision & Add
                                                </button>
                                            </div>
                                        </div>
                                    </>
                                ) : (
                                    <>
                                        <div>
                                            <p className="text-sm text-muted-foreground mb-4">
                                                Connect to a server that already has CloudNeuron installed.
                                            </p>
                                        </div>
                                        <div className="grid grid-cols-2 gap-4">
                                            <div>
                                                <label className="text-xs font-medium text-muted-foreground">Name</label>
                                                <input
                                                    value={connectForm.name}
                                                    onChange={e => setConnectForm({ ...connectForm, name: e.target.value })}
                                                    placeholder="Production VPS"
                                                    className="w-full mt-1 px-3 py-2 rounded-lg bg-background border border-border text-sm"
                                                />
                                            </div>
                                            <div>
                                                <label className="text-xs font-medium text-muted-foreground">Host (IP or domain)</label>
                                                <input
                                                    value={connectForm.host}
                                                    onChange={e => setConnectForm({ ...connectForm, host: e.target.value })}
                                                    placeholder="198.51.100.5"
                                                    className="w-full mt-1 px-3 py-2 rounded-lg bg-background border border-border text-sm"
                                                />
                                            </div>
                                            <div>
                                                <label className="text-xs font-medium text-muted-foreground">API URL</label>
                                                <input
                                                    value={connectForm.api_url}
                                                    onChange={e => setConnectForm({ ...connectForm, api_url: e.target.value })}
                                                    placeholder="https://hosting.example.com"
                                                    className="w-full mt-1 px-3 py-2 rounded-lg bg-background border border-border text-sm"
                                                />
                                            </div>
                                            <div>
                                                <label className="text-xs font-medium text-muted-foreground">API Token</label>
                                                <input
                                                    type="password"
                                                    value={connectForm.api_token}
                                                    onChange={e => setConnectForm({ ...connectForm, api_token: e.target.value })}
                                                    placeholder="smsly_..."
                                                    className="w-full mt-1 px-3 py-2 rounded-lg bg-background border border-border text-sm"
                                                />
                                            </div>
                                            <div>
                                                <label className="text-xs font-medium text-muted-foreground">Gateway Secret <span className="text-muted-foreground/60">(optional, for HMAC auth)</span></label>
                                                <input
                                                    type="password"
                                                    value={connectForm.gateway_secret}
                                                    onChange={e => setConnectForm({ ...connectForm, gateway_secret: e.target.value })}
                                                    placeholder="GATEWAY_SECRET from remote .env"
                                                    className="w-full mt-1 px-3 py-2 rounded-lg bg-background border border-border text-sm"
                                                />
                                            </div>
                                        </div>
                                        <div className="flex items-center justify-between">
                                            <label className="flex items-center gap-2 text-sm">
                                                <input
                                                    type="checkbox"
                                                    checked={connectForm.is_primary}
                                                    onChange={e => setConnectForm({ ...connectForm, is_primary: e.target.checked })}
                                                    className="rounded"
                                                />
                                                Primary server
                                            </label>
                                            <div className="flex gap-2">
                                                <button onClick={() => setShowAdd(false)} className="px-4 py-2 text-sm rounded-lg border border-border hover:bg-muted/50">
                                                    Cancel
                                                </button>
                                                <button
                                                    onClick={addServerConnect}
                                                    disabled={submitting}
                                                    className="px-4 py-2 text-sm rounded-lg bg-blue-500 text-white font-semibold flex items-center gap-2 disabled:opacity-50"
                                                >
                                                    {submitting ? <Loader2 size={14} className="animate-spin" /> : <Link2 size={14} />}
                                                    Connect
                                                </button>
                                            </div>
                                        </div>
                                    </>
                                )}
                            </motion.div>
                        )}
                    </AnimatePresence>

                    {/* Provisioning Log Viewer */}
                    <AnimatePresence>
                        {viewingLogs && (
                            <motion.div
                                initial={{ opacity: 0, y: -10 }}
                                animate={{ opacity: 1, y: 0 }}
                                exit={{ opacity: 0, y: -10 }}
                                className="bg-zinc-950 border border-zinc-800 rounded-xl overflow-hidden"
                            >
                                <div className="flex items-center justify-between px-4 py-3 border-b border-zinc-800">
                                    <div className="flex items-center gap-3">
                                        <Terminal size={16} className="text-emerald-500" />
                                        <span className="text-sm font-bold text-zinc-200">Provisioning Terminal</span>
                                        {provisionStatus && PROVISION_STATUS_CONFIG[provisionStatus] && (
                                            <span className={`text-xs font-medium ${PROVISION_STATUS_CONFIG[provisionStatus].color} flex items-center gap-1`}>
                                                {PROVISION_STATUS_CONFIG[provisionStatus].animate && (
                                                    <Loader2 size={10} className="animate-spin" />
                                                )}
                                                {PROVISION_STATUS_CONFIG[provisionStatus].label}
                                            </span>
                                        )}
                                    </div>
                                    <button
                                        onClick={() => { setViewingLogs(null); setProvisionLogs(''); }}
                                        className="text-xs text-zinc-500 hover:text-zinc-300 px-2 py-1 rounded"
                                    >
                                        Close
                                    </button>
                                </div>
                                <pre
                                    ref={logRef}
                                    className="p-4 text-xs font-mono text-emerald-400 overflow-auto max-h-80 leading-relaxed whitespace-pre-wrap"
                                >
                                    {provisionLogs || 'Waiting for provisioning to start...'}
                                </pre>
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
                            <p className="text-muted-foreground mb-6 max-w-md mx-auto">
                                Add a fresh VPS and CloudNeuron will be automatically installed,
                                or connect to an existing CloudNeuron server.
                            </p>
                            <button
                                onClick={() => setShowAdd(true)}
                                className="px-6 py-2.5 rounded-xl bg-gradient-to-r from-blue-500 to-cyan-600 text-white font-semibold shadow-lg shadow-blue-500/25"
                            >
                                <Plus size={16} className="inline mr-2" /> Add Server
                            </button>
                        </div>
                    )}

                    {/* Server Cards */}
                    {!loading && servers.length > 0 && (
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            {servers.map((server, idx) => {
                                const isProvisioning = server.provision_status === 'PENDING' || server.provision_status === 'PROVISIONING';
                                const provFailed = server.provision_status === 'FAILED';
                                const sc = isProvisioning
                                    ? { icon: Loader2, color: 'text-blue-500', bg: 'bg-blue-500/10', border: 'border-blue-500/20', label: 'Provisioning' }
                                    : provFailed
                                    ? { icon: XCircle, color: 'text-red-500', bg: 'bg-red-500/10', border: 'border-red-500/20', label: 'Failed' }
                                    : STATUS_CONFIG[server.status] || STATUS_CONFIG.UNKNOWN;
                                const StatusIcon = sc.icon;

                                return (
                                    <motion.div
                                        key={server.id}
                                        initial={{ opacity: 0, y: 20 }}
                                        animate={{ opacity: 1, y: 0 }}
                                        transition={{ delay: idx * 0.05 }}
                                        className={`bg-card border rounded-xl p-5 space-y-4 ${sc.border} hover:shadow-lg transition-shadow cursor-pointer`}
                                        onClick={() => router.push(`/servers/${server.id}`)}
                                    >
                                        {/* Header */}
                                        <div className="flex items-center justify-between">
                                            <div className="flex items-center gap-3">
                                                <div className={`w-10 h-10 rounded-lg ${sc.bg} flex items-center justify-center`}>
                                                    <StatusIcon className={`${sc.color} ${isProvisioning ? 'animate-spin' : ''}`} size={18} />
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

                                        {/* Provisioning banner */}
                                        {isProvisioning && (
                                            <button
                                                onClick={() => { setViewingLogs(server.id); setProvisionLogs(''); setProvisionStatus(server.provision_status); }}
                                                className="w-full text-left px-3 py-2 rounded-lg bg-blue-500/5 border border-blue-500/20 text-xs text-blue-400 flex items-center gap-2 hover:bg-blue-500/10 transition-colors"
                                            >
                                                <Terminal size={12} />
                                                CloudNeuron is being installed — click to view logs
                                            </button>
                                        )}

                                        {provFailed && (
                                            <button
                                                onClick={() => { setViewingLogs(server.id); setProvisionLogs(''); setProvisionStatus('FAILED'); }}
                                                className="w-full text-left px-3 py-2 rounded-lg bg-red-500/5 border border-red-500/20 text-xs text-red-400 flex items-center gap-2 hover:bg-red-500/10 transition-colors"
                                            >
                                                <Terminal size={12} />
                                                Provisioning failed — click to view logs
                                            </button>
                                        )}

                                        {/* Stats */}
                                        {!isProvisioning && (
                                            <div className="grid grid-cols-3 gap-3 text-center">
                                                <div className="bg-muted/30 rounded-lg p-2">
                                                    <p className="text-lg font-bold">{server.services_count}</p>
                                                    <p className="text-[10px] text-muted-foreground uppercase">Services</p>
                                                </div>
                                                <div className="bg-muted/30 rounded-lg p-2">
                                                    <p className="text-sm font-bold truncate">{server.server_version || '—'}</p>
                                                    <p className="text-[10px] text-muted-foreground uppercase">Version</p>
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
                                        )}

                                        {/* Actions */}
                                        <div className="flex items-center justify-between pt-2 border-t border-border" onClick={e => e.stopPropagation()}>
                                            <div className="flex items-center gap-2">
                                                {server.api_url && (
                                                    <>
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
                                                    </>
                                                )}
                                                {(isProvisioning || provFailed) && (
                                                    <button
                                                        onClick={() => { setViewingLogs(server.id); setProvisionLogs(''); setProvisionStatus(server.provision_status); }}
                                                        className="text-xs px-2.5 py-1.5 rounded-lg border border-border hover:bg-muted/50 flex items-center gap-1.5"
                                                    >
                                                        <Terminal size={12} /> Logs
                                                    </button>
                                                )}
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
