'use client';

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { servicesApi, addonsApi, serversApi, Service, Addon, AddonHaStatus, ManagedServer } from '@/lib/api';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import {
    ShieldCheck, Save, Loader2, RefreshCw,
    CheckCircle2, XCircle, ArrowUpCircle, PowerOff, Database, Network,
} from 'lucide-react';
import { toast } from '@/components/ui/use-toast';

interface HaTabProps {
    serviceId: string;
    service?: Service;
}

type HaMode = 'none' | 'local' | 'remote';

const HA_MODES: Array<{ value: HaMode; label: string; desc: string }> = [
    { value: 'none', label: 'None', desc: 'Service runs without automated failover.' },
    { value: 'local', label: 'Local (same-node)', desc: 'Multiple replicas on this node. Survives container crashes.' },
    { value: 'remote', label: 'Remote (cross-node)', desc: 'Replica on a different node. Survives node failure.' },
];

// Only these addon types support HA on the backend (enable-ha validates this).
const HA_CAPABLE_TYPES = new Set(['REDIS', 'POSTGRES']);

export function HaTab({ serviceId, service: initialService }: HaTabProps) {
    const [loading, setLoading] = useState(!initialService);
    const [saving, setSaving] = useState(false);
    const [refreshing, setRefreshing] = useState(false);
    const [haMode, setHaMode] = useState<HaMode>('none');
    const [addons, setAddons] = useState<Addon[]>([]);
    const [haStatus, setHaStatus] = useState<Record<string, AddonHaStatus>>({});
    const [busy, setBusy] = useState<Record<string, boolean>>({});
    // External HA connection: eligible remote mesh nodes for addon remote
    // standbys. Mirrors the backend validation (full-stack node, not a lite
    // agent, with a WireGuard address).
    const [nodes, setNodes] = useState<ManagedServer[]>([]);
    const [externalNodeId, setExternalNodeId] = useState<string>('');
    const [externalEndpoint, setExternalEndpoint] = useState('');
    const [externalUsername, setExternalUsername] = useState('');
    const [externalPassword, setExternalPassword] = useState('');
    const [externalDatabase, setExternalDatabase] = useState('');
    // Same dirty/seeded guard as HealthTab/ResourcesTab: the parent polls
    // the service every 3s with a fresh object reference — while the user
    // is editing, polls must not reseed the form (AGENTS.md #21).
    const dirtyRef = useRef(false);
    const seededRef = useRef<string | null>(null);

    const applyService = (s: any, force = false) => {
        if (!force && dirtyRef.current) return;
        const mode = (s.ha_mode as HaMode) || 'none';
        setHaMode(['none', 'local', 'remote'].includes(mode) ? mode : 'none');
        setExternalEndpoint(s.external_ha_endpoint || '');
        setExternalUsername(s.external_ha_username || '');
        setExternalDatabase(s.external_ha_database || '');
        setLoading(false);
    };

    useEffect(() => {
        const key = initialService?.id || serviceId;
        if (initialService && seededRef.current !== key) {
            seededRef.current = key;
            applyService(initialService, true);
            return;
        }
        if (initialService) {
            applyService(initialService);
            return;
        }
        (async () => {
            try {
                const s = await servicesApi.get(serviceId);
                applyService(s, true);
            } catch (err) { console.error(err); }
            finally { setLoading(false); }
        })();
    // Deliberately NOT depending on the full initialService object.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [serviceId, initialService?.id]);

    const fetchAddons = useCallback(async () => {
        try {
            const list = await addonsApi.list();
            const mine = (list || []).filter((a: Addon) => a.service === serviceId);
            setAddons(mine);
            // Pull live HA status for HA-capable addons.
            const entries = await Promise.all(
                mine
                    .filter((a) => HA_CAPABLE_TYPES.has((a.addon_type || '').toUpperCase()))
                    .map(async (a) => {
                        try {
                            const st = await addonsApi.haStatus(a.id);
                            return [a.id, st] as const;
                        } catch {
                            return null;
                        }
                    }),
            );
            const next: Record<string, AddonHaStatus> = {};
            for (const e of entries) {
                if (e) next[e[0]] = e[1];
            }
            setHaStatus(next);
        } catch (err) { console.error(err); }
    }, [serviceId]);

    useEffect(() => {
        fetchAddons();
    }, [fetchAddons]);

    const fetchNodes = useCallback(async () => {
        try {
            const list = await serversApi.list();
            const eligible = (list || [])
                .filter((n: ManagedServer) =>
                    !n.is_primary && !n.is_lite_agent && !!(n.wg_address || '').trim(),
                )
                .sort((a, b) =>
                    (a.status === 'ONLINE' ? 0 : 1) - (b.status === 'ONLINE' ? 0 : 1),
                );
            setNodes(eligible);
            setExternalNodeId((prev) => {
                if (prev && eligible.some((n) => n.id === prev)) return prev;
                return eligible[0]?.id || '';
            });
        } catch (err) { console.error(err); }
    }, []);

    useEffect(() => {
        fetchNodes();
    }, [fetchNodes]);

    const markDirty = () => { dirtyRef.current = true; };

    const setAddonBusy = (id: string, v: boolean) =>
        setBusy((prev) => ({ ...prev, [id]: v }));

    const handleSave = async () => {
        setSaving(true);
        try {
            const payload: Record<string, string> = {
                ha_mode: haMode,
                external_ha_endpoint: externalEndpoint.trim(),
                external_ha_username: externalUsername.trim(),
                external_ha_database: externalDatabase.trim(),
            };
            if (externalPassword) payload.external_ha_password = externalPassword;
            await servicesApi.update(serviceId, payload as any);
            dirtyRef.current = false;
            setExternalPassword('');
            toast({
                title: haMode === 'none' ? 'HA Disabled' : `HA: ${haMode === 'local' ? 'Local Replicas' : 'Remote Failover'}`,
                description: HA_MODES.find((m) => m.value === haMode)?.desc,
            });
        } catch (err) {
            toast({ title: 'Failed to save HA mode', variant: 'destructive' });
        } finally { setSaving(false); }
    };

    const handleRefresh = async () => {
        setRefreshing(true);
        try {
            const s = await servicesApi.get(serviceId);
            applyService(s, true);
            dirtyRef.current = false;
            await Promise.all([fetchAddons(), fetchNodes()]);
            toast({ title: 'HA status refreshed' });
        } catch (err) {
            toast({ title: 'Failed to refresh', variant: 'destructive' });
        } finally { setRefreshing(false); }
    };

    const runAddonAction = async (addon: Addon, action: 'enable-local' | 'enable-remote' | 'promote' | 'disable') => {
        const key = `${addon.id}:${action}`;
        if (action === 'enable-remote' && !externalNodeId) {
            toast({ title: 'Select an external HA connection first', variant: 'destructive' });
            return;
        }
        setAddonBusy(key, true);
        try {
            if (action === 'enable-local') await addonsApi.enableHa(addon.id, { placement: 'local' });
            else if (action === 'enable-remote') await addonsApi.enableHa(addon.id, { placement: 'remote', server_id: externalNodeId });
            else if (action === 'promote') await addonsApi.promoteHa(addon.id);
            else await addonsApi.disableHa(addon.id);
            await fetchAddons();
            toast({ title: `Addon ${action.replace('-', ' ')} initiated` });
        } catch (err: any) {
            toast({
                title: 'Addon HA action failed',
                description: err?.response?.data?.error || err?.message || String(err),
                variant: 'destructive',
            });
        } finally { setAddonBusy(key, false); }
    };

    if (loading) {
        return (
            <div className="flex items-center gap-2 text-muted-foreground text-sm p-6">
                <Loader2 className="w-4 h-4 animate-spin" /> Loading HA settings…
            </div>
        );
    }

    return (
        <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4">
            {/* Service HA */}
            <Card className="p-6">
                <div className="flex items-center justify-between mb-1">
                    <h3 className="font-bold text-lg flex items-center gap-2">
                        <ShieldCheck className="w-5 h-5 text-emerald-500" /> Service High Availability
                    </h3>
                    <Button variant="outline" size="sm" onClick={handleRefresh} disabled={refreshing}>
                        {refreshing ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
                        <span className="ml-1">Refresh</span>
                    </Button>
                </div>
                <p className="text-muted-foreground text-sm mb-5">
                    Automated failover for this service, evaluated continuously by the platform HA controller.
                </p>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-5">
                    {HA_MODES.map((m) => (
                        <button
                            key={m.value}
                            type="button"
                            onClick={() => { setHaMode(m.value); markDirty(); }}
                            className={`text-left p-4 rounded-xl border transition-all ${
                                haMode === m.value
                                    ? 'border-emerald-500 bg-emerald-500/5'
                                    : 'border-border bg-background hover:border-muted-foreground/40'
                            }`}
                        >
                            <div className="flex items-center gap-2 font-semibold text-sm mb-1">
                                {haMode === m.value
                                    ? <CheckCircle2 className="w-4 h-4 text-emerald-500" />
                                    : <XCircle className="w-4 h-4 text-muted-foreground" />}
                                {m.label}
                            </div>
                            <div className="text-xs text-muted-foreground">{m.desc}</div>
                        </button>
                    ))}
                </div>
                <Button onClick={handleSave} disabled={saving}>
                    {saving ? <Loader2 className="w-4 h-4 animate-spin mr-1" /> : <Save className="w-4 h-4 mr-1" />}
                    Save HA Mode
                </Button>
            </Card>

            {/* External provider connection. This is persisted securely, but
                it is deliberately not presented as active failover: the
                current service HA controller only auto-fails over to a
                ManagedServer. A provider-specific adapter must be added
                before these credentials can execute a failover. */}
            <Card className="p-6">
                <h3 className="font-bold text-lg flex items-center gap-2 mb-1">
                    <Network className="w-5 h-5 text-indigo-500" /> External HA Provider Connection
                </h3>
                <p className="text-muted-foreground text-sm mb-5">
                    Configure a non-WireGuard HA endpoint for this service. Credentials are encrypted
                    at rest. The current automatic failover engine supports managed platform nodes;
                    this external connection is stored for a provider adapter and is not used for
                    failover until that adapter is enabled.
                </p>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                        <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Endpoint</label>
                        <input
                            type="url"
                            value={externalEndpoint}
                            onChange={(e) => { setExternalEndpoint(e.target.value); markDirty(); }}
                            placeholder="https://ha-provider.example.com"
                            className="mt-1 w-full px-3 py-2 rounded-md border border-border bg-background text-sm"
                        />
                    </div>
                    <div>
                        <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Database</label>
                        <input
                            value={externalDatabase}
                            onChange={(e) => { setExternalDatabase(e.target.value); markDirty(); }}
                            placeholder="Optional database name"
                            className="mt-1 w-full px-3 py-2 rounded-md border border-border bg-background text-sm"
                        />
                    </div>
                    <div>
                        <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Username</label>
                        <input
                            value={externalUsername}
                            onChange={(e) => { setExternalUsername(e.target.value); markDirty(); }}
                            autoComplete="off"
                            className="mt-1 w-full px-3 py-2 rounded-md border border-border bg-background text-sm"
                        />
                    </div>
                    <div>
                        <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Credential</label>
                        <input
                            type="password"
                            value={externalPassword}
                            onChange={(e) => { setExternalPassword(e.target.value); markDirty(); }}
                            placeholder="Leave blank to keep existing credential"
                            autoComplete="new-password"
                            className="mt-1 w-full px-3 py-2 rounded-md border border-border bg-background text-sm"
                        />
                    </div>
                </div>
                <div className="mt-4 flex items-center justify-between gap-3 flex-wrap">
                    <span className={`text-xs px-2 py-1 rounded border ${
                        externalEndpoint
                            ? 'border-amber-500/30 bg-amber-500/10 text-amber-500'
                            : 'border-zinc-500/30 bg-zinc-500/10 text-zinc-400'
                    }`}>
                        {externalEndpoint ? 'Configured — adapter required for failover' : 'Not configured'}
                    </span>
                    <Button onClick={handleSave} disabled={saving} variant="outline">
                        {saving ? <Loader2 className="w-4 h-4 animate-spin mr-1" /> : <Save className="w-4 h-4 mr-1" />}
                        Save External Connection
                    </Button>
                </div>
            </Card>

            {/* External HA connection */}
            <Card className="p-6">
                <h3 className="font-bold text-lg flex items-center gap-2 mb-1">
                    <Network className="w-5 h-5 text-indigo-500" /> External HA Connection
                </h3>
                <p className="text-muted-foreground text-sm mb-5">
                    The remote mesh node used as the external standby target. Addon remote HA streams
                    Postgres WAL to a warm standby on this node over WireGuard (cutover stays manual
                    via Promote). Service-level remote failover picks automatically among online nodes.
                </p>
                {nodes.length === 0 ? (
                    <div className="text-sm text-amber-500">
                        No eligible external nodes found. A remote node must be a full-stack mesh node
                        (not a lite agent) with a WireGuard address. Add one from the Servers page first.
                    </div>
                ) : (
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4 items-start">
                        <div>
                            <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                                External node
                            </label>
                            <select
                                value={externalNodeId}
                                onChange={(e) => setExternalNodeId(e.target.value)}
                                className="mt-1 w-full px-3 py-2 rounded-md border border-border bg-background text-sm font-medium"
                            >
                                {nodes.map((n) => (
                                    <option key={n.id} value={n.id}>
                                        {n.name} ({n.status})
                                    </option>
                                ))}
                            </select>
                        </div>
                        {(() => {
                            const n = nodes.find((x) => x.id === externalNodeId);
                            if (!n) return null;
                            return (
                                <div className="text-sm space-y-1 rounded-xl border border-border bg-background p-4">
                                    <div className="flex justify-between">
                                        <span className="text-muted-foreground">Host</span>
                                        <span className="font-mono text-xs">{n.host || '—'}</span>
                                    </div>
                                    <div className="flex justify-between">
                                        <span className="text-muted-foreground">WireGuard</span>
                                        <span className="font-mono text-xs">{n.wg_address || '—'}</span>
                                    </div>
                                    <div className="flex justify-between items-center">
                                        <span className="text-muted-foreground">Status</span>
                                        <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider ${
                                            n.status === 'ONLINE' ? 'bg-emerald-500/10 text-emerald-500' : 'bg-zinc-500/10 text-zinc-400'
                                        }`}>
                                            {n.status}
                                        </span>
                                    </div>
                                </div>
                            );
                        })()}
                    </div>
                )}
            </Card>

            {/* Addon HA */}
            <Card className="p-6">
                <h3 className="font-bold text-lg flex items-center gap-2 mb-1">
                    <Database className="w-5 h-5 text-cyan-500" /> Addon High Availability
                </h3>
                <p className="text-muted-foreground text-sm mb-5">
                    Replication and failover for this service&apos;s databases and caches. Supported for Redis and Postgres addons.
                </p>
                {addons.length === 0 ? (
                    <div className="text-sm text-muted-foreground">
                        No addons attached to this service. Add one from the Addons tab first.
                    </div>
                ) : (
                    <div className="space-y-3">
                        {addons.map((addon) => {
                            const capable = HA_CAPABLE_TYPES.has((addon.addon_type || '').toUpperCase());
                            const st = haStatus[addon.id];
                            const enabled = st?.ha_enabled ?? addon.ha_enabled ?? false;
                            return (
                                <div key={addon.id} className="flex flex-col md:flex-row md:items-center gap-3 p-4 rounded-xl border border-border bg-background">
                                    <div className="flex-1 min-w-0">
                                        <div className="flex items-center gap-2">
                                            <span className="font-semibold text-sm truncate">{addon.name}</span>
                                            <span className="px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider bg-muted text-muted-foreground">
                                                {addon.addon_type}
                                            </span>
                                            <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider ${
                                                enabled ? 'bg-emerald-500/10 text-emerald-500' : 'bg-zinc-500/10 text-zinc-400'
                                            }`}>
                                                {enabled ? `HA ${st?.mode || ''}`.trim() : 'HA off'}
                                            </span>
                                        </div>
                                        <div className="text-xs text-muted-foreground mt-1">
                                            Status: {st?.ha_status ?? addon.ha_status ?? addon.status}
                                            {st?.master_container ? ` · Master: ${st.master_container}` : ''}
                                            {(st?.topology as any)?.server_host
                                                ? ` · External: ${(st?.topology as any).server_host}`
                                                : ''}
                                        </div>
                                    </div>
                                    {!capable ? (
                                        <span className="text-xs text-muted-foreground">HA not supported for this type</span>
                                    ) : (
                                        <div className="flex flex-wrap gap-2">
                                            {!enabled ? (
                                                <>
                                                    <Button
                                                        size="sm" variant="outline"
                                                        disabled={busy[`${addon.id}:enable-local`]}
                                                        onClick={() => runAddonAction(addon, 'enable-local')}
                                                    >
                                                        Enable local
                                                    </Button>
                                                    <Button
                                                        size="sm" variant="outline"
                                                        disabled={busy[`${addon.id}:enable-remote`] || !externalNodeId}
                                                        title={externalNodeId ? `Standby on ${nodes.find((n) => n.id === externalNodeId)?.name || 'external node'}` : 'Select an external HA connection first'}
                                                        onClick={() => runAddonAction(addon, 'enable-remote')}
                                                    >
                                                        Enable remote
                                                    </Button>
                                                </>
                                            ) : (
                                                <>
                                                    {st?.mode === 'remote' && (
                                                        <Button
                                                            size="sm" variant="outline"
                                                            disabled={busy[`${addon.id}:promote`]}
                                                            onClick={() => runAddonAction(addon, 'promote')}
                                                        >
                                                            <ArrowUpCircle className="w-4 h-4 mr-1" /> Promote
                                                        </Button>
                                                    )}
                                                    <Button
                                                        size="sm" variant="outline"
                                                        disabled={busy[`${addon.id}:disable`]}
                                                        onClick={() => runAddonAction(addon, 'disable')}
                                                    >
                                                        <PowerOff className="w-4 h-4 mr-1" /> Disable
                                                    </Button>
                                                </>
                                            )}
                                        </div>
                                    )}
                                </div>
                            );
                        })}
                    </div>
                )}
            </Card>
        </div>
    );
}
