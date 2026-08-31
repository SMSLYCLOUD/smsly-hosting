'use client';

import React, { useState, useEffect, useRef } from 'react';
import { servicesApi, systemApi, Service } from '@/lib/api';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Cpu, HardDrive, Scaling, Save, Loader2, Gauge, ArrowUpCircle, ArrowDownCircle, AlertTriangle } from 'lucide-react';
import { toast } from '@/components/ui/use-toast';

interface ResourcesTabProps {
    serviceId: string;
    service?: Service;
}

export function ResourcesTab({ serviceId, service: initialService }: ResourcesTabProps) {
    const [service, setService] = useState<any>(initialService || null);
    const [hostResources, setHostResources] = useState<{cpu_cores: number; ram_mb: number; swap_mb: number} | null>(null);
    const [loading, setLoading] = useState(!initialService);
    const [saving, setSaving] = useState(false);
    const [cpu, setCpu] = useState(0.5);
    const [memory, setMemory] = useState(512);
    const [minReplicas, setMinReplicas] = useState(1);
    const [maxReplicas, setMaxReplicas] = useState(1);
    const [cpuTarget, setCpuTarget] = useState(80);
    // Dirty-form guard: the parent polls the service every 3s and passes
    // a NEW object reference each tick. Re-seeding the form from that
    // prop fought the user's keyboard ("stubborn inputs" — sliders and
    // fields snapping back mid-edit). Seed once, then leave the form
    // alone until Save or an explicit refresh lands.
    const dirtyRef = useRef(false);
    const seededRef = useRef<string | null>(null);

    useEffect(() => {
        (async () => {
            try {
                const [s, hr] = await Promise.all([
                    initialService ? Promise.resolve(initialService) : servicesApi.get(serviceId),
                    systemApi.resources()
                ]);

                if (!initialService) setService(s);
                setHostResources(hr);

                const key = s.id || serviceId;
                const shouldSeed = seededRef.current !== key;
                seededRef.current = key;
                if (shouldSeed || !dirtyRef.current) {
                    setCpu(s.cpu_cores ?? 0.5);
                    setMemory(s.memory_mb ?? 512);
                    setMinReplicas((s as any).min_replicas ?? 1);
                    setMaxReplicas((s as any).max_replicas ?? 1);
                    setCpuTarget((s as any).autoscale_cpu_target ?? 80);
                }
            } catch (err) { console.error(err); }
            finally { setLoading(false); }
        })();
    // Re-run on service id changes only — NOT on the polled object
    // reference. hostResources is fetched once per mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [serviceId, initialService?.id]);

    const markDirty = () => { dirtyRef.current = true; };

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
            dirtyRef.current = false;
            toast({ title: '✓ Resources updated', description: 'Redeploy to apply changes.' });
        } catch (err) {
            toast({ title: 'Failed to save', variant: 'destructive' });
        } finally { setSaving(false); }
    };

    if (loading || !hostResources) return <div className="p-8 text-center text-muted-foreground">Loading resources...</div>;

    const maxCpu = hostResources.cpu_cores;
    const maxRam = hostResources.ram_mb;
    const maxTotalMem = hostResources.ram_mb + hostResources.swap_mb;

    const cpuPresets = [0.25, 0.5, 1, 2, 4, 8, 16, 32, 64].filter(v => v <= maxCpu);
    const memPresets = [256, 512, 1024, 2048, 4096, 8192, 16384, 32768, 65536].filter(v => v <= maxTotalMem);
    
    // Always ensure the selected value is at least visually represented if it was an odd size
    if (!cpuPresets.includes(maxCpu) && maxCpu >= 0.25) cpuPresets.push(maxCpu);
    if (!memPresets.includes(maxTotalMem) && maxTotalMem >= 256) memPresets.push(maxTotalMem);
    
    cpuPresets.sort((a, b) => a - b);
    memPresets.sort((a, b) => a - b);

    const monthlyEstimate = (cpu * 0.04 + (memory / 1024) * 0.02) * 730;
    
    const isUsingSwap = memory > maxRam;
    const swapUsed = isUsingSwap ? memory - maxRam : 0;

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
                                    onClick={() => { markDirty(); setCpu(v); }}
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
                            type="range" min="0.25" max={maxCpu} step="0.25"
                            value={cpu} onChange={e => { markDirty(); setCpu(parseFloat(e.target.value)); }}
                            className="w-full mt-3 accent-blue-500"
                        />
                        <div className="flex justify-between text-xs text-muted-foreground mt-1">
                            <span>0.25 vCPU</span>
                            <span>{maxCpu} vCPU (Node Max)</span>
                        </div>
                    </div>

                    {/* Memory */}
                    <div>
                        <label className="text-sm font-medium mb-3 block">
                            Memory <span className="text-muted-foreground">({memory >= 1024 ? `${(memory/1024).toFixed(1)} GB` : `${memory} MB`})</span>
                        </label>
                        <div className="flex gap-2 flex-wrap">
                            {memPresets.map(v => (
                                <button
                                    key={v}
                                    onClick={() => { markDirty(); setMemory(v); }}
                                    className={`px-4 py-2 rounded-lg text-sm font-medium border transition-all ${
                                        memory === v
                                            ? 'bg-purple-500 text-white border-purple-500 shadow-sm'
                                            : 'border-border hover:border-purple-500/50 hover:bg-purple-500/5'
                                    }`}
                                >
                                    {v >= 1024 ? `${(v/1024).toFixed(1).replace('.0', '')} GB` : `${v} MB`}
                                </button>
                            ))}
                        </div>
                        <input
                            type="range" min="128" max={maxTotalMem} step="128"
                            value={memory} onChange={e => { markDirty(); setMemory(parseInt(e.target.value)); }}
                            className={`w-full mt-3 ${isUsingSwap ? 'accent-amber-500' : 'accent-purple-500'}`}
                        />
                        <div className="flex justify-between text-xs text-muted-foreground mt-1">
                            <span>128 MB</span>
                            <span>{maxTotalMem >= 1024 ? `${(maxTotalMem/1024).toFixed(1)} GB` : `${maxTotalMem} MB`} (Node Max)</span>
                        </div>

                        {isUsingSwap && (
                            <div className="mt-3 bg-amber-500/10 border border-amber-500/20 rounded-md p-3 flex items-start gap-3">
                                <AlertTriangle className="w-5 h-5 text-amber-500 shrink-0 mt-0.5" />
                                <div className="text-sm text-amber-600 dark:text-amber-400">
                                    <p className="font-semibold">Swap Memory In Use</p>
                                    <p className="mt-0.5 opacity-90">
                                        This configuration uses <strong>{swapUsed >= 1024 ? `${(swapUsed/1024).toFixed(1)} GB` : `${swapUsed} MB`}</strong> of disk swap because it exceeds the node&apos;s physical RAM ({maxRam >= 1024 ? `${(maxRam/1024).toFixed(1)} GB` : `${maxRam} MB`}). Notice: Swap is significantly slower than physical RAM.
                                    </p>
                                </div>
                            </div>
                        )}
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
                            onChange={e => { markDirty(); setMinReplicas(parseInt(e.target.value) || 1); }}
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
                            onChange={e => { markDirty(); setMaxReplicas(parseInt(e.target.value) || 1); }}
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
                            onChange={e => { markDirty(); setCpuTarget(parseInt(e.target.value) || 80); }}
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
