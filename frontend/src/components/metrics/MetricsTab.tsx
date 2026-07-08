'use client';

import React, { useEffect, useState, useCallback } from 'react';
import { servicesApi } from '@/lib/api';
import { Card } from '@/components/ui/card';
import { Activity, Cpu, HardDrive, Network, Database, RefreshCw } from 'lucide-react';
import dynamic from 'next/dynamic';
import { Skeleton } from '@/components/ui/skeleton';
const XAxis = dynamic(() => import('recharts').then(m => m.XAxis), { ssr: false, loading: () => <Skeleton className="h-4 w-full" /> });
const YAxis = dynamic(() => import('recharts').then(m => m.YAxis), { ssr: false, loading: () => <Skeleton className="h-4 w-full" /> });
const CartesianGrid = dynamic(() => import('recharts').then(m => m.CartesianGrid), { ssr: false, loading: () => <Skeleton className="h-4 w-full" /> });
const Tooltip = dynamic(() => import('recharts').then(m => m.Tooltip), { ssr: false, loading: () => <Skeleton className="h-4 w-full" /> });
const Area = dynamic(() => import('recharts').then(m => m.Area), { ssr: false, loading: () => <Skeleton className="h-4 w-full" /> });
const AreaChart = dynamic(() => import('recharts').then(m => m.AreaChart), { ssr: false, loading: () => <Skeleton className="h-4 w-full" /> });
import { ChartContainer } from '@/components/ui/chart-container';
import { WorldTrafficMap } from './WorldTrafficMap';
import { TrafficSummary } from './TrafficSummary';

interface MetricPoint {
    timestamp: string;
    value: number;
}

interface CurrentSnapshot {
    cpu_percent: number;
    memory_usage: number;
    memory_limit: number;
    memory_percent: number;
    network_rx_kb: number;
    network_tx_kb: number;
}

interface MetricsData {
    cpu: MetricPoint[];
    memory: MetricPoint[];
    network: MetricPoint[];
    disk: MetricPoint[];
    current: CurrentSnapshot;
}

const DURATION_OPTIONS = [
    { label: '1h', value: '1h' },
    { label: '6h', value: '6h' },
    { label: '24h', value: '24h' },
    { label: '7d', value: '7d' },
];

function getThresholdColor(value: number, warn: number = 60, crit: number = 85): string {
    if (value >= crit) return 'text-red-500';
    if (value >= warn) return 'text-yellow-500';
    return 'text-emerald-500';
}

function getThresholdBg(value: number, warn: number = 60, crit: number = 85): string {
    if (value >= crit) return 'bg-red-500/10 border-red-500/30';
    if (value >= warn) return 'bg-yellow-500/10 border-yellow-500/30';
    return 'bg-emerald-500/10 border-emerald-500/30';
}

function GaugeRing({ value, color, size = 56 }: { value: number; color: string; size?: number }) {
    const radius = (size - 8) / 2;
    const circumference = 2 * Math.PI * radius;
    const offset = circumference - (Math.min(value, 100) / 100) * circumference;
    return (
        <svg width={size} height={size} className="transform -rotate-90">
            <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="currentColor"
                strokeWidth="4" className="text-white/5" />
            <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="currentColor"
                strokeWidth="4" className={color} strokeDasharray={circumference}
                strokeDashoffset={offset} strokeLinecap="round"
                style={{ transition: 'stroke-dashoffset 0.8s ease-out' }} />
        </svg>
    );
}

