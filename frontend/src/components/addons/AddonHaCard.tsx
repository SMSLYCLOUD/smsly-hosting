'use client';

import { useCallback, useEffect, useState } from 'react';
import { addonsApi, serversApi, Addon, AddonHaStatus, ManagedServer } from '@/lib/api';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { useToast } from '@/components/ui/use-toast';
import {
    CheckCircle2, AlertCircle, XCircle, Loader2, RefreshCw,
    ShieldCheck, Power, Info, ArrowUpCircle,
} from 'lucide-react';

const SUPPORTED_TYPES = ['REDIS', 'POSTGRES'];

const STATUS_STYLE: Record<string, { color: string; icon: React.ReactNode; label: string }> = {
    HEALTHY:     { color: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30', icon: <CheckCircle2 className="h-3 w-3" />, label: 'Healthy' },
    DEGRADED:    { color: 'bg-amber-500/15 text-amber-400 border-amber-500/30',      icon: <AlertCircle className="h-3 w-3" />,  label: 'Degraded' },
    FAILED_OVER: { color: 'bg-blue-500/15 text-blue-400 border-blue-500/30',         icon: <ShieldCheck className="h-3 w-3" />,  label: 'Failed Over' },
    FAILED:      { color: 'bg-red-500/15 text-red-400 border-red-500/30',            icon: <XCircle className="h-3 w-3" />,      label: 'Failed' },
    ENABLING:    { color: 'bg-yellow-500/15 text-yellow-400 border-yellow-500/30',   icon: <Loader2 className="h-3 w-3 animate-spin" />, label: 'Enabling' },
    DISABLING:   { color: 'bg-zinc-500/15 text-zinc-400 border-zinc-500/30',         icon: <Loader2 className="h-3 w-3 animate-spin" />, label: 'Disabling' },
};

export function AddonHaCard({ addon, onChanged }: { addon: Addon; onChanged?: (a: Addon) => void }) {
    const { toast } = useToast();
    const [status, setStatus] = useState<AddonHaStatus | null>(null);
    const [busy, setBusy] = useState<'enable' | 'disable' | 'refresh' | 'promote' | null>(null);
    const [placement, setPlacement] = useState<'local' | 'remote'>('local');
    const [nodes, setNodes] = useState<ManagedServer[]>([]);
    const [serverId, setServerId] = useState<string>('');

    const supported = SUPPORTED_TYPES.includes(addon.addon_type);
    const enabled = Boolean(addon.ha_enabled);
    const isPostgres = addon.addon_type === 'POSTGRES';

    useEffect(() => {
        if (!supported || enabled || !isPostgres) return;
        serversApi.list()
            .then((all) => {
                const eligible = (all || []).filter(
                    (s) => !s.is_lite_agent && s.wg_address);
                setNodes(eligible);
            })
            .catch(() => setNodes([]));
    }, [supported, enabled, isPostgres]);

    const refresh = useCallback(async (silent = false) => {
        if (!silent) setBusy('refresh');
        try {
            const s = await addonsApi.haStatus(addon.id);
            setStatus(s);
        } catch (e: unknown) {
            if (!silent) {
                toast({ title: 'Failed to load HA status', description: e instanceof Error ? e.message : 'Unknown error', variant: 'destructive' });
            }
        } finally {
            if (!silent) setBusy(null);
        }
    }, [addon.id, toast]);

    useEffect(() => {
        if (!supported || !enabled) return;
        refresh(true);
        const t = setInterval(() => refresh(true), 30000);
        return () => clearInterval(t);
    }, [supported, enabled, refresh]);

    if (!supported) return null;

    const enable = async () => {
        if (isPostgres
            && !confirm('Enable HA? The primary container will be recreated with replication settings (brief downtime ~30s), then a streaming standby is seeded.')) return;
        setBusy('enable');
        try {
            const res = await addonsApi.enableHa(addon.id, placement === 'remote' && serverId
                ? { placement, server_id: serverId }
                : { placement: 'local' });
            toast({
                title: 'HA enabled',
                description: res.warning || `Mode: ${res.mode}. Failover is automatic.`,
                variant: res.warning ? 'destructive' : 'default',
            });
            const updated = await addonsApi.get(addon.id);
            onChanged?.(updated);
            refresh(true);
        } catch (e: any) {
            const detail = e?.response?.data?.error || e?.message || 'Unknown error';
            toast({ title: 'Failed to enable HA', description: detail, variant: 'destructive' });
        } finally {
            setBusy(null);
        }
    };

    const promote = async () => {
        if (!confirm('Promote the standby to master? For remote placement this rewrites the connection URL and requires a service redeploy.')) return;
        setBusy('promote');
        try {
            const res = await addonsApi.promoteHa(addon.id);
            toast({ title: 'Standby promoted', description: res?.note || undefined, variant: res?.note ? 'destructive' : 'default' });
            const updated = await addonsApi.get(addon.id);
            onChanged?.(updated);
            refresh(true);
        } catch (e: any) {
            const detail = e?.response?.data?.error || e?.message || 'Unknown error';
            toast({ title: 'Promotion failed', description: detail, variant: 'destructive' });
        } finally {
            setBusy(null);
        }
    };

    const disable = async () => {
        if (!confirm('Disable HA? The standby and failover components will be removed and the alias restored to the primary.')) return;
        setBusy('disable');
        try {
            await addonsApi.disableHa(addon.id);
            toast({ title: 'HA disabled' });
            const updated = await addonsApi.get(addon.id);
            onChanged?.(updated);
            setStatus(null);
        } catch (e: any) {
            const detail = e?.response?.data?.error || e?.message || 'Unknown error';
            toast({ title: 'Failed to disable HA', description: detail, variant: 'destructive' });
        } finally {
            setBusy(null);
        }
    };

    const st = status?.ha_status || addon.ha_status || '';
    const style = STATUS_STYLE[st];

    return (
        <Card>
            <CardHeader>
                <div className="flex items-center justify-between flex-wrap gap-2">
                    <div>
                        <CardTitle className="text-lg">High Availability</CardTitle>
                        <CardDescription>
                            {enabled
                                ? 'Automatic failover is active. Your connection URL never changes.'
                                : 'Add an automatic-failover replica for this addon.'}
                        </CardDescription>
                    </div>
                    {style && (
                        <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs ${style.color}`}>
                            {style.icon}{style.label}
                        </span>
                    )}
                </div>
            </CardHeader>
            <CardContent className="space-y-4">
                {!enabled ? (
                    <div className="space-y-3">
                        <p className="text-sm text-muted-foreground">
                            Not configured. Enabling provisions a replica
                            {addon.addon_type === 'REDIS' ? ' plus Sentinel quorum and a stable endpoint proxy.' : ' with streaming replication managed by the platform watchdog.'}
                        </p>
                        {isPostgres && (
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                                <div className="space-y-1">
                                    <label htmlFor="ha-placement" className="text-xs font-medium">Replica placement</label>
                                    <select
                                        id="ha-placement"
                                        className="w-full rounded border border-input bg-background px-3 py-1.5 text-sm"
                                        value={placement}
                                        onChange={(e) => setPlacement(e.target.value as 'local' | 'remote')}
                                    >
                                        <option value="local">Same node (auto-failover)</option>
                                        <option value="remote" disabled={nodes.length === 0}>
                                            Remote node (warm DR, manual cutover)
                                        </option>
                                    </select>
                                </div>
                                {placement === 'remote' && (
                                    <div className="space-y-1">
                                        <label htmlFor="ha-node" className="text-xs font-medium">Target node</label>
                                        <select
                                            id="ha-node"
                                            className="w-full rounded border border-input bg-background px-3 py-1.5 text-sm"
                                            value={serverId}
                                            onChange={(e) => setServerId(e.target.value)}
                                        >
                                            <option value="">Select a mesh node…</option>
                                            {nodes.map((n) => (
                                                <option key={n.id} value={n.id}>
                                                    {n.name || n.host} ({n.wg_address})
                                                </option>
                                            ))}
                                        </select>
                                    </div>
                                )}
                            </div>
                        )}
                    </div>
                ) : (
                    <div className="space-y-2 text-sm">
                        <div className="flex gap-x-6 gap-y-1 flex-wrap">
                            <span>Mode: <code className="bg-muted px-1 rounded">{status?.mode || addon.ha_topology?.mode || '-'}</code></span>
                            {status?.master_container && (
                                <span>Master: <code className="bg-muted px-1 rounded break-all">{status.master_container}</code></span>
                            )}
                        </div>
                        <div className="text-xs text-muted-foreground font-mono break-all">
                            Replica: {addon.replica_container_name || '-'}
                        </div>
                    </div>
                )}
                <div className="flex items-center gap-2 flex-wrap">
                    {enabled ? (
                        <>
                            <Button size="sm" variant="outline" onClick={() => refresh()} disabled={busy !== null}>
                                {busy === 'refresh' ? <Loader2 className="h-3 w-3 mr-1 animate-spin" /> : <RefreshCw className="h-3 w-3 mr-1" />}
                                Refresh
                            </Button>
                            {isPostgres && (
                                <Button size="sm" variant="outline" onClick={promote} disabled={busy !== null}>
                                    {busy === 'promote' ? <Loader2 className="h-3 w-3 mr-1 animate-spin" /> : <ArrowUpCircle className="h-3 w-3 mr-1" />}
                                    Promote standby
                                </Button>
                            )}
                            <Button size="sm" variant="ghost" onClick={disable} disabled={busy !== null} className="text-red-400 hover:text-red-300">
                                {busy === 'disable' ? <Loader2 className="h-3 w-3 mr-1 animate-spin" /> : <Power className="h-3 w-3 mr-1" />}
                                Disable HA
                            </Button>
                        </>
                    ) : (
                        <Button size="sm" onClick={enable}
                            disabled={busy !== null || addon.status !== 'ACTIVE' || (placement === 'remote' && !serverId)}>
                            {busy === 'enable' ? <Loader2 className="h-3 w-3 mr-1 animate-spin" /> : <ShieldCheck className="h-3 w-3 mr-1" />}
                            Enable HA
                        </Button>
                    )}
                </div>
                {enabled && (
                    <p className="text-xs text-muted-foreground flex items-start gap-1.5">
                        <Info className="h-3 w-3 mt-0.5 shrink-0" />
                        Do not reprovision or rotate credentials while HA is enabled — disable it first.
                    </p>
                )}
            </CardContent>
        </Card>
    );
}
