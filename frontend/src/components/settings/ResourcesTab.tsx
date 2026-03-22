'use client';

import React, { useState, useEffect } from 'react';
import { servicesApi, Service } from '@/lib/api';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Cpu, HardDrive, Scaling, Save, Loader2, Gauge, ArrowUpCircle, ArrowDownCircle } from 'lucide-react';
import { toast } from '@/components/ui/use-toast';

interface ResourcesTabProps {
    serviceId: string;
    service?: Service;
}

export function ResourcesTab({ serviceId, service: initialService }: ResourcesTabProps) {
    const [service, setService] = useState<any>(initialService || null);
    const [loading, setLoading] = useState(!initialService);
    const [saving, setSaving] = useState(false);
    const [cpu, setCpu] = useState(0.5);
    const [memory, setMemory] = useState(512);
    const [minReplicas, setMinReplicas] = useState(1);
    const [maxReplicas, setMaxReplicas] = useState(1);
    const [cpuTarget, setCpuTarget] = useState(80);

    useEffect(() => {
        if (initialService) return;
        (async () => {
            try {
                const s = await servicesApi.get(serviceId);
                setService(s);
                setCpu(s.cpu_cores ?? 0.5);
                setMemory(s.memory_mb ?? 512);
                setMinReplicas((s as any).min_replicas ?? 1);
                setMaxReplicas((s as any).max_replicas ?? 1);
                setCpuTarget((s as any).autoscale_cpu_target ?? 80);
            } catch (err) { console.error(err); }
            finally { setLoading(false); }
        })();
    }, [serviceId, initialService]);

    useEffect(() => {
        if (initialService) {
            setService(initialService);
            setCpu(initialService.cpu_cores ?? 0.5);
            setMemory(initialService.memory_mb ?? 512);
            setMinReplicas((initialService as any).min_replicas ?? 1);
            setMaxReplicas((initialService as any).max_replicas ?? 1);
            setCpuTarget((initialService as any).autoscale_cpu_target ?? 80);
        }
    }, [initialService]);

    const handleSave = async () => {
        setSaving(true);
        try {
            await servicesApi.update(serviceId, {
                cpu_cores: cpu,
                memory_mb: memory,
                min_replicas: minReplicas,
                max_replicas: maxReplicas,
                autoscale_cpu_target: cpuTarget,
            } as any);
            toast({ title: '✓ Resources updated', description: 'Redeploy to apply changes.' });
        } catch (err) {
            toast({ title: 'Failed to save', variant: 'destructive' });
        } finally { setSaving(false); }
    };

    if (loading) return <div className="p-8 text-center text-muted-foreground">Loading resources...</div>;

    const cpuPresets = [0.25, 0.5, 1, 2, 4, 8, 16];
    const memPresets = [256, 512, 1024, 2048, 4096, 8192, 16384];
    const monthlyEstimate = (cpu * 0.04 + (memory / 1024) * 0.02) * 730;

    return (
        <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4">
            {/* CPU & Memory */}
            <Card className="p-6 border-border shadow-md">
                <div className="flex items-center gap-2 mb-6">
                    <Cpu className="w-5 h-5 text-blue-500" />
                    <h3 className="font-bold text-lg">Compute Resources</h3>
                    <span className="ml-auto text-sm text-muted-foreground">
                        Est. <span className="font-bold text-foreground">${monthlyEstimate.toFixed(2)}</span>/mo
                    </span>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
                    {/* CPU */}
                    <div>
                        <label className="text-sm font-medium mb-3 block">
                            CPU <span className="text-muted-foreground">({cpu} vCPU)</span>
                        </label>
                        <div className="flex gap-2 flex-wrap">
                            {cpuPresets.map(v => (
                                <button
                                    key={v}
                                    onClick={() => setCpu(v)}
                                    className={`px-4 py-2 rounded-lg text-sm font-medium border transition-all ${
                                        cpu === v
                                            ? 'bg-blue-500 text-white border-blue-500 shadow-sm'
                                            : 'border-border hover:border-blue-500/50 hover:bg-blue-500/5'
                                    }`}
                                >
                                    {v} vCPU
                                </button>
                            ))}
                        </div>
                        <input
                            type="range" min="0.25" max="64" step="0.25"
                            value={cpu} onChange={e => setCpu(parseFloat(e.target.value))}
                            className="w-full mt-3 accent-blue-500"
                        />
                    </div>

                    {/* Memory */}
                    <div>
                        <label className="text-sm font-medium mb-3 block">
                            Memory <span className="text-muted-foreground">({memory} MB)</span>
                        </label>
                        <div className="flex gap-2 flex-wrap">
                            {memPresets.map(v => (
                                <button
                                    key={v}
                                    onClick={() => setMemory(v)}
                                    className={`px-4 py-2 rounded-lg text-sm font-medium border transition-all ${
                                        memory === v
                                            ? 'bg-purple-500 text-white border-purple-500 shadow-sm'
                                            : 'border-border hover:border-purple-500/50 hover:bg-purple-500/5'
                                    }`}
                                >
                                    {v >= 1024 ? `${v/1024} GB` : `${v} MB`}
                                </button>
                            ))}
                        </div>
                        <input
                            type="range" min="128" max="131072" step="128"
                            value={memory} onChange={e => setMemory(parseInt(e.target.value))}
                            className="w-full mt-3 accent-purple-500"
                        />
                    </div>
                </div>
            </Card>

            {/* Auto-Scaling */}
            <Card className="p-6 border-border shadow-md">
                <div className="flex items-center gap-2 mb-6">
                    <Scaling className="w-5 h-5 text-emerald-500" />
                    <h3 className="font-bold text-lg">Auto-Scaling</h3>
                    <span className={`ml-auto px-2 py-0.5 rounded text-xs font-bold ${
                        maxReplicas > 1 ? 'bg-emerald-500/10 text-emerald-500' : 'bg-muted text-muted-foreground'
                    }`}>
                        {maxReplicas > 1 ? 'ENABLED' : 'DISABLED'}
                    </span>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                    <div>
                        <label className="text-sm font-medium mb-2 flex items-center gap-2">
                            <ArrowDownCircle className="w-4 h-4 text-muted-foreground" />
                            Min Replicas
                        </label>
                        <Input
                            type="number" min={1} max={10}
                            value={minReplicas}
                            onChange={e => setMinReplicas(parseInt(e.target.value) || 1)}
                        />
                    </div>
                    <div>
                        <label className="text-sm font-medium mb-2 flex items-center gap-2">
                            <ArrowUpCircle className="w-4 h-4 text-muted-foreground" />
                            Max Replicas
                        </label>
                        <Input
                            type="number" min={1} max={50}
                            value={maxReplicas}
                            onChange={e => setMaxReplicas(parseInt(e.target.value) || 1)}
                        />
                    </div>
                    <div>
                        <label className="text-sm font-medium mb-2 flex items-center gap-2">
                            <Gauge className="w-4 h-4 text-muted-foreground" />
                            CPU Threshold (%)
                        </label>
                        <Input
                            type="number" min={10} max={95}
                            value={cpuTarget}
                            onChange={e => setCpuTarget(parseInt(e.target.value) || 80)}
                        />
                        <p className="text-xs text-muted-foreground mt-1">Scale up when CPU exceeds this</p>
                    </div>
                </div>
            </Card>

            {/* Save */}
            <div className="flex justify-end">
                <Button onClick={handleSave} disabled={saving} className="gap-2">
                    {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                    {saving ? 'Saving...' : 'Save Resources'}
                </Button>
            </div>
        </div>
    );
}