export function MetricsTab({ serviceId }: { serviceId: string }) {
    const [data, setData] = useState<MetricsData | null>(null);
    const [loading, setLoading] = useState(true);
    const [duration, setDuration] = useState('1h');
    const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
    const [trafficData, setTrafficData] = useState<any>(null);

    const fetchMetrics = useCallback(async () => {
        try {
            const res = await servicesApi.getMetrics(serviceId, duration);
            setData(res);
            setLastUpdated(new Date());
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    }, [serviceId, duration]);

    useEffect(() => {
        setLoading(true);
        fetchMetrics();
        const interval = setInterval(fetchMetrics, 30000);
        return () => clearInterval(interval);
    }, [fetchMetrics]);

    const fetchTraffic = useCallback(async () => {
        try {
            const res = await servicesApi.getTrafficGeo(serviceId);
            setTrafficData(res);
        } catch {
            // Silently ignore — traffic data is optional
        }
    }, [serviceId]);

    useEffect(() => {
        fetchTraffic();
        const interval = setInterval(fetchTraffic, 60000);
        return () => clearInterval(interval);
    }, [fetchTraffic]);

    const formatTime = (ts: string) => {
        const date = new Date(ts);
        if (duration === '7d') return date.toLocaleDateString([], { month: 'short', day: 'numeric' });
        if (duration === '24h') return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    };

    if (loading) return (
        <div className="p-12 text-center text-muted-foreground flex flex-col items-center gap-3">
            <RefreshCw className="w-6 h-6 animate-spin" />
            Loading metrics...
        </div>
    );
    if (!data) return <div className="p-8 text-center text-destructive">Failed to load metrics.</div>;

    const { current } = data;
    const cpuVal = current.cpu_percent;
    const memVal = current.memory_percent;
    const netTotal = current.network_rx_kb + current.network_tx_kb;

    const tooltipStyle = {
        backgroundColor: '#18181b',
        borderColor: '#333',
        color: '#fff',
        borderRadius: '8px',
        fontSize: '12px',
    };

    return (
        <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4">

            {/* Header Row: Duration Selector + Live Status */}
            <div className="flex items-center justify-between">
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
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
                    <span>Live — {lastUpdated ? `updated ${lastUpdated.toLocaleTimeString()}` : 'loading...'}</span>
                </div>
            </div>

            {/* Live Snapshot Cards */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                {/* CPU */}
                <Card className={`p-4 border ${getThresholdBg(cpuVal)} relative overflow-hidden`}>
                    <div className="flex items-center justify-between">
                        <div>
                            <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">CPU</p>
                            <p className={`text-2xl font-bold mt-1 ${getThresholdColor(cpuVal)}`}>
                                {cpuVal.toFixed(1)}%
                            </p>
                        </div>
                        <div className="relative">
                            <GaugeRing value={cpuVal} color={getThresholdColor(cpuVal)} />
                            <Cpu className={`w-4 h-4 absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 rotate-90 ${getThresholdColor(cpuVal)}`} />
                        </div>
                    </div>
                </Card>

                {/* Memory */}
                <Card className={`p-4 border ${getThresholdBg(memVal)} relative overflow-hidden`}>
                    <div className="flex items-center justify-between">
                        <div>
                            <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">Memory</p>
                            <p className={`text-2xl font-bold mt-1 ${getThresholdColor(memVal)}`}>
                                {memVal.toFixed(1)}%
                            </p>
                            <p className="text-[10px] text-muted-foreground">
                                {current.memory_usage} / {current.memory_limit} MB
                            </p>
                        </div>
                        <div className="relative">
                            <GaugeRing value={memVal} color={getThresholdColor(memVal)} />
                            <HardDrive className={`w-4 h-4 absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 rotate-90 ${getThresholdColor(memVal)}`} />
                        </div>
                    </div>
                </Card>

                {/* Network */}
                <Card className="p-4 border border-border bg-card">
                    <div className="flex items-center justify-between">
                        <div>
                            <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">Network</p>
                            <p className="text-2xl font-bold mt-1 text-blue-500">
                                {netTotal.toFixed(1)}
                                <span className="text-sm font-normal ml-1">KB/s</span>
                            </p>
                            <p className="text-[10px] text-muted-foreground">
                                ↓ {current.network_rx_kb.toFixed(1)} / ↑ {current.network_tx_kb.toFixed(1)}
                            </p>
                        </div>
                        <Network className="w-8 h-8 text-blue-500/30" />
                    </div>
                </Card>

                {/* Disk */}
                <Card className="p-4 border border-border bg-card">
                    <div className="flex items-center justify-between">
                        <div>
                            <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">Disk I/O</p>
                            <p className="text-2xl font-bold mt-1 text-amber-500">
                                {data.disk.length > 0 ? data.disk[data.disk.length - 1].value.toFixed(1) : '0.0'}
                                <span className="text-sm font-normal ml-1">KB/s</span>
                            </p>
                        </div>
                        <Database className="w-8 h-8 text-amber-500/30" />
                    </div>
                </Card>
            </div>

            {/* CPU Chart */}
            <Card className="p-6 border-border shadow-md">
                <div className="flex items-center gap-2 mb-4">
                    <Cpu className="w-5 h-5 text-blue-500" />
                    <h3 className="font-bold">CPU Usage (%)</h3>
                </div>
                <ChartContainer className="h-[200px] w-full">
                    <AreaChart data={data.cpu}>
                        <defs>
                            <linearGradient id="cpuGrad" x1="0" y1="0" x2="0" y2="1">
                                <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3} />
                                <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
                            </linearGradient>
                        </defs>
                        <CartesianGrid strokeDasharray="3 3" stroke="#333" opacity={0.15} />
                        <XAxis dataKey="timestamp" tickFormatter={formatTime}
                            style={{ fontSize: '11px' }} stroke="#555" />
                        <YAxis style={{ fontSize: '11px' }} stroke="#555" domain={[0, 100]} />
                        <Tooltip labelFormatter={(label) => formatTime(String(label))} contentStyle={tooltipStyle} />
                        <Area type="monotone" dataKey="value" stroke="#3b82f6"
                            strokeWidth={2} fill="url(#cpuGrad)" dot={false} />
                    </AreaChart>
                </ChartContainer>
            </Card>

            {/* Memory Chart */}
            <Card className="p-6 border-border shadow-md">
                <div className="flex items-center gap-2 mb-4">
                    <HardDrive className="w-5 h-5 text-purple-500" />
                    <h3 className="font-bold">Memory Usage (MB)</h3>
                </div>
                <ChartContainer className="h-[200px] w-full">
                    <AreaChart data={data.memory}>
                        <defs>
                            <linearGradient id="memGrad" x1="0" y1="0" x2="0" y2="1">
                                <stop offset="5%" stopColor="#a855f7" stopOpacity={0.3} />
                                <stop offset="95%" stopColor="#a855f7" stopOpacity={0} />
                            </linearGradient>
                        </defs>
                        <CartesianGrid strokeDasharray="3 3" stroke="#333" opacity={0.15} />
                        <XAxis dataKey="timestamp" tickFormatter={formatTime}
                            style={{ fontSize: '11px' }} stroke="#555" />
                        <YAxis style={{ fontSize: '11px' }} stroke="#555" />
                        <Tooltip labelFormatter={(label) => formatTime(String(label))} contentStyle={tooltipStyle} />
                        <Area type="monotone" dataKey="value" stroke="#a855f7"
                            strokeWidth={2} fill="url(#memGrad)" dot={false} />
                    </AreaChart>
                </ChartContainer>
            </Card>

            {/* Network + Disk I/O — side by side on desktop */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                {/* Network Chart */}
                <Card className="p-6 border-border shadow-md">
                    <div className="flex items-center gap-2 mb-4">
                        <Network className="w-5 h-5 text-emerald-500" />
                        <h3 className="font-bold">Network I/O (KB/s)</h3>
                    </div>
                    <ChartContainer className="h-[200px] w-full">
                        <AreaChart data={data.network}>
                            <defs>
                                <linearGradient id="netGrad" x1="0" y1="0" x2="0" y2="1">
                                    <stop offset="5%" stopColor="#10b981" stopOpacity={0.3} />
                                    <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
                                </linearGradient>
                            </defs>
                            <CartesianGrid strokeDasharray="3 3" stroke="#333" opacity={0.15} />
                            <XAxis dataKey="timestamp" tickFormatter={formatTime}
                                style={{ fontSize: '11px' }} stroke="#555" />
                            <YAxis style={{ fontSize: '11px' }} stroke="#555" />
                            <Tooltip labelFormatter={(label) => formatTime(String(label))} contentStyle={tooltipStyle} />
                            <Area type="monotone" dataKey="value" stroke="#10b981"
                                strokeWidth={2} fill="url(#netGrad)" dot={false} />
                        </AreaChart>
                    </ChartContainer>
                </Card>

                {/* Disk I/O Chart */}
                <Card className="p-6 border-border shadow-md">
                    <div className="flex items-center gap-2 mb-4">
                        <Database className="w-5 h-5 text-amber-500" />
                        <h3 className="font-bold">Disk I/O (KB/s)</h3>
                    </div>
                    <ChartContainer className="h-[200px] w-full">
                        <AreaChart data={data.disk}>
                            <defs>
                                <linearGradient id="diskGrad" x1="0" y1="0" x2="0" y2="1">
                                    <stop offset="5%" stopColor="#f59e0b" stopOpacity={0.3} />
                                    <stop offset="95%" stopColor="#f59e0b" stopOpacity={0} />
                                </linearGradient>
                            </defs>
                            <CartesianGrid strokeDasharray="3 3" stroke="#333" opacity={0.15} />
                            <XAxis dataKey="timestamp" tickFormatter={formatTime}
                                style={{ fontSize: '11px' }} stroke="#555" />
                            <YAxis style={{ fontSize: '11px' }} stroke="#555" />
                            <Tooltip labelFormatter={(label) => formatTime(String(label))} contentStyle={tooltipStyle} />
                            <Area type="monotone" dataKey="value" stroke="#f59e0b"
                                strokeWidth={2} fill="url(#diskGrad)" dot={false} />
                        </AreaChart>
                    </ChartContainer>
                </Card>
            </div>

            {/* Traffic Summary + Map */}
            <TrafficSummary
                totalRequests={trafficData?.total_requests ?? 0}
                uniqueCountries={trafficData?.unique_countries ?? 0}
                uniqueIps={trafficData?.unique_ips ?? 0}
                topCountries={trafficData?.countries ?? []}
            />
            <WorldTrafficMap serviceId={serviceId} />
        </div>
    );
}
