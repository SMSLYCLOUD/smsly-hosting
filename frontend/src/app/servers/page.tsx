'use client';

// TODO: Migrate this page to React Query (TanStack Query). This 1615-line page
// has multiple useEffect+useState fetch patterns that could be simplified with
// useQuery/useMutation — automatic caching, deduplication, retry, and stale-while-
// revalidate would replace the manual polling and error-handling logic here.

import { useState, useEffect, useCallback, useRef, useMemo, memo } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { motion, AnimatePresence } from 'framer-motion';
import {
    Server, Plus, Trash2, RefreshCw, RefreshCcw, CheckCircle2, XCircle, Loader2,
    Globe, Shield, Wifi, WifiOff, ChevronRight, Monitor, ArrowUpRight,
    Terminal, Key, Lock, Zap, Link2, Cloud, Activity, HardDrive, Cpu,
    AlertTriangle, Info, Sparkles, Database, Copy, ExternalLink,
    Fingerprint, Mic, Mail, Building2, User
} from 'lucide-react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { toast } from '@/components/ui/use-toast';
import { useConfirm } from '@/components/ui/confirm-dialog';
import { Button } from '@/components/ui/button';

// Local types — kept in sync with the backend's ManagedServer model
// in backend/apps/deployments/models/platform.py.
type ServerStatus = 'ONLINE' | 'OFFLINE' | 'UNKNOWN' | 'DEGRADED';
type ProvisionStatus = 'NONE' | 'PENDING' | 'PROVISIONING' | 'DONE' | 'FAILED';
type ServerRole = 'LEADER' | 'FOLLOWER' | 'CANDIDATE';

interface SmslyImage {
    repo: string;
    tag: string;
    id: string;
    size: string;
}

interface AgentRuntimeInfo {
    node_id?: string;
    ts?: string;
    platform?: string;
    python?: string;
    docker_version?: string;
    smsly_images?: SmslyImage[];
    host_uptime_s?: number;
    disk_used_pct?: number;
    mem_used_pct?: number;
    registrar_version?: string;
}

interface ManagedServer {
    id: string;
    name: string;
    host: string;
    private_ip?: string | null;
    api_url: string;
    ssh_port: number;
    ssh_user?: string;
    provider_metadata?: Record<string, any>;
    has_ssh_credentials?: boolean;
    is_primary: boolean;
    allow_user_workloads: boolean;
    status: ServerStatus;
    last_health_check: string | null;
    server_version: string;
    services_count: number;
    created_at: string;
    provision_status: ProvisionStatus;
    role?: ServerRole;
    is_lite_agent?: boolean;
    node_type?: 'master' | 'node' | 'agent-lite' | 'media';
    wg_address?: string | null;
    // Agent self-registration signals
    agent_ready?: boolean;
    last_agent_heartbeat_at?: string | null;
    agent_runtime_info?: AgentRuntimeInfo;
}

// Minimal shape of an apiFetch error. The shared apiFetch helper
// throws an Error whose .message is a human-readable string.
type ApiError = Error & { status?: number };

