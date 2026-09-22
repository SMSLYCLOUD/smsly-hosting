'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Scaling, Activity, Cpu, Server, Layers, Radio, AlertTriangle,
  Zap, Clock, ArrowRight, Settings, RotateCw, Play, CheckCircle2,
  AlertCircle, ChevronDown, ChevronUp, Save, Bell, BellRing, Mail,
  Webhook, HardDrive, ShieldAlert, TrendingUp, Plus, Minus, Trash2,
  Inbox, ShieldCheck, Box, RefreshCw, Power, Timer, Loader2
} from 'lucide-react';
import {
  LineChart, Line, AreaChart, Area, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend
} from 'recharts';
import { ChartContainer } from '@/components/ui/chart-container';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { autoscalerApi, scalingApi, servicesApi, type Service, type AutoscalerStatus, type AutoscalerHistory, type AutoscalerService, type AutoscalerServiceReplica } from '@/lib/api';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Slider } from '@/components/ui/slider';
import { useConfirm } from '@/components/ui/confirm-dialog';
import { Switch } from '@/components/ui/switch';
import { useToast } from '@/components/ui/use-toast';
import { cn } from '@/lib/utils';
import { RequiresTier } from '@/components/licensing/RequiresTier';

// ─── Components ─────────────────────────────────────────────────────────────

function GaugeRing({ value, color, size = 56, strokeWidth = 4 }: { value: number; color: string; size?: number; strokeWidth?: number }) {
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (value / 100) * circumference;

  return (
    <div className="relative flex items-center justify-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="transform -rotate-90">
        <circle
          cx={size/2} cy={size/2} r={radius}
          fill="none" stroke="currentColor" strokeWidth={strokeWidth}
          className="text-muted/20"
        />
        <circle
          cx={size/2} cy={size/2} r={radius}
          fill="none" stroke={color} strokeWidth={strokeWidth}
          strokeDasharray={circumference} strokeDashoffset={offset} strokeLinecap="round"
          className="transition-all duration-700 ease-out"
        />
      </svg>
      <div className="absolute inset-0 flex items-center justify-center text-[10px] font-bold">
        {Math.round(value)}%
      </div>
    </div>
  );
}

function ServiceIcon({ type }: { type: string }) {
  if (type === 'gunicorn') return <Server className="text-blue-500" size={18} />;
  if (type === 'celery') return <Layers className="text-purple-500" size={18} />;
  if (type === 'daphne') return <Radio className="text-emerald-500" size={18} />;
  return <Activity className="text-zinc-500" size={18} />;
}

// ─── Page Component ─────────────────────────────────────────────────────────

