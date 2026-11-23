'use client';

import React, { useState, useEffect, useRef } from 'react';
import dynamic from 'next/dynamic';
import { Skeleton } from '@/components/ui/skeleton';
const Editor = dynamic(() => import('@monaco-editor/react'), { ssr: false, loading: () => <Skeleton className="h-[400px] w-full" /> });
import { Service, servicesApi } from '@/lib/api';
import { firstApiError } from '@/lib/apiErrors';
import { useConfirm } from '@/components/ui/confirm-dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card } from '@/components/ui/card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Save, AlertTriangle, Check, Loader2, Search, FileText, Code2, Trash2 } from 'lucide-react';

export function AdvancedTab({ service, onUpdated }: { service: Service; onUpdated?: (updated: Partial<Service>) => void }) {
    const confirm = useConfirm();
    // The effective registry comes from the backend (ScopedRegistry
    // chain → platform config). Never fabricate a hardcoded registry
    // domain here: the old fallback invented 'registry.Trulay.co/<name>'
    // for every service and then PERSISTED that bogus image on save.
    const effectiveRegistry = service.effective_registry || '';
    const defaultImage = effectiveRegistry
        ? `${effectiveRegistry}/${service.name}`
        : (service.docker_image || '');
    const splitImageAndTag = (raw: string): { repo: string; tag: string } => {
        const trimmed = (raw || '').trim();
        const lastSlash = trimmed.lastIndexOf('/');
        const lastColon = trimmed.lastIndexOf(':');
        if (lastColon > lastSlash) {
            return { repo: trimmed.slice(0, lastColon), tag: trimmed.slice(lastColon + 1) || 'latest' };
        }
        return { repo: trimmed, tag: 'latest' };
    };
    const initialParsed = splitImageAndTag(service.docker_image || defaultImage);
    const [config, setConfig] = useState<{ docker_image: string; image_tag: string; start_command: string; restart_policy: string }>({
        docker_image: initialParsed.repo,
        image_tag: initialParsed.tag,
        start_command: service.start_command || '',
        restart_policy: service.restart_policy || 'unless-stopped',
    });
    const buildRawJson = (svc: Service, img: string, tag: string, policy: string, cmd: string) => JSON.stringify({
        config: {
            containers: [
                {
                    name: svc.name,
                    image: img ? `${img}:${tag || 'latest'}` : `${svc.name}:${tag || 'latest'}`,
                    command: cmd || undefined,
                    resources: {
                        limits: {
                            cpu: String(svc.cpu_cores ?? 0.5),
                            memory: `${svc.memory_mb ?? 512}Mi`,
                        },
                    },
                    restartPolicy: policy || 'unless-stopped',
                    securityContext: {
                        allowPrivilegeEscalation: false,
                    },
                },
            ],
        },
    }, null, 2);
    const [rawJson, setRawJson] = useState<string>(() =>
        buildRawJson(service, initialParsed.repo, initialParsed.tag, service.restart_policy || 'unless-stopped', service.start_command || '')
    );
    const [scanDepth, setScanDepth] = useState<'shallow' | 'standard' | 'deep'>(service.env_scan_depth || 'shallow');
    const [fastDeploy, setFastDeploy] = useState<boolean | null>(service.fast_deploy_enabled ?? null);
    const [savingSection, setSavingSection] = useState<'raw' | 'runtime' | 'scan' | 'fast' | 'redeploy' | 'delete' | null>(null);
    const [savedSection, setSavedSection] = useState<'raw' | 'runtime' | 'scan' | 'fast' | 'redeploy' | null>(null);
    const [sectionError, setSectionError] = useState<{ section: 'raw' | 'runtime' | 'scan' | 'fast' | 'danger'; message: string } | null>(null);
    const [pruning, setPruning] = useState(false);
    const [pruned, setPruned] = useState('');

    // Seed-once keyed by service id (AGENTS.md #21): same pattern as
    // BuildTab — the 3s parent poll must not reseed, a new id must.
    const seededIdRef = useRef(service.id);
    useEffect(() => {
        if (seededIdRef.current === service.id) return;
        seededIdRef.current = service.id;
        const nextRegistry = service.effective_registry || '';
        const nextDefault = nextRegistry
            ? `${nextRegistry}/${service.name}`
            : (service.docker_image || '');
        const parsed = splitImageAndTag(service.docker_image || nextDefault);
        setConfig({
            docker_image: parsed.repo,
            image_tag: parsed.tag,
            start_command: service.start_command || '',
            restart_policy: service.restart_policy || 'unless-stopped',
        });
        setRawJson(buildRawJson(service, parsed.repo, parsed.tag, service.restart_policy || 'unless-stopped', service.start_command || ''));
        setScanDepth(service.env_scan_depth || 'shallow');
        setFastDeploy(service.fast_deploy_enabled ?? null);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [service.id]);

    const markSectionSaved = (section: 'raw' | 'runtime' | 'scan' | 'fast' | 'redeploy') => {
        setSavedSection(section);
        setTimeout(() => {
            setSavedSection((prev) => (prev === section ? null : prev));
        }, 3000);
    };

    const handleApplyRawJson = async () => {
        setSavingSection('raw');
        setSectionError(null);
        try {
            const parsed = JSON.parse(rawJson);
            const container = parsed?.config?.containers?.[0] || parsed?.containers?.[0] || parsed;
            const payload: Record<string, any> = {};
            if (typeof container?.restartPolicy === 'string') {
                payload.restart_policy = container.restartPolicy;
            }
            if (typeof container?.command === 'string') {
                payload.start_command = container.command;
            }
            if (typeof container?.image === 'string' && container.image.trim()) {
                const { repo, tag } = splitImageAndTag(container.image.trim());
                setConfig((prev) => ({
                    ...prev,
                    docker_image: repo,
                    image_tag: tag,
                    restart_policy: payload.restart_policy || prev.restart_policy,
                    start_command: payload.start_command ?? prev.start_command,
                }));
                const composedImage = tag && tag !== 'latest' ? `${repo}:${tag}` : repo;
                if (composedImage !== (service.docker_image || defaultImage)) {
                    payload.docker_image = composedImage;
                }
            }
            const cpuRaw = container?.resources?.limits?.cpu;
            if (cpuRaw !== undefined) {
                const cpuNum = parseFloat(String(cpuRaw));
                if (!Number.isNaN(cpuNum) && cpuNum >= 0.1) payload.cpu_cores = cpuNum;
            }
            const memRaw = container?.resources?.limits?.memory;
            if (memRaw !== undefined) {
                const memNum = parseInt(String(memRaw).replace(/[^0-9]/g, ''), 10);
                if (!Number.isNaN(memNum) && memNum >= 64) payload.memory_mb = memNum;
            }
            const updated = await servicesApi.update(service.id, payload);
            onUpdated?.(updated);
            markSectionSaved('raw');
        } catch (err: any) {
            const msg = err instanceof SyntaxError
                ? `Invalid JSON: ${err.message}`
                : firstApiError(err?.response?.data, 'Failed to apply raw container configuration');
            setSectionError({ section: 'raw', message: msg });
        } finally {
            setSavingSection(null);
        }
    };

    const handleSave = async () => {
        setSavingSection('runtime');
        setSectionError(null);
        try {
            const payload: Record<string, string | null> = {
                start_command: config.start_command,
                restart_policy: config.restart_policy,
            };
            const tag = (config.image_tag || 'latest').trim();
            const baseRepo = (config.docker_image || '').trim();
            const fullImage = baseRepo
                ? (tag && tag !== 'latest' ? `${baseRepo}:${tag}` : baseRepo)
                : '';
            if (
                fullImage !== (service.docker_image || defaultImage) &&
                (fullImage !== '' || Boolean(service.docker_image))
            ) {
                payload.docker_image = fullImage || null;
            }
            const updated = await servicesApi.update(service.id, payload as any);
            onUpdated?.(updated);
            markSectionSaved('runtime');
        } catch (err: any) {
            setSectionError({ section: 'runtime', message: firstApiError(err?.response?.data, 'Failed to save configuration') });
        } finally {
            setSavingSection(null);
        }
    };

    const handleForceRedeploy = async () => {
        if (!await confirm({
            title: 'Force Redeploy Service?',
            message: `Trigger a clean rebuild and redeploy of "${service.name}" from HEAD?`,
            confirmText: 'Force Redeploy',
        })) return;

        setSavingSection('redeploy');
        setSectionError(null);
        try {
            await servicesApi.deploy(service.id, 'HEAD');
            markSectionSaved('redeploy');
        } catch (err: any) {
            setSectionError({ section: 'danger', message: firstApiError(err?.response?.data, 'Failed to trigger redeploy') });
        } finally {
            setSavingSection(null);
        }
    };

    const handleDelete = async () => {
        if (!await confirm({ 
            title: 'Delete Service?', 
            message: `Are you sure you want to delete "${service.name}"? This action is irreversible.`,
            variant: 'destructive',
            confirmText: 'Delete Forever'
        })) return;

        setSavingSection('delete');
        try {
            await servicesApi.delete(service.id);
            window.location.href = '/dashboard';
        } catch (err: any) {
            setSectionError({ section: 'danger', message: firstApiError(err?.response?.data, 'Failed to delete service') });
            setSavingSection(null);
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
        setSectionError(null);
        setPruned('');
        try {
            const result = await servicesApi.pruneDocker(service.id);
            setPruned(`Removed ${result.containers_removed} containers and ${result.deployments_deleted} deployment records; reclaimed ${result.space_reclaimed_mb} MB.`);
        } catch (err: any) {
            setSectionError({ section: 'danger', message: firstApiError(err?.response?.data, 'Failed to prune service Docker state') });
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
                    <Button
                        variant="outline"
                        className="gap-2"
                        onClick={handleApplyRawJson}
                        disabled={savingSection !== null}
                    >
                        {savingSection === 'raw' ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
                        {savingSection === 'raw' ? 'Applying...' : savedSection === 'raw' ? 'Applied!' : 'Apply'}
                    </Button>
                </div>
                {sectionError?.section === 'raw' && (
                    <div className="bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-3 mb-4 text-red-500 text-sm">
                        {sectionError.message}
                    </div>
                )}
                {savedSection === 'raw' && (
                    <div className="bg-emerald-500/10 border border-emerald-500/30 rounded-lg px-4 py-3 mb-4 text-emerald-500 text-sm flex items-center gap-2">
                        <Check size={16} /> Raw container configuration applied
                    </div>
                )}
                <div className="h-96 border border-border rounded-lg overflow-hidden">
                    <Editor
                        height="100%"
                        defaultLanguage="json"
                        value={rawJson}
                        onChange={(val) => setRawJson(val ?? '')}
                        theme="vs-dark"
                        options={{ minimap: { enabled: false }, fontSize: 13 }}
                    />
                </div>
            </Card>

            {/* Container Settings Form */}
            <Card className="p-6 border-border shadow-md">
                <h3 className="font-bold text-lg mb-6">Container Runtime</h3>

                {sectionError?.section === 'runtime' && (
                    <div className="bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-3 mb-6 text-red-500 text-sm">
                        {sectionError.message}
                    </div>
                )}
                {savedSection === 'runtime' && (
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
                        <Input
                            value={config.image_tag}
                            onChange={(e) => setConfig(prev => ({ ...prev, image_tag: e.target.value }))}
                            placeholder="latest"
                        />
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
                    <Button onClick={handleSave} disabled={savingSection !== null} className="gap-2">
                        {savingSection === 'runtime' ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
                        {savingSection === 'runtime' ? 'Saving...' : savedSection === 'runtime' ? 'Saved!' : 'Save Configuration'}
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
                            setSavingSection('scan');
                            setSectionError(null);
                            try {
                                const updated = await servicesApi.update(service.id, { env_scan_depth: scanDepth });
                                onUpdated?.(updated);
                                markSectionSaved('scan');
                            } catch (err: any) {
                                setSectionError({ section: 'scan', message: err?.response?.data?.detail || 'Failed to save scan depth' });
                            } finally {
                                setSavingSection(null);
                            }
                        }} 
                        disabled={savingSection !== null} 
                        className="gap-2"
                    >
                        {savingSection === 'scan' ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
                        {savingSection === 'scan' ? 'Saving...' : savedSection === 'scan' ? 'Saved!' : 'Save Scan Depth'}
                    </Button>
                    {sectionError?.section === 'scan' && (
                        <div className="bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-3 text-red-500 text-sm">
                            {sectionError.message}
                        </div>
                    )}
                    {savedSection === 'scan' && (
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
                            setSavingSection('fast');
                            setSectionError(null);
                            try {
                                const updated = await servicesApi.update(service.id, { fast_deploy_enabled: fastDeploy } as any);
                                onUpdated?.(updated);
                                markSectionSaved('fast');
                            } catch (err: any) {
                                setSectionError({
                                    section: 'fast',
                                    message: err?.response?.data?.detail || err?.response?.data?.fast_deploy_enabled?.[0] || 'Failed to save fast deploy',
                                });
                            } finally {
                                setSavingSection(null);
                            }
                        }}
                        disabled={savingSection !== null}
                        className="gap-2"
                    >
                        {savingSection === 'fast' ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
                        {savingSection === 'fast' ? 'Saving...' : savedSection === 'fast' ? 'Saved!' : 'Save Fast Deploy'}
                    </Button>
                    {sectionError?.section === 'fast' && (
                        <div className="bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-3 text-red-500 text-sm">
                            {sectionError.message}
                        </div>
                    )}
                    {savedSection === 'fast' && (
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
                <div className="flex gap-4 flex-wrap">
                    <Button
                        variant="outline"
                        className="border-amber-500 text-amber-600 hover:bg-amber-500/10"
                        onClick={() => void handlePruneDocker()}
                        disabled={savingSection !== null || pruning}
                    >
                        {pruning ? <Loader2 size={16} className="animate-spin mr-2" /> : <Trash2 size={16} className="mr-2" />}
                        {pruning ? 'Pruning...' : 'Prune Docker State'}
                    </Button>
                    <Button
                        variant="outline"
                        className="border-destructive text-destructive hover:bg-destructive/10"
                        onClick={() => void handleForceRedeploy()}
                        disabled={savingSection !== null || pruning}
                    >
                        {savingSection === 'redeploy' ? <Loader2 size={16} className="animate-spin mr-2" /> : null}
                        {savingSection === 'redeploy' ? 'Redeploying...' : savedSection === 'redeploy' ? 'Redeploy Queued!' : 'Force Redeploy'}
                    </Button>
                    <Button variant="destructive" className="bg-red-600 hover:bg-red-700" onClick={handleDelete} disabled={savingSection !== null}>
                        {savingSection === 'delete' ? <Loader2 size={16} className="animate-spin mr-2" /> : null}
                        Delete Service
                    </Button>
                </div>
                {sectionError?.section === 'danger' && (
                    <p className="mt-3 text-sm text-red-500">{sectionError.message}</p>
                )}
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
                    <p className="text-xs text-muted-foreground">
                        Intent only — traffic shifts when you press Apply in Domains → Traffic Split.
                    </p>
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
