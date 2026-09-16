'use client';

import React, { useState, useEffect, useRef } from 'react';
import dynamic from 'next/dynamic';
import { Skeleton } from '@/components/ui/skeleton';
const Editor = dynamic(() => import('@monaco-editor/react'), { ssr: false, loading: () => <Skeleton className="h-[400px] w-full" /> });
import { Service, servicesApi } from '@/lib/api';
import { useConfirm } from '@/components/ui/confirm-dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card } from '@/components/ui/card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Save, AlertTriangle, Check, Loader2, Search, FileText, Code2, Trash2 } from 'lucide-react';

export function AdvancedTab({ service }: { service: Service }) {
    const confirm = useConfirm();
    // The effective registry comes from the backend (ScopedRegistry
    // chain → platform config). Never fabricate a hardcoded registry
    // domain here: the old fallback invented 'registry.Trulay.co/<name>'
    // for every service and then PERSISTED that bogus image on save.
    const effectiveRegistry = service.effective_registry || '';
    const defaultImage = effectiveRegistry
        ? `${effectiveRegistry}/${service.name}`
        : (service.docker_image || '');
    const [config, setConfig] = useState<{ docker_image: string; start_command: string; restart_policy: string }>({
        docker_image: service.docker_image || defaultImage,
        start_command: service.start_command || '',
        restart_policy: service.restart_policy || 'unless-stopped',
    });
    const [scanDepth, setScanDepth] = useState<'shallow' | 'standard' | 'deep'>(service.env_scan_depth || 'shallow');
    const [fastDeploy, setFastDeploy] = useState<boolean | null>(service.fast_deploy_enabled ?? null);
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);
    const [error, setError] = useState('');
    const [pruning, setPruning] = useState(false);
    const [pruned, setPruned] = useState('');

    const handleSave = async () => {
        setSaving(true);
        setError('');
        setSaved(false);
        try {
            const payload: Record<string, string> = {
                start_command: config.start_command,
                restart_policy: config.restart_policy,
            };
            // Only send docker_image when the user actually changed it —
            // otherwise the auto-filled default would get persisted as if
            // it were an explicit override.
            if (config.docker_image !== (service.docker_image || defaultImage)) {
                payload.docker_image = config.docker_image;
            }
            await servicesApi.update(service.id, payload as any);
            setSaved(true);
            setTimeout(() => setSaved(false), 3000);
        } catch (err: any) {
            setError(err?.response?.data?.detail || 'Failed to save configuration');
        } finally {
            setSaving(false);
        }
    };

    const handleDelete = async () => {
        if (!await confirm({ 
            title: 'Delete Service?', 
            message: `Are you sure you want to delete "${service.name}"? This action is irreversible.`,
            variant: 'destructive',
            confirmText: 'Delete Forever'
        })) return;

        setSaving(true);
        try {
            await servicesApi.delete(service.id);
            window.location.href = '/dashboard';
        } catch (err: any) {
            setError(err?.response?.data?.detail || 'Failed to delete service');
            setSaving(false);
        }
    };

    const handlePruneDocker = async () => {
        if (!await confirm({
            title: 'Prune service Docker state?',
            message: `Remove failed/cancelled deployment containers, failed addons, and dangling images for "${service.name}"? Active containers and images are preserved.`,
            variant: 'destructive',
            confirmText: 'Prune Service',
        })) return;

        setPruning(true);
        setError('');
        setPruned('');
        try {
            const result = await servicesApi.pruneDocker(service.id);
            setPruned(`Removed ${result.containers_removed} containers and ${result.deployments_deleted} deployment records; reclaimed ${result.space_reclaimed_mb} MB.`);
        } catch (err: any) {
            setError(err?.response?.data?.detail || 'Failed to prune service Docker state');
        } finally {
            setPruning(false);
        }
    };

    return (
        <div className="space-y-8 animate-in fade-in slide-in-from-bottom-4">

            {/* Raw JSON Config */}
            <Card className="p-6 border-border shadow-md">
                <div className="flex justify-between items-center mb-4">
                    <div>
                        <h3 className="font-bold text-lg">Raw Container Configuration</h3>
                        <p className="text-sm text-muted-foreground">Directly override Docker specifications.</p>
                    </div>
                    <Button variant="outline" className="gap-2">
                        <Save size={16} /> Apply
                    </Button>
                </div>
                <div className="h-96 border border-border rounded-lg overflow-hidden">
                    <Editor
                        height="100%"
                        defaultLanguage="json"
                        defaultValue={`{
  "config": {
    "containers": [
      {
        "name": "${service.name}",
        "image": "${config.docker_image}:latest",
        "resources": {
          "limits": {
            "cpu": "${service.cpu_cores}",
            "memory": "${service.memory_mb}Mi"
          }
        },
        "restartPolicy": "${config.restart_policy}",
        "securityContext": {
          "allowPrivilegeEscalation": false
        }
      }
    ]
  }
}`}
                        theme="vs-dark"
                        options={{ minimap: { enabled: false }, fontSize: 13 }}
                    />
                </div>
            </Card>

            {/* Container Settings Form */}
            <Card className="p-6 border-border shadow-md">
                <h3 className="font-bold text-lg mb-6">Container Runtime</h3>

                {error && (
                    <div className="bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-3 mb-6 text-red-500 text-sm">
                        {error}
                    </div>
                )}
                {saved && (
                    <div className="bg-emerald-500/10 border border-emerald-500/30 rounded-lg px-4 py-3 mb-6 text-emerald-500 text-sm flex items-center gap-2">
                        <Check size={16} /> Configuration saved successfully
                    </div>
                )}

                <div className="grid grid-cols-2 gap-6">
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Docker Image</label>
                        <Input
                            value={config.docker_image}
                            onChange={(e) => setConfig(prev => ({ ...prev, docker_image: e.target.value }))}
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Image Tag</label>
                        <Input defaultValue="latest" />
                    </div>
                    <div className="col-span-2 space-y-2">
                        <label className="text-sm font-medium">Command Override</label>
                        <Input
                            placeholder="/bin/sh -c '...'"
                            value={config.start_command}
                            onChange={(e) => setConfig(prev => ({ ...prev, start_command: e.target.value }))}
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Restart Policy</label>
                        <select
                            value={config.restart_policy}
                            onChange={(e) => setConfig(prev => ({ ...prev, restart_policy: e.target.value }))}
                            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                        >
                            <option value="always">Always</option>
                            <option value="unless-stopped">Unless Stopped</option>
                            <option value="on-failure">On Failure</option>
                            <option value="no">Never</option>
                        </select>
                    </div>
                </div>
                <div className="mt-6 flex justify-end">
                    <Button onClick={handleSave} disabled={saving} className="gap-2">
                        {saving ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
                        {saving ? 'Saving...' : saved ? 'Saved!' : 'Save Configuration'}
                    </Button>
                </div>
            </Card>

            {/* Environment Scan Depth */}
            <Card className="p-6 border-border shadow-md">
                <div className="flex justify-between items-center mb-4">
                    <div>
                        <h3 className="font-bold text-lg flex items-center gap-2">
                            <Search size={20} /> Environment Variable Scan Depth
                        </h3>
                        <p className="text-sm text-muted-foreground">Control how deeply the AI scans your repository for environment variables during deployment analysis.</p>
                    </div>
                </div>
                <div className="space-y-4">
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Scan Depth</label>
                        <Select value={scanDepth} onValueChange={(value) => setScanDepth(value as 'shallow' | 'standard' | 'deep')}>
                            <SelectTrigger className="w-[250px]">
                                <SelectValue placeholder="Select scan depth" />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="shallow">
                                    <div className="flex items-center gap-2">
                                        <FileText className="w-4 h-4 text-muted-foreground" />
                                        <span>Shallow</span>
                                    </div>
                                </SelectItem>
                                <SelectItem value="standard">
                                    <div className="flex items-center gap-2">
                                        <Code2 className="w-4 h-4 text-muted-foreground" />
                                        <span>Standard</span>
                                    </div>
                                </SelectItem>
                                <SelectItem value="deep">
                                    <div className="flex items-center gap-2">
                                        <Search className="w-4 h-4 text-muted-foreground" />
                                        <span>Deep</span>
                                    </div>
                                </SelectItem>
                            </SelectContent>
                        </Select>
                        <p className="text-xs text-muted-foreground">
                            {scanDepth === 'shallow' && 'Only scans .env files. Fastest but may miss variables.'}
                            {scanDepth === 'standard' && 'Scans .env files + config files + package manifests. Good balance.'}
                            {scanDepth === 'deep' && 'Full codebase scan including all source files. Most thorough but slowest.'}
                        </p>
                    </div>
                    <Button 
                        onClick={async () => {
                            setSaving(true);
                            setError('');
                            setSaved(false);
                            try {
                                await servicesApi.update(service.id, { env_scan_depth: scanDepth });
                                setSaved(true);
                                setTimeout(() => setSaved(false), 3000);
                            } catch (err: any) {
                                setError(err?.response?.data?.detail || 'Failed to save scan depth');
                            } finally {
                                setSaving(false);
                            }
                        }} 
                        disabled={saving} 
                        className="gap-2"
                    >
                        {saving ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
                        {saving ? 'Saving...' : saved ? 'Saved!' : 'Save Scan Depth'}
                    </Button>
                    {error && (
                        <div className="bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-3 text-red-500 text-sm">
                            {error}
                        </div>
                    )}
                    {saved && (
                        <div className="bg-emerald-500/10 border border-emerald-500/30 rounded-lg px-4 py-3 text-emerald-500 text-sm flex items-center gap-2">
                            <Check size={16} /> Scan depth saved successfully
                        </div>
                    )}
                </div>
            </Card>

            {/* Fast Deploy */}
            <Card className="p-6 border-border shadow-md">
                <div className="flex justify-between items-center mb-4">
                    <div>
                        <h3 className="font-bold text-lg flex items-center gap-2">
                            <span className="h-5 w-5 rounded bg-amber-500/20 flex items-center justify-center text-amber-600 text-xs font-bold">⚡</span> Fast Deploy
                        </h3>
                        <p className="text-sm text-muted-foreground">Skip AI analysis and REVIEW gates for this service. Inherits platform default when not set.</p>
                    </div>
                </div>
                <div className="space-y-4">
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Deploy Mode</label>
                        <Select value={fastDeploy === null ? 'inherit' : fastDeploy ? 'fast' : 'standard'} onValueChange={(value) => setFastDeploy(value === 'inherit' ? null : value === 'fast')}>
                            <SelectTrigger className="w-[320px]">
                                <SelectValue placeholder="Select deploy mode" />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="inherit">Inherit — follow platform default</SelectItem>
                                <SelectItem value="fast">Fast — skip AI & review, go straight to live</SelectItem>
                                <SelectItem value="standard">Standard — full AI analysis + review gates</SelectItem>
                            </SelectContent>
                        </Select>
                        <p className="text-xs text-muted-foreground">
                            {fastDeploy === null && 'Uses Platform → Deploy Pipeline → Fast Deploy default.'}
                            {fastDeploy === true && 'This service always fast-deploys, even if platform default is off.'}
                            {fastDeploy === false && 'This service always goes through full review, even if platform default is on.'}
                        </p>
                    </div>
                    <Button
                        onClick={async () => {
                            setSaving(true);
                            setError('');
                            setSaved(false);
                            try {
                                await servicesApi.update(service.id, { fast_deploy_enabled: fastDeploy } as any);
                                setSaved(true);
                                setTimeout(() => setSaved(false), 3000);
                            } catch (err: any) {
                                setError(err?.response?.data?.detail || err?.response?.data?.fast_deploy_enabled?.[0] || 'Failed to save fast deploy');
                            } finally {
                                setSaving(false);
                            }
                        }}
                        disabled={saving}
                        className="gap-2"
                    >
                        {saving ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
                        {saving ? 'Saving...' : saved ? 'Saved!' : 'Save Fast Deploy'}
                    </Button>
                    {error && (
                        <div className="bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-3 text-red-500 text-sm">
                            {error}
                        </div>
                    )}
                    {saved && (
                        <div className="bg-emerald-500/10 border border-emerald-500/30 rounded-lg px-4 py-3 text-emerald-500 text-sm flex items-center gap-2">
                            <Check size={16} /> Fast deploy saved successfully
                        </div>
                    )}
                </div>
            </Card>

            {/* Promotion Policy (STAGED → ACTIVE overrides) */}
            <PromotionPolicyCard service={service} />

            {/* Danger Zone */}
            <Card className="p-6 border-red-200/50 bg-red-50/10 dark:bg-red-900/10">
                <h3 className="font-bold text-lg text-destructive mb-2 flex items-center gap-2">
                    <AlertTriangle size={20} /> Danger Zone
                </h3>
                <p className="text-sm text-muted-foreground mb-4">
                    Irreversible actions that affect your service availability.
                </p>
                <div className="flex gap-4">
                    <Button
                        variant="outline"
                        className="border-amber-500 text-amber-600 hover:bg-amber-500/10"
                        onClick={() => void handlePruneDocker()}
                        disabled={saving || pruning}
                    >
                        {pruning ? <Loader2 size={16} className="animate-spin mr-2" /> : <Trash2 size={16} className="mr-2" />}
                        {pruning ? 'Pruning...' : 'Prune Docker State'}
                    </Button>
                    <Button variant="outline" className="border-destructive text-destructive hover:bg-destructive/10">Force Redeploy</Button>
                    <Button variant="destructive" className="bg-red-600 hover:bg-red-700" onClick={handleDelete} disabled={saving}>
                        {saving ? <Loader2 size={16} className="animate-spin mr-2" /> : null}
                        Delete Service
                    </Button>
                </div>
                {pruned && <p className="mt-3 text-sm text-emerald-600">{pruned}</p>}
            </Card>
        </div>
    );
}

const PROMOTION_BOOL_KEYS = [
    { key: 'require_green_healthy', label: 'Require green healthy', desc: 'Block promotion while the green container is missing, stopped, or unhealthy.' },
    { key: 'require_migration_passed', label: 'Require migration passed', desc: 'Block promotion unless this commit has a PASSED migration validation.' },
    { key: 'require_approval_high_critical', label: 'Require approval (high/critical)', desc: 'Block promotion of HIGH/CRITICAL-risk migrations without an approval.' },
    { key: 'block_when_canary_active', label: 'Block while canary active', desc: 'Block promotion while a canary split is serving traffic.' },
    { key: 'block_contract_unsafe', label: 'Block contract-unsafe', desc: 'Block promotion when migrations are contract-unsafe (rollback would be impossible).' },
    { key: 'canary_get_only', label: 'Canary GET-only', desc: 'Restrict canary splits to GET/HEAD requests; writes stay on live.' },
    { key: 'canary_sticky', label: 'Canary sticky sessions', desc: 'Pin canary clients to one variant with a sticky cookie.' },
] as const;

function firstPromotionError(data: any, fallback: string): string {
    if (!data) return fallback;
    if (typeof data === 'string') return data;
    if (typeof data.detail === 'string') return data.detail;
    for (const key of Object.keys(data)) {
        const v = (data as any)[key];
        if (Array.isArray(v) && v.length) return `${key}: ${String(v[0])}`;
        if (typeof v === 'string') return `${key}: ${v}`;
    }
    return fallback;
}

function PromotionPolicyCard({ service }: { service: Service }) {
    // Only explicitly set keys are sent — missing keys inherit the
    // platform default (Settings → Pipeline). Sending explicit false
    // RELAXES a platform-true default, so "Inherit" omits the key.
    const seedFrom = (svc: Service) => {
        const policy = (svc.promotion_policy ?? {}) as Record<string, unknown>;
        const bools: Record<string, boolean | null> = {};
        for (const { key } of PROMOTION_BOOL_KEYS) {
            const v = policy[key];
            bools[key] = typeof v === 'boolean' ? v : null;
        }
        return {
            bools,
            minStaging: policy.min_staging_seconds === undefined || policy.min_staging_seconds === null
                ? '' : String(policy.min_staging_seconds),
            strategy: svc.deploy_strategy || 'ROLLING',
            canaryPct: svc.canary_percentage === undefined || svc.canary_percentage === null
                ? '' : String(svc.canary_percentage),
        };
    };
    const seed = seedFrom(service);
    const [bools, setBools] = useState<Record<string, boolean | null>>(seed.bools);
    const [minStaging, setMinStaging] = useState<string>(seed.minStaging);
    const [strategy, setStrategy] = useState<string>(seed.strategy);
    const [canaryPct, setCanaryPct] = useState(seed.canaryPct);
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);
    const [error, setError] = useState('');
    const dirtyRef = useRef(false);
    const serviceIdRef = useRef(service.id);

    // Poll-safe: the detail page refreshes `service` in the background —
    // never clobber in-progress edits, re-seed only on service switch.
    useEffect(() => {
        if (service.id !== serviceIdRef.current) {
            serviceIdRef.current = service.id;
            dirtyRef.current = false;
            const s = seedFrom(service);
            setBools(s.bools);
            setMinStaging(s.minStaging);
            setStrategy(s.strategy);
            setCanaryPct(s.canaryPct);
        }
    }, [service]);

    const markDirty = (fn: () => void) => { dirtyRef.current = true; setSaved(false); fn(); };

    const handleSave = async () => {
        setSaving(true);
        setError('');
        setSaved(false);
        try {
            const policy: Record<string, unknown> = {};
            for (const { key } of PROMOTION_BOOL_KEYS) {
                if (bools[key] !== null) policy[key] = bools[key];
            }
            if (minStaging.trim() !== '') {
                const n = parseInt(minStaging.trim(), 10);
                if (Number.isNaN(n) || n < 0 || n > 86400) {
                    throw new Error('Soak seconds must be a number between 0 and 86400, or empty to inherit.');
                }
                policy.min_staging_seconds = n;
            }
            const payload: Record<string, unknown> = {};
            if (strategy !== (service.deploy_strategy || 'ROLLING')) payload.deploy_strategy = strategy;
            if (canaryPct.trim() !== '' && canaryPct.trim() !== String(service.canary_percentage ?? '')) {
                const n = parseInt(canaryPct.trim(), 10);
                if (Number.isNaN(n) || n < 0 || n > 100) {
                    throw new Error('Canary weight must be a number between 0 and 100.');
                }
                payload.canary_percentage = n;
            }
            const hadOverrides = Object.keys((service.promotion_policy ?? {}) as object).length > 0;
            if (Object.keys(policy).length > 0) {
                payload.promotion_policy = policy;
            } else if (hadOverrides) {
                payload.promotion_policy = {}; // explicit clear → fully inherit
            }
            if (Object.keys(payload).length === 0) {
                setSaved(true);
                setTimeout(() => setSaved(false), 3000);
                return;
            }
            await servicesApi.update(service.id, payload as any);
            dirtyRef.current = false;
            setSaved(true);
            setTimeout(() => setSaved(false), 3000);
        } catch (err: any) {
            setError(err?.message || firstPromotionError(err?.response?.data, 'Failed to save promotion policy'));
        } finally {
            setSaving(false);
        }
    };

    const triValue = (v: boolean | null) => (v === null ? 'inherit' : v ? 'on' : 'off');

    return (
        <Card className="p-6 border-border shadow-md">
            <div className="mb-4">
                <h3 className="font-bold text-lg">Promotion Policy</h3>
                <p className="text-sm text-muted-foreground">
                    Per-service overrides for the STAGED → ACTIVE readiness gate.
                    Platform defaults live in Settings → Pipeline → Promotion Readiness.
                    Only explicitly set keys win — <span className="font-semibold">Inherit</span> follows the platform.
                </p>
            </div>
            {error && (
                <div className="bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-3 mb-6 text-red-500 text-sm">
                    {error}
                </div>
            )}
            {saved && (
                <div className="bg-emerald-500/10 border border-emerald-500/30 rounded-lg px-4 py-3 mb-6 text-emerald-500 text-sm flex items-center gap-2">
                    <Check size={16} /> Promotion policy saved successfully
                </div>
            )}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="space-y-2">
                    <label className="text-sm font-medium">Deploy strategy</label>
                    <Select value={strategy} onValueChange={(v) => markDirty(() => setStrategy(v))}>
                        <SelectTrigger><SelectValue /></SelectTrigger>
                        <SelectContent>
                            <SelectItem value="ROLLING">Rolling update</SelectItem>
                            <SelectItem value="BLUE_GREEN">Blue/Green (staged → promote)</SelectItem>
                            <SelectItem value="CANARY">Canary (weighted split)</SelectItem>
                        </SelectContent>
                    </Select>
                </div>
                <div className="space-y-2">
                    <label className="text-sm font-medium">Canary weight % {strategy !== 'CANARY' && <span className="text-muted-foreground font-normal">(only used by CANARY)</span>}</label>
                    <Input
                        type="number" min={0} max={100} placeholder={String(service.canary_percentage ?? 10)}
                        value={canaryPct} onChange={(e) => markDirty(() => setCanaryPct(e.target.value))}
                    />
                </div>
            </div>
            <div className="mt-6 space-y-4">
                {PROMOTION_BOOL_KEYS.map(({ key, label, desc }) => (
                    <div key={key} className="flex items-center justify-between gap-4 rounded-lg border p-4">
                        <div className="space-y-0.5">
                            <p className="text-sm font-medium">{label}</p>
                            <p className="text-xs text-muted-foreground">{desc}</p>
                        </div>
                        <Select value={triValue(bools[key] ?? null)} onValueChange={(v) => markDirty(() => setBools((p) => ({ ...p, [key]: v === 'inherit' ? null : v === 'on' })))}>
                            <SelectTrigger className="w-[180px]"><SelectValue /></SelectTrigger>
                            <SelectContent>
                                <SelectItem value="inherit">Inherit platform</SelectItem>
                                <SelectItem value="on">On — enforce</SelectItem>
                                <SelectItem value="off">Off — relax</SelectItem>
                            </SelectContent>
                        </Select>
                    </div>
                ))}
                <div className="flex items-center justify-between gap-4 rounded-lg border p-4">
                    <div className="space-y-0.5">
                        <p className="text-sm font-medium">Staging soak seconds</p>
                        <p className="text-xs text-muted-foreground">Minimum seconds STAGED before promotion. Empty inherits the platform default.</p>
                    </div>
                    <Input
                        type="number" min={0} max={86400} className="w-[180px]"
                        placeholder="Inherit" value={minStaging}
                        onChange={(e) => markDirty(() => setMinStaging(e.target.value))}
                    />
                </div>
            </div>
            <div className="mt-6 flex justify-end">
                <Button onClick={handleSave} disabled={saving} className="gap-2">
                    {saving ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
                    {saving ? 'Saving...' : saved ? 'Saved!' : 'Save Promotion Policy'}
                </Button>
            </div>
        </Card>
    );
}
