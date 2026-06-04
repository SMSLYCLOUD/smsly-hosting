'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Activity, Cpu, HardDrive, Network, AlertCircle, RefreshCcw, Server, Database } from 'lucide-react';
import { servicesApi, addonsApi, type Service, type Addon } from '@/lib/api';
import { useRouter } from 'next/navigation';

interface ServiceHealth {
    id: string;
    name: string;
    cpu: number;
    memory: number;
    network: number;
    status: string;
    public_domain?: string;
}

const DURATION_OPTIONS = [
    { label: '1h', value: '1h' },
    { label: '6h', value: '6h' },
    { label: '24h', value: '24h' },
];

function getThresholdColor(value: number, warn = 60, crit = 85) {
    if (value >= crit) return 'text-red-500';
    if (value >= warn) return 'text-yellow-500';
    return 'text-emerald-500';
}

export default function MonitoringPage() {
    const router = useRouter();
    const [services, setServices] = useState<Service[]>([]);
    const [addons, setAddons] = useState<Addon[]>([]);
    const [health, setHealth] = useState<Record<string, ServiceHealth>>({});
    const [loading, setLoading] = useState(true);
    const [duration, setDuration] = useState('1h');
    const [error, setError] = useState<string | null>(null);
    const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

    const fetchData = useCallback(async () => {
        try {
            const [svcList, addonList] = await Promise.all([
                servicesApi.list().catch(() => []),
                addonsApi.list().catch(() => []),
            ]);
            setServices(svcList);
            setAddons(addonList);

            const sample = (svcList as Service[]).slice(0, 20);
            const entries = await Promise.all(
                sample.map(async (svc) => {
                    try {
                        const m = await servicesApi.getMetrics(svc.id, duration);
                        const current = m?.current || {};
                        return [
                            svc.id,
                            {
                                id: svc.id,
                                name: svc.name,
                                cpu: Number(current.cpu_percent || 0),
                                memory: Number(current.memory_percent || 0),
                                network:
                                    Number(current.network_rx_kb || 0) +
                                    Number(current.network_tx_kb || 0),
                                status: svc.latest_deployment?.status || 'UNKNOWN',
                                public_domain: svc.public_domain,
                            },
                        ] as const;
                    } catch {
                        return [
                            svc.id,
                            {
                                id: svc.id,
                                name: svc.name,
                                cpu: 0,
                                memory: 0,
                                network: 0,
                                status: svc.latest_deployment?.status || 'UNKNOWN',
                                public_domain: svc.public_domain,
                            },
                        ] as const;
                    }
                })
            );
            setHealth(Object.fromEntries(entries));
            setLastUpdated(new Date());
            setError(null);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to load services');
        } finally {
            setLoading(false);
        }
    }, [duration]);

    useEffect(() => {
        setLoading(true);
        fetchData();
        const interval = setInterval(fetchData, 30000);
        return () => clearInterval(interval);
    }, [fetchData]);

    const healthList = useMemo(() => Object.values(health), [health]);

    const activeCount = healthList.filter((h) => h.status === 'ACTIVE' || h.status === 'LIVE').length;
    const failedCount = healthList.filter((h) => h.status === 'FAILED').length;
    const highCpu = healthList.filter((h) => h.cpu >= 85).length;

    return (
        <div className="container mx-auto py-8 space-y-6">
            <div className="flex flex-wrap items-end justify-between gap-3">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Monitoring</h1>
                    <p className="text-sm text-muted-foreground mt-1">
                        Aggregate health across all services and addons.
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <div className="flex bg-muted rounded-lg p-1 gap-1">
                        {DURATION_OPTIONS.map((opt) => (
                            <button
                                key={opt.value}
                                onClick={() => setDuration(opt.value)}
                                className={`px-3 py-1.5 rounded-md text-xs font-bold transition-all ${
                                    duration === opt.value
                                        ? 'bg-primary text-primary-foreground shadow-sm'
                                        : 'text-muted-foreground hover:text-foreground hover:bg-muted-foreground/10'
                                }`}
                            >
                                {opt.label}
                            </button>
                        ))}
                    </div>
                    <button
                        onClick={fetchData}
                        className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-md border border-border hover:bg-muted"
                    >
                        <RefreshCcw className="w-3 h-3" /> Refresh
                    </button>
                </div>
            </div>

            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <Card>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-xs uppercase tracking-wide text-muted-foreground flex items-center gap-2">
                            <Server className="w-3.5 h-3.5" /> Services
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <p className="text-2xl font-bold">{services.length}</p>
                        <p className="text-[10px] text-muted-foreground">{activeCount} active</p>
                    </CardContent>
                </Card>
                <Card>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-xs uppercase tracking-wide text-muted-foreground flex items-center gap-2">
                            <Database className="w-3.5 h-3.5" /> Addons
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <p className="text-2xl font-bold">{addons.length}</p>
                        <p className="text-[10px] text-muted-foreground">across all services</p>
                    </CardContent>
                </Card>
                <Card>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-xs uppercase tracking-wide text-muted-foreground flex items-center gap-2">
                            <AlertCircle className="w-3.5 h-3.5" /> Failed
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <p className={`text-2xl font-bold ${failedCount > 0 ? 'text-red-500' : 'text-emerald-500'}`}>
                            {failedCount}
                        </p>
                        <p className="text-[10px] text-muted-foreground">in current window</p>
                    </CardContent>
                </Card>
                <Card>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-xs uppercase tracking-wide text-muted-foreground flex items-center gap-2">
                            <Cpu className="w-3.5 h-3.5" /> Hot CPU
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <p className={`text-2xl font-bold ${highCpu > 0 ? 'text-yellow-500' : 'text-emerald-500'}`}>
                            {highCpu}
                        </p>
                        <p className="text-[10px] text-muted-foreground">≥ 85% utilization</p>
                    </CardContent>
                </Card>
            </div>

            {error && (
                <Card className="border-red-500/30 bg-red-500/5">
                    <CardContent className="p-4 text-sm text-red-500 flex items-center gap-2">
                        <AlertCircle className="w-4 h-4" /> {error}
                    </CardContent>
                </Card>
            )}

            <Card>
                <CardHeader>
                    <CardTitle className="text-base flex items-center gap-2">
                        <Activity className="w-4 h-4" /> Service Health
                    </CardTitle>
                </CardHeader>
                <CardContent>
                    {loading ? (
                        <div className="p-8 text-center text-muted-foreground">Loading…</div>
                    ) : healthList.length === 0 ? (
                        <div className="p-8 text-center text-muted-foreground">
                            No services yet. Deploy one to see live health metrics.
                        </div>
                    ) : (
                        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                            {healthList.map((h) => (
                                <button
                                    key={h.id}
                                    onClick={() => router.push(`/services/${h.id}`)}
                                    className="text-left p-3 rounded-lg border border-border bg-card hover:bg-muted/40 transition-colors"
                                >
                                    <div className="flex items-center justify-between gap-2 mb-2">
                                        <p className="font-semibold text-sm truncate">{h.name}</p>
                                        <span
                                            className={`text-[10px] px-1.5 py-0.5 rounded font-bold uppercase ${
                                                h.status === 'ACTIVE' || h.status === 'LIVE'
                                                    ? 'bg-emerald-500/10 text-emerald-500'
                                                    : h.status === 'FAILED'
                                                        ? 'bg-red-500/10 text-red-500'
                                                        : 'bg-zinc-500/10 text-zinc-400'
                                            }`}
                                        >
                                            {h.status}
                                        </span>
                                    </div>
                                    {h.public_domain && (
                                        <p className="text-[10px] text-muted-foreground truncate mb-2">
                                            {h.public_domain}
                                        </p>
                                    )}
                                    <div className="grid grid-cols-3 gap-2 text-[10px] text-muted-foreground">
                                        <div className="flex items-center gap-1">
                                            <Cpu className="w-3 h-3" />
                                            <span className={getThresholdColor(h.cpu)}>{h.cpu.toFixed(1)}%</span>
                                        </div>
                                        <div className="flex items-center gap-1">
                                            <HardDrive className="w-3 h-3" />
                                            <span className={getThresholdColor(h.memory)}>{h.memory.toFixed(1)}%</span>
                                        </div>
                                        <div className="flex items-center gap-1">
                                            <Network className="w-3 h-3" />
                                            <span>{h.network.toFixed(0)}</span>
                                        </div>
                                    </div>
                                </button>
                            ))}
                        </div>
                    )}
                </CardContent>
            </Card>

            {lastUpdated && (
                <p className="text-xs text-muted-foreground text-right">
                    Last updated {lastUpdated.toLocaleTimeString()}
                </p>
            )}
        </div>
    );
}
