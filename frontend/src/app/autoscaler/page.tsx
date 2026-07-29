'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Scaling, Activity, Cpu, Server, Layers, Radio, AlertTriangle,
  Zap, Clock, ArrowRight, Settings, RotateCw, Play, CheckCircle2,
  AlertCircle, ChevronDown, ChevronUp, Save, Bell, BellRing, Mail,
  Webhook, HardDrive, ShieldAlert, TrendingUp
} from 'lucide-react';
import {
  LineChart, Line, AreaChart, Area, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend
} from 'recharts';
import { ChartContainer } from '@/components/ui/chart-container';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { autoscalerApi, scalingApi, servicesApi, type AutoscalerStatus, type AutoscalerHistory, type AutoscalerService } from '@/lib/api';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Slider } from '@/components/ui/slider';
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

  const fetchData = useCallback(async () => {
    try {
      const [s, h] = await Promise.all([
        autoscalerApi.getStatus(),
        autoscalerApi.getHistory(historyDuration)
      ]);
      setStatus(s);
      setHistory(h);

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
  const budgetColor = usedPercent > 80 ? '#ef4444' : usedPercent > 60 ? '#f59e0b' : '#10b981';

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
               status ? "bg-emerald-500/10 text-emerald-500" : "bg-red-500/10 text-red-500"
             )}>
               <div className={cn("w-1.5 h-1.5 rounded-full", status ? "bg-emerald-500 animate-pulse" : "bg-red-500")} />
               {status ? "Running" : "Offline"}
             </div>
          </div>
        </div>

        {/* ── Hero: Memory Budget Ring ─────────────────────────────────────── */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
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
                    {/* Card Header */}
                    <div className="flex items-start justify-between">
                      <div className="space-y-1">
                        <div className="flex items-center gap-2">
                          <ServiceIcon type={svc.type} />
                          <h3 className="font-bold text-sm tracking-tight">{name}</h3>
                        </div>
                        <div className="flex items-center gap-2">
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
                        </div>
                      </div>
                      <div className="text-right">
                        <div className="text-2xl font-bold font-mono leading-none">
                          {svc.current_workers}<span className="text-muted-foreground text-sm font-normal">/{svc.max_workers}</span>
                        </div>
                        <div className="text-[10px] text-muted-foreground mt-1">Workers</div>
                      </div>
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
                    <div className="grid grid-cols-2 gap-4 pt-2">
                       <div className="flex items-center gap-3">
                         <GaugeRing value={svc.cpu_percent} color="#3b82f6" size={42} strokeWidth={3} />
                         <div>
                           <div className="text-xs font-bold text-foreground">{svc.cpu_percent.toFixed(1)}%</div>
                           <div className="text-[10px] text-muted-foreground">CPU</div>
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