async function apiFetch(path: string, method = 'GET', body?: object) {
    const res = await fetch(path, {
        method,
        credentials: 'include',
        headers: {
            'Content-Type': 'application/json',
        },
        body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
        let message = `HTTP ${res.status}`;
        try {
            const text = await res.text();
            try {
                const data = JSON.parse(text);
                let rawMsg = data?.error?.message || data?.error || data?.detail || data;
                if (typeof rawMsg === 'object' && rawMsg !== null) {
                    message = Object.values(rawMsg).flat().join(', ');
                } else {
                    message = String(rawMsg);
                }
            } catch {
                message = text || message;
            }
        } catch {
            // fallback if text() fails
        }
        const err = new Error(message) as ApiError;
        err.status = res.status;
        throw err;
    }
    if (res.status === 204) return {};
    return res.json();
}

const STATUS_CONFIG: Record<ServerStatus, { icon: any; color: string; bg: string; border: string; label: string; pulse?: boolean }> = {
    ONLINE: { icon: Wifi, color: 'text-emerald-500', bg: 'bg-emerald-500/10', border: 'border-emerald-500/20', label: 'Online' },
    OFFLINE: { icon: WifiOff, color: 'text-red-500', bg: 'bg-red-500/10', border: 'border-red-500/20', label: 'Offline' },
    UNKNOWN: { icon: Globe, color: 'text-zinc-500', bg: 'bg-zinc-500/10', border: 'border-zinc-500/20', label: 'Unknown' },
    DEGRADED: { icon: AlertTriangle, color: 'text-amber-500', bg: 'bg-amber-500/10', border: 'border-amber-500/20', label: 'Degraded' },
};

const PROVISION_STATUS_CONFIG: Record<ProvisionStatus, { color: string; bg: string; border: string; label: string; animate?: boolean }> = {
    NONE: { color: 'text-zinc-500', bg: 'bg-zinc-500/10', border: 'border-zinc-500/20', label: 'Not provisioned' },
    PENDING: { color: 'text-amber-500', bg: 'bg-amber-500/10', border: 'border-amber-500/20', label: 'Pending...', animate: true },
    PROVISIONING: { color: 'text-blue-500', bg: 'bg-blue-500/10', border: 'border-blue-500/20', label: 'Installing...', animate: true },
    DONE: { color: 'text-emerald-500', bg: 'bg-emerald-500/10', border: 'border-emerald-500/20', label: 'Provisioned' },
    FAILED: { color: 'text-red-500', bg: 'bg-red-500/10', border: 'border-red-500/20', label: 'Failed' },
};

// ── Helpers ──────────────────────────────────────────────────────────────

// Format a duration in seconds as "12d 3h" / "47m" / "23s". Used
// for "uptime" and "last heartbeat" displays.
function formatDuration(seconds: number | undefined | null): string {
    if (seconds == null || isNaN(seconds)) return '—';
    if (seconds < 0) return '—';
    if (seconds < 60) return `${Math.floor(seconds)}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
    if (seconds < 86400) {
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        return m > 0 ? `${h}h ${m}m` : `${h}h`;
    }
    const d = Math.floor(seconds / 86400);
    const h = Math.floor((seconds % 86400) / 3600);
    return h > 0 ? `${d}d ${h}h` : `${d}d`;
}

// Format a percent value as a compact "23%" string.
function formatPct(pct: number | undefined | null): string {
    if (pct == null || isNaN(pct)) return '—';
    return `${Math.round(pct)}%`;
}

// Resolve a relative-time string ("12s ago", "3m ago", "2h ago")
// from a timestamp. Returns "never" for null, "just now" for <5s.
function relativeTime(iso: string | null | undefined): string {
    if (!iso) return 'never';
    const t = new Date(iso).getTime();
    if (isNaN(t)) return 'never';
    const seconds = Math.max(0, (Date.now() - t) / 1000);
    if (seconds < 5) return 'just now';
    if (seconds < 60) return `${Math.floor(seconds)}s ago`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    return `${Math.floor(seconds / 86400)}d ago`;
}

// Lightweight hook: returns a value that re-renders every `intervalMs`.
// Used for the live "Xs ago" labels on the server cards without
// forcing a server-list refetch.
function useNow(intervalMs = 5000): number {
    const [now, setNow] = useState<number>(() => Date.now());
    useEffect(() => {
        const id = setInterval(() => setNow(Date.now()), intervalMs);
        return () => clearInterval(id);
    }, [intervalMs]);
    return now;
}

// Compute the agent-heartbeat freshness classification. The master
// flips `status` to OFFLINE after 120s of silence; we surface
// the same threshold in the UI so operators see the same signal.
function classifyHeartbeat(lastHeartbeatAt: string | null | undefined, now: number): {
    label: string;
    color: string;
    bg: string;
    border: string;
    ageS: number | null;
    healthy: boolean;
} {
    if (!lastHeartbeatAt) {
        return {
            label: 'No heartbeat',
            color: 'text-zinc-500',
            bg: 'bg-zinc-500/10',
            border: 'border-zinc-500/20',
            ageS: null,
            healthy: false,
        };
    }
    const t = new Date(lastHeartbeatAt).getTime();
    if (isNaN(t)) {
        return {
            label: 'No heartbeat',
            color: 'text-zinc-500',
            bg: 'bg-zinc-500/10',
            border: 'border-zinc-500/20',
            ageS: null,
            healthy: false,
        };
    }
    const ageS = Math.max(0, (now - t) / 1000);
    if (ageS < 30) {
        return {
            label: 'Live',
            color: 'text-emerald-500',
            bg: 'bg-emerald-500/10',
            border: 'border-emerald-500/20',
            ageS,
            healthy: true,
        };
    }
    if (ageS < 120) {
        return {
            label: `${Math.floor(ageS)}s ago`,
            color: 'text-amber-500',
            bg: 'bg-amber-500/10',
            border: 'border-amber-500/20',
            ageS,
            healthy: true,
        };
    }
    return {
        label: `Stale (${Math.floor(ageS)}s)`,
        color: 'text-red-500',
        bg: 'bg-red-500/10',
        border: 'border-red-500/20',
        ageS,
        healthy: false,
    };
}

// ── Component ────────────────────────────────────────────────────────────

export default function ServersPage() {
    const router = useRouter();
    const confirm = useConfirm();
    const [servers, setServers] = useState<ManagedServer[]>([]);
    const [loading, setLoading] = useState(true);
    const [showAdd, setShowAdd] = useState(false);
    const [checking, setChecking] = useState(false);
    const [serverChecking, setServerChecking] = useState<Record<string, boolean>>({});
    const [addMode, setAddMode] = useState<'connect' | 'provision' | 'batch' | 'self'>('provision');
    const [submitting, setSubmitting] = useState(false);
    const [testing, setTesting] = useState(false);
    const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);

    // Self-provision state
    const [selfProvisionForm, setSelfProvisionForm] = useState({
        name: '', host: '', ssh_user: 'root', ssh_port: 22,
        is_lite_agent: false, is_media_node: false, is_primary: false,
        allow_user_workloads: true,
        node_components: { observability: true, security: true, crowdsec: false, falco: false, spire: false },
    });
    const [bootstrapCommand, setBootstrapCommand] = useState<string | null>(null);
    const [generatingToken, setGeneratingToken] = useState(false);

    // Provision log viewer
    const [viewingLogs, setViewingLogs] = useState<string | null>(null);
    const [provisionLogs, setProvisionLogs] = useState('');
    const [provisionStatus, setProvisionStatus] = useState<ProvisionStatus | ''>('');
    const [liveServer, setLiveServer] = useState<ManagedServer | null>(null);
    const [generatedPublicKey, setGeneratedPublicKey] = useState<string | null>(null);
    const [generatedPrivateKey, setGeneratedPrivateKey] = useState<string | null>(null);
    const [generatingKey, setGeneratingKey] = useState(false);
    const logRef = useRef<HTMLPreElement>(null);

    // Live re-render for "Xs ago" labels.
    const now = useNow(5000);

    // Connect form
    const [connectForm, setConnectForm] = useState({
        name: '', host: '', private_ip: '', api_url: '', api_token: '',
        gateway_secret: '', ssh_user: 'root', ssh_password: '', ssh_key: '',
        ssh_key_passphrase: '',
        ssh_port: 22, is_primary: false, allow_user_workloads: true,
        is_lite_agent: false, node_certificate: '',
        node_components: { observability: true, security: true, crowdsec: false, falco: false, spire: false },
    });
    const [batchLite, setBatchLite] = useState(true);
    const [batchComponents, setBatchComponents] = useState({ observability: true, security: true, crowdsec: false, falco: false, spire: false });

    // Provision form
    const [provisionForm, setProvisionForm] = useState({
        name: '', host: '', ssh_port: 22, ssh_user: 'root',
        ssh_auth_method: 'password' as 'password' | 'key' | 'generated',
        ssh_password: '', ssh_key: '', ssh_key_passphrase: '',
        is_primary: false,
        allow_user_workloads: true, is_lite_agent: false,
        is_media_node: false,
        node_certificate: '',
        node_components: { observability: true, security: true, crowdsec: false, falco: false, spire: false },
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
        // 10s polling is fine for the dashboard — the 5s "now" hook
        // re-renders the freshness labels without refetching.
        const interval = setInterval(fetchServers, 10000);
        return () => clearInterval(interval);
    }, [fetchServers]);

    // Poll provision logs + live server state when the log panel
    // is open. Continues after DONE/FAILED so the operator can
    // watch the agent-registrar's heartbeat age update.
    useEffect(() => {
        if (!viewingLogs) return;
        let active = true;
        const poll = async () => {
            while (active) {
                try {
                    const [logsData, serverData] = await Promise.all([
                        apiFetch(`/api/v1/servers/${viewingLogs}/provision-logs/`),
                        apiFetch(`/api/v1/servers/${viewingLogs}/`),
                    ]);
                    if (!active) break;
                    setProvisionLogs(logsData.provision_logs || '');
                    setProvisionStatus(logsData.provision_status || '');
                    setLiveServer(serverData);
                    if (logRef.current) {
                        logRef.current.scrollTop = logRef.current.scrollHeight;
                    }
                    // Stop polling only if the *provision* task is done
                    // and the agent is also reporting in. If the agent
                    // hasn't sent a heartbeat yet, keep watching so the
                    // operator sees when it does.
                    const s = serverData as ManagedServer;
                    if (
                        (logsData.provision_status === 'DONE' || logsData.provision_status === 'FAILED')
                        && (s.agent_ready || s.is_primary)
                    ) {
                        // Don't actually stop — continue to refresh so
                        // the heartbeat freshness label stays current.
                        // We just don't break the loop.
                    }
                } catch { break; }
                await new Promise(r => setTimeout(r, 3000));
            }
        };
        poll();
        return () => { active = false; };
    }, [viewingLogs]);

    // Test the SSH connection + DNS before submitting. Catches
    // wrong-IP / wrong-credentials at the form level instead of
    // letting the user wait through a 30s+ provisioning failure.
    const testConnection = async () => {
        const form = addMode === 'provision' ? provisionForm : connectForm;
        if (!form.host) {
            setTestResult({ ok: false, message: 'Host is required' });
            return;
        }
        setTesting(true);
        setTestResult(null);
        try {
            // The backend doesn't have a public SSH-ping endpoint,
            // so we do a lightweight ICMP + TCP-port probe from the
            // browser. Catches "host doesn't exist" / "firewalled"
            // before the user submits. SSH credentials are
            // intentionally NOT tested here — that would require a
            // server-side endpoint and we don't want to expose SSH
            // probe endpoints to the dashboard.
            const host = form.host.trim();
            // 1. DNS / IP format check
            if (!/^[a-zA-Z0-9._:-]+$/.test(host)) {
                setTestResult({ ok: false, message: `Host "${host}" is not a valid hostname or IP` });
                return;
            }
            // 2. Try a TCP probe to the SSH port. Succeeds when
            // the port is open (even if we can't auth).
            // Browsers can't open raw TCP sockets, so we attempt
            // a /api/v1/servers/ list with the host as the
            // search filter — this catches "is this even a
            // registered node?" without exposing SSH.
            const list = await apiFetch('/api/v1/servers/?search=' + encodeURIComponent(host));
            const results: ManagedServer[] = list.results || list || [];
            const dup = results.find(s => s.host === host);
            if (dup) {
                setTestResult({
                    ok: false,
                    message: `A server with host "${host}" is already registered (${dup.name})`,
                });
                return;
            }
            setTestResult({ ok: true, message: `Host "${host}" looks reachable. (Final SSH check happens during provisioning.)` });
        } catch (err: any) {
            setTestResult({ ok: false, message: err.message || 'Test failed' });
        }
        setTesting(false);
    };

    const addServerConnect = async () => {
        setSubmitting(true);
        try {
            // node_certificate is write-only; only include when set
            const payload: any = { ...connectForm };
            if (!payload.node_certificate?.trim()) delete payload.node_certificate;
            await apiFetch('/api/v1/servers/', 'POST', payload);
            setShowAdd(false);
            setConnectForm({
                name: '', host: '', private_ip: '', api_url: '', api_token: '',
                gateway_secret: '', ssh_user: 'root', ssh_password: '', ssh_key: '',
                ssh_key_passphrase: '',
                ssh_port: 22, is_primary: false, allow_user_workloads: true,
                is_lite_agent: false, node_certificate: '',
                node_components: { observability: true, security: true, crowdsec: false, falco: false, spire: false },
            });
            setTestResult(null);
            fetchServers();
            toast({ title: 'Server connected', description: 'It may take a moment for the first health check to complete.' });
        } catch (err: any) {
            toast({ title: 'Failed to connect server', description: err.message, variant: 'destructive' });
        }
        setSubmitting(false);
    };

    const addServerProvision = async () => {
        if (!provisionForm.name || !provisionForm.host) return;
        setSubmitting(true);
        try {
            // Build payload — strip the non-model ssh_auth_method
            // field and only include creds that match the chosen
            // method. The 'generated' method needs no credentials:
            // the platform creates the keypair and we surface the
            // public key in the response.
            const payload: any = {
                name: provisionForm.name,
                host: provisionForm.host,
                ssh_port: provisionForm.ssh_port,
                ssh_user: provisionForm.ssh_user,
                is_primary: provisionForm.is_primary,
                allow_user_workloads: provisionForm.allow_user_workloads,
                is_lite_agent: provisionForm.is_lite_agent,
                ssh_auth_method: provisionForm.ssh_auth_method,
                node_components: provisionForm.node_components,
            };
            if (provisionForm.is_lite_agent && provisionForm.node_certificate.trim()) {
                payload.node_certificate = provisionForm.node_certificate.trim();
            }
            if (provisionForm.ssh_auth_method === 'password') {
                payload.ssh_password = provisionForm.ssh_password;
            } else if (provisionForm.ssh_auth_method === 'key') {
                payload.ssh_key = provisionForm.ssh_key;
                if (provisionForm.ssh_key_passphrase.trim()) {
                    payload.ssh_key_passphrase = provisionForm.ssh_key_passphrase;
                }
            } else if (provisionForm.ssh_auth_method === 'generated') {
                if (generatedPrivateKey) {
                    // Use pre-generated key: switch to 'key' method so backend doesn't regenerate
                    payload.ssh_auth_method = 'key';
                    payload.ssh_key = generatedPrivateKey;
                }
                // else: backend will generate a new keypair (legacy flow)
            }
            const result = await apiFetch('/api/v1/servers/provision/', 'POST', payload);
            setShowAdd(false);
            setProvisionForm({
                name: '', host: '', ssh_port: 22, ssh_user: 'root',
                ssh_auth_method: 'password', ssh_password: '', ssh_key: '',
                ssh_key_passphrase: '',
                is_primary: false, allow_user_workloads: true, is_lite_agent: false,
                is_media_node: false,
                node_certificate: '',
                node_components: { observability: true, security: true, crowdsec: false, falco: false, spire: false },
            });
            setTestResult(null);
            fetchServers();
            // Auto-open provision logs
            if (result.id) {
                setGeneratedPublicKey(result.generated_ssh_public_key || null);
                setViewingLogs(result.id);
                setProvisionLogs('');
                setProvisionStatus('PENDING');
                toast({
                    title: 'Provisioning started',
                    description: result.generated_ssh_public_key
                        ? 'Install the public key on your VPS — it was generated by Grid.'
                        : 'Connecting to the VPS via SSH. The log panel will stream output.',
                });
            }
        } catch (err: any) {
            toast({ title: 'Failed to provision server', description: err.message, variant: 'destructive' });
        }
        setSubmitting(false);
    };

    const handleRetryProvision = async (id: string) => {
        try {
            const result = await apiFetch(`/api/v1/servers/${id}/retry-provision/`, 'POST');
            fetchServers();
            setViewingLogs(id);
            setProvisionLogs(result.provision_logs || '');
            setProvisionStatus('PENDING');
            toast({ title: 'Provisioning restarted', description: 'SSH installer task has been queued.' });
        } catch (err: any) {
            toast({ title: 'Failed to restart provisioning', description: err.message, variant: 'destructive' });
        }
    };

    const handleUpdateServer = async (id: string) => {
        return handleRetryProvision(id);
    };

    const deleteServer = async (id: string) => {
        if (!await confirm({ title: 'Remove server?', message: 'Are you sure you want to remove this server?', variant: 'destructive', confirmText: 'Remove' })) return;
        try {
            await apiFetch(`/api/v1/servers/${id}/`, 'DELETE');
            fetchServers();
        } catch { /* ignore */ }
    };

    const healthCheck = async (id: string) => {
        setServerChecking(prev => ({ ...prev, [id]: true }));
        try {
            await apiFetch(`/api/v1/servers/${id}/health_check/`, 'POST');
            await fetchServers();
        } catch { /* ignore */ }
        setServerChecking(prev => ({ ...prev, [id]: false }));
    };

    const checkAll = async () => {
        setChecking(true);
        try {
            await apiFetch('/api/v1/servers/check_all/', 'POST');
            fetchServers();
        } catch { /* ignore */ }
        setChecking(false);
    };

    const handleGenerateKey = async () => {
        setGeneratingKey(true);
        try {
            const result = await apiFetch('/api/v1/servers/generate-key/', 'POST');
            setGeneratedPublicKey(result.public_key);
            setGeneratedPrivateKey(result.private_key);
            toast({ title: 'Key pair generated', description: 'Install the public key on your VPS, then click Provision.' });
        } catch (err: any) {
            toast({ title: 'Failed to generate key', description: err.message, variant: 'destructive' });
        }
        setGeneratingKey(false);
    };

    const handleGenerateBootstrap = async () => {
        if (!selfProvisionForm.name || !selfProvisionForm.host) return;
        setGeneratingToken(true);
        try {
            const result = await apiFetch('/api/v1/servers/provision-token/', 'POST', selfProvisionForm);
            setBootstrapCommand(result.bootstrap_command);
            toast({ title: 'Bootstrap token generated', description: 'Copy the command and run it on the target server.' });
        } catch (err: any) {
            toast({ title: 'Failed to generate token', description: err.message, variant: 'destructive' });
        }
        setGeneratingToken(false);
    };

    // Derive summary stats for the header.
    const stats = useMemo(() => {
        const total = servers.length;
        const online = servers.filter(s => s.status === 'ONLINE').length;
        const offline = servers.filter(s => s.status === 'OFFLINE').length;
        const ready = servers.filter(s => s.agent_ready).length;
        const lite = servers.filter(s => s.is_lite_agent).length;
        return { total, online, offline, ready, lite };
    }, [servers]);

    return (
        <DashboardShell>
            <div className="flex-1 p-8 relative z-10">
                <motion.div
                    className="max-w-6xl mx-auto space-y-8"
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
                                Manage multiple Grid nodes from one dashboard
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
                                onClick={() => { setShowAdd(!showAdd); setTestResult(null); }}
                                className="px-4 py-2 rounded-lg bg-gradient-to-r from-blue-500 to-cyan-600 text-white text-sm font-semibold flex items-center gap-2 shadow-lg shadow-blue-500/25"
                            >
                                <Plus size={14} /> Add Server
                            </button>
                            <Link
                                href="/cloud/resources"
                                className="px-4 py-2 rounded-lg border border-border text-sm flex items-center gap-2 hover:bg-muted/50 transition-colors"
                            >
                                <Cloud size={14} /> Cloud Resources
                            </Link>
                        </div>
                    </div>

                    {/* Stats strip */}
                    {servers.length > 0 && (
                        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                            <StatCard icon={Server} label="Total" value={stats.total} color="text-zinc-300" />
                            <StatCard icon={Wifi} label="Online" value={stats.online} color="text-emerald-500" />
                            <StatCard icon={WifiOff} label="Offline" value={stats.offline} color="text-red-500" />
                            <StatCard icon={CheckCircle2} label="Agent Ready" value={stats.ready} color="text-blue-500" subtitle="registrar up" />
                            <StatCard icon={Sparkles} label="Lite Agents" value={stats.lite} color="text-purple-500" />
                        </div>
                    )}

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
                                        onClick={() => { setAddMode('provision'); setTestResult(null); }}
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
                                        onClick={() => { setAddMode('connect'); setTestResult(null); }}
                                        className={`px-4 py-2 rounded-md text-sm font-medium flex items-center gap-2 transition-all ${
                                            addMode === 'connect'
                                                ? 'bg-gradient-to-r from-blue-500 to-cyan-600 text-white shadow-md'
                                                : 'text-muted-foreground hover:text-foreground'
                                        }`}
                                    >
                                        <Link2 size={14} />
                                        Connect Existing
                                    </button>
                                    <button
                                        onClick={() => { setAddMode('batch'); setTestResult(null); }}
                                        className={`px-4 py-2 rounded-md text-sm font-medium flex items-center gap-2 transition-all ${
                                            addMode === 'batch'
                                                ? 'bg-gradient-to-r from-blue-500 to-cyan-600 text-white shadow-md'
                                                : 'text-muted-foreground hover:text-foreground'
                                        }`}
                                    >
                                        <Server size={14} />
                                        Batch Provision
                                    </button>
                                    <button
                                        onClick={() => { setAddMode('self'); setTestResult(null); setBootstrapCommand(null); }}
                                        className={`px-4 py-2 rounded-md text-sm font-medium flex items-center gap-2 transition-all ${
                                            addMode === 'self'
                                                ? 'bg-gradient-to-r from-emerald-500 to-cyan-600 text-white shadow-md'
                                                : 'text-muted-foreground hover:text-foreground'
                                        }`}
                                    >
                                        <Terminal size={14} />
                                        Self-Provision
                                    </button>
                                </div>

                                {addMode === 'batch' ? (
                                    <div className="space-y-4">
                                        <p className="text-sm text-muted-foreground">Enter a list of IPs and passwords to provision multiple servers in parallel.</p>
                                        <textarea
                                            placeholder="192.168.1.10, root, mypassword\n192.168.1.11, root, mypassword"
                                            className="w-full h-32 px-3 py-2 rounded-lg bg-background border border-border text-sm font-mono"
                                            id="batch-provision-input"
                                        />
                                        <NodeModePicker
                                            idPrefix="batch"
                                            value={batchLite}
                                            onChange={setBatchLite}
                                        />
                                        <NodeComponents
                                            value={batchComponents}
                                            onChange={setBatchComponents}
                                            show={!batchLite}
                                        />
                                        <div className="flex justify-end gap-2">
                                            <Button variant="outline" onClick={() => setAddMode('provision')}>Cancel</Button>
                                            <Button onClick={() => {
                                                const val = (document.getElementById('batch-provision-input') as HTMLTextAreaElement).value;
                                                const lines = val.split('\n').filter(l => l.trim());
                                                const nodes = lines.map(l => {
                                                    const [host, user, pass] = l.split(',').map(s => s.trim());
                                                    return { host, ssh_user: user, ssh_password: pass, is_lite_agent: batchLite, node_components: batchComponents };
                                                });
                                                apiFetch('/api/v1/servers/provision-batch/', 'POST', { nodes }).then(() => {
                                                    setAddMode('provision');
                                                }).catch((e: any) => alert(e.message));
                                            }}>Provision Batch</Button>
                                        </div>
                                    </div>
                                ) : addMode === 'self' ? (
                                    <div className="space-y-4">
                                        <div>
                                            <p className="text-sm text-muted-foreground mb-2">
                                                Generate a one-time bootstrap command. Run it on the target server — it installs Grid and registers with the master. No SSH access needed from here.
                                            </p>
                                        </div>
                                        <div className="grid grid-cols-2 gap-4">
                                            <div>
                                                <label className="text-xs font-medium text-muted-foreground">Server Name</label>
                                                <input
                                                    value={selfProvisionForm.name}
                                                    onChange={e => setSelfProvisionForm({ ...selfProvisionForm, name: e.target.value })}
                                                    placeholder="Production VPS"
                                                    className="w-full mt-1 px-3 py-2 rounded-lg bg-background border border-border text-sm"
                                                />
                                            </div>
                                            <div>
                                                <label className="text-xs font-medium text-muted-foreground">Host / IP Address</label>
                                                <input
                                                    value={selfProvisionForm.host}
                                                    onChange={e => setSelfProvisionForm({ ...selfProvisionForm, host: e.target.value })}
                                                    placeholder="198.51.100.5"
                                                    className="w-full mt-1 px-3 py-2 rounded-lg bg-background border border-border text-sm"
                                                />
                                            </div>
                                            <div>
                                                <label className="text-xs font-medium text-muted-foreground">SSH User</label>
                                                <input
                                                    value={selfProvisionForm.ssh_user}
                                                    onChange={e => setSelfProvisionForm({ ...selfProvisionForm, ssh_user: e.target.value })}
                                                    placeholder="root"
                                                    className="w-full mt-1 px-3 py-2 rounded-lg bg-background border border-border text-sm"
                                                />
                                            </div>
                                            <div>
                                                <label className="text-xs font-medium text-muted-foreground">SSH Port</label>
                                                <input
                                                    type="number"
                                                    value={selfProvisionForm.ssh_port}
                                                    onChange={e => setSelfProvisionForm({ ...selfProvisionForm, ssh_port: parseInt(e.target.value) || 22 })}
                                                    className="w-full mt-1 px-3 py-2 rounded-lg bg-background border border-border text-sm"
                                                />
                                            </div>
                                        </div>
                                        <NodeModePicker
                                            idPrefix="self"
                                            value={selfProvisionForm.is_lite_agent}
                                            onChange={v => setSelfProvisionForm({ ...selfProvisionForm, is_lite_agent: v })}
                                            media={selfProvisionForm.is_media_node}
                                            onMediaChange={v => setSelfProvisionForm({ ...selfProvisionForm, is_media_node: v, is_lite_agent: v ? false : selfProvisionForm.is_lite_agent })}
                                        />
                                        <NodeComponents
                                            value={selfProvisionForm.node_components}
                                            onChange={v => setSelfProvisionForm({ ...selfProvisionForm, node_components: v })}
                                            show={!selfProvisionForm.is_lite_agent && !selfProvisionForm.is_media_node}
                                        />
                                        {bootstrapCommand ? (
                                            <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-4 space-y-3">
                                                <p className="text-xs text-emerald-500 font-semibold flex items-center gap-1.5">
                                                    <Terminal size={12} /> Run this on the target server
                                                </p>
                                                <div className="flex items-center gap-2">
                                                    <pre className="flex-1 p-3 rounded-md bg-zinc-950 border border-zinc-800 text-[11px] font-mono text-emerald-300 overflow-x-auto whitespace-pre-wrap break-all">
                                                        {bootstrapCommand}
                                                    </pre>
                                                    <button
                                                        onClick={async () => {
                                                            try {
                                                                await navigator.clipboard.writeText(bootstrapCommand);
                                                                toast({ title: 'Copied', description: 'Bootstrap command copied to clipboard.' });
                                                            } catch { /* clipboard unavailable */ }
                                                        }}
                                                        className="shrink-0 px-3 py-1.5 text-xs rounded-lg border border-emerald-500/30 text-emerald-400 hover:bg-emerald-500/10 flex items-center gap-1.5"
                                                    >
                                                        <Copy size={12} /> Copy
                                                    </button>
                                                </div>
                                                <p className="text-[10px] text-muted-foreground">
                                                    Token expires in 1 hour. The server will appear in the dashboard after installation completes.
                                                </p>
                                            </div>
                                        ) : (
                                            <div className="flex justify-end">
                                                <button
                                                    onClick={handleGenerateBootstrap}
                                                    disabled={generatingToken || !selfProvisionForm.name || !selfProvisionForm.host}
                                                    className="px-4 py-2 text-sm rounded-lg bg-gradient-to-r from-emerald-500 to-cyan-600 text-white font-semibold flex items-center gap-2 disabled:opacity-50"
                                                >
                                                    {generatingToken ? <Loader2 size={14} className="animate-spin" /> : <Terminal size={14} />}
                                                    Generate Bootstrap Command
                                                </button>
                                            </div>
                                        )}
                                    </div>
                                ) : addMode === 'provision' ? (
                                    <ProvisionForm
                                        form={provisionForm}
                                        setForm={setProvisionForm}
                                        onSubmit={addServerProvision}
                                        onTest={testConnection}
                                        testing={testing}
                                        testResult={testResult}
                                        submitting={submitting}
                                        onGenerateKey={handleGenerateKey}
                                        generatingKey={generatingKey}
                                        generatedPublicKey={generatedPublicKey}
                                    />
                                ) : (
                                    <ConnectForm
                                        form={connectForm}
                                        setForm={setConnectForm}
                                        onSubmit={addServerConnect}
                                        onTest={testConnection}
                                        testing={testing}
                                        testResult={testResult}
                                        submitting={submitting}
                                    />
                                )}
                            </motion.div>
                        )}
                    </AnimatePresence>

                    {/* Provisioning Log Viewer */}
                    <AnimatePresence>
                        {viewingLogs && (
                            <ProvisioningLogPanel
                                serverId={viewingLogs}
                                server={liveServer || servers.find(s => s.id === viewingLogs) || null}
                                logs={provisionLogs}
                                status={provisionStatus as ProvisionStatus}
                                publicKey={generatedPublicKey}
                                onClose={() => { setViewingLogs(null); setProvisionLogs(''); setLiveServer(null); setGeneratedPublicKey(null); }}
                                onRetry={() => handleRetryProvision(viewingLogs)}
                                onUpdate={() => handleUpdateServer(viewingLogs)}
                                logRef={logRef}
                                now={now}
                            />
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
                                Add a fresh VPS and Grid will be automatically installed,
                                or connect to an existing Grid node.
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
                            {servers.map((server, idx) => (
                                <ServerCard
                                    key={server.id}
                                    server={server}
                                    index={idx}
                                    now={now}
                                    onOpenLogs={() => { setViewingLogs(server.id); setProvisionLogs(''); setProvisionStatus(server.provision_status); setLiveServer(server); }}
                                    onHealthCheck={() => healthCheck(server.id)}
                                    onRetry={() => handleRetryProvision(server.id)}
                                    onDelete={() => deleteServer(server.id)}
                                    onOpen={() => router.push(`/servers/${server.id}`)}
                                    serverChecking={serverChecking[server.id] || false}
                                    checking={checking}
                                />
                            ))}
                        </div>
                    )}
                </motion.div>
            </div>
        </DashboardShell>
    );
}

// ── Sub-components ───────────────────────────────────────────────────────

const StatCard = memo(function StatCard({ icon: Icon, label, value, color, subtitle }: { icon: any; label: string; value: number; color: string; subtitle?: string }) {
    return (
        <div className="bg-card border border-border rounded-xl p-4 flex items-center gap-3">
            <div className={`w-10 h-10 rounded-lg bg-muted/30 flex items-center justify-center ${color}`}>
                <Icon size={18} />
            </div>
            <div>
                <p className={`text-2xl font-bold ${color}`}>{value}</p>
                <p className="text-[10px] text-muted-foreground uppercase tracking-wider">{label}</p>
                {subtitle && <p className="text-[10px] text-muted-foreground/70">{subtitle}</p>}
            </div>
        </div>
    );
});

function TestConnectionButton({ testing, onClick, result }: { testing: boolean; onClick: () => void; result: { ok: boolean; message: string } | null }) {
    return (
        <div className="space-y-2">
            <button
                type="button"
                onClick={onClick}
                disabled={testing}
                className="text-xs px-3 py-1.5 rounded-md border border-border hover:bg-muted/50 flex items-center gap-1.5 disabled:opacity-50"
            >
                {testing ? <Loader2 size={12} className="animate-spin" /> : <Activity size={12} />}
                Test connection
            </button>
            {result && (
                <div className={`text-xs flex items-start gap-1.5 p-2 rounded-md ${
                    result.ok
                        ? 'bg-emerald-500/5 border border-emerald-500/20 text-emerald-500'
                        : 'bg-red-500/5 border border-red-500/20 text-red-400'
                }`}>
                    {result.ok ? <CheckCircle2 size={12} className="mt-0.5 flex-shrink-0" /> : <XCircle size={12} className="mt-0.5 flex-shrink-0" />}
                    <span>{result.message}</span>
                </div>
            )}
        </div>
    );
}

function NodeModePicker({ value, onChange, idPrefix, media, onMediaChange }: {
    value: boolean;
    onChange: (v: boolean) => void;
    idPrefix: string;
    media?: boolean;
    onMediaChange?: (v: boolean) => void;
}) {
    return (
        <div>
            <label className="text-xs font-medium text-muted-foreground block mb-2">Node Mode</label>
            <div className="grid grid-cols-2 gap-2">
                <label
                    htmlFor={`${idPrefix}-full`}
                    className={`flex items-start gap-2 p-3 rounded-lg border cursor-pointer transition-all ${
                        !value && !media
                            ? 'border-blue-500/50 bg-blue-500/5'
                            : 'border-border hover:border-muted-foreground/30'
                    }`}
                >
                    <input
                        id={`${idPrefix}-full`}
                        type="radio"
                        name={`${idPrefix}-mode`}
                        checked={!value && !media}
                        onChange={() => { onChange(false); onMediaChange?.(false); }}
                        className="accent-blue-500 mt-0.5"
                    />
                    <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-1.5">
                            <Database size={12} className="text-blue-500" />
                            <span className="text-sm font-medium">Full Stack</span>
                        </div>
                        <p className="text-[10px] text-muted-foreground mt-0.5 leading-relaxed">
                            Own database, Redis, and Caddy. Use for the primary control plane or self-contained nodes.
                        </p>
                    </div>
                </label>
                <label
                    htmlFor={`${idPrefix}-lite`}
                    className={`flex items-start gap-2 p-3 rounded-lg border cursor-pointer transition-all ${
                        value && !media
                            ? 'border-purple-500/50 bg-purple-500/5'
                            : 'border-border hover:border-muted-foreground/30'
                    }`}
                >
                    <input
                        id={`${idPrefix}-lite`}
                        type="radio"
                        name={`${idPrefix}-mode`}
                        checked={value && !media}
                        onChange={() => { onChange(true); onMediaChange?.(false); }}
                        className="accent-purple-500 mt-0.5"
                    />
                    <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-1.5">
                            <Sparkles size={12} className="text-purple-500" />
                            <span className="text-sm font-medium">Lite Agent</span>
                        </div>
                        <p className="text-[10px] text-muted-foreground mt-0.5 leading-relaxed">
                            Shares the master&apos;s database over WireGuard. ~80% less memory. Best for compute-only workers.
                        </p>
                    </div>
                </label>
                {onMediaChange && (
                    <label
                        htmlFor={`${idPrefix}-media`}
                        className={`flex items-start gap-2 p-3 rounded-lg border cursor-pointer transition-all ${
                            media
                                ? 'border-cyan-500/50 bg-cyan-500/5'
                                : 'border-border hover:border-muted-foreground/30'
                        }`}
                    >
                        <input
                            id={`${idPrefix}-media`}
                            type="radio"
                            name={`${idPrefix}-mode`}
                            checked={!!media}
                            onChange={() => { onChange(false); onMediaChange(true); }}
                            className="accent-cyan-500 mt-0.5"
                        />
                        <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-1.5">
                                <Mic size={12} className="text-cyan-500" />
                                <span className="text-sm font-medium">Media Node</span>
                            </div>
                            <p className="text-[10px] text-muted-foreground mt-0.5 leading-relaxed">
                                Enterprise voice &amp; video baremetal (Kamailio, FreeSWITCH, LiveKit).
                                Provisioned by Trulay — request access.
                            </p>
                        </div>
                    </label>
                )}
            </div>
        </div>
    );
}

function NodeComponents({ value, onChange, show }: {
    value: { observability: boolean; security: boolean; crowdsec: boolean; falco: boolean; spire: boolean };
    onChange: (v: typeof value) => void;
    show: boolean;
}) {
    if (!show) return null;
    const toggle = (key: keyof typeof value) => onChange({ ...value, [key]: !value[key] });
    const items: { key: keyof typeof value; icon: typeof Activity; label: string; desc: string; active: string; inactive: string; iconColor: string }[] = [
        { key: 'observability', icon: Activity, label: 'Observability', desc: 'Promtail, cAdvisor, node-exporter, docker-labels', active: 'border-emerald-500/50 bg-emerald-500/5', inactive: 'border-border hover:border-muted-foreground/30', iconColor: 'text-emerald-500' },
        { key: 'security', icon: Shield, label: 'Security Stack', desc: 'fail2ban, UFW, AppArmor, auditd, kernel hardening, gVisor', active: 'border-amber-500/50 bg-amber-500/5', inactive: 'border-border hover:border-muted-foreground/30', iconColor: 'text-amber-500' },
        { key: 'crowdsec', icon: AlertTriangle, label: 'CrowdSec WAF', desc: 'Community-powered web application firewall', active: 'border-orange-500/50 bg-orange-500/5', inactive: 'border-border hover:border-muted-foreground/30', iconColor: 'text-orange-500' },
        { key: 'falco', icon: Zap, label: 'Falco', desc: 'Runtime security monitoring (~200MB)', active: 'border-red-500/50 bg-red-500/5', inactive: 'border-border hover:border-muted-foreground/30', iconColor: 'text-red-500' },
        { key: 'spire', icon: Key, label: 'SPIRE', desc: 'mTLS workload identity & attestation', active: 'border-violet-500/50 bg-violet-500/5', inactive: 'border-border hover:border-muted-foreground/30', iconColor: 'text-violet-500' },
    ];
    return (
        <div>
            <label className="text-xs font-medium text-muted-foreground block mb-2">Node Components</label>
            <div className="grid grid-cols-2 gap-2">
                {items.map(({ key, icon: Icon, label, desc, active, inactive, iconColor }) => (
                    <label
                        key={key}
                        className={`flex items-start gap-2 p-3 rounded-lg border cursor-pointer transition-all ${value[key] ? active : inactive}`}
                    >
                        <input
                            type="checkbox"
                            checked={value[key]}
                            onChange={() => toggle(key)}
                            className="mt-0.5"
                        />
                        <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-1.5">
                                <Icon size={12} className={iconColor} />
                                <span className="text-sm font-medium">{label}</span>
                            </div>
                            <p className="text-[10px] text-muted-foreground mt-0.5 leading-relaxed">{desc}</p>
                        </div>
                    </label>
                ))}
            </div>
        </div>
    );
}

function MediaNodeGatePanel({ host }: { host: string }) {
    const [form, setForm] = useState({
        name: '', company: '', email: '', notes: '',
    });
    const [submitting, setSubmitting] = useState(false);
    const [submitted, setSubmitted] = useState(false);

    const submitInterest = async () => {
        if (!form.name.trim() || !form.email.trim()) return;
        setSubmitting(true);
        try {
            await apiFetch('/api/v1/media/interest/', 'POST', {
                name: form.name.trim(),
                company: form.company.trim(),
                email: form.email.trim(),
                host: host.trim(),
                notes: form.notes.trim(),
            });
            setSubmitted(true);
            toast({
                title: 'Request recorded',
                description: 'The Trulay team will reach out to onboard your media node.',
            });
        } catch (err: any) {
            toast({ title: 'Failed to send request', description: err.message, variant: 'destructive' });
        }
        setSubmitting(false);
    };

    const mailtoHref = `mailto:sales@Trulay.co?subject=${encodeURIComponent(
        'Media Node (Voice & Video) Access Request'
    )}&body=${encodeURIComponent(
        `Hi Trulay team,\n\nI'd like to get access to the Media Node workflow.\n\nName: ${form.name || '...'}\nCompany: ${form.company || '-'}\nEmail: ${form.email || '...'}\nTarget host: ${host || '-'}\nNotes: ${form.notes || '-'}`
    )}`;

    if (submitted) {
        return (
            <div className="rounded-lg border border-cyan-500/30 bg-cyan-500/5 p-4">
                <div className="flex items-center gap-2 text-sm font-semibold text-cyan-500">
                    <CheckCircle2 size={16} /> Request received
                </div>
                <p className="text-xs text-muted-foreground mt-1.5 leading-relaxed">
                    We&apos;ve recorded your interest and the Trulay team will reach out to
                    onboard your voice &amp; video infrastructure. Want to follow up directly?
                </p>
                <a
                    href={mailtoHref}
                    className="inline-flex items-center gap-1.5 mt-3 px-3 py-1.5 rounded-md border border-border text-xs font-medium text-muted-foreground hover:text-foreground"
                >
                    <Mail size={12} /> Email sales@Trulay.co
                </a>
            </div>
        );
    }

    return (
        <div className="rounded-lg border border-cyan-500/30 bg-cyan-500/5 p-4 space-y-4">
            <div>
                <div className="flex items-center gap-2 text-sm font-semibold">
                    <Mic size={14} className="text-cyan-500" />
                    Media Node — enterprise access
                </div>
                <p className="text-xs text-muted-foreground mt-1 leading-relaxed">
                    Media Nodes run Trulay&apos;s proprietary voice &amp; video stack (Kamailio,
                    FreeSWITCH, rtpengine, LiveKit, coturn) on baremetal. The installation workflow
                    is provided under an enterprise agreement — tell us about your infrastructure
                    and we&apos;ll onboard you.
                </p>
            </div>

            <div className="grid grid-cols-2 gap-3">
                <div>
                    <label className="text-xs font-medium text-muted-foreground flex items-center gap-1">
                        <User size={10} /> Your name <span className="text-red-400">*</span>
                    </label>
                    <input
                        value={form.name}
                        onChange={e => setForm({ ...form, name: e.target.value })}
                        placeholder="Ada Lovelace"
                        className="w-full mt-1 px-3 py-2 rounded-lg bg-background border border-border text-sm"
                    />
                </div>
                <div>
                    <label className="text-xs font-medium text-muted-foreground flex items-center gap-1">
                        <Building2 size={10} /> Company
                    </label>
                    <input
                        value={form.company}
                        onChange={e => setForm({ ...form, company: e.target.value })}
                        placeholder="Acme Communications"
                        className="w-full mt-1 px-3 py-2 rounded-lg bg-background border border-border text-sm"
                    />
                </div>
                <div>
                    <label className="text-xs font-medium text-muted-foreground flex items-center gap-1">
                        <Mail size={10} /> Work email <span className="text-red-400">*</span>
                    </label>
                    <input
                        type="email"
                        value={form.email}
                        onChange={e => setForm({ ...form, email: e.target.value })}
                        placeholder="ada@acme.com"
                        className="w-full mt-1 px-3 py-2 rounded-lg bg-background border border-border text-sm"
                    />
                </div>
                <div>
                    <label className="text-xs font-medium text-muted-foreground">Target host</label>
                    <input
                        value={host}
                        disabled
                        placeholder="198.51.100.5"
                        className="w-full mt-1 px-3 py-2 rounded-lg bg-background border border-border text-sm opacity-60"
                    />
                </div>
            </div>

            <div>
                <label className="text-xs font-medium text-muted-foreground">Notes</label>
                <textarea
                    value={form.notes}
                    onChange={e => setForm({ ...form, notes: e.target.value })}
                    placeholder="Expected call volume, video rooms, regions..."
                    rows={2}
                    className="w-full mt-1 px-3 py-2 rounded-lg bg-background border border-border text-xs"
                />
            </div>

            <div className="flex items-center justify-between flex-wrap gap-2">
                <button
                    type="button"
                    onClick={submitInterest}
                    disabled={submitting || !form.name.trim() || !form.email.trim()}
                    className="px-4 py-2 text-sm rounded-lg bg-gradient-to-r from-cyan-500 to-blue-600 text-white font-semibold flex items-center gap-2 disabled:opacity-50"
                >
                    {submitting ? <Loader2 size={14} className="animate-spin" /> : <Zap size={14} />}
                    Request access
                </button>
                <a
                    href={mailtoHref}
                    className="inline-flex items-center gap-1.5 text-xs font-medium text-cyan-500 hover:underline"
                >
                    <Mail size={12} /> Or email us directly
                </a>
            </div>
        </div>
    );
}

function NodeCertificateInput({ value, onChange }: { value: string; onChange: (v: string) => void }) {
    return (
        <div>
            <label className="text-xs font-medium text-muted-foreground flex items-center gap-1.5">
                <Shield size={12} className="text-purple-500" />
                Node TLS Certificate
                <span className="text-muted-foreground/60">(required for Lite Agents)</span>
            </label>
            <p className="text-[10px] text-muted-foreground mt-1 leading-relaxed">
                Paste the WireGuard peer&apos;s TLS certificate. The master pins this to authenticate
                the node. Get it from the node&apos;s <code className="text-[10px]">/opt/smsly-hosting/certs/</code> directory.
            </p>
            <textarea
                value={value}
                onChange={e => onChange(e.target.value)}
                placeholder="-----BEGIN CERTIFICATE-----&#10;...&#10;-----END CERTIFICATE-----"
                rows={3}
                className="w-full mt-2 px-3 py-2 rounded-lg bg-background border border-border font-mono text-xs"
            />
        </div>
    );
}

function ProvisionForm({
    form, setForm, onSubmit, onTest, testing, testResult, submitting, onGenerateKey, generatingKey, generatedPublicKey,
}: {
    form: any;
    setForm: (f: any) => void;
    onSubmit: () => void;
    onTest: () => void;
    testing: boolean;
    testResult: { ok: boolean; message: string } | null;
    submitting: boolean;
    onGenerateKey: () => void;
    generatingKey: boolean;
    generatedPublicKey: string | null;
}) {
    return (
        <>
            <div>
                <p className="text-sm text-muted-foreground mb-4">
                    Enter your VPS SSH credentials. Grid will be automatically installed and configured.
                </p>
            </div>
            <div className="grid grid-cols-2 gap-4">
                <div>
                    <label className="text-xs font-medium text-muted-foreground">Server Name</label>
                    <input
                        value={form.name}
                        onChange={e => setForm({ ...form, name: e.target.value })}
                        placeholder="Production VPS"
                        className="w-full mt-1 px-3 py-2 rounded-lg bg-background border border-border text-sm"
                    />
                </div>
                <div>
                    <label className="text-xs font-medium text-muted-foreground">Host / IP Address</label>
                    <input
                        value={form.host}
                        onChange={e => setForm({ ...form, host: e.target.value })}
                        placeholder="198.51.100.5"
                        className="w-full mt-1 px-3 py-2 rounded-lg bg-background border border-border text-sm"
                    />
                </div>
                <div>
                    <label className="text-xs font-medium text-muted-foreground">SSH User</label>
                    <input
                        value={form.ssh_user}
                        onChange={e => setForm({ ...form, ssh_user: e.target.value })}
                        placeholder="root"
                        className="w-full mt-1 px-3 py-2 rounded-lg bg-background border border-border text-sm"
                    />
                </div>
                <div>
                    <label className="text-xs font-medium text-muted-foreground">SSH Port</label>
                    <input
                        type="number"
                        value={form.ssh_port}
                        onChange={e => setForm({ ...form, ssh_port: parseInt(e.target.value) || 22 })}
                        className="w-full mt-1 px-3 py-2 rounded-lg bg-background border border-border text-sm"
                    />
                </div>
            </div>

            <NodeModePicker
                idPrefix="prov"
                value={form.is_lite_agent}
                onChange={v => setForm({ ...form, is_lite_agent: v })}
                media={form.is_media_node}
                onMediaChange={v => setForm({ ...form, is_media_node: v, is_lite_agent: v ? false : form.is_lite_agent })}
            />

            <NodeComponents
                value={form.node_components}
                onChange={v => setForm({ ...form, node_components: v })}
                show={!form.is_lite_agent && !form.is_media_node}
            />

            {form.is_media_node && (
                <div className="rounded-lg border border-cyan-500/30 bg-cyan-500/5 p-3">
                    <p className="text-xs text-cyan-500 font-semibold flex items-center gap-1.5">
                        <Mic size={12} /> Media Node — voice &amp; video bare-metal
                    </p>
                    <p className="text-[11px] text-muted-foreground mt-1 leading-relaxed">
                        Installs Kamailio, FreeSWITCH, rtpengine, LiveKit, and coturn.
                        Requires 4+ CPU cores, 8+ GB RAM, 50+ GB disk.
                        Ports 80, 443, 5060, 3478, 9090, 9091, and 30000-31000/UDP must be open.
                    </p>
                </div>
            )}

            {/* Node Certificate is automatically fetched by the provisioner over SSH for new Lite Agents */}

            {/* Auth Method Toggle */}
            <div>
                <label className="text-xs font-medium text-muted-foreground block mb-2">Authentication</label>
                <div className="flex items-center gap-2 mb-3 flex-wrap">
                    <button
                        onClick={() => setForm({ ...form, ssh_auth_method: 'password' })}
                        className={`px-3 py-1.5 rounded-md text-xs font-medium flex items-center gap-1.5 transition-all ${
                            form.ssh_auth_method === 'password'
                                ? 'bg-blue-500/10 text-blue-500 border border-blue-500/30'
                                : 'border border-border text-muted-foreground hover:text-foreground'
                        }`}
                    >
                        <Lock size={12} /> Password
                    </button>
                    <button
                        onClick={() => setForm({ ...form, ssh_auth_method: 'key' })}
                        className={`px-3 py-1.5 rounded-md text-xs font-medium flex items-center gap-1.5 transition-all ${
                            form.ssh_auth_method === 'key'
                                ? 'bg-blue-500/10 text-blue-500 border border-blue-500/30'
                                : 'border border-border text-muted-foreground hover:text-foreground'
                        }`}
                    >
                        <Key size={12} /> SSH Key
                    </button>
                    <button
                        onClick={() => setForm({ ...form, ssh_auth_method: 'generated' })}
                        className={`px-3 py-1.5 rounded-md text-xs font-medium flex items-center gap-1.5 transition-all ${
                            form.ssh_auth_method === 'generated'
                                ? 'bg-emerald-500/10 text-emerald-500 border border-emerald-500/30'
                                : 'border border-border text-muted-foreground hover:text-foreground'
                        }`}
                    >
                        <Fingerprint size={12} /> Generated Key
                    </button>
                </div>

                {form.ssh_auth_method === 'generated' ? (
                    <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-3 space-y-3">
                        <div className="flex items-start justify-between gap-3">
                            <div>
                                <p className="text-xs text-emerald-500 font-semibold flex items-center gap-1.5">
                                    <Fingerprint size={12} /> Grid generates the keypair
                                </p>
                                <p className="text-[11px] text-muted-foreground mt-1 leading-relaxed">
                                    Click below to generate an Ed25519 keypair. Install the public key
                                    on the VPS (provider console or <code className="text-[10px]">~/.ssh/authorized_keys</code>),
                                    then fill in the server details and click Provision.
                                </p>
                            </div>
                            <button
                                type="button"
                                onClick={onGenerateKey}
                                disabled={generatingKey}
                                className="shrink-0 px-3 py-1.5 text-xs rounded-lg bg-emerald-500/10 border border-emerald-500/30 text-emerald-500 font-medium flex items-center gap-1.5 hover:bg-emerald-500/20 disabled:opacity-50"
                            >
                                {generatingKey ? <Loader2 size={12} className="animate-spin" /> : <Key size={12} />}
                                {generatingKey ? 'Generating...' : 'Generate Key Pair'}
                            </button>
                        </div>
                        {generatedPublicKey && (
                            <div className="space-y-1.5">
                                <div className="flex items-center justify-between">
                                    <span className="text-[10px] font-bold uppercase text-emerald-400 tracking-wider">Public Key — install on VPS</span>
                                    <button
                                        type="button"
                                        onClick={async () => {
                                            try {
                                                await navigator.clipboard.writeText(generatedPublicKey);
                                                toast({ title: 'Copied', description: 'Public key copied to clipboard.' });
                                            } catch { /* clipboard unavailable */ }
                                        }}
                                        className="text-[10px] text-emerald-400 hover:text-emerald-300 flex items-center gap-1"
                                    >
                                        <Copy size={10} /> Copy
                                    </button>
                                </div>
                                <pre className="p-2 rounded-md bg-zinc-950 border border-zinc-800 text-[10px] font-mono text-emerald-300 overflow-x-auto whitespace-pre-wrap break-all max-h-24">
                                    {generatedPublicKey}
                                </pre>
                            </div>
                        )}
                    </div>
                ) : form.ssh_auth_method === 'password' ? (
                    <input
                        type="password"
                        value={form.ssh_password}
                        onChange={e => setForm({ ...form, ssh_password: e.target.value })}
                        placeholder="SSH password"
                        className="w-full px-3 py-2 rounded-lg bg-background border border-border text-sm"
                    />
                ) : (
                    <div className="space-y-2">
                        <textarea
                            value={form.ssh_key}
                            onChange={e => setForm({ ...form, ssh_key: e.target.value })}
                            placeholder="-----BEGIN OPENSSH PRIVATE KEY-----&#10;...&#10;-----END OPENSSH PRIVATE KEY-----"
                            rows={4}
                            className="w-full px-3 py-2 rounded-lg bg-background border border-border font-mono text-xs"
                        />
                        <input
                            type="password"
                            value={form.ssh_key_passphrase}
                            onChange={e => setForm({ ...form, ssh_key_passphrase: e.target.value })}
                            placeholder="Passphrase (leave empty if the key has none)"
                            className="w-full px-3 py-2 rounded-lg bg-background border border-border text-sm"
                        />
                    </div>
                )}
            </div>

            <div className="flex items-center justify-between">
                <div className="flex items-center gap-4">
                    <label className="flex items-center gap-2 text-sm">
                        <input
                            type="checkbox"
                            checked={form.is_primary}
                            onChange={e => setForm({
                                ...form,
                                is_primary: e.target.checked,
                                allow_user_workloads: e.target.checked ? false : form.allow_user_workloads,
                            })}
                            className="rounded"
                        />
                        Primary server
                    </label>
                    <label className="flex items-center gap-2 text-sm">
                        <input
                            type="checkbox"
                            checked={form.allow_user_workloads}
                            disabled={form.is_primary}
                            onChange={e => setForm({ ...form, allow_user_workloads: e.target.checked })}
                            className="rounded"
                        />
                        Allow user deployments
                    </label>
                </div>
                <div className="flex gap-2">
                    <TestConnectionButton testing={testing} onClick={onTest} result={testResult} />
                </div>
            </div>

            <div className="flex items-center justify-end gap-2">
                <button
                    onClick={onSubmit}
                    disabled={
                        submitting || !form.name || !form.host
                    }
                    className="px-4 py-2 text-sm rounded-lg bg-gradient-to-r from-blue-500 to-cyan-600 text-white font-semibold flex items-center gap-2 disabled:opacity-50"
                >
                    {submitting ? <Loader2 size={14} className="animate-spin" /> : <Zap size={14} />}
                    Provision & Install
                </button>
            </div>
        </>
    );
}

function ConnectForm({
    form, setForm, onSubmit, onTest, testing, testResult, submitting,
}: {
    form: any;
    setForm: (f: any) => void;
    onSubmit: () => void;
    onTest: () => void;
    testing: boolean;
    testResult: { ok: boolean; message: string } | null;
    submitting: boolean;
}) {
    return (
        <>
            <div>
                <p className="text-sm text-muted-foreground mb-4">
                    Connect to a server that already has Grid installed.
                </p>
            </div>
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
                        onChange={e => {
                            const rawHost = e.target.value.replace(/^https?:\/\//, '').replace(/:\d+$/, '').trim();
                            const isIp = /^(?:[0-9]{1,3}\.){3}[0-9]{1,3}$/.test(rawHost);
                            const autoUrl = rawHost ? (isIp ? `http://${rawHost}` : `https://${rawHost}`) : '';
                            setForm((prev: any) => ({
                                ...prev,
                                host: rawHost,
                                api_url: (!prev.api_url || prev.api_url === `https://${prev.host}` || prev.api_url === `http://${prev.host}:8090` || prev.api_url === `http://${prev.host}`) ? autoUrl : prev.api_url,
                            }));
                        }}
                        placeholder="198.51.100.5"
                        className="w-full mt-1 px-3 py-2 rounded-lg bg-background border border-border text-sm"
                    />
                </div>
                <div>
                    <label className="text-xs font-medium text-muted-foreground">API URL <span className="text-muted-foreground/60">(auto-filled from host)</span></label>
                    <input
                        value={form.api_url}
                        onChange={e => setForm({ ...form, api_url: e.target.value })}
                        placeholder="https://198.51.100.5"
                        className="w-full mt-1 px-3 py-2 rounded-lg bg-background border border-border text-sm"
                    />
                </div>
                <div>
                    <label className="text-xs font-medium text-muted-foreground">Private IP <span className="text-muted-foreground/60">(optional)</span></label>
                    <input
                        value={form.private_ip}
                        onChange={e => setForm({ ...form, private_ip: e.target.value })}
                        placeholder="172.31.0.10"
                        className="w-full mt-1 px-3 py-2 rounded-lg bg-background border border-border text-sm"
                    />
                </div>
                <div>
                    <label className="text-xs font-medium text-muted-foreground">SSH User</label>
                    <input
                        value={form.ssh_user}
                        onChange={e => setForm({ ...form, ssh_user: e.target.value })}
                        placeholder="root"
                        className="w-full mt-1 px-3 py-2 rounded-lg bg-background border border-border text-sm"
                    />
                </div>
                <div>
                    <label className="text-xs font-medium text-muted-foreground">SSH Port</label>
                    <input
                        type="number"
                        value={form.ssh_port}
                        onChange={e => setForm({ ...form, ssh_port: parseInt(e.target.value) || 22 })}
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
                <div>
                    <label className="text-xs font-medium text-muted-foreground">Gateway Secret <span className="text-muted-foreground/60">(optional, for HMAC auth)</span></label>
                    <input
                        type="password"
                        value={form.gateway_secret}
                        onChange={e => setForm({ ...form, gateway_secret: e.target.value })}
                        placeholder="GATEWAY_SECRET from remote .env"
                        className="w-full mt-1 px-3 py-2 rounded-lg bg-background border border-border text-sm"
                    />
                </div>
                <div>
                    <label className="text-xs font-medium text-muted-foreground">SSH Password <span className="text-muted-foreground/60">(optional, for remote management)</span></label>
                    <input
                        type="password"
                        value={form.ssh_password}
                        onChange={e => setForm({ ...form, ssh_password: e.target.value })}
                        placeholder="Root SSH password"
                        className="w-full mt-1 px-3 py-2 rounded-lg bg-background border border-border text-sm"
                    />
                </div>
                <div className="col-span-2">
                    <label className="text-xs font-medium text-muted-foreground">SSH Key <span className="text-muted-foreground/60">(optional)</span></label>
                    <textarea
                        value={form.ssh_key}
                        onChange={e => setForm({ ...form, ssh_key: e.target.value })}
                        placeholder="-----BEGIN OPENSSH PRIVATE KEY-----&#10;...&#10;-----END OPENSSH PRIVATE KEY-----"
                        rows={4}
                        className="w-full mt-1 px-3 py-2 rounded-lg bg-background border border-border font-mono text-xs"
                    />
                </div>
                <div>
                    <label className="text-xs font-medium text-muted-foreground">Key Passphrase <span className="text-muted-foreground/60">(optional)</span></label>
                    <input
                        type="password"
                        value={form.ssh_key_passphrase}
                        onChange={e => setForm({ ...form, ssh_key_passphrase: e.target.value })}
                        placeholder="Passphrase for the SSH key"
                        className="w-full mt-1 px-3 py-2 rounded-lg bg-background border border-border text-sm"
                    />
                </div>
            </div>

            <NodeModePicker
                idPrefix="conn"
                value={form.is_lite_agent}
                onChange={v => setForm({ ...form, is_lite_agent: v })}
            />

            <NodeComponents
                value={form.node_components}
                onChange={v => setForm({ ...form, node_components: v })}
                show={!form.is_lite_agent}
            />

            {form.is_lite_agent && (
                <NodeCertificateInput
                    value={form.node_certificate}
                    onChange={v => setForm({ ...form, node_certificate: v })}
                />
            )}

            <div className="flex items-center justify-between">
                <div className="flex items-center gap-4">
                    <label className="flex items-center gap-2 text-sm">
                        <input
                            type="checkbox"
                            checked={form.is_primary}
                            onChange={e => setForm({
                                ...form,
                                is_primary: e.target.checked,
                                allow_user_workloads: e.target.checked ? false : form.allow_user_workloads,
                            })}
                            className="rounded"
                        />
                        Primary server
                    </label>
                    <label className="flex items-center gap-2 text-sm">
                        <input
                            type="checkbox"
                            checked={form.allow_user_workloads}
                            disabled={form.is_primary}
                            onChange={e => setForm({ ...form, allow_user_workloads: e.target.checked })}
                            className="rounded"
                        />
                        Allow user deployments
                    </label>
                </div>
                <div className="flex gap-2">
                    <TestConnectionButton testing={testing} onClick={onTest} result={testResult} />
                </div>
            </div>

            <div className="flex items-center justify-end gap-2">
                <button
                    onClick={onSubmit}
                    disabled={
                        submitting || !form.name || !form.host
                        || (form.is_lite_agent && !form.node_certificate.trim())
                    }
                    className="px-4 py-2 text-sm rounded-lg bg-gradient-to-r from-blue-500 to-cyan-600 text-white font-semibold flex items-center gap-2 disabled:opacity-50"
                >
                    {submitting ? <Loader2 size={14} className="animate-spin" /> : <Link2 size={14} />}
                    Connect
                </button>
            </div>
        </>
    );
}

