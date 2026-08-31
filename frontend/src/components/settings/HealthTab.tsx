'use client';

import React, { useState, useEffect, useRef } from 'react';
import { servicesApi, Service } from '@/lib/api';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { 
    HeartPulse, Save, Loader2, RefreshCw, 
    CheckCircle2, XCircle, AlertCircle, Clock 
} from 'lucide-react';
import { toast } from '@/components/ui/use-toast';

interface HealthTabProps {
    serviceId: string;
    service?: Service;
}

const STATUS_CONFIG: Record<string, { icon: any; color: string; label: string }> = {
    healthy: { icon: CheckCircle2, color: 'text-emerald-500', label: 'Healthy' },
    unhealthy: { icon: XCircle, color: 'text-red-500', label: 'Unhealthy' },
    starting: { icon: Clock, color: 'text-yellow-500', label: 'Starting' },
    unknown: { icon: AlertCircle, color: 'text-muted-foreground', label: 'Unknown' },
};

export function HealthTab({ serviceId, service: initialService }: HealthTabProps) {
    const [loading, setLoading] = useState(!initialService);
    const [saving, setSaving] = useState(false);
    const [rechecking, setRechecking] = useState(false);
    const [healthPath, setHealthPath] = useState('/health');
    const [healthPort, setHealthPort] = useState<number | ''>('');
    const [interval, setInterval_] = useState(30);
    const [timeout, setTimeout_] = useState(5);
    const [retries, setRetries] = useState(3);
    const [autoRestart, setAutoRestart] = useState(true);
    const [autoRollbackEnabled, setAutoRollbackEnabled] = useState(true);
    const [healthStatus, setHealthStatus] = useState('unknown');
    // True once the user has modified any form field. While dirty, the
    // 3-second parent poll (which passes a fresh `service` object each
    // tick) must NOT overwrite what the user is typing — that was the
    // "stubborn inputs" bug: every poll re-seeded the form and reverted
    // edits mid-keystroke. Only the live status badge keeps updating.
    const dirtyRef = useRef(false);

    const applyService = (s: any, force = false) => {
        if (!force && dirtyRef.current) {
            // Keep the user's edits; only refresh the status badge.
            setHealthStatus(s.health_status ?? 'unknown');
            return;
        }
        setHealthPath(s.health_check_path ?? '/health');
        setHealthPort(s.health_check_port ?? '');
        setInterval_(s.health_check_interval ?? 30);
        setTimeout_(s.health_check_timeout ?? 5);
        setRetries(s.health_check_retries ?? 3);
        setAutoRestart(s.auto_restart ?? true);
        setAutoRollbackEnabled(s.auto_rollback_enabled ?? true);
        setHealthStatus(s.health_status ?? 'unknown');
        setLoading(false);
    };

    // Seed the form ONCE on mount (or first time a service object arrives).
    // The parent re-renders every poll tick with a new object reference —
    // a `service` dep here would re-run this effect every 3 seconds and
    // fight the user's keyboard. applyService's dirty-guard also protects
    // the "fetch on mount" path for the rare case the prop arrives late.
    const seededRef = useRef<string | null>(null);
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
    }, [serviceId, initialService?.id]);

    const markDirty = () => { dirtyRef.current = true; };

    const handleSave = async () => {
        setSaving(true);
        try {
            await servicesApi.update(serviceId, {
                health_check_path: healthPath,
                health_check_port: healthPort === '' ? null : healthPort,
                health_check_interval: interval,
                health_check_timeout: timeout,
                health_check_retries: retries,
                auto_restart: autoRestart,
                auto_rollback_enabled: autoRollbackEnabled,
            } as any);
            dirtyRef.current = false;
            toast({ title: '✓ Health check settings saved' });
        } catch (err) {
            toast({ title: 'Failed to save', variant: 'destructive' });
        } finally { setSaving(false); }
    };

    const handleRefresh = async () => {
        try {
            const s = await servicesApi.get(serviceId);
            applyService(s, true);
            dirtyRef.current = false;
            toast({ title: 'Health status refreshed' });
        } catch (err) {
            toast({ title: 'Failed to refresh', variant: 'destructive' });
        }
    };

    const handleRecheck = async () => {
        setRechecking(true);
        try {
            await servicesApi.recheckHealth(serviceId, true);
            const s = await servicesApi.get(serviceId);
            applyService(s);
            toast({
                title: 'Recheck triggered',
                description: 'Health checks and restart backoff were reset for this service.',
            });
        } catch (err) {
            toast({ title: 'Recheck failed', variant: 'destructive' });
        } finally {
            setRechecking(false);
        }
    };

    if (loading) return <div className="p-8 text-center text-muted-foreground">Loading health settings...</div>;

    const statusConfig = STATUS_CONFIG[healthStatus] || STATUS_CONFIG.unknown;
    const StatusIcon = statusConfig.icon;

    return (
        <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4">
            {/* Health Status Card */}
            <Card className="p-6 border-border shadow-md">
                <div className="flex items-center justify-between mb-6">
                    <div className="flex items-center gap-3">
                        <div className={`p-3 rounded-xl ${
                            healthStatus === 'healthy' ? 'bg-emerald-500/10' :
                            healthStatus === 'unhealthy' ? 'bg-red-500/10' : 'bg-muted'
                        }`}>
                            <StatusIcon className={`w-8 h-8 ${statusConfig.color}`} />
                        </div>
                        <div>
                            <h3 className="font-bold text-lg">Service Health</h3>
                            <p className={`text-sm font-medium ${statusConfig.color}`}>
                                {statusConfig.label}
                            </p>
                        </div>
                    </div>
                    <div className="flex items-center gap-2">
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={handleRecheck}
                        disabled={rechecking}
                        className="gap-2"
                    >
                        <RefreshCw className={`w-4 h-4 ${rechecking ? 'animate-spin' : ''}`} />
                        {rechecking ? 'Rechecking...' : 'Recheck Now'}
                    </Button>
                    <Button variant="outline" size="sm" onClick={handleRefresh} className="gap-2">
                        <RefreshCw className="w-4 h-4" />
                        Refresh
                    </Button>
                    </div>
                </div>

                {healthStatus === 'unhealthy' && (
                    <div className="bg-red-500/10 border border-red-500/20 rounded-lg p-4 text-sm text-red-400">
                        <strong>Service is unhealthy.</strong> {autoRestart
                            ? 'Auto-restart is enabled and will attempt recovery.'
                            : 'Consider enabling auto-restart for automatic recovery.'}
                    </div>
                )}
            </Card>

            {/* Health Check Configuration */}
            <Card className="p-6 border-border shadow-md">
                <div className="flex items-center gap-2 mb-6">
                    <HeartPulse className="w-5 h-5 text-pink-500" />
                    <h3 className="font-bold text-lg">Health Check Configuration</h3>
                </div>

                <div className="space-y-6">
                    <div>
                        <label className="text-sm font-medium mb-2 block">Health Check Path</label>
                        <Input
                            value={healthPath}
                            onChange={e => { markDirty(); setHealthPath(e.target.value); }}
                            placeholder="/health"
                            className="font-mono max-w-md"
                        />
                        <p className="text-xs text-muted-foreground mt-1">
                            HTTP endpoint that returns 2xx when the service is healthy
                        </p>
                    </div>

                    <div>
                        <label className="text-sm font-medium mb-2 block">Health Check Port</label>
                        <Input
                            type="number"
                            min={1}
                            max={65535}
                            value={healthPort}
                            onChange={e => { markDirty(); setHealthPort(e.target.value ? parseInt(e.target.value) : ''); }}
                            placeholder="Auto-detect from PORT env var"
                            className="font-mono max-w-md"
                        />
                        <p className="text-xs text-muted-foreground mt-1">
                            Port your app listens on (e.g. 3000 for Next.js, 8000 for Django). Leave blank to use the PORT variable.
                        </p>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                        <div>
                            <label className="text-sm font-medium mb-2 block">Interval (seconds)</label>
                            <Input
                                type="number" min={5} max={300}
                                value={interval}
                                onChange={e => { markDirty(); setInterval_(parseInt(e.target.value) || 30); }}
                            />
                            <p className="text-xs text-muted-foreground mt-1">Time between checks</p>
                        </div>
                        <div>
                            <label className="text-sm font-medium mb-2 block">Timeout (seconds)</label>
                            <Input
                                type="number" min={1} max={30}
                                value={timeout}
                                onChange={e => { markDirty(); setTimeout_(parseInt(e.target.value) || 5); }}
                            />
                            <p className="text-xs text-muted-foreground mt-1">Max response wait</p>
                        </div>
                        <div>
                            <label className="text-sm font-medium mb-2 block">Retries</label>
                            <Input
                                type="number" min={1} max={10}
                                value={retries}
                                onChange={e => { markDirty(); setRetries(parseInt(e.target.value) || 3); }}
                            />
                            <p className="text-xs text-muted-foreground mt-1">Failures before unhealthy</p>
                        </div>
                    </div>

                    {/* Auto-Restart Toggle */}
                    <div className="flex items-center justify-between p-4 bg-muted/30 rounded-lg border border-border">
                        <div>
                            <label className="text-sm font-medium">Auto-Restart</label>
                            <p className="text-xs text-muted-foreground">
                                Automatically restart the container when health checks fail
                            </p>
                        </div>
                        <button
                            onClick={() => { markDirty(); setAutoRestart(!autoRestart); }}
                            className={`relative w-12 h-6 rounded-full transition-colors ${
                                autoRestart ? 'bg-emerald-500' : 'bg-muted-foreground/30'
                            }`}
                        >
                            <span className={`absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform ${
                                autoRestart ? 'left-6' : 'left-0.5'
                            }`} />
                        </button>
                    </div>

                    {/* Auto-Rollback Toggle */}
                    <div className="flex items-center justify-between p-4 bg-muted/30 rounded-lg border border-border">
                        <div>
                            <label className="text-sm font-medium">Auto-Rollback</label>
                            <p className="text-xs text-muted-foreground">
                                Automatically roll back to the last successful deployment after repeated failures
                            </p>
                        </div>
                        <button
                            onClick={() => { markDirty(); setAutoRollbackEnabled(!autoRollbackEnabled); }}
                            className={`relative w-12 h-6 rounded-full transition-colors ${
                                autoRollbackEnabled ? 'bg-emerald-500' : 'bg-muted-foreground/30'
                            }`}
                        >
                            <span className={`absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform ${
                                autoRollbackEnabled ? 'left-6' : 'left-0.5'
                            }`} />
                        </button>
                    </div>
                </div>
            </Card>

            {/* Save */}
            <div className="flex justify-end">
                <Button onClick={handleSave} disabled={saving} className="gap-2">
                    {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                    {saving ? 'Saving...' : 'Save Health Settings'}
                </Button>
            </div>
        </div>
    );
}
