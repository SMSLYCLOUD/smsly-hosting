'use client';

import { useState, useEffect, useCallback } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { motion, AnimatePresence } from 'framer-motion';
import {
    ArrowLeft, Server, RefreshCw, Loader2, CheckCircle2, XCircle, Globe,
    Wifi, WifiOff, Monitor, ArrowUpRight, Pencil, Save, X, Rocket,
    Square, RotateCcw, Plus, Trash2, Shield, Copy, ExternalLink,
    Activity, Clock, ChevronDown, ChevronRight, AlertCircle, Wrench
} from 'lucide-react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { serversApi, ManagedServer } from '@/lib/api';
import { toast } from '@/components/ui/use-toast';
import { useConfirm } from '@/components/ui/confirm-dialog';

// ─── Types ──────────────────────────────────────────────────────────────────

interface RemoteService {
    id: string;
    name: string;
    repository_url?: string;
    branch?: string;
    public_domain?: string;
    custom_domains?: string[];
    health_status?: string;
    latest_deployment?: {
        id: string;
        status: string;
        commit_hash?: string;
        created_at: string;
    };
}

interface RemoteDeployment {
    id: string;
    service: string;
    service_name?: string;
    commit_hash?: string;
    commit_message?: string;
    status: string;
    created_at: string;
    finished_at?: string;
    duration_seconds?: number;
}

interface RemoteDomain {
    domain: string;
    service_id: string;
    service_name: string;
    public_domain: string;
    verified: boolean;
    verification_token: string;
}

type TabId = 'overview' | 'services' | 'deployments' | 'dns';

// ─── Status Configs ─────────────────────────────────────────────────────────

const STATUS_COLORS: Record<string, { color: string; bg: string; label: string }> = {
    ONLINE: { color: 'text-emerald-500', bg: 'bg-emerald-500/10', label: 'Online' },
    OFFLINE: { color: 'text-red-500', bg: 'bg-red-500/10', label: 'Offline' },
    UNKNOWN: { color: 'text-zinc-500', bg: 'bg-zinc-500/10', label: 'Unknown' },
};

const DEPLOY_STATUS: Record<string, { color: string; bg: string }> = {
    ACTIVE: { color: 'text-emerald-500', bg: 'bg-emerald-500/10' },
    BUILDING: { color: 'text-blue-500', bg: 'bg-blue-500/10' },
    DEPLOYING: { color: 'text-blue-500', bg: 'bg-blue-500/10' },
    QUEUED: { color: 'text-amber-500', bg: 'bg-amber-500/10' },
    FAILED: { color: 'text-red-500', bg: 'bg-red-500/10' },
    CANCELLED: { color: 'text-zinc-500', bg: 'bg-zinc-500/10' },
    REVIEW: { color: 'text-purple-500', bg: 'bg-purple-500/10' },
};

const HEALTH_STATUS: Record<string, { color: string; icon: typeof CheckCircle2 }> = {
    healthy: { color: 'text-emerald-500', icon: CheckCircle2 },
    unhealthy: { color: 'text-red-500', icon: XCircle },
    starting: { color: 'text-blue-500', icon: Loader2 },
    unknown: { color: 'text-zinc-500', icon: AlertCircle },
};

// ─── Main Component ─────────────────────────────────────────────────────────