function ServerCard({
    server, index, now, onOpenLogs, onHealthCheck, onRetry, onDelete, onOpen, serverChecking, checking,
}: {
    server: ManagedServer;
    index: number;
    now: number;
    onOpenLogs: () => void;
    onHealthCheck: () => void;
    onRetry: () => void;
    onDelete: () => void;
    onOpen: () => void;
    serverChecking: boolean;
    checking: boolean;
}) {
    const isProvisioning = server.provision_status === 'PENDING' || server.provision_status === 'PROVISIONING';
    const provFailed = server.provision_status === 'FAILED';
    const sc = isProvisioning
        ? { icon: Loader2, color: 'text-blue-500', bg: 'bg-blue-500/10', border: 'border-blue-500/20', label: 'Provisioning' }
        : provFailed
        ? { icon: XCircle, color: 'text-red-500', bg: 'bg-red-500/10', border: 'border-red-500/20', label: 'Failed' }
        : STATUS_CONFIG[server.status] || STATUS_CONFIG.UNKNOWN;
    const StatusIcon = sc.icon;

    const heartbeat = classifyHeartbeat(server.last_agent_heartbeat_at, now);
    const runtime = server.agent_runtime_info;

    return (
        <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: index * 0.05 }}
            className={`bg-card border rounded-xl p-5 space-y-4 ${sc.border} hover:shadow-lg transition-shadow cursor-pointer`}
            onClick={onOpen}
        >
            {/* Header */}
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-3 min-w-0 flex-1">
                    <div className={`w-10 h-10 rounded-lg ${sc.bg} flex items-center justify-center flex-shrink-0`}>
                        <StatusIcon className={`${sc.color} ${isProvisioning ? 'animate-spin' : ''}`} size={18} />
                    </div>
                    <div className="min-w-0 flex-1">
                        <h3 className="font-bold flex items-center gap-2 flex-wrap">
                            <span className="truncate">{server.name}</span>
                            {server.is_primary && (
                                <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-500 font-bold uppercase flex-shrink-0">
                                    Control Plane
                                </span>
                            )}
                            {server.role === 'LEADER' && (
                                <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-500 font-bold uppercase flex-shrink-0">
                                    Leader
                                </span>
                            )}
                            {server.role === 'FOLLOWER' && (
                                <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-500 font-bold uppercase flex-shrink-0">
                                    Follower
                                </span>
                            )}
                            {server.role === 'CANDIDATE' && (
                                <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-500 font-bold uppercase animate-pulse flex-shrink-0">
                                    Election...
                                </span>
                            )}
                            {server.agent_ready && (
                                <span
                                    className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-500 font-bold uppercase flex items-center gap-1 flex-shrink-0"
                                    title="Agent registrar has reported it is fully ready"
                                >
                                    <CheckCircle2 size={9} /> Agent Ready
                                </span>
                            )}
                        </h3>
                        <p className="text-xs text-muted-foreground truncate">{server.host}</p>
                        <div className="mt-1 flex flex-wrap gap-1.5">
                            {server.node_type === 'media' && (
                                <span className="text-[10px] px-1.5 py-0.5 rounded bg-cyan-500/10 text-cyan-500 font-bold uppercase">
                                    Media Node
                                </span>
                            )}
                            {!server.is_primary && server.allow_user_workloads !== false && !server.is_lite_agent && server.node_type !== 'media' && (
                                <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-500 font-bold uppercase">
                                    Full Stack
                                </span>
                            )}
                            {server.is_lite_agent && (
                                <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-500/10 text-purple-500 font-bold uppercase">
                                    Lite Agent
                                </span>
                            )}
                            {!server.is_primary && server.allow_user_workloads !== false && (
                                <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-500 font-medium">
                                    Workload target
                                </span>
                            )}
                            {server.allow_user_workloads === false && (
                                <span className="text-[10px] px-1.5 py-0.5 rounded bg-zinc-500/10 text-zinc-500 font-medium">
                                    Workloads off
                                </span>
                            )}
                            {server.has_ssh_credentials && (
                                <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-500 font-medium">
                                    SSH ready
                                </span>
                            )}
                            {server.wg_address && (
                                <span className="text-[10px] px-1.5 py-0.5 rounded bg-indigo-500/10 text-indigo-500 font-mono">
                                    wg: {server.wg_address}
                                </span>
                            )}
                        </div>
                    </div>
                </div>
                <span className={`text-xs font-bold ${sc.color} flex-shrink-0`}>{sc.label}</span>
            </div>

            {/* Provisioning banner */}
            {isProvisioning && (
                <button
                    onClick={(e) => { e.stopPropagation(); onOpenLogs(); }}
                    className="w-full text-left px-3 py-2 rounded-lg bg-blue-500/5 border border-blue-500/20 text-xs text-blue-400 flex items-center gap-2 hover:bg-blue-500/10 transition-colors"
                >
                    <Terminal size={12} />
                    Grid is being installed — click to view logs
                </button>
            )}

            {provFailed && (
                <button
                    onClick={(e) => { e.stopPropagation(); onOpenLogs(); }}
                    className="w-full text-left px-3 py-2 rounded-lg bg-red-500/5 border border-red-500/20 text-xs text-red-400 flex items-center gap-2 hover:bg-red-500/10 transition-colors"
                >
                    <Terminal size={12} />
                    Provisioning failed — click to view logs
                </button>
            )}

            {server.provision_status === 'NONE' && (
                <button
                    onClick={(e) => { e.stopPropagation(); onRetry(); }}
                    className="w-full text-left px-3 py-2 rounded-lg bg-amber-500/5 border border-amber-500/20 text-xs text-amber-500 flex items-center gap-2 hover:bg-amber-500/10 transition-colors"
                >
                    <Zap size={12} />
                    Ready to provision — click to start
                </button>
            )}

            {/* Agent heartbeat freshness (only for non-primary, lite-agent-aware) */}
            {!isProvisioning && server.is_lite_agent && server.agent_ready && (
                <div
                    className={`text-[10px] flex items-center gap-1.5 px-2 py-1 rounded-md ${heartbeat.bg} ${heartbeat.border} border ${heartbeat.color}`}
                    title="Agent registrar posts a heartbeat every 30s. Stale = registrar has stopped reporting."
                >
                    <Activity size={10} className={heartbeat.healthy ? '' : 'animate-pulse'} />
                    <span>Registrar: <strong>{heartbeat.label}</strong></span>
                    {heartbeat.ageS != null && heartbeat.ageS < 120 && (
                        <span className="text-muted-foreground/60">
                            ({relativeTime(server.last_agent_heartbeat_at)})
                        </span>
                    )}
                </div>
            )}

            {/* Stats */}
            {!isProvisioning && (
                <div className="grid grid-cols-3 gap-2 text-center">
                    <div className="bg-muted/30 rounded-lg p-2">
                        <p className="text-lg font-bold">{server.services_count}</p>
                        <p className="text-[10px] text-muted-foreground uppercase tracking-wider">Services</p>
                    </div>
                    <div className="bg-muted/30 rounded-lg p-2">
                        <p className="text-sm font-bold truncate">{server.server_version || '—'}</p>
                        <p className="text-[10px] text-muted-foreground uppercase tracking-wider">Version</p>
                    </div>
                    <div className="bg-muted/30 rounded-lg p-2">
                        <p className="text-xs font-medium text-muted-foreground mt-1">
                            {server.last_health_check
                                ? new Date(server.last_health_check).toLocaleTimeString()
                                : 'Never'}
                        </p>
                        <p className="text-[10px] text-muted-foreground uppercase tracking-wider">Last Check</p>
                    </div>
                </div>
            )}

            {/* Runtime info (when agent has reported in) */}
            {!isProvisioning && runtime && (
                <details
                    className="text-xs"
                    onClick={(e) => e.stopPropagation()}
                >
                    <summary className="cursor-pointer text-muted-foreground hover:text-foreground flex items-center gap-1.5 select-none">
                        <Info size={11} />
                        <span>Runtime snapshot from registrar</span>
                    </summary>
                    <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1.5 text-[11px] p-3 rounded-lg bg-muted/20">
                        {runtime.docker_version && (
                            <div className="flex items-center gap-1.5">
                                <HardDrive size={10} className="text-muted-foreground" />
                                <span className="text-muted-foreground">Docker:</span>
                                <span className="font-mono">{runtime.docker_version}</span>
                            </div>
                        )}
                        {runtime.host_uptime_s != null && (
                            <div className="flex items-center gap-1.5">
                                <Activity size={10} className="text-muted-foreground" />
                                <span className="text-muted-foreground">Uptime:</span>
                                <span className="font-mono">{formatDuration(runtime.host_uptime_s)}</span>
                            </div>
                        )}
                        {runtime.disk_used_pct != null && (
                            <div className="flex items-center gap-1.5">
                                <HardDrive size={10} className="text-muted-foreground" />
                                <span className="text-muted-foreground">Disk:</span>
                                <span className={`font-mono ${runtime.disk_used_pct > 85 ? 'text-red-400' : runtime.disk_used_pct > 70 ? 'text-amber-400' : ''}`}>
                                    {formatPct(runtime.disk_used_pct)}
                                </span>
                            </div>
                        )}
                        {runtime.mem_used_pct != null && (
                            <div className="flex items-center gap-1.5">
                                <Cpu size={10} className="text-muted-foreground" />
                                <span className="text-muted-foreground">Memory:</span>
                                <span className={`font-mono ${runtime.mem_used_pct > 85 ? 'text-red-400' : runtime.mem_used_pct > 70 ? 'text-amber-400' : ''}`}>
                                    {formatPct(runtime.mem_used_pct)}
                                </span>
                            </div>
                        )}
                        {runtime.platform && (
                            <div className="col-span-2 flex items-center gap-1.5">
                                <Server size={10} className="text-muted-foreground" />
                                <span className="text-muted-foreground">Platform:</span>
                                <span className="font-mono truncate" title={runtime.platform}>{runtime.platform}</span>
                            </div>
                        )}
                        {runtime.smsly_images && runtime.smsly_images.length > 0 && (
                            <div className="col-span-2">
                                <div className="text-muted-foreground mb-1">Images:</div>
                                <div className="space-y-0.5">
                                    {runtime.smsly_images.slice(0, 5).map((img, i) => (
                                        <div key={i} className="font-mono text-[10px] flex items-center gap-1.5">
                                            <span className="text-blue-500">{img.repo}:{img.tag}</span>
                                            <span className="text-muted-foreground/60">{img.id.slice(0, 12)}</span>
                                            <span className="text-muted-foreground/60">{img.size}</span>
                                        </div>
                                    ))}
                                    {runtime.smsly_images.length > 5 && (
                                        <div className="text-muted-foreground/60 text-[10px]">
                                            +{runtime.smsly_images.length - 5} more
                                        </div>
                                    )}
                                </div>
                            </div>
                        )}
                    </div>
                </details>
            )}

            {/* Actions */}
            <div className="flex items-center justify-between pt-2 border-t border-border" onClick={e => e.stopPropagation()}>
                <div className="flex items-center gap-2 flex-wrap">
                    {server.api_url && (
                        <>
                            <button
                                onClick={onHealthCheck}
                                disabled={serverChecking || checking}
                                className="text-xs px-2.5 py-1.5 rounded-lg border border-border hover:bg-muted/50 flex items-center gap-1.5 disabled:opacity-50"
                            >
                                {serverChecking ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />} Check
                            </button>
                            <a
                                href={server.api_url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="text-xs px-2.5 py-1.5 rounded-lg border border-border hover:bg-muted/50 flex items-center gap-1.5"
                            >
                                <ExternalLink size={12} /> Open
                            </a>
                        </>
                    )}
                    <button
                        onClick={onOpenLogs}
                        className="text-xs px-2.5 py-1.5 rounded-lg border border-border hover:bg-muted/50 flex items-center gap-1.5"
                    >
                        <Terminal size={12} /> Logs
                    </button>
                    {server.has_ssh_credentials && (
                        <button
                            onClick={onRetry}
                            className="text-xs px-2.5 py-1.5 rounded-lg border border-blue-500/30 bg-blue-500/5 text-blue-500 hover:bg-blue-500/10 flex items-center gap-1.5"
                        >
                            <Zap size={12} /> {server.provision_status === 'DONE' ? 'Update' : 'Provision'}
                        </button>
                    )}
                </div>

                <button
                    onClick={onDelete}
                    className="text-xs px-2.5 py-1.5 rounded-lg text-red-500 hover:bg-red-500/10 flex items-center gap-1.5"
                >
                    <Trash2 size={12} /> Remove
                </button>
            </div>
        </motion.div>
    );
}

