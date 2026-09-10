'use client';

import { useEffect, useState } from 'react';
import { Copy, ExternalLink, Loader2, RefreshCw, ShieldCheck, Wrench } from 'lucide-react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { toast } from '@/components/ui/use-toast';

type Props = {
    serviceId: string;
    serviceName: string;
    internalPort?: number | null;
    publicDomain?: string | null;
};

export function ServiceMtlsTab({ serviceId, serviceName, internalPort, publicDomain }: Props) {
    const [status, setStatus] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [repairing, setRepairing] = useState(false);
    const [repairReport, setRepairReport] = useState<any>(null);

    const load = async () => {
        setLoading(true);
        try {
            const response = await api.get(`/services/${serviceId}/mtls/status/`);
            setStatus(response.data);
        } catch (error: any) {
            toast({
                title: 'mTLS status unavailable',
                description: error?.response?.data?.detail || 'Could not load service mTLS status.',
                variant: 'destructive',
            });
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { void load(); }, [serviceId]);

    const handleRepair = async () => {
        setRepairing(true);
        setRepairReport(null);
        try {
            const response = await api.post(`/services/${serviceId}/mtls/repair/`);
            const report = response.data;
            setRepairReport(report);
            const fixedCount =
                (report.env_repaired?.length ?? 0) +
                (report.mtls_normalized?.length ?? 0) +
                (report.sidecars_injected?.length ?? 0) +
                (report.orphan_containers_removed?.length ?? 0);
            toast({
                title: fixedCount > 0 ? `Repair complete — ${fixedCount} fix(es) applied` : 'Repair complete — nothing to fix',
                description: 'Env placeholders, mTLS config, sidecars, and orphan containers were checked.',
            });
            void load();
        } catch (error: any) {
            toast({
                title: 'Repair failed',
                description: error?.response?.data?.detail || 'Could not run the repair suite.',
                variant: 'destructive',
            });
        } finally {
            setRepairing(false);
        }
    };

    const urls = [
        ['Status API', `/api/v1/services/${serviceId}/mtls/status/`],
        ['Enable API', `/api/v1/services/${serviceId}/mtls/enable/`],
        ['Disable API', `/api/v1/services/${serviceId}/mtls/disable/`],
        ['Envoy sidecar API', `/api/v1/services/${serviceId}/mtls/sidecar/`],
        ['SPIRE health API', '/api/v1/mtls/health/'],
        ['SPIRE overview API', '/api/v1/mtls/overview/'],
        ['SPIRE deploy API', '/api/v1/mtls/spire/deploy/'],
    ];

    const copy = async (value: string) => {
        await navigator.clipboard.writeText(value);
        toast({ title: 'Copied', description: value });
    };

    const internalUrl = `http://${serviceName}:${internalPort || 8000}`;
    const publicUrl = publicDomain ? `https://${publicDomain}` : '';
    const serviceUrls = [
        ['Plain HTTP URL', internalUrl],
        ['Ecosystem placeholder', `{{SERVICE:${serviceName}}}`],
        ...(status?.mtls_url ? [['mTLS URL', status.mtls_url]] : []),
        ...(publicUrl ? [['Public browser URL', publicUrl]] : []),
    ];

    return (
        <div className="space-y-4 animate-in fade-in slide-in-from-bottom-4">
            <Card className="p-6">
                <div className="flex items-start justify-between gap-4">
                    <div>
                        <h3 className="flex items-center gap-2 text-lg font-bold">
                            <ShieldCheck className="h-5 w-5 text-emerald-500" /> mTLS / Envoy
                        </h3>
                        <p className="mt-1 text-sm text-muted-foreground">
                            SPIFFE identity, SVID, and Envoy sidecar configuration for {serviceName}.
                        </p>
                    </div>
                    <div className="flex items-center gap-2">
                        <Button
                            variant="secondary"
                            size="sm"
                            onClick={() => void handleRepair()}
                            disabled={repairing || loading}
                            title="Fix all ecosystem issues: env placeholders, mTLS config drift, missing Envoy sidecars, orphan containers"
                        >
                            {repairing
                                ? <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                : <Wrench className="mr-2 h-4 w-4" />}
                            Fix All Issues
                        </Button>
                        <Button variant="outline" size="sm" onClick={() => void load()} disabled={loading}>
                            {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                            Refresh
                        </Button>
                    </div>
                </div>

                {repairReport && (
                    <div className="mt-5 rounded-lg border bg-muted/30 p-4">
                        <h4 className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                            Repair report
                        </h4>
                        <ul className="mt-2 space-y-1 text-xs">
                            <li>
                                <span className="font-semibold">Env placeholders repaired:</span>{' '}
                                {repairReport.env_repaired?.length
                                    ? repairReport.env_repaired.map((item: any) => `${item.service} (${item.keys?.join(', ')})`).join('; ')
                                    : 'none'}
                            </li>
                            <li>
                                <span className="font-semibold">mTLS config normalized:</span>{' '}
                                {repairReport.mtls_normalized?.length ? repairReport.mtls_normalized.join(', ') : 'none'}
                            </li>
                            <li>
                                <span className="font-semibold">Envoy sidecars injected:</span>{' '}
                                {repairReport.sidecars_injected?.length ? repairReport.sidecars_injected.join(', ') : 'none'}
                            </li>
                            <li>
                                <span className="font-semibold">Orphan containers removed:</span>{' '}
                                {repairReport.orphan_containers_removed?.length ? repairReport.orphan_containers_removed.join(', ') : 'none'}
                            </li>
                            {repairReport.sidecar_errors?.length ? (
                                <li className="text-destructive">
                                    <span className="font-semibold">Sidecar errors:</span>{' '}
                                    {repairReport.sidecar_errors.map((item: any) => `${item.service}: ${item.error}`).join('; ')}
                                </li>
                            ) : null}
                        </ul>
                    </div>
                )}

                {status && (
                    <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                        {[
                            ['mTLS', status.mtls_enabled ? 'Enabled' : 'Disabled'],
                            ['Trust domain', status.trust_domain || 'ecosystem.local'],
                            ['SPIFFE ID', status.spiffe_id || 'Not issued'],
                            ['SVID', status.svid_status === 'missing' ? 'Missing' : status.svid_status === 'expired' ? 'Expired' : 'Valid'],
                            ['SVID expiry', status.svid_expiry ? new Date(status.svid_expiry).toLocaleString() : '—'],
                            ['SVID TTL', status.svid_ttl_remaining != null ? `${Math.floor(status.svid_ttl_remaining / 3600)}h ${Math.floor((status.svid_ttl_remaining % 3600) / 60)}m` : '—'],
                            ['Sidecar', status.sidecar?.status === 'running' ? (status.sidecar?.healthy ? `Healthy (${status.sidecar.name})` : `Running, unhealthy (${status.sidecar.name})`) : status.sidecar?.status || 'Not running'],
                            ['Last rotation', status.last_rotation ? new Date(status.last_rotation).toLocaleString() : '—'],
                        ].map(([label, value]) => (
                            <div key={label} className="rounded-lg border bg-muted/30 p-3">
                                <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{label}</p>
                                <p className="mt-1 break-all font-mono text-sm">{value}</p>
                            </div>
                        ))}
                    </div>
                )}
            </Card>

            <Card className="p-6">
                <h4 className="text-sm font-bold uppercase tracking-wide text-muted-foreground">Service URLs</h4>
                <p className="mt-1 text-xs text-muted-foreground">
                    The plain HTTP URL does not provide mTLS. Use the mTLS URL only when an Envoy sidecar is running; use the public URL from browsers or external clients.
                </p>
                <div className="mt-4 space-y-2">
                    {serviceUrls.map(([label, value]) => (
                        <div key={label} className="flex items-center gap-3 rounded-lg border bg-card p-3">
                            <span className="w-40 shrink-0 text-xs font-semibold text-muted-foreground">{label}</span>
                            <code className="min-w-0 flex-1 break-all text-xs">{value}</code>
                            <Button variant="ghost" size="icon" className="h-8 w-8 shrink-0" onClick={() => void copy(value)} title="Copy URL">
                                <Copy className="h-3.5 w-3.5" />
                            </Button>
                            {label === 'Public browser URL' && (
                                <a href={value} target="_blank" rel="noreferrer" className="text-primary" title="Open URL">
                                    <ExternalLink className="h-4 w-4" />
                                </a>
                            )}
                        </div>
                    ))}
                </div>
                <div className="mt-4 rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-xs text-amber-200">
                    Envoy sidecar: {status?.sidecar?.status === 'running' ? `running (${status.sidecar.name})` : 'not running'}.
                    {status?.mtls_url ? ` mTLS endpoint: ${status.mtls_url}` : ' No mTLS HTTPS endpoint is currently available.'}
                </div>
            </Card>

            <Card className="p-6">
                <h4 className="text-sm font-bold uppercase tracking-wide text-muted-foreground">Configuration URLs</h4>
                <p className="mt-1 text-xs text-muted-foreground">
                    These are the authenticated platform endpoints. The sidecar endpoint enables Envoy on the next redeploy.
                </p>
                <div className="mt-4 space-y-2">
                    {urls.map(([label, path]) => (
                        <div key={path} className="flex items-center gap-3 rounded-lg border bg-card p-3">
                            <span className="w-36 shrink-0 text-xs font-semibold text-muted-foreground">{label}</span>
                            <code className="min-w-0 flex-1 break-all text-xs">{path}</code>
                            <Button variant="ghost" size="icon" className="h-8 w-8 shrink-0" onClick={() => void copy(path)} title="Copy URL">
                                <Copy className="h-3.5 w-3.5" />
                            </Button>
                            {(label === 'Status API' || label === 'SPIRE health API') && (
                                <a href={path} target="_blank" rel="noreferrer" className="text-primary" title="Open URL">
                                    <ExternalLink className="h-4 w-4" />
                                </a>
                            )}
                        </div>
                    ))}
                </div>
            </Card>
        </div>
    );
}