export default function ServerDetailPage() {
    const params = useParams();
    const router = useRouter();
    const serverId = params.id as string;

    const [server, setServer] = useState<ManagedServer | null>(null);
    const [loading, setLoading] = useState(true);
    const confirm = useConfirm();
    const [activeTab, setActiveTab] = useState<TabId>('overview');

    // Overview state
    const [editing, setEditing] = useState(false);
    const [editName, setEditName] = useState('');
    const [saving, setSaving] = useState(false);
    const [checking, setChecking] = useState(false);
    const [updating, setUpdating] = useState(false);
    const [diagnosing, setDiagnosing] = useState(false);
    const [healing, setHealing] = useState(false);

    // Services state
    const [services, setServices] = useState<RemoteService[]>([]);
    const [servicesLoading, setServicesLoading] = useState(false);
    const [expandedService, setExpandedService] = useState<string | null>(null);
    const [actionLoading, setActionLoading] = useState<Record<string, string>>({});

    // Deployments state
    const [deployments, setDeployments] = useState<RemoteDeployment[]>([]);
    const [deploymentsLoading, setDeploymentsLoading] = useState(false);

    // DNS state
    const [domains, setDomains] = useState<RemoteDomain[]>([]);
    const [domainsLoading, setDomainsLoading] = useState(false);
    const [addingDomain, setAddingDomain] = useState(false);
    const [newDomainService, setNewDomainService] = useState('');
    const [newDomainName, setNewDomainName] = useState('');
    const [domainSubmitting, setDomainSubmitting] = useState(false);
    const [verifyingDomain, setVerifyingDomain] = useState<string | null>(null);

    // ── Data Fetching ──────────────────────────────────────────────────────

    const fetchServer = useCallback(async () => {
        try {
            const data = await serversApi.get(serverId);
            setServer(data);
            setEditName(data.name);
        } catch {
            toast({ title: 'Failed to load server', variant: 'destructive' });
        }
        setLoading(false);
    }, [serverId]);

    const fetchServices = useCallback(async () => {
        setServicesLoading(true);
        try {
            const data = await serversApi.remoteServices(serverId);
            const list = data?.results || data || [];
            setServices(Array.isArray(list) ? list : []);
        } catch {
            toast({ title: 'Failed to fetch remote services', variant: 'destructive' });
        }
        setServicesLoading(false);
    }, [serverId]);

    const fetchDeployments = useCallback(async () => {
        setDeploymentsLoading(true);
        try {
            const data = await serversApi.remoteDeployments(serverId);
            const list = data?.results || data || [];
            setDeployments(Array.isArray(list) ? list : []);
        } catch {
            toast({ title: 'Failed to fetch remote deployments', variant: 'destructive' });
        }
        setDeploymentsLoading(false);
    }, [serverId]);

    const fetchDomains = useCallback(async () => {
        setDomainsLoading(true);
        try {
            const data = await serversApi.remoteDomains(serverId);
            setDomains(data?.domains || []);
        } catch {
            toast({ title: 'Failed to fetch domains', variant: 'destructive' });
        }
        setDomainsLoading(false);
    }, [serverId]);

    useEffect(() => {
        fetchServer();
    }, [fetchServer]);

    useEffect(() => {
        if (activeTab === 'services') fetchServices();
        if (activeTab === 'deployments') fetchDeployments();
        if (activeTab === 'dns') {
            fetchDomains();
            // Also fetch services for the domain add form
            if (services.length === 0) fetchServices();
        }
    }, [activeTab, fetchServices, fetchDeployments, fetchDomains, services.length]);

    // ── Actions ────────────────────────────────────────────────────────────

    const handleHealthCheck = async () => {
        setChecking(true);
        try {
            const data = await serversApi.healthCheck(serverId);
            setServer(data);
            toast({ title: `Server is ${data.status.toLowerCase()}` });
        } catch {
            toast({ title: 'Health check failed', variant: 'destructive' });
        }
        setChecking(false);
    };

    const handleUpdateServer = async () => {
        if (!await confirm({
            title: 'Update Grid?',
            message: 'This will pull the latest code from GitHub and restart the services on the remote VPS. Your services may be briefly unavailable during restart.',
            variant: 'warning',
            confirmText: 'Update Now'
        })) return;

        setUpdating(true);
        try {
            await serversApi.updateServer(serverId);
            toast({
                title: 'Update Started',
                description: 'The server is pulling the latest code and rebuilding services in the background.',
            });
            // Show overview tab to see logs
            setActiveTab('overview');
        } catch {
            toast({ title: 'Failed to trigger update', variant: 'destructive' });
        }
        setUpdating(false);
    };

    const handleRunDiagnostics = async () => {
        setDiagnosing(true);
        try {
            const res = await serversApi.runDiagnostics(serverId);
            const d = res?.data || res;
            toast({
                title: 'Diagnostics Complete',
                description: `Docker: ${d.docker_running ? 'Running' : 'Stopped'}, Disk: ${d.disk_usage_pct}%, Mem: ${d.memory_usage_pct}%`,
            });
        } catch {
            toast({ title: 'Diagnostics failed', variant: 'destructive' });
        }
        setDiagnosing(false);
    };

    const handleTriggerHealing = async () => {
        if (!await confirm({
            title: 'Trigger Node Healing?',
            message: 'This will attempt automated self-healing (restarting containers or Docker stack) on the remote node.',
            variant: 'warning',
            confirmText: 'Heal Now'
        })) return;

        setHealing(true);
        try {
            await serversApi.triggerHealing(serverId, { action: 'restart_stack' });
            toast({
                title: 'Healing Triggered',
                description: 'Automated recovery action executed on remote node.',
            });
            fetchServer();
        } catch {
            toast({ title: 'Healing failed', variant: 'destructive' });
        }
        setHealing(false);
    };

    const handleSaveName = async () => {
        if (!editName.trim()) return;
        setSaving(true);
        try {
            const data = await serversApi.update(serverId, { name: editName.trim() });
            setServer(data);
            setEditing(false);
            toast({ title: 'Server name updated' });
        } catch {
            toast({ title: 'Failed to update', variant: 'destructive' });
        }
        setSaving(false);
    };

    const handleServiceAction = async (serviceId: string, action: 'deploy' | 'stop' | 'restart') => {
        if (action === 'restart') {
            if (!await confirm({ title: 'Restart service?', message: 'Fast-restart the container (~5 seconds). No rebuild required.', confirmText: 'Restart' })) return;
        }
        setActionLoading(prev => ({ ...prev, [serviceId]: action }));
        try {
            if (action === 'deploy') await serversApi.remoteDeployService(serverId, serviceId);
            else if (action === 'stop') await serversApi.remoteStopService(serverId, serviceId);
            else await serversApi.remoteRestartService(serverId, serviceId);

            toast({ title: `${action.charAt(0).toUpperCase() + action.slice(1)} triggered` });
            setTimeout(fetchServices, 2000);
        } catch {
            toast({ title: `Failed to ${action} service`, variant: 'destructive' });
        }
        setActionLoading(prev => {
            const next = { ...prev };
            delete next[serviceId];
            return next;
        });
    };

    const handleAddDomain = async () => {
        if (!newDomainService || !newDomainName.trim()) return;
        setDomainSubmitting(true);
        try {
            const result = await serversApi.remoteAddDomain(serverId, newDomainService, newDomainName.trim());
            if (result?.caddy_synced === false) {
                toast({
                    title: `Domain ${newDomainName} saved with warning`,
                    description: result?.warning || result?.message || 'Automatic routing sync failed.',
                    variant: 'destructive',
                });
            } else {
                toast({ title: `Domain ${newDomainName} added` });
            }
            setNewDomainName('');
            setAddingDomain(false);
            fetchDomains();
        } catch {
            toast({ title: 'Failed to add domain', variant: 'destructive' });
        }
        setDomainSubmitting(false);
    };

    const handleDeleteDomain = async (serviceId: string, domain: string) => {
        if (!await confirm({ title: 'Remove domain?', message: `Remove domain ${domain}?`, variant: 'destructive', confirmText: 'Remove' })) return;
        try {
            const result = await serversApi.remoteDeleteDomain(serverId, serviceId, domain);
            if (result?.caddy_synced === false) {
                toast({
                    title: `Domain ${domain} removed with warning`,
                    description: result?.warning || result?.message || 'Automatic routing sync failed.',
                    variant: 'destructive',
                });
            } else {
                toast({ title: `Domain ${domain} removed` });
            }
            fetchDomains();
        } catch {
            toast({ title: 'Failed to remove domain', variant: 'destructive' });
        }
    };

    const handleVerifyDomain = async (serviceId: string, domain: string) => {
        setVerifyingDomain(domain);
        try {
            const result = await serversApi.remoteVerifyDomain(serverId, serviceId, domain);
            if (result?.verified) {
                toast({ title: `Domain ${domain} verified!` });
            } else {
                toast({ title: 'Domain not yet verified', description: 'Check your DNS records', variant: 'destructive' });
            }
            fetchDomains();
        } catch {
            toast({ title: 'Verification failed', variant: 'destructive' });
        }
        setVerifyingDomain(null);
    };

    const copyToClipboard = (text: string) => {
        navigator.clipboard.writeText(text);
        toast({ title: 'Copied to clipboard' });
    };

    // ── Tab definitions ────────────────────────────────────────────────────

    const tabs: { id: TabId; label: string; icon: typeof Monitor }[] = [
        { id: 'overview', label: 'Overview', icon: Monitor },
        { id: 'services', label: 'Services', icon: Activity },
        { id: 'deployments', label: 'Deployments', icon: Rocket },
        { id: 'dns', label: 'DNS', icon: Globe },
    ];

    if (loading) {
        return (
            <DashboardShell>
                <div className="flex-1 flex items-center justify-center">
                    <Loader2 className="animate-spin text-muted-foreground" size={24} />
                </div>
            </DashboardShell>
        );
    }

    if (!server) {
        return (
            <DashboardShell>
                <div className="flex-1 flex flex-col items-center justify-center gap-4">
                    <XCircle className="text-red-500" size={32} />
                    <p>Server not found</p>
                    <button onClick={() => router.push('/servers')} className="text-blue-500 text-sm hover:underline">
                        ← Back to servers
                    </button>
                </div>
            </DashboardShell>
        );
    }

    const sc = STATUS_COLORS[server.status] || STATUS_COLORS.UNKNOWN;

    return (
        <DashboardShell>
            <div className="flex-1 p-8 relative z-10">
                <motion.div
                    className="max-w-6xl mx-auto space-y-6"
                    initial={{ opacity: 0, y: 20 }}
                    animate={{ opacity: 1, y: 0 }}
                >
                    {/* Back + Header */}
                    <div className="flex items-center gap-4">
                        <button
                            onClick={() => router.push('/servers')}
                            className="p-2 rounded-lg hover:bg-muted/50 transition-colors"
                        >
                            <ArrowLeft size={18} />
                        </button>
                        <div className="flex-1">
                            <div className="flex items-center gap-3">
                                {editing ? (
                                    <div className="flex items-center gap-2">
                                        <input
                                            value={editName}
                                            onChange={e => setEditName(e.target.value)}
                                            className="text-2xl font-bold bg-background border border-border rounded-lg px-3 py-1"
                                            autoFocus
                                        />
                                        <button onClick={handleSaveName} disabled={saving} className="p-1.5 rounded-lg hover:bg-muted/50">
                                            {saving ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} className="text-emerald-500" />}
                                        </button>
                                        <button onClick={() => { setEditing(false); setEditName(server.name); }} className="p-1.5 rounded-lg hover:bg-muted/50">
                                            <X size={16} className="text-muted-foreground" />
                                        </button>
                                    </div>
                                ) : (
                                    <>
                                        <h1 className="text-2xl font-bold tracking-tight">{server.name}</h1>
                                        <button onClick={() => setEditing(true)} className="p-1 rounded hover:bg-muted/50">
                                            <Pencil size={14} className="text-muted-foreground" />
                                        </button>
                                    </>
                                )}
                                {server.node_type === 'media' && (
                                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-cyan-500/10 text-cyan-500 font-bold uppercase">
                                        Media Node
                                    </span>
                                )}
                                {server.is_primary && (
                                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-500 font-bold uppercase">
                                        Control Plane
                                    </span>
                                )}
                                {server.is_lite_agent && (
                                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-500/10 text-purple-500 font-bold uppercase">
                                        Lite Agent
                                    </span>
                                )}
                                {!server.is_primary && !server.is_lite_agent && server.node_type !== 'media' && (
                                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-500 font-bold uppercase">
                                        Full Stack
                                    </span>
                                )}
                                <span className={`text-xs font-bold px-2 py-0.5 rounded ${sc.bg} ${sc.color}`}>
                                    {sc.label}
                                </span>
                            </div>
                            <p className="text-sm text-muted-foreground mt-0.5">{server.host}</p>
                        </div>
                        <div className="flex items-center gap-2">
                                <button
                                    onClick={handleHealthCheck}
                                    disabled={checking}
                                    className="px-3 py-2 rounded-lg border border-border text-sm flex items-center gap-2 hover:bg-muted/50 transition-colors"
                                >
                                    {checking ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                                    Health Check
                                </button>
                                <button
                                    onClick={handleUpdateServer}
                                    disabled={updating || server.status === 'OFFLINE'}
                                    className="px-3 py-2 rounded-lg border border-blue-500/30 text-blue-500 text-sm flex items-center gap-2 hover:bg-blue-500/10 transition-colors disabled:opacity-50"
                                >
                                    {updating ? <Loader2 size={14} className="animate-spin" /> : <Rocket size={14} />}
                                    Update Grid
                                </button>
                                <button
                                    onClick={handleRunDiagnostics}
                                    disabled={diagnosing || server.status === 'OFFLINE'}
                                    className="px-3 py-2 rounded-lg border border-purple-500/30 text-purple-500 text-sm flex items-center gap-2 hover:bg-purple-500/10 transition-colors disabled:opacity-50"
                                >
                                    {diagnosing ? <Loader2 size={14} className="animate-spin" /> : <Activity size={14} />}
                                    Diagnostics
                                </button>
                                <button
                                    onClick={handleTriggerHealing}
                                    disabled={healing || server.status === 'OFFLINE'}
                                    className="px-3 py-2 rounded-lg border border-amber-500/30 text-amber-500 text-sm flex items-center gap-2 hover:bg-amber-500/10 transition-colors disabled:opacity-50"
                                >
                                    {healing ? <Loader2 size={14} className="animate-spin" /> : <Wrench size={14} />}
                                    Heal Node
                                </button>
                            {server.api_url && (
                                <a
                                    href={server.api_url}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="px-3 py-2 rounded-lg bg-gradient-to-r from-blue-500 to-cyan-600 text-white text-sm font-semibold flex items-center gap-2"
                                >
                                    <ArrowUpRight size={14} />
                                    Open Dashboard
                                </a>
                            )}
                        </div>
                    </div>

                    {/* Tabs */}
                    <div className="flex items-center gap-1 border-b border-border">
                        {tabs.map(tab => {
                            const Icon = tab.icon;
                            return (
                                <button
                                    key={tab.id}
                                    onClick={() => setActiveTab(tab.id)}
                                    className={`px-4 py-2.5 text-sm font-medium flex items-center gap-2 border-b-2 transition-colors ${
                                        activeTab === tab.id
                                            ? 'border-blue-500 text-foreground'
                                            : 'border-transparent text-muted-foreground hover:text-foreground'
                                    }`}
                                >
                                    <Icon size={14} />
                                    {tab.label}
                                </button>
                            );
                        })}
                    </div>

                    {/* Tab Content */}
                    <AnimatePresence mode="wait">
                        <motion.div
                            key={activeTab}
                            initial={{ opacity: 0, y: 10 }}
                            animate={{ opacity: 1, y: 0 }}
                            exit={{ opacity: 0, y: -10 }}
                            transition={{ duration: 0.15 }}
                        >
                            {activeTab === 'overview' && <OverviewTab server={server} />}
                            {activeTab === 'services' && (
                                <ServicesTab
                                    services={services}
                                    loading={servicesLoading}
                                    onRefresh={fetchServices}
                                    expandedService={expandedService}
                                    setExpandedService={setExpandedService}
                                    actionLoading={actionLoading}
                                    onAction={handleServiceAction}
                                />
                            )}
                            {activeTab === 'deployments' && (
                                <DeploymentsTab
                                    deployments={deployments}
                                    loading={deploymentsLoading}
                                    onRefresh={fetchDeployments}
                                />
                            )}
                            {activeTab === 'dns' && (
                                <DnsTab
                                    domains={domains}
                                    services={services}
                                    loading={domainsLoading}
                                    serverId={serverId}
                                    addingDomain={addingDomain}
                                    setAddingDomain={setAddingDomain}
                                    newDomainService={newDomainService}
                                    setNewDomainService={setNewDomainService}
                                    newDomainName={newDomainName}
                                    setNewDomainName={setNewDomainName}
                                    domainSubmitting={domainSubmitting}
                                    verifyingDomain={verifyingDomain}
                                    onAddDomain={handleAddDomain}
                                    onDeleteDomain={handleDeleteDomain}
                                    onVerifyDomain={handleVerifyDomain}
                                    onCopy={copyToClipboard}
                                    onRefresh={fetchDomains}
                                />
                            )}
                        </motion.div>
                    </AnimatePresence>
                </motion.div>
            </div>
        </DashboardShell>
    );
}

