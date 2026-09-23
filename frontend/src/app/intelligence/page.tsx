'use client';

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { motion } from 'framer-motion';
import { EcosystemSuggestion } from '@/components/dashboard/EcosystemSuggestion';
import {
  Brain, Cpu, Zap, Shield, Eye, Activity, BarChart3, Sparkles,
  RefreshCw, Send, CheckCircle2, XCircle, Loader2, TrendingUp,
  Gauge, CircuitBoard, Bot, MessageSquare, AlertTriangle, Flame,
  Target, Lightbulb, DollarSign, Clock, ArrowUpRight, Settings, Lock,
  Code2, Server, Siren, ShieldCheck, ShieldAlert, Bug,
  Search, Filter, Ban, ChevronDown, ChevronUp, Copy, Check, Terminal
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import CodeMapView from '@/components/intelligence/CodeMapView';
import { DashboardShell } from '@/components/layout/DashboardShell';
import {
  aiApi,
  type AIProvidersResponse,
  serversApi,
  systemSecurityApi,
  type SecurityStatusData,
  type SecurityEventsResponse,
  type SecurityAnalysisResponse,
  type SecurityActivityEvent,
} from '@/lib/api';
import api from '@/lib/api';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useConfirm } from '@/components/ui/confirm-dialog';
import { useToast } from '@/components/ui/use-toast';
import { cn } from '@/lib/utils';
import { Switch } from '@/components/ui/switch';
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { RequiresTier } from '@/components/licensing/RequiresTier';
import { Progress } from '@/components/ui/progress';

// ─── Types ──────────────────────────────────────────────────────────────────

interface DeploymentInsight {
  id: string;
  service_name: string;
  status: string;
  ai_diagnosis: string | null;
  created_at: string;
}

interface Anomaly {
  id: string;
  service_name: string;
  issue_type: string;
  severity: string;
  detected_at: string;
  auto_fixed: boolean;
  fix_result: string;
}

const MODE_CONFIG: Record<string, { label: string; color: string; icon: React.ReactNode; description: string }> = {
  mock:             { label: 'Mock',             color: 'bg-zinc-500/20 text-zinc-400',   icon: <Bot className="w-4 h-4" />, description: 'No real AI — using test responses' },
  solo:             { label: 'Solo Provider',    color: 'bg-blue-500/20 text-blue-400',   icon: <Cpu className="w-4 h-4" />, description: 'Single AI provider active' },
  senate_committee: { label: 'Senate Committee', color: 'bg-purple-500/20 text-purple-400', icon: <Shield className="w-4 h-4" />, description: 'Multiple providers for consensus' },
};

// ─── Component ──────────────────────────────────────────────────────────────

