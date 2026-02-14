'use client';

import React, { useEffect, useState } from 'react';
import { servicesApi } from '@/lib/api';
import { Card } from '@/components/ui/card';
import { Activity, Cpu, HardDrive, Network } from 'lucide-react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';

interface MetricPoint {
    timestamp: number;
    value: number;
}

interface MetricsData {
    cpu: MetricPoint[];
    memory: MetricPoint[];
    network: MetricPoint[];
}

export function MetricsTab({ serviceId }: { serviceId: string }) {
    const [data, setData] = useState<MetricsData | null>(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        const fetchMetrics = async () => {
            try {
                const res = await servicesApi.getMetrics(serviceId);
                setData(res);
            } catch (err) {
                console.error(err);
            } finally {
                setLoading(false);
            }
        };

        fetchMetrics();
        const interval = setInterval(fetchMetrics, 30000); // Poll every 30s
        return () => clearInterval(interval);
    }, [serviceId]);

    if (loading) return <div className="p-8 text-center text-muted-foreground">Loading metrics...</div>;
    if (!data) return <div className="p-8 text-center text-destructive">Failed to load metrics.</div>;

    const formatTime = (ts: number) => {
        const date = new Date(ts * 1000);
        return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    };

    return (
        <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4">

            {/* CPU Chart */}
            <Card className="p-6 border-border shadow-md">
                <div className="flex items-center gap-2 mb-4">
                    <Cpu className="w-5 h-5 text-blue-500" />
                    <h3 className="font-bold">CPU Usage (%)</h3>
                </div>
                <div className="h-[250px] w-full">
                    <ResponsiveContainer width="100%" height="100%" minHeight={200}>
                        <LineChart data={data.cpu}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#333" opacity={0.2} />
                            <XAxis
                                dataKey="timestamp"
                                tickFormatter={formatTime}
                                style={{ fontSize: '12px' }}
                            />
                            <YAxis style={{ fontSize: '12px' }} />
                            <Tooltip
                                labelFormatter={(label) => formatTime(label)}
                                contentStyle={{ backgroundColor: '#18181b', borderColor: '#333', color: '#fff' }}
                            />
                            <Line type="monotone" dataKey="value" stroke="#3b82f6" strokeWidth={2} dot={false} />
                        </LineChart>
                    </ResponsiveContainer>
                </div>
            </Card>

            {/* Memory Chart */}
            <Card className="p-6 border-border shadow-md">
                <div className="flex items-center gap-2 mb-4">
                    <HardDrive className="w-5 h-5 text-purple-500" />
                    <h3 className="font-bold">Memory Usage (MB)</h3>
                </div>
                <div className="h-[250px] w-full">
                    <ResponsiveContainer width="100%" height="100%" minHeight={200}>
                        <LineChart data={data.memory}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#333" opacity={0.2} />
                            <XAxis
                                dataKey="timestamp"
                                tickFormatter={formatTime}
                                style={{ fontSize: '12px' }}
                            />
                            <YAxis style={{ fontSize: '12px' }} />
                            <Tooltip
                                labelFormatter={(label) => formatTime(label)}
                                contentStyle={{ backgroundColor: '#18181b', borderColor: '#333', color: '#fff' }}
                            />
                            <Line type="monotone" dataKey="value" stroke="#a855f7" strokeWidth={2} dot={false} />
                        </LineChart>
                    </ResponsiveContainer>
                </div>
            </Card>

            {/* Network Chart */}
            <Card className="p-6 border-border shadow-md">
                <div className="flex items-center gap-2 mb-4">
                    <Network className="w-5 h-5 text-emerald-500" />
                    <h3 className="font-bold">Network I/O (KB/s)</h3>
                </div>
                <div className="h-[250px] w-full">
                    <ResponsiveContainer width="100%" height="100%" minHeight={200}>
                        <LineChart data={data.network}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#333" opacity={0.2} />
                            <XAxis
                                dataKey="timestamp"
                                tickFormatter={formatTime}
                                style={{ fontSize: '12px' }}
                            />
                            <YAxis style={{ fontSize: '12px' }} />
                            <Tooltip
                                labelFormatter={(label) => formatTime(label)}
                                contentStyle={{ backgroundColor: '#18181b', borderColor: '#333', color: '#fff' }}
                            />
                            <Line type="monotone" dataKey="value" stroke="#10b981" strokeWidth={2} dot={false} />
                        </LineChart>
                    </ResponsiveContainer>
                </div>
            </Card>
        </div>
    );
}