// ─── Overview Tab ───────────────────────────────────────────────────────────

function OverviewTab({ server }: { server: ManagedServer }) {
    const sc = STATUS_COLORS[server.status] || STATUS_COLORS.UNKNOWN;
    const StatusIcon = server.status === 'ONLINE' ? Wifi : server.status === 'OFFLINE' ? WifiOff : Globe;

    const stats = [
        { label: 'Services', value: server.services_count, icon: Activity },
        { label: 'SSH Port', value: server.ssh_port, icon: Shield },
        { label: 'Version', value: server.server_version || 'Unknown', icon: Server },
        {
            label: 'Last Check',
            value: server.last_health_check
                ? new Date(server.last_health_check).toLocaleString()
                : 'Never',
            icon: Clock,
        },
    ];

    return (
        <div className="space-y-6">
            {/* Status Banner */}
            <div className={`flex items-center gap-4 p-5 rounded-xl border ${sc.bg} border-opacity-20`}>
                <div className={`w-14 h-14 rounded-xl ${sc.bg} flex items-center justify-center`}>
                    <StatusIcon className={sc.color} size={24} />
                </div>
                <div className="flex-1">
                    <p className={`text-lg font-bold ${sc.color}`}>{sc.label}</p>
                    <p className="text-sm text-muted-foreground">
                        {server.status === 'ONLINE'
                            ? `Serving ${server.services_count} service${server.services_count !== 1 ? 's' : ''}`
                            : 'Server is not responding'}
                    </p>
                </div>
                {server.api_url && (
                    <div className="text-right">
                        <p className="text-xs text-muted-foreground">API Endpoint</p>
                        <p className="text-sm font-mono">{server.api_url}</p>
                    </div>
                )}
            </div>

            {/* Stats Grid */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                {stats.map(stat => {
                    const Icon = stat.icon;
                    return (
                        <div key={stat.label} className="bg-card border border-border rounded-xl p-4">
                            <div className="flex items-center gap-2 mb-2">
                                <Icon size={14} className="text-muted-foreground" />
                                <p className="text-xs text-muted-foreground uppercase">{stat.label}</p>
                            </div>
                            <p className="text-lg font-bold">{stat.value}</p>
                        </div>
                    );
                })}
            </div>

            {/* Server Details */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="bg-card border border-border rounded-xl p-5 space-y-3">
                    <h3 className="font-bold text-sm uppercase text-muted-foreground">Connection Details</h3>
                    <div className="grid grid-cols-2 gap-4 text-sm">
                        <div>
                            <p className="text-muted-foreground text-xs">Host</p>
                            <p className="font-mono">{server.host}</p>
                        </div>
                        <div>
                            <p className="text-muted-foreground text-xs">API URL</p>
                            <p className="font-mono truncate">{server.api_url || '—'}</p>
                        </div>
                        <div>
                            <p className="text-muted-foreground text-xs">Created</p>
                            <p>{new Date(server.created_at).toLocaleDateString()}</p>
                        </div>
                        <div>
                            <p className="text-muted-foreground text-xs">SSH Port</p>
                            <p className="font-mono">{server.ssh_port}</p>
                        </div>
                    </div>
                </div>

                {server.provision_logs && (
                    <div className="bg-card border border-border rounded-xl p-5 space-y-3">
                        <div className="flex items-center justify-between">
                            <h3 className="font-bold text-sm uppercase text-muted-foreground">Console Logs</h3>
                            {server.provision_status && (
                                <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${
                                    server.provision_status === 'DONE' ? 'bg-emerald-500/10 text-emerald-500' :
                                    server.provision_status === 'FAILED' ? 'bg-red-500/10 text-red-500' :
                                    'bg-blue-500/10 text-blue-500'
                                }`}>
                                    {server.provision_status}
                                </span>
                            )}
                        </div>
                        <div className="bg-black/50 rounded-lg p-3 font-mono text-[10px] leading-relaxed max-h-[160px] overflow-y-auto whitespace-pre-wrap text-zinc-300">
                            {server.provision_logs}
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}

// ─── Services Tab ───────────────────────────────────────────────────────────

function ServicesTab({
    services,
    loading,
    onRefresh,
    expandedService,
    setExpandedService,
    actionLoading,
    onAction,
}: {
    services: RemoteService[];
    loading: boolean;
    onRefresh: () => void;
    expandedService: string | null;
    setExpandedService: (id: string | null) => void;
    actionLoading: Record<string, string>;
    onAction: (serviceId: string, action: 'deploy' | 'stop' | 'restart') => void;
}) {
    if (loading) {
        return (
            <div className="text-center py-16">
                <Loader2 className="animate-spin mx-auto text-muted-foreground" size={24} />
                <p className="text-sm text-muted-foreground mt-2">Loading services from remote server...</p>
            </div>
        );
    }

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between">
                <p className="text-sm text-muted-foreground">{services.length} service{services.length !== 1 ? 's' : ''} deployed</p>
                <button onClick={onRefresh} className="px-3 py-1.5 rounded-lg border border-border text-xs flex items-center gap-1.5 hover:bg-muted/50">
                    <RefreshCw size={12} /> Refresh
                </button>
            </div>

            {services.length === 0 ? (
                <div className="text-center py-12 bg-card border border-border rounded-xl">
                    <Server className="mx-auto text-muted-foreground mb-3" size={32} />
                    <p className="text-muted-foreground">No services deployed on this server yet</p>
                </div>
            ) : (
                <div className="space-y-2">
                    {services.map(svc => {
                        const isExpanded = expandedService === svc.id;
                        const healthCfg = HEALTH_STATUS[svc.health_status || 'unknown'] || HEALTH_STATUS.unknown;
                        const HealthIcon = healthCfg.icon;
                        const currentAction = actionLoading[svc.id];

                        return (
                            <div key={svc.id} className="bg-card border border-border rounded-xl overflow-hidden">
                                <button
                                    onClick={() => setExpandedService(isExpanded ? null : svc.id)}
                                    className="w-full flex items-center gap-4 p-4 text-left hover:bg-muted/30 transition-colors"
                                >
                                    <div className={`w-8 h-8 rounded-lg flex items-center justify-center ${healthCfg.color === 'text-emerald-500' ? 'bg-emerald-500/10' : healthCfg.color === 'text-red-500' ? 'bg-red-500/10' : 'bg-zinc-500/10'}`}>
                                        <HealthIcon className={healthCfg.color} size={14} />
                                    </div>
                                    <div className="flex-1 min-w-0">
                                        <p className="font-bold text-sm">{svc.name}</p>
                                        <p className="text-xs text-muted-foreground truncate">
                                            {svc.public_domain || svc.repository_url || 'No domain'}
                                        </p>
                                    </div>
                                    {svc.latest_deployment && (
                                        <div className="text-right hidden sm:block">
                                            <span className={`text-[10px] font-bold px-2 py-0.5 rounded ${DEPLOY_STATUS[svc.latest_deployment.status]?.bg || 'bg-zinc-500/10'} ${DEPLOY_STATUS[svc.latest_deployment.status]?.color || 'text-zinc-500'}`}>
                                                {svc.latest_deployment.status}
                                            </span>
                                        </div>
                                    )}
                                    {isExpanded ? <ChevronDown size={16} className="text-muted-foreground" /> : <ChevronRight size={16} className="text-muted-foreground" />}
                                </button>

                                <AnimatePresence>
                                    {isExpanded && (
                                        <motion.div
                                            initial={{ height: 0, opacity: 0 }}
                                            animate={{ height: 'auto', opacity: 1 }}
                                            exit={{ height: 0, opacity: 0 }}
                                            className="border-t border-border"
                                        >
                                            <div className="p-4 space-y-3">
                                                {/* Service Details */}
                                                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
                                                    <div>
                                                        <p className="text-muted-foreground">Branch</p>
                                                        <p className="font-mono">{svc.branch || 'main'}</p>
                                                    </div>
                                                    <div>
                                                        <p className="text-muted-foreground">Domain</p>
                                                        <p className="font-mono truncate">{svc.public_domain || '—'}</p>
                                                    </div>
                                                    <div>
                                                        <p className="text-muted-foreground">Custom Domains</p>
                                                        <p>{svc.custom_domains?.length || 0}</p>
                                                    </div>
                                                    <div>
                                                        <p className="text-muted-foreground">Last Deploy</p>
                                                        <p>{svc.latest_deployment ? new Date(svc.latest_deployment.created_at).toLocaleString() : '—'}</p>
                                                    </div>
                                                </div>

                                                {/* Action Buttons */}
                                                <div className="flex items-center gap-2 pt-2 border-t border-border">
                                                    <button
                                                        onClick={() => onAction(svc.id, 'deploy')}
                                                        disabled={!!currentAction}
                                                        className="px-3 py-1.5 rounded-lg bg-gradient-to-r from-blue-500 to-cyan-600 text-white text-xs font-semibold flex items-center gap-1.5 disabled:opacity-50"
                                                    >
                                                        {currentAction === 'deploy' ? <Loader2 size={12} className="animate-spin" /> : <Rocket size={12} />}
                                                        Deploy
                                                    </button>
                                                    <button
                                                        onClick={() => onAction(svc.id, 'restart')}
                                                        disabled={!!currentAction}
                                                        className="px-3 py-1.5 rounded-lg border border-border text-xs flex items-center gap-1.5 hover:bg-muted/50 disabled:opacity-50"
                                                    >
                                                        {currentAction === 'restart' ? <Loader2 size={12} className="animate-spin" /> : <RotateCcw size={12} />}
                                                        Restart
                                                    </button>
                                                    <button
                                                        onClick={() => onAction(svc.id, 'stop')}
                                                        disabled={!!currentAction}
                                                        className="px-3 py-1.5 rounded-lg border border-red-500/30 text-red-500 text-xs flex items-center gap-1.5 hover:bg-red-500/10 disabled:opacity-50"
                                                    >
                                                        {currentAction === 'stop' ? <Loader2 size={12} className="animate-spin" /> : <Square size={12} />}
                                                        Stop
                                                    </button>
                                                    {svc.public_domain && (
                                                        <a
                                                            href={`https://${svc.public_domain}`}
                                                            target="_blank"
                                                            rel="noopener noreferrer"
                                                            className="ml-auto px-3 py-1.5 rounded-lg border border-border text-xs flex items-center gap-1.5 hover:bg-muted/50"
                                                        >
                                                            <ExternalLink size={12} /> Visit
                                                        </a>
                                                    )}
                                                </div>
                                            </div>
                                        </motion.div>
                                    )}
                                </AnimatePresence>
                            </div>
                        );
                    })}
                </div>
            )}
        </div>
    );
}