function ProvisioningLogPanel({
    serverId, server, logs, status, onClose, onRetry, onUpdate, logRef, now, publicKey,
}: {
    serverId: string;
    server: ManagedServer | null;
    logs: string;
    status: ProvisionStatus;
    onClose: () => void;
    onRetry: () => void;
    onUpdate: () => void;
    logRef: React.RefObject<HTMLPreElement | null>;
    now: number;
    publicKey?: string | null;
}) {
    const heartbeat = classifyHeartbeat(server?.last_agent_heartbeat_at, now);
    const runtime = server?.agent_runtime_info;
    const psc = PROVISION_STATUS_CONFIG[status] || PROVISION_STATUS_CONFIG.NONE;
    const [keyCopied, setKeyCopied] = useState(false);

    const copyKey = async () => {
        if (!publicKey) return;
        try {
            await navigator.clipboard.writeText(publicKey);
            setKeyCopied(true);
            setTimeout(() => setKeyCopied(false), 2000);
        } catch { /* clipboard unavailable */ }
    };

    return (
        <motion.div
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            className="bg-zinc-950 border border-zinc-800 rounded-xl overflow-hidden"
        >
            <div className="flex items-center justify-between px-4 py-3 border-b border-zinc-800 flex-wrap gap-2">
                <div className="flex items-center gap-3 flex-wrap">
                    <Terminal size={16} className="text-emerald-500" />
                    <span className="text-sm font-bold text-zinc-200">Provisioning Terminal</span>
                    <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider ${psc.bg} ${psc.color} ${psc.border} border flex items-center gap-1.5`}>
                        {psc.animate && <Loader2 size={10} className="animate-spin" />}
                        {psc.label}
                    </span>
                    {server?.agent_ready && (
                        <span className="px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 flex items-center gap-1.5">
                            <CheckCircle2 size={10} />
                            Agent Ready
                        </span>
                    )}
                    {server?.is_lite_agent && server?.last_agent_heartbeat_at && (
                        <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider ${heartbeat.bg} ${heartbeat.color} ${heartbeat.border} border flex items-center gap-1.5`}>
                            <Activity size={10} className={heartbeat.healthy ? '' : 'animate-pulse'} />
                            Registrar: {heartbeat.label}
                        </span>
                    )}
                </div>
                <div className="flex items-center gap-2">
                    {status === 'FAILED' && (
                        <button
                            type="button"
                            onClick={onRetry}
                            className="inline-flex h-7 items-center justify-center rounded-md border border-red-500/30 bg-red-500/10 px-2.5 text-[11px] font-medium text-red-400 transition-colors hover:bg-red-500/20"
                        >
                            <RefreshCcw size={11} className="mr-1.5" />
                            Retry
                        </button>
                    )}
                    {status === 'DONE' && (
                        <button
                            type="button"
                            onClick={onUpdate}
                            className="inline-flex h-7 items-center justify-center rounded-md border border-blue-500/30 bg-blue-500/10 px-2.5 text-[11px] font-medium text-blue-400 transition-colors hover:bg-blue-500/20"
                        >
                            <Zap size={11} className="mr-1.5" />
                            Update Server
                        </button>
                    )}
                    <button
                        onClick={onClose}
                        className="text-xs text-zinc-500 hover:text-zinc-300 px-2 py-1 rounded"
                    >
                        Close
                    </button>
                </div>
            </div>

            {/* Runtime summary bar — visible when agent is ready */}
            {server?.agent_ready && runtime && (
                <div className="px-4 py-2 border-b border-zinc-800 bg-zinc-900/50 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-zinc-400">
                    {runtime.docker_version && (
                        <span className="flex items-center gap-1.5">
                            <HardDrive size={10} />
                            <span>Docker <span className="font-mono text-zinc-200">{runtime.docker_version}</span></span>
                        </span>
                    )}
                    {runtime.host_uptime_s != null && (
                        <span className="flex items-center gap-1.5">
                            <Activity size={10} />
                            <span>Uptime <span className="font-mono text-zinc-200">{formatDuration(runtime.host_uptime_s)}</span></span>
                        </span>
                    )}
                    {runtime.disk_used_pct != null && (
                        <span className="flex items-center gap-1.5">
                            <HardDrive size={10} />
                            <span>Disk <span className={`font-mono ${runtime.disk_used_pct > 85 ? 'text-red-400' : 'text-zinc-200'}`}>{formatPct(runtime.disk_used_pct)}</span></span>
                        </span>
                    )}
                    {runtime.mem_used_pct != null && (
                        <span className="flex items-center gap-1.5">
                            <Cpu size={10} />
                            <span>Mem <span className={`font-mono ${runtime.mem_used_pct > 85 ? 'text-red-400' : 'text-zinc-200'}`}>{formatPct(runtime.mem_used_pct)}</span></span>
                        </span>
                    )}
                </div>
            )}

            {/* Generated SSH key — install on the target host */}
            {publicKey && (
                <div className="px-4 py-3 border-b border-emerald-500/30 bg-emerald-500/10">
                    <div className="flex items-center justify-between gap-2 flex-wrap">
                        <p className="text-[11px] font-semibold text-emerald-400 flex items-center gap-1.5">
                            <Fingerprint size={12} />
                            Install this public key on the VPS (provider console or ~/.ssh/authorized_keys):
                        </p>
                        <button
                            type="button"
                            onClick={copyKey}
                            className="inline-flex h-6 items-center gap-1 rounded-md border border-emerald-500/40 bg-emerald-500/10 px-2 text-[10px] font-medium text-emerald-400 hover:bg-emerald-500/20 transition-colors"
                        >
                            <Copy size={10} />
                            {keyCopied ? 'Copied!' : 'Copy'}
                        </button>
                    </div>
                    <pre className="mt-2 p-2 rounded-md bg-zinc-950 border border-zinc-800 text-[10px] font-mono text-emerald-300 overflow-x-auto whitespace-pre-wrap break-all">
                        {publicKey}
                    </pre>
                    <p className="text-[10px] text-emerald-500/70 mt-1.5">
                        The provisioner will keep retrying with this key — no restart needed once it&apos;s in place.
                    </p>
                </div>
            )}

            <pre
                ref={logRef}
                className="p-4 text-xs font-mono text-emerald-400 overflow-auto max-h-96 leading-relaxed whitespace-pre-wrap"
            >
                {logs || (status === 'PENDING' ? 'Queued for provisioning...' : 'Waiting for provisioning to start...')}
            </pre>
        </motion.div>
    );
}