export default function AutoscalerPage() {
  const [status, setStatus] = useState<AutoscalerStatus | null>(null);
  const [history, setHistory] = useState<AutoscalerHistory | null>(null);
  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [historyDuration, setHistoryDuration] = useState(60); // minutes
  const [configOpen, setConfigOpen] = useState(false);
  const [localConfig, setLocalConfig] = useState<any>(null);
  const { toast } = useToast();
  const confirm = useConfirm();

  // Alert thresholds
  const [alertConfig, setAlertConfig] = useState({
    cpu_warning: 70,
    cpu_critical: 90,
    memory_warning: 75,
    memory_critical: 90,
    disk_warning: 80,
    disk_critical: 95,
    notify_email: true,
    notify_webhook: false,
    webhook_url: '',
  });
  const [alertsOpen, setAlertsOpen] = useState(false);
  const [savingAlertConfig, setSavingAlertConfig] = useState(false);
  const [servicesList, setServicesList] = useState<Service[]>([]);
  const [togglingService, setTogglingService] = useState<string | null>(null);
  const [scalingServiceId, setScalingServiceId] = useState<string | null>(null);
  const [destroyingReplicaId, setDestroyingReplicaId] = useState<string | null>(null);
  const [expandedReplicas, setExpandedReplicas] = useState<Record<string, boolean>>({});
  const [powerBusy, setPowerBusy] = useState<string | null>(null);
  const [timerMinutes, setTimerMinutes] = useState(30);
  const [pendingTimer, setPendingTimer] = useState<{ task_id: string; fires_at: string; actor: string } | null>(null);

  const handleManualScaleUp = async (serviceId: string, svcName: string) => {
    setScalingServiceId(serviceId);
    try {
      await scalingApi.spawnReplica(serviceId, 'horizontal');
      toast({ title: "Scale Up Spawned", description: `Provisioning +1 replica for ${svcName}` });
      await fetchData();
    } catch (err: any) {
      toast({
        title: "Scale Up Failed",
        description: err?.response?.data?.error || err.message || "Failed to spawn replica",
        variant: "destructive"
      });
    } finally {
      setScalingServiceId(null);
    }
  };

  const handleManualScaleDown = async (serviceId: string, svcName: string) => {
    setScalingServiceId(serviceId);
    try {
      await scalingApi.scaleDown(serviceId, 1);
      toast({ title: "Scale Down Triggered", description: `Reconciled -1 replica for ${svcName}` });
      await fetchData();
    } catch (err: any) {
      toast({
        title: "Scale Down Failed",
        description: err?.response?.data?.error || err.message || "Failed to scale down replica",
        variant: "destructive"
      });
    } finally {
      setScalingServiceId(null);
    }
  };

  const handleDestroyReplica = async (replicaId: string, containerName?: string | null) => {
    setDestroyingReplicaId(replicaId);
    try {
      await scalingApi.destroyReplica(replicaId);
      toast({ title: "Replica Removed", description: `Replica ${containerName || replicaId.slice(0, 8)} removed` });
      await fetchData();
    } catch (err: any) {
      toast({
        title: "Removal Failed",
        description: err?.response?.data?.error || err.message || "Failed to remove replica",
        variant: "destructive"
      });
    } finally {
      setDestroyingReplicaId(null);
    }
  };

  const refreshTimer = async () => {
    try {
      const res = await servicesApi.autoOffGet();
      setPendingTimer(res.pending);
    } catch { /* timer state is best-effort */ }
  };

  const handleBulkPower = async (op: 'stop' | 'start' | 'restart') => {
    const labels = {
      stop: { title: 'Power off ALL services?', message: 'Stops every ACTIVE tenant service container now. Platform infra is untouched. Use Power On to bring them back.', confirm: 'Power Off All' },
      start: { title: 'Power on all stopped services?', message: 'Starts every STOPPED service container.', confirm: 'Power On All' },
      restart: { title: 'Restart ALL services?', message: 'Restarts every ACTIVE service sequentially (~3s apart to avoid a CPU spike).', confirm: 'Restart All' },
    } as const;
    if (!await confirm({ title: labels[op].title, message: labels[op].message, confirmText: labels[op].confirm, variant: op === 'stop' ? 'destructive' : 'default' })) return;
    setPowerBusy(op);
    try {
      await servicesApi.bulkPower(op);
      toast({ title: `${labels[op].confirm} queued`, description: 'Sweep running in background — watch the grid.' });
    } catch (err: any) {
      toast({
        title: 'Bulk power failed',
        description: err?.response?.status === 403 ? 'Admin access required.' : err?.response?.data?.error || err.message,
        variant: 'destructive',
      });
    } finally {
      setPowerBusy(null);
    }
  };

  const handleScheduleTimer = async () => {
    const minutes = Math.max(1, Math.min(1440, Math.floor(timerMinutes) || 30));
    if (!await confirm({ title: 'Schedule auto power-off?', message: `All ACTIVE services will power off in ${minutes} minute(s).`, confirmText: 'Schedule', variant: 'destructive' })) return;
    setPowerBusy('timer');
    try {
      const res = await servicesApi.autoOffSchedule(minutes);
      setPendingTimer({ ...res.scheduled, actor: '' });
      toast({ title: 'Auto-off scheduled', description: `Fires at ${new Date(res.scheduled.fires_at).toLocaleString()}` });
    } catch (err: any) {
      toast({
        title: 'Schedule failed',
        description: err?.response?.status === 403 ? 'Admin access required.' : err?.response?.data?.error || err.message,
        variant: 'destructive',
      });
    } finally {
      setPowerBusy(null);
    }
  };

  const handleCancelTimer = async () => {
    setPowerBusy('timer');
    try {
      await servicesApi.autoOffCancel();
      setPendingTimer(null);
      toast({ title: 'Auto-off cancelled' });
    } catch (err: any) {
      toast({ title: 'Cancel failed', description: err?.response?.data?.error || err.message, variant: 'destructive' });
    } finally {
      setPowerBusy(null);
    }
  };

  const toggleReplicaDrawer = (serviceKey: string) => {
    setExpandedReplicas(prev => ({ ...prev, [serviceKey]: !prev[serviceKey] }));
  };

  const getCooldownInfo = (lastScaleAt: string | null | undefined, cooldownDownMin: number = 10) => {
    if (!lastScaleAt) return { inCooldown: false, remainingMin: 0, text: "Ready" };
    const lastTime = new Date(lastScaleAt).getTime();
    const now = Date.now();
    const elapsedMinutes = (now - lastTime) / 60000;
    if (elapsedMinutes < cooldownDownMin) {
      const remaining = Math.max(1, Math.ceil(cooldownDownMin - elapsedMinutes));
      return { inCooldown: true, remainingMin: remaining, text: `Cooldown: ${remaining}m left` };
    }
    return { inCooldown: false, remainingMin: 0, text: "Ready" };
  };

  const fetchData = useCallback(async () => {
    try {
      const [s, h, svcList] = await Promise.all([
        autoscalerApi.getStatus(),
        autoscalerApi.getHistory(historyDuration),
        servicesApi.list(),
      ]);
      setStatus(s);
      setHistory(h);
      setServicesList(svcList);
      refreshTimer().catch(() => {});

      // Initialize local config from status if not edited
      if (!localConfig && s) {
        setLocalConfig({
          total_system_mb: s.budget.total_system_mb,
          infra_reserve_mb: s.budget.infra_reserve_mb,
          check_interval: s.check_interval,
          services: Object.entries(s.services).reduce((acc, [name, svc]) => ({
            ...acc,
            [name]: {
              priority: svc.priority,
              min_workers: svc.min_workers,
              max_workers: svc.max_workers
            }
          }), {})
        });
      }
    } catch (err: unknown) {
      // Silently handle 503 (service not installed) — show offline state
      const is503 = (err as { response?: { status?: number } })?.response?.status === 503;
      if (is503) {
        setOffline(true);
        setAutoRefresh(false); // Stop polling when offline
      } else {
        console.error('Autoscaler fetch failed:', err);
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [historyDuration, localConfig]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  useEffect(() => {
    if (!autoRefresh) return;
    const interval = setInterval(fetchData, 10000); // 10s poll
    return () => clearInterval(interval);
  }, [autoRefresh, fetchData]);

  const handleTrigger = async () => {
    setRefreshing(true);
    try {
      await autoscalerApi.trigger();
      await fetchData();
      toast({ title: "Check Triggered", description: "Autoscaler cycle executed successfully." });
    } catch (err) {
      toast({ title: "Trigger Failed", description: "Could not force autoscaler check.", variant: "destructive" });
    } finally {
      setRefreshing(false);
    }
  };

  const handleSaveConfig = async () => {
    try {
      await autoscalerApi.updateConfig(localConfig);
      toast({ title: "Configuration Saved", description: "Autoscaler settings updated." });
      setConfigOpen(false);
      fetchData();
    } catch (err) {
      toast({ title: "Save Failed", description: "Could not update configuration.", variant: "destructive" });
    }
  };

  const handleToggleAutoscale = async (serviceId: string, current: boolean | undefined) => {
    const next = current === false ? true : false;
    setTogglingService(serviceId);
    try {
      await servicesApi.update(serviceId, { autoscale_enabled: next } as any);
      setServicesList(prev => prev.map(s => s.id === serviceId ? { ...s, autoscale_enabled: next } : s));
      // The switch reads autoscale_enabled from the autoscaler status map,
      // not servicesList — patch it too or the toggle visibly snaps back
      // until the next status poll.
      setStatus(prev => {
        if (!prev) return prev;
        const services = { ...prev.services };
        for (const [key, entry] of Object.entries(services)) {
          if ((entry as any).service_id === serviceId) {
            services[key] = { ...entry, autoscale_enabled: next };
            break;
          }
        }
        return { ...prev, services };
      });
      toast({ title: next ? 'Auto-scaling enabled' : 'Auto-scaling disabled' });
    } catch (err) {
      toast({ title: 'Failed to toggle auto-scaling', variant: 'destructive' });
    } finally {
      setTogglingService(null);
    }
  };

  if (loading && !status) {
    return (
      <DashboardShell>
        <div className="flex-1 flex items-center justify-center">
          <RotateCw className="animate-spin text-muted-foreground" size={32} />
        </div>
      </DashboardShell>
    );
  }

  if (offline && !status) {
    return (
      <DashboardShell>
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center space-y-4 max-w-md">
            <div className="p-4 bg-amber-500/10 rounded-full w-fit mx-auto">
              <AlertTriangle className="h-10 w-10 text-amber-500" />
            </div>
            <h2 className="text-xl font-bold">Autoscaler Not Installed</h2>
            <p className="text-muted-foreground text-sm">
              The autoscaler service is not running on this VPS. It&apos;s an optional systemd service
              that automatically adjusts worker counts based on resource usage.
            </p>
            <Button variant="outline" onClick={() => { setOffline(false); setLoading(true); fetchData(); }}>
              <RotateCw className="mr-2 h-4 w-4" /> Retry Connection
            </Button>
          </div>
        </div>
      </DashboardShell>
    );
  }

  // Derived metrics
  const usedPercent = status ? (status.budget.used_mb / status.budget.total_system_mb) * 100 : 0;
  const cpuPercent = status?.host?.cpu_percent ?? 0;
  const budgetColor = usedPercent > 80 ? '#ef4444' : usedPercent > 60 ? '#f59e0b' : '#10b981';
  const cpuColor = cpuPercent > 85 ? '#ef4444' : cpuPercent > 60 ? '#f59e0b' : '#10b981';

  // Group services by app
  const groupedServices: Record<string, [string, AutoscalerService][]> = {};
  if (status) {
    Object.entries(status.services).forEach(([name, svc]) => {
      const app = svc.app || 'other';
      if (!groupedServices[app]) groupedServices[app] = [];
      groupedServices[app].push([name, svc]);
    });
  }

  // Chart data preparation
  const chartData = history?.timestamps.map((ts, i) => {
    const point: Record<string, string | number> = { timestamp: new Date(ts).toLocaleTimeString() };
    Object.keys(history.services).forEach(svc => {
      point[`${svc}_mem`] = history.services[svc].memory_mb[i];
      point[`${svc}_demand`] = history.services[svc].demand_score[i];
    });
    return point;
  }) || [];

  return (
    <DashboardShell>
      <RequiresTier tier="pro">
      <div className="flex-1 p-8 relative z-10 space-y-8 max-w-7xl mx-auto">

        {/* ── Header ───────────────────────────────────────────────────────── */}
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div>
            <h1 className="text-3xl font-bold tracking-tight flex items-center gap-3">
              <div className="p-2 bg-gradient-to-br from-blue-500 to-cyan-600 rounded-xl shadow-lg shadow-blue-500/20">
                <Scaling className="text-white h-6 w-6" />
              </div>
              VPS Autoscaler
            </h1>
            <p className="text-muted-foreground mt-1 text-sm">
              Cross-service resource manager — balancing {Object.keys(status?.services || {}).length} services on {(status?.budget.total_system_mb || 0) / 1024}GB VPS
            </p>
          </div>

          <div className="flex items-center gap-3">
             <div className="flex items-center gap-2 px-3 py-1.5 bg-card border border-border rounded-lg text-xs font-medium">
               <span>Auto-refresh</span>
               <Switch checked={autoRefresh} onCheckedChange={setAutoRefresh} className="scale-75" />
             </div>
             <Button
               variant="outline"
               size="sm"
               onClick={handleTrigger}
               disabled={refreshing}
               className="gap-2"
             >
               {refreshing ? <RotateCw className="animate-spin h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
               Force Check
             </Button>
              <div className={cn(
                "px-2.5 py-1 rounded-full text-xs font-bold uppercase tracking-wide flex items-center gap-1.5",
                !status ? "bg-red-500/10 text-red-500"
                  : status.status === "degraded" ? "bg-amber-500/10 text-amber-500"
                  : "bg-emerald-500/10 text-emerald-500"
              )}>
                <div className={cn(
                  "w-1.5 h-1.5 rounded-full",
                  !status ? "bg-red-500"
                    : status.status === "degraded" ? "bg-amber-500 animate-pulse"
                    : "bg-emerald-500 animate-pulse"
                )} />
                {!status ? "Offline" : status.status === "degraded" ? "Degraded" : "Running"}
              </div>
          </div>
        </div>

        {status?._stale && (
          <div className="flex items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 px-4 py-2.5 text-xs text-amber-200">
            <AlertTriangle size={14} className="shrink-0 text-amber-500" />
            <span>Showing last known data — the live check timed out (daemon under pressure). Numbers may be stale; Force Check retries.</span>
          </div>
        )}

        {/* ── Emergency Power ──────────────────────────────────────────── */}
        <Card className="border-red-500/30 bg-gradient-to-b from-red-500/5 to-card/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground uppercase tracking-widest flex items-center gap-2">
              <Power size={14} className="text-red-400" /> Emergency Power
            </CardTitle>
            <CardDescription>Stop, start, or restart every tenant service at once. Platform infra is never touched. Bulk runs async in the background.</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col md:flex-row md:items-center gap-3">
            <div className="flex gap-2 flex-wrap">
              <Button variant="destructive" size="sm" disabled={powerBusy !== null} onClick={() => handleBulkPower('stop')} className="gap-2">
                {powerBusy === 'stop' ? <Loader2 size={14} className="animate-spin" /> : <Power size={14} />} Power Off All
              </Button>
              <Button variant="outline" size="sm" disabled={powerBusy !== null} onClick={() => handleBulkPower('start')} className="gap-2">
                {powerBusy === 'start' ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />} Power On All
              </Button>
              <Button variant="outline" size="sm" disabled={powerBusy !== null} onClick={() => handleBulkPower('restart')} className="gap-2">
                {powerBusy === 'restart' ? <Loader2 size={14} className="animate-spin" /> : <RotateCw size={14} />} Restart All
              </Button>
            </div>
            <div className="flex items-center gap-2 md:ml-auto">
              <Timer size={14} className="text-muted-foreground" />
              {pendingTimer ? (
                <>
                  <span className="text-xs text-amber-400">
                    Auto-off fires {new Date(pendingTimer.fires_at).toLocaleString()}
                  </span>
                  <Button variant="ghost" size="sm" disabled={powerBusy !== null} onClick={handleCancelTimer}>Cancel</Button>
                </>
              ) : (
                <>
                  <Input
                    type="number" min={1} max={1440}
                    value={timerMinutes}
                    onChange={(e) => setTimerMinutes(parseInt(e.target.value) || 30)}
                    className="w-20 h-8 text-xs"
                  />
                  <span className="text-xs text-muted-foreground">min</span>
                  <Button variant="outline" size="sm" disabled={powerBusy !== null} onClick={handleScheduleTimer} className="gap-2">
                    {powerBusy === 'timer' ? <Loader2 size={14} className="animate-spin" /> : <Clock size={14} />} Auto-Off
                  </Button>
                </>
              )}
            </div>
          </CardContent>
        </Card>

        {/* ── Hero: Memory Budget Ring ─────────────────────────────────────── */}
        <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
          <Card className="col-span-1 border-border/50 bg-gradient-to-b from-card to-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground uppercase tracking-widest flex items-center gap-2">
                <Cpu size={14} /> Global Memory Budget
              </CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col items-center justify-center py-6">
              <div className="relative">
                <svg width="220" height="220" className="transform -rotate-90">
                  {/* Background Track */}
                  <circle cx="110" cy="110" r="90" fill="none" stroke="currentColor" strokeWidth="12" className="text-muted/10" />
                  {/* Value Arc */}
                  <circle
                    cx="110" cy="110" r="90"
                    fill="none" stroke={budgetColor} strokeWidth="12"
                    strokeDasharray={2 * Math.PI * 90}
                    strokeDashoffset={(2 * Math.PI * 90) * (1 - usedPercent / 100)}
                    strokeLinecap="round"
                    className="transition-all duration-1000 ease-out"
                  />
                </svg>
                <div className="absolute inset-0 flex flex-col items-center justify-center">
                  <span className="text-3xl font-bold tracking-tight">
                    {(status?.budget.used_mb || 0) > 1024
                      ? `${((status?.budget.used_mb || 0) / 1024).toFixed(1)}GB`
                      : `${Math.round(status?.budget.used_mb || 0)}MB`}
                  </span>
                  <span className="text-xs text-muted-foreground mt-1">
                    of {((status?.budget.total_system_mb || 0) / 1024).toFixed(1)}GB used
                  </span>
                </div>
              </div>

              <div className="flex items-center gap-6 mt-6 text-xs text-muted-foreground">
                <div className="flex items-center gap-2">
                   <div className="w-2 h-2 rounded-full bg-slate-500" />
                   Infra Reserved: {((status?.budget.infra_reserve_mb || 0) / 1024).toFixed(1)}GB
                </div>
                <div className="flex items-center gap-2">
                   <div className="w-2 h-2 rounded-full bg-emerald-500" />
                   Free: {((status?.budget.free_mb || 0) / 1024).toFixed(1)}GB
                </div>
              </div>
            </CardContent>
          </Card>

          {/* ── Hero: Host CPU Ring ──────────────────────────────────────────── */}
          <Card className="col-span-1 border-border/50 bg-gradient-to-b from-card to-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground uppercase tracking-widest flex items-center gap-2">
                <Cpu size={14} /> Host CPU Load
              </CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col items-center justify-center py-6">
              <div className="relative">
                <svg width="220" height="220" className="transform -rotate-90">
                  {/* Background Track */}
                  <circle cx="110" cy="110" r="90" fill="none" stroke="currentColor" strokeWidth="12" className="text-muted/10" />
                  {/* Value Arc */}
                  <circle
                    cx="110" cy="110" r="90"
                    fill="none" stroke={cpuColor} strokeWidth="12"
                    strokeDasharray={2 * Math.PI * 90}
                    strokeDashoffset={(2 * Math.PI * 90) * (1 - Math.min(cpuPercent, 100) / 100)}
                    strokeLinecap="round"
                    className="transition-all duration-1000 ease-out"
                  />
                </svg>
                <div className="absolute inset-0 flex flex-col items-center justify-center">
                  <span className="text-3xl font-bold tracking-tight">
                    {status?.host ? `${cpuPercent.toFixed(0)}%` : '—'}
                  </span>
                  <span className="text-xs text-muted-foreground mt-1">
                    {status?.host?.cpu_count ? `of ${status.host.cpu_count} cores` : 'host cpu'}
                  </span>
                </div>
              </div>

              <div className="flex items-center gap-6 mt-6 text-xs text-muted-foreground">
                <div className="flex items-center gap-2">
                   <div className="w-2 h-2 rounded-full bg-slate-500" />
                   Cores: {status?.host?.cpu_count ?? '—'}
                </div>
                <div className="flex items-center gap-2">
                   <div className="w-2 h-2 rounded-full bg-emerald-500" />
                   Idle: {status?.host ? `${Math.max(0, 100 - cpuPercent).toFixed(0)}%` : '—'}
                </div>
              </div>
            </CardContent>
          </Card>

          {/* ── Recent Decisions Timeline ──────────────────────────────────── */}
          <Card className="col-span-1 lg:col-span-2 border-border/50 h-[380px] flex flex-col">
            <CardHeader className="pb-3 border-b border-border/50">
              <CardTitle className="text-base flex items-center gap-2">
                <Activity size={16} className="text-purple-500" />
                Recent Scaling Decisions
              </CardTitle>
            </CardHeader>
            <CardContent className="flex-1 overflow-y-auto p-0 scrollbar-hide">
              <div className="divide-y divide-border/30">
                {status?.recent_decisions.length === 0 && (
                   <div className="p-8 text-center text-muted-foreground text-sm">No recent scaling actions recorded.</div>
                )}
                {status?.recent_decisions.map((decision, i) => (
                  <div key={i} className="p-4 flex items-start gap-4 hover:bg-muted/30 transition-colors">
                    <div className="text-[10px] font-mono text-muted-foreground w-16 pt-1">
                      {new Date(decision.timestamp).toLocaleTimeString([], { hour: '2-digit', minute:'2-digit', second:'2-digit' })}
                    </div>
                    <div className="flex-1 space-y-1">
                      <div className="flex items-center justify-between">
                        <span className="font-medium text-sm text-foreground">{decision.container}</span>
                        <span className={cn(
                          "text-[10px] px-2 py-0.5 rounded-full font-bold uppercase",
                          decision.action === 'scale_up' ? "bg-emerald-500/10 text-emerald-500" :
                          decision.action === 'scale_down' ? "bg-amber-500/10 text-amber-500" :
                          "bg-blue-500/10 text-blue-500"
                        )}>
                          {decision.action.replace('_', ' ')}
                        </span>
                      </div>
                      <p className="text-xs text-muted-foreground">
                        {decision.current_workers} → {decision.target_workers} workers •
                        {Math.round(decision.current_memory_mb)}MB → {Math.round(decision.target_memory_mb)}MB
                      </p>
                      <p className="text-[10px] text-zinc-500 font-mono mt-1 bg-zinc-900/50 p-1 rounded w-fit px-2">
                        {decision.reason}
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </div>

        {/* ── Infrastructure Scaling & Celery Burst Pool ─────────────────── */}
        <Card className="border-border/50 bg-gradient-to-b from-card to-card/40 overflow-hidden">
          <CardHeader className="pb-4 border-b border-border/40">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
              <div>
                <CardTitle className="text-base font-bold flex items-center gap-2">
                  <div className="p-1.5 bg-purple-500/10 text-purple-400 rounded-lg">
                    <Layers size={18} />
                  </div>
                  Infrastructure Autoscaling & Celery Burst Pool
                </CardTitle>
                <CardDescription className="text-xs mt-1">
                  Idle-minimal RabbitMQ queue monitoring, burst worker scaling (celery-fast / celery-deploy), and in-flight drain protection.
                </CardDescription>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <div className="px-2.5 py-1 rounded-md bg-muted/60 border border-border/50 text-xs font-mono flex items-center gap-1.5">
                  <Inbox size={13} className="text-blue-400" />
                  <span className="text-muted-foreground">Queue Backlog:</span>
                  <span className="font-bold text-foreground">{status?.infra?.total_queue_depth ?? 0} msgs</span>
                </div>

                {status?.infra?.scale_down_held ? (
                  <div className="px-2.5 py-1 rounded-md bg-amber-500/10 border border-amber-500/30 text-xs text-amber-400 font-medium flex items-center gap-1.5 animate-pulse">
                    <ShieldAlert size={13} />
                    <span>Scale-Down Held (Draining {status.infra.unacknowledged_burst_tasks} tasks)</span>
                  </div>
                ) : (
                  <div className="px-2.5 py-1 rounded-md bg-emerald-500/10 border border-emerald-500/30 text-xs text-emerald-400 font-medium flex items-center gap-1.5">
                    <ShieldCheck size={13} />
                    <span>Drain Safety Active</span>
                  </div>
                )}
              </div>
            </div>
          </CardHeader>

          <CardContent className="p-6 space-y-6">
            {/* Queue depths */}
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 gap-3">
              {Object.entries(status?.infra?.queues || { celery: 0, deploy: 0, fast: 0, 'media-telemetry': 0, 'media-audit': 0 }).map(([qName, count]) => (
                <div key={qName} className="p-3 rounded-lg bg-card/60 border border-border/40 space-y-1">
                  <div className="flex items-center justify-between text-[11px] text-muted-foreground font-mono">
                    <span className="truncate">{qName}</span>
                    <span className={cn(
                      "px-1.5 py-0.5 rounded text-[10px] font-bold",
                      count >= (status?.infra?.scale_up_threshold || 50) ? "bg-red-500/20 text-red-400" :
                      count > 0 ? "bg-amber-500/20 text-amber-400" : "bg-muted text-muted-foreground"
                    )}>
                      {count}
                    </span>
                  </div>
                  <div className="h-1.5 w-full bg-muted/40 rounded-full overflow-hidden">
                    <div
                      className={cn(
                        "h-full rounded-full transition-all duration-500",
                        count >= (status?.infra?.scale_up_threshold || 50) ? "bg-red-500" :
                        count > 0 ? "bg-amber-500" : "bg-emerald-500"
                      )}
                      style={{ width: `${Math.min(100, Math.max(count > 0 ? 12 : 0, (count / (status?.infra?.scale_up_threshold || 50)) * 100))}%` }}
                    />
                  </div>
                </div>
              ))}
            </div>

            {/* Threshold rules banner */}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 text-xs bg-muted/20 border border-border/40 p-3 rounded-lg text-muted-foreground">
              <div className="flex items-center gap-2">
                <TrendingUp size={14} className="text-blue-400 shrink-0" />
                <span>Scale-Up: <strong className="text-foreground">≥ {status?.infra?.scale_up_threshold || 50} msgs</strong> ({status?.infra?.scale_up_after_seconds || 60}s sustained)</span>
              </div>
              <div className="flex items-center gap-2">
                <ArrowRight size={14} className="text-amber-400 shrink-0" />
                <span>Scale-Down: <strong className="text-foreground">≤ {status?.infra?.scale_down_threshold || 5} msgs</strong> ({status?.infra?.scale_down_after_seconds || 120}s idle)</span>
              </div>
              <div className="flex items-center gap-2">
                <Clock size={14} className="text-purple-400 shrink-0" />
                <span>Poll Interval: <strong className="text-foreground">{status?.infra?.check_interval_seconds || 15}s</strong> systemd service</span>
              </div>
              <div className="flex items-center gap-2">
                <ShieldCheck size={14} className="text-emerald-400 shrink-0" />
                <span>In-Flight Drain: <strong className="text-foreground">0 unacked required</strong> before stop</span>
              </div>
            </div>

            {/* Burst Workers Grid */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {(status?.infra?.burst_workers || []).map((bw) => (
                <div key={bw.name} className="p-4 rounded-xl border border-border/50 bg-card/80 space-y-3">
                  <div className="flex items-start justify-between">
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="font-bold text-sm font-mono">{bw.name}</span>
                        <span className="text-[10px] px-2 py-0.5 rounded bg-muted font-mono text-muted-foreground">
                          queue: {bw.target_queue}
                        </span>
                      </div>
                      <p className="text-xs text-muted-foreground mt-0.5">{bw.description}</p>
                    </div>

                    <div className={cn(
                      "px-2.5 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wide flex items-center gap-1",
                      bw.status === 'running' ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20" :
                      bw.status === 'draining' ? "bg-amber-500/10 text-amber-400 border border-amber-500/20 animate-pulse" :
                      bw.status === 'pending_scale_down' ? "bg-cyan-500/10 text-cyan-400 border border-cyan-500/20" :
                      bw.status === 'busy' ? "bg-purple-500/10 text-purple-400 border border-purple-500/20" :
                      "bg-zinc-500/10 text-zinc-400 border border-zinc-500/20"
                    )}>
                      <div className={cn(
                        "w-1.5 h-1.5 rounded-full",
                        bw.status === 'running' ? "bg-emerald-500" :
                        bw.status === 'draining' ? "bg-amber-500 animate-pulse" :
                        bw.status === 'pending_scale_down' ? "bg-cyan-500" :
                        bw.status === 'busy' ? "bg-purple-500" : "bg-zinc-500"
                      )} />
                      {bw.status.replace(/_/g, ' ')}
                    </div>
                  </div>

                  {/* Worker Live Metrics */}
                  <div className="grid grid-cols-4 gap-2 py-2 px-3 rounded-lg bg-muted/30 border border-border/30 text-center font-mono">
                    <div>
                      <div className="text-xs font-bold text-foreground">{bw.active_tasks}</div>
                      <div className="text-[9px] text-muted-foreground uppercase">Active Tasks</div>
                    </div>
                    <div>
                      <div className="text-xs font-bold text-foreground">{bw.reserved_tasks}</div>
                      <div className="text-[9px] text-muted-foreground uppercase">Reserved</div>
                    </div>
                    <div>
                      <div className="text-xs font-bold text-foreground">{bw.cpu_percent.toFixed(1)}%</div>
                      <div className="text-[9px] text-muted-foreground uppercase">CPU</div>
                    </div>
                    <div>
                      <div className="text-xs font-bold text-foreground">{Math.round(bw.memory_mb)}MB</div>
                      <div className="text-[9px] text-muted-foreground uppercase">RAM</div>
                    </div>
                  </div>

                  <p className="text-[11px] text-muted-foreground bg-muted/20 p-2 rounded border border-border/30 flex items-center gap-1.5">
                    <Activity size={12} className="text-muted-foreground shrink-0" />
                    <span>{bw.status_message}</span>
                  </p>
                </div>
              ))}
            </div>

            {/* Platform Core Workers & Web Scaler */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3 pt-2 border-t border-border/40">
              <div className="p-3 rounded-lg bg-muted/20 border border-border/30 space-y-1">
                <div className="flex items-center justify-between text-xs font-medium">
                  <span className="flex items-center gap-1.5 font-bold">
                    <Server size={14} className="text-blue-400" />
                    celery (Primary Worker)
                  </span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 font-bold uppercase">Always On</span>
                </div>
                <p className="text-[11px] text-muted-foreground">
                  Drains celery, deploy, and fast queues. Baseline worker that never terminates.
                </p>
                <div className="flex items-center gap-3 text-[10px] font-mono text-muted-foreground pt-1">
                  <span>Active: {status?.infra?.primary_worker.active_tasks ?? 0}</span>
                  <span>CPU: {status?.infra?.primary_worker.cpu_percent?.toFixed(1) ?? 0}%</span>
                  <span>RAM: {Math.round(status?.infra?.primary_worker.memory_mb ?? 0)}MB</span>
                </div>
              </div>

              <div className="p-3 rounded-lg bg-muted/20 border border-border/30 space-y-1">
                <div className="flex items-center justify-between text-xs font-medium">
                  <span className="flex items-center gap-1.5 font-bold">
                    <Clock size={14} className="text-purple-400" />
                    celery-beat (Scheduler)
                  </span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 font-bold uppercase">Active</span>
                </div>
                <p className="text-[11px] text-muted-foreground">
                  RedBeat distributed periodic task scheduler (health probes, cleanups, autoscaler cron).
                </p>
                <div className="flex items-center gap-3 text-[10px] font-mono text-muted-foreground pt-1">
                  <span>CPU: {status?.infra?.scheduler.cpu_percent?.toFixed(1) ?? 0}%</span>
                  <span>RAM: {Math.round(status?.infra?.scheduler.memory_mb ?? 0)}MB</span>
                </div>
              </div>

              <div className="p-3 rounded-lg bg-muted/20 border border-border/30 space-y-1">
                <div className="flex items-center justify-between text-xs font-medium">
                  <span className="flex items-center gap-1.5 font-bold">
                    <Zap size={14} className="text-amber-400" />
                    Web Process Scaler
                  </span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-400 border border-blue-500/20 font-bold uppercase">SIGHUP / TTIN / TTOU</span>
                </div>
                <p className="text-[11px] text-muted-foreground">
                  Dynamic Gunicorn worker expansion (+1) / contraction (-1) within memory budget floors.
                </p>
                <div className="flex items-center gap-3 text-[10px] font-mono text-muted-foreground pt-1">
                  <span>Workers: {status?.infra?.web_scaling.current_workers ?? 2} (min: {status?.infra?.web_scaling.min_workers ?? 2}, max: {status?.infra?.web_scaling.max_workers ?? 8})</span>
                  <span>PIDs: {status?.infra?.web_scaling.pids ?? 0}</span>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* ── Service Cards Grid ───────────────────────────────────────────── */}
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6">
          {Object.entries(groupedServices).map(([app, services]) => (
            <div key={app} className="contents">
              {services.map(([name, svc]) => (
                <Card key={name} className={cn(
                  "border-l-4 overflow-hidden transition-all hover:shadow-md",
                  app === 'smsly-helper' ? "border-l-blue-500" :
                  app === 'lina-deluxe' ? "border-l-purple-500" :
                  app === 'buyforfront' ? "border-l-emerald-500" : "border-l-amber-500"
                )}>
                  <CardContent className="p-5 space-y-4">
                    {(() => {
                      const matched = servicesList.find(s => s.name === svc.app || s.name === name);
                      const serviceId = svc.service_id || matched?.id;
                      const isAutoscaleOn = svc.autoscale_enabled !== undefined ? svc.autoscale_enabled : (matched?.autoscale_enabled !== false);
                      const cooldownInfo = getCooldownInfo(svc.last_scale_at, svc.cooldown_down_min || 10);
                      const hasReplicas = svc.replicas && svc.replicas.length > 0;
                      const isExpanded = !!expandedReplicas[name];

                      return (
                        <>
                          {/* Card Header */}
                          <div className="flex items-start justify-between">
                            <div className="space-y-1">
                              <div className="flex items-center gap-2">
                                <ServiceIcon type={svc.type} />
                                <h3 className="font-bold text-sm tracking-tight">{name}</h3>
                              </div>
                              <div className="flex flex-wrap items-center gap-1.5">
                                 <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground font-mono">
                                   {svc.app}
                                 </span>
                                 <span className={cn(
                                   "text-[10px] px-1.5 py-0.5 rounded font-bold uppercase",
                                   svc.priority === 3 ? "bg-red-500/10 text-red-500" :
                                   svc.priority === 2 ? "bg-blue-500/10 text-blue-500" : "bg-zinc-500/10 text-zinc-500"
                                 )}>
                                   P{svc.priority}
                                 </span>
                                 {svc.vpa_enabled && (
                                   <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-500/10 text-purple-400 border border-purple-500/20 font-bold">
                                     VPA
                                   </span>
                                 )}
                                 {svc.min_replicas === 0 && (
                                   <span className="text-[10px] px-1.5 py-0.5 rounded bg-cyan-500/10 text-cyan-400 border border-cyan-500/20 font-bold">
                                     Scale-to-0
                                   </span>
                                 )}
                                 {serviceId && (
                                   <Switch
                                     checked={isAutoscaleOn}
                                     onCheckedChange={() => handleToggleAutoscale(serviceId, isAutoscaleOn)}
                                     disabled={togglingService === serviceId}
                                     className="scale-75"
                                     title={isAutoscaleOn ? 'Auto-scaling ON' : 'Auto-scaling OFF'}
                                   />
                                 )}
                              </div>
                            </div>
                            <div className="text-right">
                              <div className="text-2xl font-bold font-mono leading-none">
                                {svc.current_workers}<span className="text-muted-foreground text-sm font-normal">/{svc.max_workers}</span>
                              </div>
                              <div className="text-[10px] text-muted-foreground mt-1">
                                Min: {svc.min_workers ?? 1} | Max: {svc.max_workers}
                              </div>
                            </div>
                          </div>

                          {/* PaaS Target & Resource Limits Bar */}
                          <div className="flex flex-wrap items-center justify-between text-[11px] bg-muted/30 px-2.5 py-1.5 rounded-lg border border-border/40 font-mono">
                            <span className="text-muted-foreground">
                              Target: <strong className="text-foreground">{svc.autoscale_cpu_target || 80}% CPU</strong>
                            </span>
                            <span className="text-muted-foreground">
                              Limit: <strong className="text-foreground">{svc.cpu_cores ? `${svc.cpu_cores} Cores` : 'Uncapped'} / {svc.memory_mb_allocated ? `${svc.memory_mb_allocated}MB` : `${Math.round(svc.memory_limit_mb)}MB`}</strong>
                            </span>
                            <span className={cn("text-[10px] font-sans font-medium", cooldownInfo.inCooldown ? "text-amber-400" : "text-emerald-400")}>
                              {cooldownInfo.text}
                            </span>
                          </div>

                          {/* Demand Bar */}
                          <div className="space-y-1.5">
                            <div className="flex justify-between text-[10px] uppercase font-bold text-muted-foreground">
                              <span>Demand Score</span>
                              <span>{(svc.demand_score * 100).toFixed(0)}%</span>
                            </div>
                            <div className="h-2 w-full bg-muted rounded-full overflow-hidden">
                              <div
                                className={cn("h-full rounded-full transition-all duration-500",
                                  svc.demand_score > 0.6 ? "bg-red-500" :
                                  svc.demand_score > 0.3 ? "bg-amber-500" : "bg-emerald-500"
                                )}
                                style={{ width: `${Math.min(svc.demand_score * 100, 100)}%` }}
                              />
                            </div>
                          </div>

                          {/* Stats Grid */}
                          <div className="grid grid-cols-2 gap-4 pt-1">
                             <div className="flex items-center gap-3">
                               <GaugeRing value={svc.cpu_percent} color="#3b82f6" size={42} strokeWidth={3} />
                               <div>
                                 <div className="text-xs font-bold text-foreground">{svc.cpu_percent.toFixed(1)}%</div>
                                 <div className="text-[10px] text-muted-foreground">CPU Usage</div>
                               </div>
                             </div>
                             <div className="flex items-center gap-3">
                               <GaugeRing value={svc.memory_percent} color="#8b5cf6" size={42} strokeWidth={3} />
                               <div>
                                 <div className="text-xs font-bold text-foreground">{Math.round(svc.memory_mb)}MB</div>
                                 <div className="text-[10px] text-muted-foreground">of {Math.round(svc.memory_limit_mb)}MB</div>
                               </div>
                             </div>
                          </div>

                          {/* Manual Scaling Actions */}
                          {serviceId && (
                            <div className="flex items-center gap-2 pt-2 border-t border-border/40">
                              <Button
                                size="sm"
                                variant="outline"
                                className="flex-1 h-7 text-xs gap-1 hover:bg-emerald-500/10 hover:text-emerald-400 hover:border-emerald-500/30"
                                onClick={() => handleManualScaleUp(serviceId, name)}
                                disabled={scalingServiceId === serviceId || svc.current_workers >= svc.max_workers}
                              >
                                <Plus size={12} />
                                Scale Up (+1)
                              </Button>
                              <Button
                                size="sm"
                                variant="outline"
                                className="flex-1 h-7 text-xs gap-1 hover:bg-amber-500/10 hover:text-amber-400 hover:border-amber-500/30"
                                onClick={() => handleManualScaleDown(serviceId, name)}
                                disabled={scalingServiceId === serviceId || svc.current_workers <= (svc.min_workers ?? 1)}
                              >
                                <Minus size={12} />
                                Scale Down (-1)
                              </Button>
                            </div>
                          )}

                          {/* Replicas Drawer Toggle */}
                          {hasReplicas && (
                            <div className="space-y-2 pt-2 border-t border-border/30">
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => toggleReplicaDrawer(name)}
                                className="w-full justify-between h-7 px-2 text-xs text-muted-foreground hover:text-foreground"
                              >
                                <span className="flex items-center gap-1.5 font-medium">
                                  <Box size={13} className="text-purple-400" />
                                  Active Replicas ({svc.replicas?.length || 0})
                                </span>
                                {isExpanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
                              </Button>

                              <AnimatePresence>
                                {isExpanded && (
                                  <motion.div
                                    initial={{ height: 0, opacity: 0 }}
                                    animate={{ height: 'auto', opacity: 1 }}
                                    exit={{ height: 0, opacity: 0 }}
                                    className="overflow-hidden space-y-1.5 pt-1"
                                  >
                                    {(svc.replicas || []).map((replica) => (
                                      <div
                                        key={replica.id}
                                        className="flex items-center justify-between p-2 rounded-lg bg-muted/40 border border-border/40 text-[11px]"
                                      >
                                        <div className="space-y-0.5 truncate mr-2">
                                          <div className="font-mono font-medium text-foreground truncate">
                                            {replica.container_name || replica.id.slice(0, 8)}
                                          </div>
                                          <div className="text-[10px] text-muted-foreground">
                                            node: {replica.node} • {new Date(replica.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                                          </div>
                                        </div>

                                        <div className="flex items-center gap-2 shrink-0">
                                          <span className={cn(
                                            "px-1.5 py-0.5 rounded text-[9px] font-bold uppercase",
                                            replica.status === 'RUNNING' ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20" :
                                            replica.status === 'DRAINING' ? "bg-amber-500/10 text-amber-400 border border-amber-500/20 animate-pulse" :
                                            replica.status === 'SPAWNING' ? "bg-blue-500/10 text-blue-400 border border-blue-500/20 animate-pulse" :
                                            "bg-red-500/10 text-red-400 border border-red-500/20"
                                          )}>
                                            {replica.status}
                                          </span>
                                          <Button
                                            variant="ghost"
                                            size="sm"
                                            onClick={() => handleDestroyReplica(replica.id, replica.container_name)}
                                            disabled={destroyingReplicaId === replica.id}
                                            className="h-6 w-6 p-0 text-muted-foreground hover:text-red-400"
                                            title="Destroy replica"
                                          >
                                            <Trash2 size={12} />
                                          </Button>
                                        </div>
                                      </div>
                                    ))}
                                  </motion.div>
                                )}
                              </AnimatePresence>
                            </div>
                          )}

                          {/* Footer Stats */}
                          <div className="flex items-center justify-between pt-2 border-t border-border/50 text-[10px] text-muted-foreground">
                            <div className="flex items-center gap-2">
                              <span>PID: {svc.pids}</span>
                              <span>•</span>
                              <span>Net: {(svc.net_rx_mb + svc.net_tx_mb).toFixed(1)}MB</span>
                            </div>
                            <div>
                              {svc.last_action !== 'none' && (
                                <span className="text-amber-500 flex items-center gap-1">
                                  <Clock size={10} /> {new Date(svc.last_action_at).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})}
                                </span>
                              )}
                            </div>
                          </div>
                        </>
                      );
                    })()}
                  </CardContent>
                </Card>
              ))}
            </div>
          ))}
        </div>

        {/* ── Charts ───────────────────────────────────────────────────────── */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <Card className="p-6 border-border/50">
             <div className="flex items-center justify-between mb-6">
               <h3 className="font-bold text-base flex items-center gap-2">
                 <Zap size={16} className="text-amber-500" /> Memory Usage
               </h3>
               <div className="flex gap-1">
                 {[15, 30, 60].map(m => (
                   <button
                     key={m}
                     onClick={() => setHistoryDuration(m)}
                     className={cn(
                       "text-[10px] px-2 py-1 rounded border",
                       historyDuration === m ? "bg-muted border-foreground/20 text-foreground" : "border-transparent text-muted-foreground hover:bg-muted/50"
                     )}
                   >
                     {m}m
                   </button>
                 ))}
               </div>
             </div>
             <ChartContainer className="h-[250px] w-full" minHeight={250}>
                <AreaChart data={chartData}>
                  <defs>
                    <linearGradient id="colorMem" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#8b5cf6" stopOpacity={0.3}/>
                      <stop offset="95%" stopColor="#8b5cf6" stopOpacity={0}/>
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#333" vertical={false} />
                  <XAxis dataKey="timestamp" stroke="#666" fontSize={10} tickLine={false} axisLine={false} />
                  <YAxis stroke="#666" fontSize={10} tickLine={false} axisLine={false} unit="MB" />
                  <Tooltip
                    contentStyle={{ backgroundColor: '#18181b', border: '1px solid #333', borderRadius: '8px', fontSize: '12px' }}
                    itemStyle={{ padding: 0 }}
                  />
                  {Object.keys(history?.services || {}).slice(0, 5).map((svc, i) => (
                     <Area
                       key={svc}
                       type="monotone"
                       dataKey={`${svc}_mem`}
                       stackId="1"
                       stroke={`hsl(${i * 60}, 70%, 50%)`}
                       fill={`hsl(${i * 60}, 70%, 50%)`}
                       fillOpacity={0.6}
                     />
                  ))}
                </AreaChart>
             </ChartContainer>
          </Card>

          <Card className="p-6 border-border/50">
             <div className="flex items-center justify-between mb-6">
               <h3 className="font-bold text-base flex items-center gap-2">
                 <Activity size={16} className="text-emerald-500" /> Demand Scores
               </h3>
             </div>
             <ChartContainer className="h-[250px] w-full" minHeight={250}>
                <LineChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#333" vertical={false} />
                  <XAxis dataKey="timestamp" stroke="#666" fontSize={10} tickLine={false} axisLine={false} />
                  <YAxis stroke="#666" fontSize={10} tickLine={false} axisLine={false} domain={[0, 1]} />
                  <Tooltip
                    contentStyle={{ backgroundColor: '#18181b', border: '1px solid #333', borderRadius: '8px', fontSize: '12px' }}
                  />
                  {Object.keys(history?.services || {}).slice(0, 5).map((svc, i) => (
                     <Line
                       key={svc}
                       type="monotone"
                       dataKey={`${svc}_demand`}
                       stroke={`hsl(${i * 60}, 70%, 50%)`}
                       strokeWidth={2}
                       dot={false}
                     />
                  ))}
                </LineChart>
             </ChartContainer>
          </Card>
        </div>

        {/* ── Config Panel (Collapsible) ───────────────────────────────────── */}
        <div className="pt-4">
           <Button
             variant="outline"
             className="w-full flex justify-between items-center"
             onClick={() => setConfigOpen(!configOpen)}
           >
             <span className="flex items-center gap-2 font-bold"><Settings size={16} /> Configuration</span>
             {configOpen ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
           </Button>

           <AnimatePresence>
             {configOpen && localConfig && (
               <motion.div
                 initial={{ height: 0, opacity: 0 }}
                 animate={{ height: 'auto', opacity: 1 }}
                 exit={{ height: 0, opacity: 0 }}
                 className="overflow-hidden"
               >
                 <Card className="mt-4 border-border/50 bg-muted/20">
                   <CardContent className="p-6 space-y-6">
                     <div className="space-y-4">
                       <h3 className="text-sm font-bold uppercase tracking-wider text-muted-foreground border-b border-border/50 pb-2">Global Settings</h3>
                       <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
                          <div className="space-y-3">
                            <div className="flex justify-between text-sm">
                              <span>Total System Memory</span>
                              <span className="font-mono">{localConfig.total_system_mb} MB</span>
                            </div>
                            <Slider
                              value={[localConfig.total_system_mb]}
                              min={1024} max={131072} step={512}
                              onValueChange={([v]) => setLocalConfig({...localConfig, total_system_mb: v})}
                            />
                          </div>
                          <div className="space-y-3">
                            <div className="flex justify-between text-sm">
                              <span>Infra Reserve</span>
                              <span className="font-mono">{localConfig.infra_reserve_mb} MB</span>
                            </div>
                            <Slider
                              value={[localConfig.infra_reserve_mb]}
                              min={512} max={32768} step={128}
                              onValueChange={([v]) => setLocalConfig({...localConfig, infra_reserve_mb: v})}
                            />
                          </div>
                       </div>
                     </div>

                     <div className="flex justify-end gap-3 pt-4 border-t border-border/50">
                       <Button variant="ghost" onClick={() => {
                         fetchData(); // Reset
                       }}>Reset to Current</Button>
                       <Button className="bg-emerald-600 hover:bg-emerald-700" onClick={handleSaveConfig}>
                         <Save size={14} className="mr-2" /> Save Configuration
                       </Button>
                     </div>
                   </CardContent>
                 </Card>
               </motion.div>
             )}
           </AnimatePresence>
        </div>

        {/* ── Resource Alerts ────────────────────────────────────────────── */}
        <div className="pt-4">
           <Button
             variant="outline"
             className="w-full flex justify-between items-center"
             onClick={() => setAlertsOpen(!alertsOpen)}
           >
             <span className="flex items-center gap-2 font-bold"><BellRing size={16} /> Resource Alerts</span>
             {alertsOpen ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
           </Button>

           <AnimatePresence>
             {alertsOpen && (
               <motion.div
                 initial={{ height: 0, opacity: 0 }}
                 animate={{ height: 'auto', opacity: 1 }}
                 exit={{ height: 0, opacity: 0 }}
                 className="overflow-hidden"
               >
                 <Card className="mt-4 border-border/50 bg-muted/20">
                   <CardContent className="p-6 space-y-6">
                     {/* Active Alerts */}
                     {status && (() => {
                       const alerts: { service: string; metric: string; value: number; level: 'warning' | 'critical' }[] = [];
                       Object.entries(status.services).forEach(([name, svc]) => {
                         if (svc.cpu_percent >= alertConfig.cpu_critical)
                           alerts.push({ service: name, metric: 'CPU', value: svc.cpu_percent, level: 'critical' });
                         else if (svc.cpu_percent >= alertConfig.cpu_warning)
                           alerts.push({ service: name, metric: 'CPU', value: svc.cpu_percent, level: 'warning' });
                         if (svc.memory_percent >= alertConfig.memory_critical)
                           alerts.push({ service: name, metric: 'Memory', value: svc.memory_percent, level: 'critical' });
                         else if (svc.memory_percent >= alertConfig.memory_warning)
                           alerts.push({ service: name, metric: 'Memory', value: svc.memory_percent, level: 'warning' });
                       });

                       return alerts.length > 0 ? (
                         <div className="space-y-2">
                           <h3 className="text-sm font-bold uppercase tracking-wider text-red-400 flex items-center gap-2">
                             <ShieldAlert size={14} /> Active Alerts ({alerts.length})
                           </h3>
                           <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                             {alerts.map((a, i) => (
                               <div
                                 key={i}
                                 className={cn(
                                   "flex items-center gap-3 p-3 rounded-lg border",
                                   a.level === 'critical'
                                     ? 'bg-red-500/10 border-red-500/30 text-red-400'
                                     : 'bg-amber-500/10 border-amber-500/30 text-amber-400'
                                 )}
                               >
                                 {a.level === 'critical' ? <AlertCircle size={14} /> : <AlertTriangle size={14} />}
                                 <div className="flex-1">
                                   <span className="font-bold text-xs">{a.service}</span>
                                   <span className="text-[10px] ml-2">{a.metric}: {a.value.toFixed(1)}%</span>
                                 </div>
                                 <span className="text-[10px] uppercase font-bold">{a.level}</span>
                               </div>
                             ))}
                           </div>
                         </div>
                       ) : (
                         <div className="flex items-center gap-2 text-emerald-400 text-sm">
                           <CheckCircle2 size={14} /> All services within thresholds
                         </div>
                       );
                     })()}

                     {/* Threshold Config */}
                     <div className="space-y-4">
                       <h3 className="text-sm font-bold uppercase tracking-wider text-muted-foreground border-b border-border/50 pb-2">
                         Alert Thresholds
                       </h3>
                       <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                         {/* CPU */}
                         <div className="space-y-3">
                           <div className="flex items-center gap-2 text-sm font-medium"><Cpu size={14} className="text-blue-500" /> CPU</div>
                           <div className="space-y-2">
                             <div className="flex justify-between text-xs">
                               <span className="text-amber-400">Warning</span>
                               <span className="font-mono">{alertConfig.cpu_warning}%</span>
                             </div>
                             <Slider
                               value={[alertConfig.cpu_warning]} min={30} max={95} step={5}
                               onValueChange={([v]) => setAlertConfig({ ...alertConfig, cpu_warning: v })}
                             />
                             <div className="flex justify-between text-xs">
                               <span className="text-red-400">Critical</span>
                               <span className="font-mono">{alertConfig.cpu_critical}%</span>
                             </div>
                             <Slider
                               value={[alertConfig.cpu_critical]} min={50} max={100} step={5}
                               onValueChange={([v]) => setAlertConfig({ ...alertConfig, cpu_critical: v })}
                             />
                           </div>
                         </div>

                         {/* Memory */}
                         <div className="space-y-3">
                           <div className="flex items-center gap-2 text-sm font-medium"><TrendingUp size={14} className="text-purple-500" /> Memory</div>
                           <div className="space-y-2">
                             <div className="flex justify-between text-xs">
                               <span className="text-amber-400">Warning</span>
                               <span className="font-mono">{alertConfig.memory_warning}%</span>
                             </div>
                             <Slider
                               value={[alertConfig.memory_warning]} min={30} max={95} step={5}
                               onValueChange={([v]) => setAlertConfig({ ...alertConfig, memory_warning: v })}
                             />
                             <div className="flex justify-between text-xs">
                               <span className="text-red-400">Critical</span>
                               <span className="font-mono">{alertConfig.memory_critical}%</span>
                             </div>
                             <Slider
                               value={[alertConfig.memory_critical]} min={50} max={100} step={5}
                               onValueChange={([v]) => setAlertConfig({ ...alertConfig, memory_critical: v })}
                             />
                           </div>
                         </div>

                         {/* Disk */}
                         <div className="space-y-3">
                           <div className="flex items-center gap-2 text-sm font-medium"><HardDrive size={14} className="text-emerald-500" /> Disk</div>
                           <div className="space-y-2">
                             <div className="flex justify-between text-xs">
                               <span className="text-amber-400">Warning</span>
                               <span className="font-mono">{alertConfig.disk_warning}%</span>
                             </div>
                             <Slider
                               value={[alertConfig.disk_warning]} min={50} max={95} step={5}
                               onValueChange={([v]) => setAlertConfig({ ...alertConfig, disk_warning: v })}
                             />
                             <div className="flex justify-between text-xs">
                               <span className="text-red-400">Critical</span>
                               <span className="font-mono">{alertConfig.disk_critical}%</span>
                             </div>
                             <Slider
                               value={[alertConfig.disk_critical]} min={60} max={100} step={5}
                               onValueChange={([v]) => setAlertConfig({ ...alertConfig, disk_critical: v })}
                             />
                           </div>
                         </div>
                       </div>
                     </div>

                     {/* Notification Channels */}
                     <div className="space-y-4">
                       <h3 className="text-sm font-bold uppercase tracking-wider text-muted-foreground border-b border-border/50 pb-2">
                         Notification Channels
                       </h3>
                       <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                         <div className="flex items-center justify-between p-3 bg-card rounded-lg border border-border">
                           <div className="flex items-center gap-2">
                             <Mail size={14} className="text-blue-400" />
                             <span className="text-sm">Email Notifications</span>
                           </div>
                           <Switch
                             checked={alertConfig.notify_email}
                             onCheckedChange={(v) => setAlertConfig({ ...alertConfig, notify_email: v })}
                           />
                         </div>
                         <div className="flex items-center justify-between p-3 bg-card rounded-lg border border-border">
                           <div className="flex items-center gap-2">
                             <Webhook size={14} className="text-purple-400" />
                             <span className="text-sm">Webhook (Slack/Discord)</span>
                           </div>
                           <Switch
                             checked={alertConfig.notify_webhook}
                             onCheckedChange={(v) => setAlertConfig({ ...alertConfig, notify_webhook: v })}
                           />
                         </div>
                       </div>
                       {alertConfig.notify_webhook && (
                         <input
                           type="url"
                           placeholder="https://hooks.slack.com/services/..."
                           value={alertConfig.webhook_url}
                           onChange={(e) => setAlertConfig({ ...alertConfig, webhook_url: e.target.value })}
                           className="w-full px-4 py-2 text-sm rounded-lg bg-background border border-border"
                         />
                       )}
                     </div>

                      <div className="flex justify-end gap-3 pt-4 border-t border-border/50">
                        <Button
                          className="bg-emerald-600 hover:bg-emerald-700"
                          disabled={savingAlertConfig}
                          onClick={async () => {
                            setSavingAlertConfig(true);
                            try {
                              const services = await servicesApi.list();
                              if (!services.length) {
                                toast({ title: 'No services to save against', description: 'Deploy a service first to persist alert thresholds.' });
                                return;
                              }
                              await Promise.all(services.map((svc) =>
                                scalingApi.updateAlertConfig(svc.id, alertConfig),
                              ));
                              toast({ title: 'Alert Config Saved', description: `Resource alert thresholds updated for ${services.length} service(s).` });
                              setAlertsOpen(false);
                            } catch (err: unknown) {
                              toast({ title: 'Save failed', description: err instanceof Error ? err.message : 'Could not persist alert config.', variant: 'destructive' });
                            } finally {
                              setSavingAlertConfig(false);
                            }
                          }}
                        >
                          <Save size={14} className="mr-2" /> {savingAlertConfig ? 'Saving…' : 'Save Alert Config'}
                        </Button>
                      </div>
                   </CardContent>
                 </Card>
               </motion.div>
             )}
           </AnimatePresence>
        </div>

      </div>
      </RequiresTier>
    </DashboardShell>
  );
}