// ─── Deployments Tab ────────────────────────────────────────────────────────

function DeploymentsTab({
    deployments,
    loading,
    onRefresh,
}: {
    deployments: RemoteDeployment[];
    loading: boolean;
    onRefresh: () => void;
}) {
    if (loading) {
        return (
            <div className="text-center py-16">
                <Loader2 className="animate-spin mx-auto text-muted-foreground" size={24} />
                <p className="text-sm text-muted-foreground mt-2">Loading deployments...</p>
            </div>
        );
    }

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between">
                <p className="text-sm text-muted-foreground">{deployments.length} deployment{deployments.length !== 1 ? 's' : ''}</p>
                <button onClick={onRefresh} className="px-3 py-1.5 rounded-lg border border-border text-xs flex items-center gap-1.5 hover:bg-muted/50">
                    <RefreshCw size={12} /> Refresh
                </button>
            </div>

            {deployments.length === 0 ? (
                <div className="text-center py-12 bg-card border border-border rounded-xl">
                    <Rocket className="mx-auto text-muted-foreground mb-3" size={32} />
                    <p className="text-muted-foreground">No deployments found</p>
                </div>
            ) : (
                <div className="bg-card border border-border rounded-xl overflow-hidden">
                    <table className="w-full text-sm">
                        <thead>
                            <tr className="border-b border-border text-left">
                                <th className="px-4 py-3 text-xs text-muted-foreground uppercase font-medium">Status</th>
                                <th className="px-4 py-3 text-xs text-muted-foreground uppercase font-medium">Commit</th>
                                <th className="px-4 py-3 text-xs text-muted-foreground uppercase font-medium hidden md:table-cell">Message</th>
                                <th className="px-4 py-3 text-xs text-muted-foreground uppercase font-medium">Time</th>
                                <th className="px-4 py-3 text-xs text-muted-foreground uppercase font-medium hidden sm:table-cell">Duration</th>
                            </tr>
                        </thead>
                        <tbody>
                            {deployments.map(dep => {
                                const dsc = DEPLOY_STATUS[dep.status] || { color: 'text-zinc-500', bg: 'bg-zinc-500/10' };
                                return (
                                    <tr key={dep.id} className="border-b border-border/50 last:border-0 hover:bg-muted/30 transition-colors">
                                        <td className="px-4 py-3">
                                            <span className={`text-[10px] font-bold px-2 py-0.5 rounded ${dsc.bg} ${dsc.color}`}>
                                                {dep.status}
                                            </span>
                                        </td>
                                        <td className="px-4 py-3 font-mono text-xs">
                                            {dep.commit_hash ? dep.commit_hash.substring(0, 7) : '—'}
                                        </td>
                                        <td className="px-4 py-3 text-xs text-muted-foreground truncate max-w-[200px] hidden md:table-cell">
                                            {dep.commit_message || '—'}
                                        </td>
                                        <td className="px-4 py-3 text-xs text-muted-foreground">
                                            {new Date(dep.created_at).toLocaleString()}
                                        </td>
                                        <td className="px-4 py-3 text-xs text-muted-foreground hidden sm:table-cell">
                                            {dep.duration_seconds ? `${dep.duration_seconds}s` : '—'}
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}

// ─── DNS Tab ────────────────────────────────────────────────────────────────

function DnsTab({
    domains,
    services,
    loading,
    serverId,
    addingDomain,
    setAddingDomain,
    newDomainService,
    setNewDomainService,
    newDomainName,
    setNewDomainName,
    domainSubmitting,
    verifyingDomain,
    onAddDomain,
    onDeleteDomain,
    onVerifyDomain,
    onCopy,
    onRefresh,
}: {
    domains: RemoteDomain[];
    services: RemoteService[];
    loading: boolean;
    serverId: string;
    addingDomain: boolean;
    setAddingDomain: (v: boolean) => void;
    newDomainService: string;
    setNewDomainService: (v: string) => void;
    newDomainName: string;
    setNewDomainName: (v: string) => void;
    domainSubmitting: boolean;
    verifyingDomain: string | null;
    onAddDomain: () => void;
    onDeleteDomain: (serviceId: string, domain: string) => void;
    onVerifyDomain: (serviceId: string, domain: string) => void;
    onCopy: (text: string) => void;
    onRefresh: () => void;
}) {
    if (loading) {
        return (
            <div className="text-center py-16">
                <Loader2 className="animate-spin mx-auto text-muted-foreground" size={24} />
                <p className="text-sm text-muted-foreground mt-2">Loading domains...</p>
            </div>
        );
    }

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between">
                <p className="text-sm text-muted-foreground">
                    {domains.length} custom domain{domains.length !== 1 ? 's' : ''} across all services
                </p>
                <div className="flex items-center gap-2">
                    <button onClick={onRefresh} className="px-3 py-1.5 rounded-lg border border-border text-xs flex items-center gap-1.5 hover:bg-muted/50">
                        <RefreshCw size={12} /> Refresh
                    </button>
                    <button
                        onClick={() => setAddingDomain(!addingDomain)}
                        className="px-3 py-1.5 rounded-lg bg-gradient-to-r from-blue-500 to-cyan-600 text-white text-xs font-semibold flex items-center gap-1.5"
                    >
                        <Plus size={12} /> Add Domain
                    </button>
                </div>
            </div>

            {/* Add Domain Form */}
            <AnimatePresence>
                {addingDomain && (
                    <motion.div
                        initial={{ opacity: 0, height: 0 }}
                        animate={{ opacity: 1, height: 'auto' }}
                        exit={{ opacity: 0, height: 0 }}
                        className="bg-card border border-border rounded-xl p-5 space-y-4"
                    >
                        <h3 className="font-bold text-sm">Add Custom Domain</h3>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div>
                                <label className="text-xs font-medium text-muted-foreground">Service</label>
                                <select
                                    value={newDomainService}
                                    onChange={e => setNewDomainService(e.target.value)}
                                    className="w-full mt-1 px-3 py-2 rounded-lg bg-background border border-border text-sm"
                                >
                                    <option value="">Select a service</option>
                                    {services.map(svc => (
                                        <option key={svc.id} value={svc.id}>{svc.name}</option>
                                    ))}
                                </select>
                            </div>
                            <div>
                                <label className="text-xs font-medium text-muted-foreground">Domain</label>
                                <input
                                    value={newDomainName}
                                    onChange={e => setNewDomainName(e.target.value)}
                                    placeholder="app.example.com"
                                    className="w-full mt-1 px-3 py-2 rounded-lg bg-background border border-border text-sm"
                                />
                            </div>
                        </div>
                        <div className="flex items-center gap-2">
                            <button
                                onClick={onAddDomain}
                                disabled={domainSubmitting || !newDomainService || !newDomainName.trim()}
                                className="px-4 py-2 rounded-lg bg-blue-500 text-white text-sm font-semibold flex items-center gap-2 disabled:opacity-50"
                            >
                                {domainSubmitting ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
                                Add
                            </button>
                            <button
                                onClick={() => setAddingDomain(false)}
                                className="px-4 py-2 rounded-lg border border-border text-sm hover:bg-muted/50"
                            >
                                Cancel
                            </button>
                        </div>
                        <div className="bg-blue-500/5 border border-blue-500/20 rounded-lg p-3">
                            <p className="text-xs text-blue-400">
                                <strong>DNS Setup:</strong> Create a CNAME record pointing your domain to the service&apos;s public domain.
                                After adding, click &quot;Verify&quot; to confirm DNS propagation.
                            </p>
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>

            {/* Domains List */}
            {domains.length === 0 ? (
                <div className="text-center py-12 bg-card border border-border rounded-xl">
                    <Globe className="mx-auto text-muted-foreground mb-3" size={32} />
                    <p className="text-muted-foreground">No custom domains configured</p>
                    <p className="text-xs text-muted-foreground mt-1">Add a domain to get started</p>
                </div>
            ) : (
                <div className="space-y-2">
                    {domains.map(d => (
                        <div key={`${d.service_id}-${d.domain}`} className="bg-card border border-border rounded-xl p-4 flex items-center gap-4">
                            <div className={`w-8 h-8 rounded-lg flex items-center justify-center ${d.verified ? 'bg-emerald-500/10' : 'bg-amber-500/10'}`}>
                                {d.verified
                                    ? <CheckCircle2 className="text-emerald-500" size={14} />
                                    : <AlertCircle className="text-amber-500" size={14} />
                                }
                            </div>
                            <div className="flex-1 min-w-0">
                                <div className="flex items-center gap-2">
                                    <p className="font-bold text-sm">{d.domain}</p>
                                    <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${d.verified ? 'bg-emerald-500/10 text-emerald-500' : 'bg-amber-500/10 text-amber-500'}`}>
                                        {d.verified ? 'Verified' : 'Pending'}
                                    </span>
                                </div>
                                <p className="text-xs text-muted-foreground">
                                    {d.service_name} → {d.public_domain || 'no public domain'}
                                </p>
                            </div>

                            {/* CNAME Info */}
                            {d.public_domain && !d.verified && (
                                <div className="hidden md:flex items-center gap-2 text-xs text-muted-foreground">
                                    <span>CNAME →</span>
                                    <code className="bg-muted/50 px-2 py-0.5 rounded font-mono">{d.public_domain}</code>
                                    <button onClick={() => onCopy(d.public_domain)} className="p-1 rounded hover:bg-muted/50">
                                        <Copy size={12} />
                                    </button>
                                </div>
                            )}

                            <div className="flex items-center gap-1.5">
                                {!d.verified && (
                                    <button
                                        onClick={() => onVerifyDomain(d.service_id, d.domain)}
                                        disabled={verifyingDomain === d.domain}
                                        className="px-2.5 py-1.5 rounded-lg border border-border text-xs flex items-center gap-1.5 hover:bg-muted/50 disabled:opacity-50"
                                    >
                                        {verifyingDomain === d.domain ? <Loader2 size={12} className="animate-spin" /> : <Shield size={12} />}
                                        Verify
                                    </button>
                                )}
                                <button
                                    onClick={() => onDeleteDomain(d.service_id, d.domain)}
                                    className="px-2.5 py-1.5 rounded-lg text-red-500 hover:bg-red-500/10 text-xs flex items-center gap-1.5"
                                >
                                    <Trash2 size={12} />
                                </button>
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}