export default function IntelligencePage() {
  const [providers, setProviders] = useState<AIProvidersResponse | null>(null);
  const [deployments, setDeployments] = useState<DeploymentInsight[]>([]);
  const [anomalies, setAnomalies] = useState<Anomaly[]>([]);
  const [report, setReport] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [chatInput, setChatInput] = useState('');
  const [chatMessages, setChatMessages] = useState<{ role: 'user' | 'assistant'; content: string }[]>([]);
  const [chatLoading, setChatLoading] = useState(false);
  const chatAbortRef = useRef<AbortController | null>(null);
  const { toast } = useToast();
  const confirm = useConfirm();

  // Cost Estimate State
  const [costConfig, setCostConfig] = useState({ cpu: 1, ram: 512 });
  const [costEstimates, setCostEstimates] = useState<any>(null);
  const [costAnalysis, setCostAnalysis] = useState<string | null>(null);
  const [costLoading, setCostLoading] = useState(false);

  // Platform Health State
  const [allServices, setAllServices] = useState<any[]>([]);
  const [autoscalerStatus, setAutoscalerStatus] = useState<any>(null);
  const [servers, setServers] = useState<any[]>([]);
  const [serverReports, setServerReports] = useState<Record<string, any>>({});
  const [serverReportsLoading, setServerReportsLoading] = useState(false);

  // Security Intelligence State
  const [securityStatus, setSecurityStatus] = useState<SecurityStatusData | null>(null);
  const [securityEvents, setSecurityEvents] = useState<SecurityEventsResponse | null>(null);
  const [securityLoading, setSecurityLoading] = useState(false);
  const [securityAnalysis, setSecurityAnalysis] = useState<SecurityAnalysisResponse | null>(null);
  const [securityAnalyzing, setSecurityAnalyzing] = useState(false);
  const [securitySourceFilter, setSecuritySourceFilter] = useState<string>('all');
  const [securitySeverityFilter, setSecuritySeverityFilter] = useState<string>('all');
  const [securitySearchQuery, setSecuritySearchQuery] = useState('');
  const [expandedEventId, setExpandedEventId] = useState<string | null>(null);
  const [unbanningIp, setUnbanningIp] = useState<string | null>(null);
  const [copiedEventId, setCopiedEventId] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const withTimeout = <T,>(promise: Promise<T>, ms: number, fallback: T): Promise<T> =>
        Promise.race([
          promise,
          new Promise<T>((resolve) => setTimeout(() => resolve(fallback), ms)),
        ]);

      const [prov, deps, anoms, rep, svcs, autoStatus, svrs, secStat, secEvts] = await Promise.all([
        withTimeout(
          aiApi.getProviders(false).catch(() => ({
            providers: [],
            mode: 'mock',
            mode_label: 'Mock AI (provider status unavailable)',
            active_count: 0,
            total_available: 0,
            degraded: true,
            degraded_reason: 'providers_request_failed',
          }) as any),
          12000,
          { providers: [], mode: 'mock', mode_label: 'Mock AI (timeout)', active_count: 0, total_available: 0, degraded: true, degraded_reason: 'providers_timeout' } as any,
        ),
        withTimeout(
          api.get('/deployments/', {
            params: { page_size: 20 },
            _skipRemoteProxy: true,
          } as any).then(r => r.data?.results || r.data || []).catch(() => []),
          12000,
          [],
        ),
        withTimeout(
          aiApi.getAnomalies().then(r => r.anomalies).catch(() => []),
          12000,
          [],
        ),
        withTimeout(
          aiApi.getReport().catch(() => null),
          12000,
          null,
        ),
        withTimeout(
          api.get('/services/', { params: { page_size: 50 } }).then(r => r.data?.results || r.data || []).catch(() => []),
          12000,
          [],
        ),
        withTimeout(
          api.get('/autoscaler/status/').then(r => r.data).catch(() => null),
          12000,
          null,
        ),
        withTimeout(
          api.get('/servers/').then(r => r.data?.results || r.data || []).catch(() => []),
          12000,
          [],
        ),
        withTimeout(
          systemSecurityApi.getStatus().catch(() => null),
          12000,
          null,
        ),
        withTimeout(
          systemSecurityApi.getEvents(undefined, 100).catch(() => null),
          12000,
          null,
        ),
      ]);
      setProviders(prov);
      setAnomalies(anoms);
      setReport(rep);
      setAllServices(svcs);
      setAutoscalerStatus(autoStatus);
      setServers(svrs);
      if (secStat) setSecurityStatus(secStat);
      if (secEvts) setSecurityEvents(secEvts);

      const insights = (deps as DeploymentInsight[]).filter(
        d => d.ai_diagnosis || d.status === 'FAILED'
      ).slice(0, 10);
      setDeployments(insights);
    } catch (err) {
      console.error('Intelligence fetch failed:', err);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  const refreshSecurityData = useCallback(async (source?: string) => {
    setSecurityLoading(true);
    try {
      const [stat, evts] = await Promise.all([
        systemSecurityApi.getStatus().catch(() => null),
        systemSecurityApi.getEvents(source && source !== 'all' ? source : undefined, 100).catch(() => null),
      ]);
      if (stat) setSecurityStatus(stat);
      if (evts) setSecurityEvents(evts);
      if (!stat && !evts) {
        toast({
          title: 'Security refresh failed',
          description: 'Could not reach the security APIs. Showing last loaded data.',
          variant: 'destructive',
        });
      }
    } catch (err) {
      console.error('Failed to refresh security data:', err);
    } finally {
      setSecurityLoading(false);
    }
  }, [toast]);

  const handleRunSecurityAnalysis = async () => {
    setSecurityAnalyzing(true);
    try {
      const res = await systemSecurityApi.analyze();
      setSecurityAnalysis(res);
      toast({
        title: "AI Security Assessment Complete",
        description: `Threat level: ${res.threat_level} (Risk score: ${res.risk_score}/100)`,
      });
    } catch (err: any) {
      toast({
        title: "Security Analysis Failed",
        description: err?.response?.data?.error || err.message || "Failed to analyze security posture",
        variant: "destructive",
      });
    } finally {
      setSecurityAnalyzing(false);
    }
  };

  const handleUnbanIp = async (ip: string) => {
    if (!await confirm({
      title: 'Remove CrowdSec ban?',
      message: `Revoking the ban lets ${ip} reach the edge again. Continue?`,
      confirmText: 'Unban IP',
      variant: 'destructive',
    })) return;
    setUnbanningIp(ip);
    try {
      await systemSecurityApi.unbanCrowdSec(ip);
      toast({
        title: "CrowdSec Ban Removed",
        description: `Decision for IP ${ip} has been revoked.`,
      });
      await refreshSecurityData(securitySourceFilter !== 'all' ? securitySourceFilter : undefined);
    } catch (err: any) {
      toast({
        title: "Failed to Unban IP",
        description: err?.response?.data?.error || err.message || `Could not unban ${ip}`,
        variant: "destructive",
      });
    } finally {
      setUnbanningIp(null);
    }
  };

  const handleCopyJson = (id: string, data: any) => {
    navigator.clipboard.writeText(JSON.stringify(data, null, 2));
    setCopiedEventId(id);
    setTimeout(() => setCopiedEventId(null), 2000);
  };

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 30000);
    return () => clearInterval(interval);
  }, [fetchData]);

  const handleRefresh = () => {
    setRefreshing(true);
    fetchData();
  };

  // Load server incident reports on demand when servers tab is opened
  const loadServerReports = useCallback(async () => {
    if (serverReportsLoading) return;
    setServerReportsLoading(true);
    try {
      const results = await Promise.all(
        servers.map(async (srv: any) => {
          try {
            const rep = await serversApi.getIncidentReport(srv.id);
            return { id: srv.id, report: rep };
          } catch {
            return { id: srv.id, report: null };
          }
        })
      );
      const map: Record<string, any> = {};
      for (const r of results) {
        map[r.id] = r.report;
      }
      setServerReports(map);
    } finally {
      setServerReportsLoading(false);
    }
  }, [servers, serverReportsLoading]);

  const handleChat = async () => {
    if (!chatInput.trim()) return;
    const prompt = chatInput.trim();
    setChatInput('');
    setChatLoading(true);

    setChatMessages(prev => [...prev, { role: 'user', content: prompt }, { role: 'assistant', content: '' }]);

    const controller = new AbortController();
    chatAbortRef.current = controller;

    try {
      const response = await fetch('/api/v1/ai/chat/stream/', {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ prompt }),
        signal: controller.signal,
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const reader = response.body?.getReader();
      if (!reader) throw new Error('No reader available');

      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6).trim();
            if (data === '[DONE]') {
              setChatLoading(false);
              return;
            }
            try {
              const parsed = JSON.parse(data);
              if (parsed.content) {
                setChatMessages(prev => {
                  const updated = [...prev];
                  const last = updated[updated.length - 1];
                  if (last.role === 'assistant') {
                    last.content += parsed.content;
                  }
                  return [...updated];
                });
              }
              if (parsed.error) {
                throw new Error(parsed.error);
              }
            } catch (e: any) {
              if (e.message && !e.message.includes('JSON')) throw e;
            }
          }
        }
      }
    } catch (err: any) {
      if (err.name !== 'AbortError') {
        console.error('Streaming error:', err);
        try {
          const result = await aiApi.testPrompt(prompt,
            'You are the SMSLY Hosting AI assistant. Help users with deployment, infrastructure, and DevOps questions. Be concise and actionable.'
          );
          setChatMessages(prev => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last.role === 'assistant') {
              last.content = result.response;
            }
            return [...updated];
          });
        } catch (fallbackErr: any) {
          setChatMessages(prev => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last.role === 'assistant') {
              last.content = `Error: ${fallbackErr.message || 'AI request failed'}`;
            }
            return [...updated];
          });
        }
      }
    } finally {
      setChatLoading(false);
      chatAbortRef.current = null;
    }
  };

  const handleCostAnalysis = async () => {
    setCostLoading(true);
    try {
      const res = await aiApi.costEstimate({
        cpu_cores: costConfig.cpu,
        memory_mb: costConfig.ram,
        stack: "Generic",
        provider: "Comparison"
      });
      setCostEstimates(res.estimates);
      setCostAnalysis(res.ai_recommendations);
    } catch (err) {
      toast({ title: "Analysis Failed", variant: "destructive" });
    } finally {
      setCostLoading(false);
    }
  };

  const activeProviders = providers?.providers?.filter(p => p.configured) || [];
  const modeConfig = MODE_CONFIG[providers?.mode || 'mock'] || MODE_CONFIG.mock;
  const failedDeploys = deployments.filter(d => d.status === 'FAILED');
  const diagnosedDeploys = deployments.filter(d => d.ai_diagnosis);

  const filteredSecurityEvents = (securityEvents?.recent_activities || []).filter((evt) => {
    if (securitySourceFilter !== 'all' && evt.source !== securitySourceFilter) {
      return false;
    }
    if (securitySeverityFilter !== 'all' && evt.severity !== securitySeverityFilter) {
      return false;
    }
    if (securitySearchQuery.trim()) {
      const q = securitySearchQuery.toLowerCase();
      const matchTitle = evt.title?.toLowerCase().includes(q);
      const matchDetails = evt.details?.toLowerCase().includes(q);
      const matchTarget = evt.target?.toLowerCase().includes(q);
      const matchSource = evt.source?.toLowerCase().includes(q);
      if (!matchTitle && !matchDetails && !matchTarget && !matchSource) {
        return false;
      }
    }
    return true;
  });

  if (loading) {
    return (
      <DashboardShell>
        <div className="flex-1 flex items-center justify-center p-8 relative z-10">
          <Loader2 className="h-8 w-8 animate-spin text-purple-500" />
        </div>
      </DashboardShell>
    );
  }

  return (
    <DashboardShell>
      <RequiresTier tier="pro">
      <div className="flex-1 p-4 pt-safe sm:p-8 relative z-10 w-full overflow-x-hidden">
        <motion.div
          className="max-w-6xl mx-auto space-y-6 sm:space-y-8"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
        >
          {/* Header */}
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-3xl font-bold tracking-tight flex items-center gap-3">
                <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-purple-500 to-pink-600 flex items-center justify-center shadow-lg shadow-purple-500/25">
                  <Brain className="text-white" size={22} />
                </div>
                Intelligence
              </h1>
              <p className="text-muted-foreground mt-1">
                AI operations dashboard — autonomous DevOps brain
              </p>
            </div>
            <div className="flex gap-2">
              <a href="/settings?tab=ai">
                <Button variant="outline">
                  <Settings className="w-4 h-4 mr-2" /> Configure AI
                </Button>
              </a>
              <Button onClick={handleRefresh} disabled={refreshing} variant="ghost" size="icon">
                <RefreshCw size={18} className={refreshing ? 'animate-spin' : ''} />
              </Button>
            </div>
          </div>

          {/* SMSLY Ecosystem Cross-Sell */}
          <motion.div
            initial={{ opacity: 0, y: -6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.4 }}
            className="mb-6"
          >
            <EcosystemSuggestion context="intelligence" dismissible={true} />
          </motion.div>

          {/* ── Stats Row ──────────────────────────────────────────── */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <motion.div className="bg-card border border-border rounded-xl p-4">
              <div className="flex items-center gap-3 mb-2">
                <div className="w-9 h-9 rounded-lg bg-purple-500/10 flex items-center justify-center">
                  <CircuitBoard className="text-purple-500" size={18} />
                </div>
                <span className="text-xs text-muted-foreground uppercase tracking-wider font-semibold">AI Mode</span>
              </div>
              <p className="text-lg font-bold">{modeConfig.label}</p>
              <p className="text-xs text-muted-foreground mt-0.5">{modeConfig.description}</p>
            </motion.div>

            <motion.div className="bg-card border border-border rounded-xl p-4">
              <div className="flex items-center gap-3 mb-2">
                <div className="w-9 h-9 rounded-lg bg-emerald-500/10 flex items-center justify-center">
                  <Zap className="text-emerald-500" size={18} />
                </div>
                <span className="text-xs text-muted-foreground uppercase tracking-wider font-semibold">Active Providers</span>
              </div>
              <p className="text-lg font-bold">{providers?.active_count || 0} <span className="text-sm text-muted-foreground font-normal">/ {providers?.total_available || 0}</span></p>
              <p className="text-xs text-muted-foreground mt-0.5">Ready for consensus</p>
            </motion.div>

            <motion.div className="bg-card border border-border rounded-xl p-4">
              <div className="flex items-center gap-3 mb-2">
                <div className="w-9 h-9 rounded-lg bg-amber-500/10 flex items-center justify-center">
                  <AlertTriangle className="text-amber-500" size={18} />
                </div>
                <span className="text-xs text-muted-foreground uppercase tracking-wider font-semibold">Anomalies</span>
              </div>
              <p className="text-lg font-bold">{anomalies.length}</p>
              <p className="text-xs text-muted-foreground mt-0.5">Detected in last 24h</p>
            </motion.div>

            <motion.div className="bg-card border border-border rounded-xl p-4">
              <div className="flex items-center gap-3 mb-2">
                <div className="w-9 h-9 rounded-lg bg-blue-500/10 flex items-center justify-center">
                  <Target className="text-blue-500" size={18} />
                </div>
                <span className="text-xs text-muted-foreground uppercase tracking-wider font-semibold">Success Rate</span>
              </div>
              <p className="text-lg font-bold">{report?.success_rate || "N/A"}</p>
              <p className="text-xs text-muted-foreground mt-0.5">{report?.total_deployments || 0} deployments today</p>
            </motion.div>
          </div>

          <Tabs defaultValue="dashboard" className="w-full">
            <div className="w-full overflow-x-auto scrollbar-hide pb-2 -mx-4 px-4 sm:mx-0 sm:px-0">
              <TabsList className="inline-flex sm:grid w-max sm:w-full sm:grid-cols-9 bg-muted/20 gap-2 sm:gap-0 p-1">
                <TabsTrigger value="dashboard" className="rounded-full sm:rounded-sm px-3">Dashboard</TabsTrigger>
                <TabsTrigger value="security" className="rounded-full sm:rounded-sm px-3 flex items-center gap-1.5"><Shield className="w-3.5 h-3.5 text-purple-400" />Security Intel</TabsTrigger>
                <TabsTrigger value="anomalies" className="rounded-full sm:rounded-sm px-3">Anomalies</TabsTrigger>
                <TabsTrigger value="services" className="rounded-full sm:rounded-sm px-3">Services</TabsTrigger>
                <TabsTrigger value="autoscaler" className="rounded-full sm:rounded-sm px-3">Autoscaler</TabsTrigger>
                <TabsTrigger value="cost" className="rounded-full sm:rounded-sm px-3">Cost Intel</TabsTrigger>
                <TabsTrigger value="codemap" className="rounded-full sm:rounded-sm px-3"><Code2 className="w-3.5 h-3.5 mr-1 inline" />Code Map</TabsTrigger>
                <TabsTrigger value="chat" className="rounded-full sm:rounded-sm px-3">AI Chat</TabsTrigger>
                <TabsTrigger value="servers" className="rounded-full sm:rounded-sm px-3"><Siren className="w-3.5 h-3.5 mr-1 inline" />Servers</TabsTrigger>
              </TabsList>
            </div>

            {/* ── Dashboard Tab ──────────────────────────────────────── */}
            <TabsContent value="dashboard" className="space-y-6 mt-6">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                {/* Daily Report */}
                <Card>
                   <CardHeader>
                     <CardTitle className="flex items-center gap-2">
                       <BarChart3 className="text-emerald-500" /> Daily Intelligence Report
                     </CardTitle>
                   </CardHeader>
                   <CardContent>
                      {report ? (
                        <div className="space-y-4">
                          <div className="grid grid-cols-2 gap-4">
                             <div className="p-3 bg-muted/30 rounded-lg">
                               <div className="text-sm text-muted-foreground">Total Deploys</div>
                               <div className="text-2xl font-bold">{report.total_deployments}</div>
                             </div>
                             <div className="p-3 bg-muted/30 rounded-lg">
                               <div className="text-sm text-muted-foreground">Failed</div>
                               <div className="text-2xl font-bold text-red-500">{report.failed_deployments}</div>
                             </div>
                          </div>
                          <div className="p-3 bg-muted/30 rounded-lg">
                             <div className="text-sm text-muted-foreground mb-1">Anomalies Detected</div>
                             <div className="text-2xl font-bold text-amber-500">{report.anomalies_detected}</div>
                          </div>
                          <div className="text-xs text-muted-foreground text-right">
                            Generated at: {new Date(report.generated_at).toLocaleString()}
                          </div>
                        </div>
                      ) : (
                        <div className="text-center py-8 text-muted-foreground">No report generated for today yet.</div>
                      )}
                   </CardContent>
                </Card>

                {/* Recent Diagnoses */}
                <Card>
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                      <Brain className="text-purple-500" /> Recent AI Diagnoses
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-4">
                     {diagnosedDeploys.length === 0 && <div className="text-center py-4 text-muted-foreground">No diagnoses found.</div>}
                     {diagnosedDeploys.slice(0, 3).map(dep => (
                       <div key={dep.id} className="p-3 bg-muted/20 rounded-lg border-l-2 border-purple-500">
                          <div className="font-bold text-sm mb-1">{dep.service_name}</div>
                          <div className="text-xs text-muted-foreground line-clamp-3">{dep.ai_diagnosis}</div>
                       </div>
                     ))}
                  </CardContent>
                </Card>
              </div>
            </TabsContent>

            {/* ── Security Intel Tab ───────────────────────────────── */}
            <TabsContent value="security" className="space-y-6 mt-6">
              {/* Header and Trigger Bar */}
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                <div>
                  <h2 className="text-xl font-bold flex items-center gap-2">
                    <Shield className="w-5 h-5 text-purple-400" />
                    Security Infrastructure & Threat Intelligence
                  </h2>
                  <p className="text-sm text-muted-foreground mt-0.5">
                    Unified telemetry, kernel eBPF monitoring, ML WAF, and autonomous threat defense.
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <Button
                    onClick={handleRunSecurityAnalysis}
                    disabled={securityAnalyzing}
                    className="bg-purple-600 hover:bg-purple-700 text-white shadow-sm flex items-center gap-2"
                  >
                    {securityAnalyzing ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <Sparkles className="w-4 h-4 text-purple-200" />
                    )}
                    Run AI Threat Assessment
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => refreshSecurityData(securitySourceFilter !== 'all' ? securitySourceFilter : undefined)}
                    disabled={securityLoading}
                  >
                    <RefreshCw className={cn("w-4 h-4 mr-1.5", securityLoading && "animate-spin")} />
                    Refresh
                  </Button>
                </div>
              </div>

              {/* Subsystems Status Matrix (6 Cards) */}
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                {/* Falco eBPF */}
                <Card className="bg-card border-border">
                  <CardContent className="p-4 space-y-2">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2 font-semibold text-sm">
                        <ShieldAlert className="w-4 h-4 text-blue-400" />
                        <span>Falco Kernel eBPF</span>
                      </div>
                      <Badge variant="outline" className={cn(
                        "text-xs px-2 py-0.5",
                        securityStatus?.falco?.running ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20" : "bg-zinc-500/10 text-zinc-400"
                      )}>
                        {securityStatus?.falco?.running ? "Active" : "Offline"}
                      </Badge>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      Real-time kernel syscall tracing & anomaly alerting.
                    </p>
                    <div className="pt-2 border-t border-border/50 grid grid-cols-2 gap-2 text-xs">
                      <div>
                        <span className="text-muted-foreground block text-[10px] uppercase">Driver</span>
                        <span className="font-mono font-medium">{securityStatus?.falco?.driver || "modern-bpf"}</span>
                      </div>
                      <div>
                        <span className="text-muted-foreground block text-[10px] uppercase">Events Sniffed</span>
                        <span className="font-mono font-medium">{securityStatus?.falco?.events_detected ?? securityEvents?.summary?.falco_alerts_count ?? 0}</span>
                      </div>
                    </div>
                  </CardContent>
                </Card>

                {/* CrowdSec IPS */}
                <Card className="bg-card border-border">
                  <CardContent className="p-4 space-y-2">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2 font-semibold text-sm">
                        <Shield className="w-4 h-4 text-purple-400" />
                        <span>CrowdSec IPS</span>
                      </div>
                      <Badge variant="outline" className={cn(
                        "text-xs px-2 py-0.5",
                        securityStatus?.crowdsec?.running ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20" : "bg-zinc-500/10 text-zinc-400"
                      )}>
                        {securityStatus?.crowdsec?.running ? "Armed" : "Disabled"}
                      </Badge>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      Multi-server crowd intelligence & behavioral bouncer.
                    </p>
                    <div className="pt-2 border-t border-border/50 grid grid-cols-2 gap-2 text-xs">
                      <div>
                        <span className="text-muted-foreground block text-[10px] uppercase">Active Decisions</span>
                        <span className="font-mono font-medium">{securityEvents?.summary?.crowdsec_bans_count ?? securityStatus?.crowdsec?.active_bans ?? 0} banned</span>
                      </div>
                      <div>
                        <span className="text-muted-foreground block text-[10px] uppercase">First-Strike</span>
                        <span className="font-mono font-medium">{securityStatus?.crowdsec?.first_strike_enabled ? "Active" : "Standard"}</span>
                      </div>
                    </div>
                  </CardContent>
                </Card>

                {/* Fail2ban Host Defense */}
                <Card className="bg-card border-border">
                  <CardContent className="p-4 space-y-2">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2 font-semibold text-sm">
                        <Ban className="w-4 h-4 text-amber-400" />
                        <span>Fail2ban Host Defense</span>
                      </div>
                      <Badge variant="outline" className={cn(
                        "text-xs px-2 py-0.5",
                        securityStatus?.fail2ban?.active ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20" : "bg-zinc-500/10 text-zinc-400"
                      )}>
                        {securityStatus?.fail2ban?.active ? "Active" : "Inactive"}
                      </Badge>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      Brute-force mitigation on SSH, auth endpoints, & DoS limits.
                    </p>
                    <div className="pt-2 border-t border-border/50 grid grid-cols-2 gap-2 text-xs">
                      <div>
                        <span className="text-muted-foreground block text-[10px] uppercase">Jails</span>
                        <span className="font-mono font-medium">{securityStatus?.fail2ban?.jails?.length || 0} active</span>
                      </div>
                      <div>
                        <span className="text-muted-foreground block text-[10px] uppercase">Currently Banned</span>
                        <span className="font-mono font-medium">{securityEvents?.summary?.fail2ban_banned_count || 0} IPs</span>
                      </div>
                    </div>
                  </CardContent>
                </Card>

                {/* open-appsec ML WAF */}
                <Card className="bg-card border-border">
                  <CardContent className="p-4 space-y-2">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2 font-semibold text-sm">
                        <Lock className="w-4 h-4 text-teal-400" />
                        <span>open-appsec ML WAF</span>
                      </div>
                      <Badge variant="outline" className={cn(
                        "text-xs px-2 py-0.5",
                        securityStatus?.openappsec?.agent_running ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20" : "bg-zinc-500/10 text-zinc-400"
                      )}>
                        {securityStatus?.openappsec?.policy_mode?.toUpperCase() || "MONITORING"}
                      </Badge>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      Dual-engine AI payload parsing & OWASP Top 10 defense.
                    </p>
                    <div className="pt-2 border-t border-border/50 grid grid-cols-2 gap-2 text-xs">
                      <div>
                        <span className="text-muted-foreground block text-[10px] uppercase">Agent / Envoy</span>
                        <span className="font-mono font-medium">
                          {securityStatus?.openappsec?.agent_running ? "OK" : "Down"} / {securityStatus?.openappsec?.envoy_running ? "OK" : "Down"}
                        </span>
                      </div>
                      <div>
                        <span className="text-muted-foreground block text-[10px] uppercase">Shadow Port</span>
                        <span className="font-mono font-medium">:{securityStatus?.openappsec?.shadow_port || 8089}</span>
                      </div>
                    </div>
                  </CardContent>
                </Card>

                {/* MicroVM Sandbox & Hardening */}
                <Card className="bg-card border-border">
                  <CardContent className="p-4 space-y-2">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2 font-semibold text-sm">
                        <Cpu className="w-4 h-4 text-emerald-400" />
                        <span>Sandboxed Runtime</span>
                      </div>
                      <Badge variant="outline" className={cn(
                        "text-xs px-2 py-0.5",
                        securityStatus?.container_runtime?.sandboxed ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20" : "bg-blue-500/10 text-blue-400 border-blue-500/20"
                      )}>
                        {securityStatus?.container_runtime?.active || "runc"}
                      </Badge>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      MicroVM hypervisor or hardened container namespace boundary.
                    </p>
                    <div className="pt-2 border-t border-border/50 grid grid-cols-2 gap-2 text-xs">
                      <div>
                        <span className="text-muted-foreground block text-[10px] uppercase">AppArmor / Seccomp</span>
                        <span className="font-mono font-medium">
                          {securityStatus?.apparmor?.enabled ? "Active" : "Off"} / {securityStatus?.seccomp?.enabled ? "Active" : "Off"}
                        </span>
                      </div>
                      <div>
                        <span className="text-muted-foreground block text-[10px] uppercase">Kernel Isolation</span>
                        <span className="font-mono font-medium">{securityStatus?.kernel_hardening?.enabled ? "Hardened" : "Standard"}</span>
                      </div>
                    </div>
                  </CardContent>
                </Card>

                {/* Trivy CVE Scanner & Device Trust */}
                <Card className="bg-card border-border">
                  <CardContent className="p-4 space-y-2">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2 font-semibold text-sm">
                        <Bug className="w-4 h-4 text-rose-400" />
                        <span>Trivy CVE & Device Trust</span>
                      </div>
                      <Badge variant="outline" className={cn(
                        "text-xs px-2 py-0.5",
                        securityStatus?.trivy?.enabled ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20" : "bg-zinc-500/10 text-zinc-400"
                      )}>
                        {securityStatus?.trivy?.enabled ? "Enforced" : "Inactive"}
                      </Badge>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      Build-time container vulnerability gates & WebAuthn device trust.
                    </p>
                    <div className="pt-2 border-t border-border/50 grid grid-cols-2 gap-2 text-xs">
                      <div>
                        <span className="text-muted-foreground block text-[10px] uppercase">Gate Policy</span>
                        <span className="font-mono font-medium">{securityStatus?.trivy?.fail_on_severity || "CRITICAL,HIGH"}</span>
                      </div>
                      <div>
                        <span className="text-muted-foreground block text-[10px] uppercase">Device Trust</span>
                        <span className="font-mono font-medium">{securityStatus?.device_trust?.enabled ? "MFA Enforced" : "Standard"}</span>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              </div>

              {/* AI Security Threat Posture & Defense Analysis Card */}
              <Card className="border-border">
                <CardHeader className="pb-3">
                  <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                    <div className="flex items-center gap-2">
                      <Brain className="w-5 h-5 text-purple-400" />
                      <CardTitle className="text-base font-semibold">AI Threat Posture & Attack Vector Analysis</CardTitle>
                    </div>
                    {securityAnalysis && (
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-muted-foreground">
                          Analyzed: {new Date(securityAnalysis.analyzed_at).toLocaleTimeString()}
                        </span>
                        <Badge
                          className={cn(
                            "font-semibold uppercase tracking-wider text-xs",
                            securityAnalysis.threat_level === 'LOW' && "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
                            securityAnalysis.threat_level === 'ELEVATED' && "bg-amber-500/15 text-amber-400 border-amber-500/30",
                            securityAnalysis.threat_level === 'HIGH' && "bg-orange-500/15 text-orange-400 border-orange-500/30",
                            securityAnalysis.threat_level === 'SEVERE' && "bg-red-500/15 text-red-400 border-red-500/30"
                          )}
                        >
                          {securityAnalysis.threat_level} THREAT
                        </Badge>
                      </div>
                    )}
                  </div>
                  <CardDescription>
                    Synthesized neural evaluation of active bans, open-appsec ML anomalies, Falco eBPF security events, and host audit logs.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  {securityAnalysis ? (
                    <>
                      {/* Risk Gauge Bar */}
                      <div className="space-y-1.5 p-3 rounded-lg bg-muted/20 border border-border/50">
                        <div className="flex justify-between items-center text-xs">
                          <span className="font-medium text-muted-foreground uppercase tracking-wider text-[11px]">System Risk Score</span>
                          <span className="font-mono font-bold text-sm">{securityAnalysis.risk_score} <span className="text-muted-foreground font-normal text-xs">/ 100</span></span>
                        </div>
                        <Progress
                          value={securityAnalysis.risk_score}
                          className={cn(
                            "h-2",
                            securityAnalysis.risk_score < 25 ? "[&>div]:bg-emerald-500" :
                            securityAnalysis.risk_score < 60 ? "[&>div]:bg-amber-500" :
                            securityAnalysis.risk_score < 80 ? "[&>div]:bg-orange-500" : "[&>div]:bg-red-500"
                          )}
                        />
                      </div>

                      {/* Executive Summary */}
                      <div className="text-sm bg-muted/30 p-4 rounded-lg leading-relaxed border-l-2 border-purple-500 whitespace-pre-wrap">
                        {securityAnalysis.executive_summary}
                      </div>

                      {/* Vectors & Recommendations */}
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        {/* Vectors */}
                        <div className="p-3 rounded-lg bg-muted/10 border border-border/50 space-y-2">
                          <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
                            <Target className="w-3.5 h-3.5 text-amber-400" />
                            Identified Attack Vectors ({securityAnalysis.attack_vectors?.length || 0})
                          </h4>
                          <ul className="space-y-1.5 text-xs">
                            {securityAnalysis.attack_vectors?.map((vec, idx) => (
                              <li key={idx} className="flex items-start gap-2 text-muted-foreground">
                                <span className="text-amber-500 font-bold">•</span>
                                <span>{vec}</span>
                              </li>
                            ))}
                          </ul>
                        </div>

                        {/* Recommendations */}
                        <div className="p-3 rounded-lg bg-muted/10 border border-border/50 space-y-2">
                          <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
                            <ShieldCheck className="w-3.5 h-3.5 text-emerald-400" />
                            Hardening Recommendations ({securityAnalysis.hardening_actions?.length || 0})
                          </h4>
                          <ul className="space-y-1.5 text-xs">
                            {securityAnalysis.hardening_actions?.map((act, idx) => (
                              <li key={idx} className="flex items-start gap-2 text-muted-foreground">
                                <span className="text-emerald-500 font-bold">✓</span>
                                <span>{act}</span>
                              </li>
                            ))}
                          </ul>
                        </div>
                      </div>
                    </>
                  ) : (
                    <div className="p-6 text-center rounded-lg border border-dashed border-border/60 bg-muted/10">
                      <Sparkles className="w-8 h-8 mx-auto text-purple-400 mb-2 opacity-60" />
                      <p className="text-sm font-medium">No real-time threat assessment generated yet</p>
                      <p className="text-xs text-muted-foreground mt-1 max-w-md mx-auto">
                        Click &quot;Run AI Threat Assessment&quot; to synthesize all recent kernel events, CrowdSec bans, open-appsec ML verdicts, and Fail2ban logs.
                      </p>
                    </div>
                  )}
                </CardContent>
              </Card>

              {/* Unified Security Activity & Audit Stream Console */}
              <Card className="border-border">
                <CardHeader className="pb-3">
                  <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                    <div>
                      <CardTitle className="text-base font-semibold flex items-center gap-2">
                        <Activity className="w-4 h-4 text-emerald-400" />
                        Security Event & Audit Stream
                      </CardTitle>
                      <CardDescription>
                        Live aggregate feed across Falco eBPF, CrowdSec, Fail2ban, WAF, and CVE scans.
                      </CardDescription>
                    </div>
                    {/* Summary count pills */}
                    <div className="flex flex-wrap items-center gap-1.5 text-xs">
                      <Badge variant="outline" className="bg-muted/40">
                        Total: {securityEvents?.summary?.total_events || 0}
                      </Badge>
                      <Badge variant="outline" className="bg-blue-500/10 text-blue-400 border-blue-500/20">
                        Falco: {securityEvents?.summary?.falco_alerts_count || 0}
                      </Badge>
                      <Badge variant="outline" className="bg-purple-500/10 text-purple-400 border-purple-500/20">
                        CrowdSec: {securityEvents?.summary?.crowdsec_bans_count || 0}
                      </Badge>
                      <Badge variant="outline" className="bg-amber-500/10 text-amber-400 border-amber-500/20">
                        Fail2ban: {securityEvents?.summary?.fail2ban_banned_count || 0}
                      </Badge>
                      <Badge variant="outline" className="bg-teal-500/10 text-teal-400 border-teal-500/20">
                        WAF: {securityEvents?.summary?.waf_events_count || 0}
                      </Badge>
                      <Badge variant="outline" className="bg-rose-500/10 text-rose-400 border-rose-500/20">
                        CVEs: {securityEvents?.summary?.trivy_cves_count || 0}
                      </Badge>
                    </div>
                  </div>

                  {/* Filter Bar */}
                  <div className="pt-3 flex flex-col md:flex-row gap-3">
                    {/* Search */}
                    <div className="relative flex-1">
                      <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
                      <Input
                        placeholder="Search by event title, IP address, container, or rule..."
                        value={securitySearchQuery}
                        onChange={(e) => setSecuritySearchQuery(e.target.value)}
                        className="pl-9 h-9 text-xs"
                      />
                    </div>

                    {/* Source Filter Tabs */}
                    <div className="flex flex-wrap gap-1 items-center">
                      {[
                        { id: 'all', label: 'All Sources' },
                        { id: 'falco', label: 'Falco' },
                        { id: 'crowdsec', label: 'CrowdSec' },
                        { id: 'fail2ban', label: 'Fail2ban' },
                        { id: 'openappsec', label: 'WAF' },
                        { id: 'trivy', label: 'Trivy' },
                        { id: 'auditd', label: 'Auditd' },
                      ].map((tab) => (
                        <Button
                          key={tab.id}
                          variant={securitySourceFilter === tab.id ? "default" : "outline"}
                          size="sm"
                          className="h-8 text-xs px-2.5 py-0"
                          onClick={() => {
                            setSecuritySourceFilter(tab.id);
                            refreshSecurityData(tab.id !== 'all' ? tab.id : undefined);
                          }}
                        >
                          {tab.label}
                        </Button>
                      ))}
                    </div>

                    {/* Severity Filter */}
                    <div className="flex gap-1 items-center">
                      {['all', 'CRITICAL', 'HIGH', 'WARNING', 'INFO'].map((sev) => (
                        <Button
                          key={sev}
                          variant={securitySeverityFilter === sev ? "secondary" : "ghost"}
                          size="sm"
                          className="h-8 text-xs px-2 py-0"
                          onClick={() => setSecuritySeverityFilter(sev)}
                        >
                          {sev === 'all' ? 'All Sev' : sev}
                        </Button>
                      ))}
                    </div>
                  </div>
                </CardHeader>
                <CardContent>
                  {/* Event Stream List */}
                  <div className="space-y-2">
                    {filteredSecurityEvents.length === 0 ? (
                      <div className="text-center py-10 text-muted-foreground text-sm">
                        <ShieldCheck className="h-8 w-8 mx-auto mb-2 text-emerald-500/40" />
                        <p>No security activity events matching current filters.</p>
                        <p className="text-xs text-muted-foreground mt-0.5">All monitored security layers are operating normally.</p>
                      </div>
                    ) : (
                      filteredSecurityEvents.map((evt) => {
                        const isExpanded = expandedEventId === evt.id;
                        const isCrowdSecBan = evt.source === 'crowdsec' && (evt.type === 'decision' || evt.target);
                        const targetIp = evt.target && /^[\d\.:a-fA-F]+$/.test(evt.target) ? evt.target : null;

                        return (
                          <div
                            key={evt.id}
                            className="p-3 bg-muted/20 hover:bg-muted/30 transition-colors rounded-lg border border-border/50 text-xs space-y-2"
                          >
                            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                              <div className="flex items-center gap-2 flex-wrap">
                                {/* Severity Badge */}
                                <Badge
                                  variant="outline"
                                  className={cn(
                                    "text-[10px] font-semibold uppercase px-1.5 py-0",
                                    evt.severity === 'CRITICAL' && "bg-red-500/15 text-red-400 border-red-500/30",
                                    evt.severity === 'HIGH' && "bg-orange-500/15 text-orange-400 border-orange-500/30",
                                    evt.severity === 'WARNING' && "bg-amber-500/15 text-amber-400 border-amber-500/30",
                                    evt.severity === 'INFO' && "bg-sky-500/15 text-sky-400 border-sky-500/30"
                                  )}
                                >
                                  {evt.severity}
                                </Badge>

                                {/* Source Chip */}
                                <Badge
                                  variant="secondary"
                                  className={cn(
                                    "text-[10px] uppercase font-mono px-1.5 py-0",
                                    evt.source === 'falco' && "bg-blue-500/10 text-blue-400",
                                    evt.source === 'crowdsec' && "bg-purple-500/10 text-purple-400",
                                    evt.source === 'fail2ban' && "bg-amber-500/10 text-amber-400",
                                    evt.source === 'openappsec' && "bg-teal-500/10 text-teal-400",
                                    evt.source === 'trivy' && "bg-rose-500/10 text-rose-400",
                                    evt.source === 'auditd' && "bg-zinc-500/10 text-zinc-400"
                                  )}
                                >
                                  {evt.source}
                                </Badge>

                                {/* Title */}
                                <span className="font-semibold text-foreground">{evt.title}</span>

                                {/* Target pill */}
                                {evt.target && (
                                  <span className="px-1.5 py-0.5 rounded bg-muted/60 text-muted-foreground font-mono text-[10px]">
                                    target: {evt.target}
                                  </span>
                                )}
                              </div>

                              {/* Timestamp & Actions */}
                              <div className="flex items-center gap-2 shrink-0 text-muted-foreground">
                                <span>{new Date(evt.timestamp).toLocaleString()}</span>

                                {/* Unban Action if IP target */}
                                {targetIp && isCrowdSecBan && (
                                  <Button
                                    variant="ghost"
                                    size="sm"
                                    className="h-6 px-2 text-[10px] text-red-400 hover:text-red-300 hover:bg-red-500/10"
                                    disabled={unbanningIp === targetIp}
                                    onClick={() => handleUnbanIp(targetIp)}
                                  >
                                    {unbanningIp === targetIp ? (
                                      <Loader2 className="w-3 h-3 animate-spin mr-1" />
                                    ) : (
                                      <Ban className="w-3 h-3 mr-1" />
                                    )}
                                    Unban IP
                                  </Button>
                                )}

                                {/* Expand Payload */}
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  className="h-6 px-1.5 text-[10px]"
                                  onClick={() => setExpandedEventId(isExpanded ? null : evt.id)}
                                >
                                  {isExpanded ? (
                                    <ChevronUp className="w-3.5 h-3.5" />
                                  ) : (
                                    <ChevronDown className="w-3.5 h-3.5" />
                                  )}
                                </Button>
                              </div>
                            </div>

                            {/* Details text */}
                            <div className="text-muted-foreground pl-1 break-words font-mono text-[11px]">
                              {evt.details}
                            </div>

                            {/* Collapsible raw data */}
                            {isExpanded && (
                              <div className="mt-2 pt-2 border-t border-border/40 space-y-1.5">
                                <div className="flex items-center justify-between">
                                  <span className="text-[10px] uppercase font-semibold text-muted-foreground flex items-center gap-1">
                                    <Terminal className="w-3 h-3" /> Raw Event Payload
                                  </span>
                                  <Button
                                    variant="ghost"
                                    size="sm"
                                    className="h-5 px-1.5 text-[10px]"
                                    onClick={() => handleCopyJson(evt.id, evt.raw || evt)}
                                  >
                                    {copiedEventId === evt.id ? (
                                      <>
                                        <Check className="w-3 h-3 text-emerald-400 mr-1" /> Copied
                                      </>
                                    ) : (
                                      <>
                                        <Copy className="w-3 h-3 mr-1" /> Copy JSON
                                      </>
                                    )}
                                  </Button>
                                </div>
                                <pre className="p-2 rounded bg-black/40 border border-border/40 text-[10px] font-mono text-zinc-300 overflow-x-auto max-h-48 scrollbar-thin">
                                  {JSON.stringify(evt.raw || evt, null, 2)}
                                </pre>
                              </div>
                            )}
                          </div>
                        );
                      })
                    )}
                  </div>
                </CardContent>
              </Card>
            </TabsContent>

            {/* ── Anomalies Tab ──────────────────────────────────────── */}
            <TabsContent value="anomalies" className="mt-6">
              <Card>
                <CardHeader>
                   <CardTitle>Detected Anomalies & Auto-Remediation</CardTitle>
                   <CardDescription>History of AI-detected issues and autonomous fixes</CardDescription>
                </CardHeader>
                <CardContent>
                   <div className="space-y-2">
                     {anomalies.length === 0 && <div className="text-center py-8 text-muted-foreground">No anomalies detected recently.</div>}
                     {anomalies.map(anom => (
                       <div key={anom.id} className="flex items-center justify-between p-4 bg-muted/20 rounded-lg border border-border/50">
                          <div className="flex items-center gap-4">
                             <div className={cn(
                               "p-2 rounded-lg",
                               anom.severity === 'CRITICAL' ? "bg-red-500/10 text-red-500" : "bg-amber-500/10 text-amber-500"
                             )}>
                               <AlertTriangle size={20} />
                             </div>
                             <div>
                               <div className="font-bold">{anom.service_name}</div>
                               <div className="text-xs text-muted-foreground">{anom.issue_type} • {new Date(anom.detected_at).toLocaleString()}</div>
                             </div>
                          </div>
                          <div className="text-right">
                             {anom.auto_fixed ? (
                               <div className="flex items-center gap-2 text-emerald-500 text-sm font-bold">
                                 <CheckCircle2 size={16} /> Auto-Fixed
                               </div>
                             ) : (
                               <div className="flex items-center gap-2 text-zinc-500 text-sm font-bold">
                                 <XCircle size={16} /> Reported
                               </div>
                             )}
                             <div className="text-[10px] text-muted-foreground mt-1 max-w-[200px] truncate">
                               {anom.fix_result}
                             </div>
                          </div>
                       </div>
                     ))}
                   </div>
                </CardContent>
              </Card>
            </TabsContent>

            {/* ── Cost Tab ───────────────────────────────────────────── */}
            <TabsContent value="cost" className="mt-6">
               <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                 <Card>
                    <CardHeader>
                      <CardTitle>Cost Estimator</CardTitle>
                      <CardDescription>Compare monthly costs across providers</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                       <div className="grid grid-cols-2 gap-4">
                          <div>
                            <label className="text-xs font-bold uppercase text-muted-foreground">CPU Cores</label>
                            <Input
                              type="number" min={0.1} step={0.1}
                              value={costConfig.cpu}
                              onChange={e => setCostConfig({...costConfig, cpu: parseFloat(e.target.value)})}
                            />
                          </div>
                          <div>
                            <label className="text-xs font-bold uppercase text-muted-foreground">RAM (MB)</label>
                            <Input
                              type="number" min={128} step={128}
                              value={costConfig.ram}
                              onChange={e => setCostConfig({...costConfig, ram: parseInt(e.target.value)})}
                            />
                          </div>
                       </div>
                       <Button onClick={handleCostAnalysis} disabled={costLoading} className="w-full">
                         {costLoading ? <Loader2 className="animate-spin mr-2" /> : <DollarSign className="mr-2 h-4 w-4" />}
                         Analyze Costs
                       </Button>
                    </CardContent>
                 </Card>

                 <Card>
                    <CardHeader>
                      <CardTitle>AI Analysis</CardTitle>
                    </CardHeader>
                    <CardContent>
                       {costAnalysis ? (
                         <div className="space-y-4">
                           <div className="text-sm bg-muted/30 p-4 rounded-lg whitespace-pre-wrap leading-relaxed border-l-2 border-emerald-500">
                             {costAnalysis}
                           </div>
                           {costEstimates && (
                             <div className="space-y-2">
                                {Object.entries(costEstimates).map(([prov, cost]) => (
                                  <div key={prov} className="flex justify-between items-center text-sm p-2 rounded bg-muted/20">
                                    <span className="font-bold">{prov}</span>
                                    <span>${cost as number}/mo</span>
                                  </div>
                                ))}
                             </div>
                           )}
                         </div>
                       ) : (
                         <div className="text-center py-12 text-muted-foreground">Run analysis to see AI recommendations.</div>
                       )}
                    </CardContent>
                 </Card>
                </div>
            </TabsContent>

            {/* ── Services Tab ─────────────────────────────────────── */}
            <TabsContent value="services" className="mt-6">
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <Server className="text-blue-500" /> Platform Services Health
                  </CardTitle>
                  <CardDescription>Overview of all deployed services and their status</CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="space-y-2">
                    {allServices.length === 0 ? (
                      <div className="text-center py-8 text-muted-foreground">No services found.</div>
                    ) : (
                      allServices.map((svc: any) => {
                        const depStatus = svc.latest_deployment?.status || "UNKNOWN";
                        const isHealthy = depStatus === "SUCCESS" || depStatus === "RUNNING";
                        const isFailed = depStatus === "FAILED";
                        return (
                          <div key={svc.id} className="flex items-center justify-between p-3 rounded-lg border border-border bg-muted/10">
                            <div className="flex items-center gap-3">
                              <div className={`w-2 h-2 rounded-full ${isHealthy ? "bg-emerald-500" : isFailed ? "bg-red-500" : "bg-amber-500"}`} />
                              <div>
                                <div className="font-bold text-sm">{svc.name}</div>
                                <div className="text-xs text-muted-foreground">{svc.repository_url ? "Git" : svc.docker_image ? "Docker" : "Unknown source"}</div>
                              </div>
                            </div>
                            <div className="flex items-center gap-3">
                              <Badge variant={isHealthy ? "default" : isFailed ? "destructive" : "outline"} className="text-[10px]">
                                {depStatus}
                              </Badge>
                              {svc.latest_deployment?.created_at && (
                                <span className="text-[10px] text-muted-foreground">{new Date(svc.latest_deployment.created_at).toLocaleDateString()}</span>
                              )}
                            </div>
                          </div>
                        );
                      })
                    )}
                  </div>
                </CardContent>
              </Card>
            </TabsContent>

            {/* ── Autoscaler Tab ─────────────────────────────────── */}
            <TabsContent value="autoscaler" className="mt-6">
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <Gauge className="text-amber-500" /> Autoscaler Status
                  </CardTitle>
                  <CardDescription>Platform-wide autoscaling engine status and recent decisions</CardDescription>
                </CardHeader>
                <CardContent>
                  {autoscalerStatus ? (
                    <div className="space-y-4">
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                        <div className="p-3 rounded-lg bg-muted/30 text-center">
                          <div className="text-lg font-bold">{autoscalerStatus.status || "Unknown"}</div>
                          <div className="text-[10px] text-muted-foreground uppercase">Status</div>
                        </div>
                        <div className="p-3 rounded-lg bg-muted/30 text-center">
                          <div className="text-lg font-bold">{autoscalerStatus.uptime_seconds ? `${Math.floor(autoscalerStatus.uptime_seconds / 3600)}h` : "—"}</div>
                          <div className="text-[10px] text-muted-foreground uppercase">Uptime</div>
                        </div>
                        <div className="p-3 rounded-lg bg-muted/30 text-center">
                          <div className="text-lg font-bold">{autoscalerStatus.check_interval || "—"}</div>
                          <div className="text-[10px] text-muted-foreground uppercase">Check Interval</div>
                        </div>
                        <div className="p-3 rounded-lg bg-muted/30 text-center">
                          <div className="text-lg font-bold">{autoscalerStatus.services?.length ?? 0}</div>
                          <div className="text-[10px] text-muted-foreground uppercase">Tracked Services</div>
                        </div>
                      </div>

                      {/* Recent Decisions */}
                      {autoscalerStatus.recent_decisions && autoscalerStatus.recent_decisions.length > 0 && (
                        <div>
                          <h4 className="text-sm font-bold mb-2">Recent Decisions</h4>
                          <div className="space-y-2 max-h-60 overflow-y-auto">
                            {autoscalerStatus.recent_decisions.map((dec: any, i: number) => (
                              <div key={i} className="flex items-center justify-between p-2 rounded bg-muted/20 text-xs">
                                <div className="flex items-center gap-2">
                                  <Badge variant={dec.action === "scale_up" ? "destructive" : dec.action === "scale_down" ? "secondary" : "outline"} className="text-[9px]">
                                    {dec.action?.toUpperCase() || "N/A"}
                                  </Badge>
                                  <span className="font-medium">{dec.service_name || dec.service}</span>
                                </div>
                                <span className="text-muted-foreground">{dec.reason?.slice(0, 60) || "—"}</span>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="text-center py-12">
                      <Gauge className="w-10 h-10 text-muted-foreground mx-auto mb-2 opacity-40" />
                      <p className="text-sm text-muted-foreground">Autoscaler status unavailable.</p>
                      <p className="text-xs text-muted-foreground mt-1">The autoscaler runs on the admin endpoint.</p>
                    </div>
                  )}
                </CardContent>
              </Card>
            </TabsContent>

            {/* ── Chat Tab ───────────────────────────────────────────── */}
            <TabsContent value="chat" className="mt-6">
               <Card className="h-[500px] flex flex-col">
                  <CardHeader>
                     <CardTitle className="flex items-center gap-2">
                       <MessageSquare className="text-cyan-500" /> AI Ops Chat
                     </CardTitle>
                  </CardHeader>
                   <CardContent className="flex-1 flex flex-col gap-4">
                     <div className="flex-1 overflow-y-auto p-4 bg-muted/10 rounded-lg space-y-4">
                        {chatMessages.length > 0 && chatMessages.map((msg, i) => (
                          <div key={i} className={`flex gap-3 ${msg.role === 'user' ? 'justify-end' : ''}`}>
                             {msg.role === 'assistant' && (
                               <div className="w-8 h-8 rounded-full bg-purple-500/10 flex items-center justify-center shrink-0">
                                 <Bot size={16} className="text-purple-500" />
                               </div>
                             )}
                             <div className={`max-w-[80%] p-4 rounded-lg text-sm leading-relaxed whitespace-pre-wrap ${
                               msg.role === 'user'
                                 ? 'bg-purple-600 text-white rounded-tr-none'
                                 : 'bg-muted/30 border border-border/50 rounded-tl-none'
                             }`}>
                               {msg.role === 'assistant' && (
                                 <div className="text-xs font-bold text-muted-foreground mb-1">AI</div>
                               )}
                               {msg.content || (msg.role === 'assistant' && chatLoading ? <Loader2 className="animate-spin h-4 w-4" /> : '')}
                             </div>
                          </div>
                        ))}
                        {chatMessages.length === 0 && <div className="text-center text-muted-foreground mt-20">Ask me anything about your infrastructure.</div>}
                     </div>

                     <div className="flex gap-2">
                        <Input
                          value={chatInput}
                          onChange={e => setChatInput(e.target.value)}
                          onKeyDown={e => e.key === 'Enter' && !chatLoading && handleChat()}
                          placeholder="Ask about logs, costs, or configuration..."
                        />
                        <Button onClick={handleChat} disabled={chatLoading || !chatInput.trim()}>
                          {chatLoading ? <Loader2 className="animate-spin" /> : <Send size={16} />}
                        </Button>
                     </div>
                   </CardContent>
               </Card>
            </TabsContent>

            {/* ── Code Map Tab ─────────────────────────────────────────── */}
            <TabsContent value="codemap" className="mt-6">
              <CodeMapView />
            </TabsContent>

            {/* ── Servers Tab ─────────────────────────────────── */}
            <TabsContent value="servers" className="space-y-6 mt-6">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="text-lg font-bold flex items-center gap-2">
                    <Siren className="text-red-500" /> Server Incident Reports
                  </h3>
                  <p className="text-sm text-muted-foreground mt-1">
                    Consolidated incident timeline for each managed server.
                  </p>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={loadServerReports}
                  disabled={serverReportsLoading}
                >
                  <RefreshCw className={`w-4 h-4 mr-1 ${serverReportsLoading ? 'animate-spin' : ''}`} />
                  Load Reports
                </Button>
              </div>

              {Object.keys(serverReports).length === 0 && (
                <Card className="bg-muted/20 border-violet-500/10">
                  <CardContent className="py-10 text-center text-muted-foreground">
                    <Server className="h-10 w-10 mx-auto mb-3 opacity-30" />
                    <p>Click &quot;Load Reports&quot; to fetch incident data for each server.</p>
                  </CardContent>
                </Card>
              )}

              <div className="grid grid-cols-1 gap-4">
                {servers.map((srv: any) => {
                  const report = serverReports[srv.id];
                  return (
                    <Card key={srv.id} className="bg-muted/10 border-border">
                      <CardHeader className="pb-2">
                        <div className="flex items-center justify-between">
                          <CardTitle className="text-base flex items-center gap-2">
                            <Server className="h-4 w-4 text-blue-500" />
                            {srv.name || srv.host || srv.id}
                          </CardTitle>
                          <Badge variant={srv.status === 'ONLINE' ? 'default' : 'outline'}
                            className={srv.status === 'ONLINE' ? 'bg-emerald-500/10 text-emerald-500' : 'text-muted-foreground'}>
                            {srv.status || 'UNKNOWN'}
                          </Badge>
                        </div>
                        <CardDescription>
                          {srv.host} · {srv.services_count || 0} services
                        </CardDescription>
                      </CardHeader>
                      <CardContent>
                        {report === undefined ? (
                          <p className="text-sm text-muted-foreground">Click &quot;Load Reports&quot; to fetch.</p>
                        ) : report === null ? (
                          <p className="text-sm text-red-400">Failed to load report.</p>
                        ) : (
                          <div className="grid grid-cols-4 gap-3 text-center">
                            <div>
                              <p className="text-2xl font-bold">{report.total_events || 0}</p>
                              <p className="text-[10px] text-muted-foreground uppercase tracking-wider">Events</p>
                            </div>
                            <div>
                              <p className="text-2xl font-bold text-red-500">{report.critical || 0}</p>
                              <p className="text-[10px] text-muted-foreground uppercase tracking-wider">Critical</p>
                            </div>
                            <div>
                              <p className="text-2xl font-bold text-amber-500">{report.warning || 0}</p>
                              <p className="text-[10px] text-muted-foreground uppercase tracking-wider">Warning</p>
                            </div>
                            <div>
                              <p className="text-2xl font-bold text-blue-500">{report.info || 0}</p>
                              <p className="text-[10px] text-muted-foreground uppercase tracking-wider">Info</p>
                            </div>
                          </div>
                        )}
                        {report && report.events && report.events.length > 0 && (
                          <div className="mt-3 space-y-1 max-h-32 overflow-y-auto">
                            {report.events.slice(0, 5).map((evt: any, i: number) => (
                              <div key={i} className="flex items-center gap-2 text-xs">
                                <span className={`w-1.5 h-1.5 rounded-full ${
                                  evt.severity === 'critical' ? 'bg-red-500' :
                                  evt.severity === 'warning' ? 'bg-amber-500' : 'bg-blue-500'
                                }`} />
                                <span className="text-muted-foreground truncate flex-1">{evt.title}</span>
                                <Badge variant="outline" className="text-[9px]">{evt.type}</Badge>
                              </div>
                            ))}
                          </div>
                        )}
                      </CardContent>
                    </Card>
                  );
                })}
              </div>
            </TabsContent>

          </Tabs>

        </motion.div>
      </div>
      </RequiresTier>
    </DashboardShell>
  );
}
