'use client';

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { EcosystemSuggestion } from '@/components/dashboard/EcosystemSuggestion';
import {
  Brain, Cpu, Zap, Shield, Eye, Activity, BarChart3, Sparkles,
  RefreshCw, Send, CheckCircle2, XCircle, Loader2, TrendingUp,
  Gauge, CircuitBoard, Bot, MessageSquare, AlertTriangle, Flame,
  Target, Lightbulb, DollarSign, Clock, ArrowUpRight, Settings, Save, Lock,
  Code2, Server, Siren
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import CodeMapView from '@/components/intelligence/CodeMapView';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { aiApi, type AIProvidersResponse } from '@/lib/api';
import api from '@/lib/api';
import { serversApi } from '@/lib/api';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useToast } from '@/components/ui/use-toast';
import { cn } from '@/lib/utils';
import { Switch } from '@/components/ui/switch';
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { RequiresTier } from '@/components/licensing/RequiresTier';

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

const baseUrlPlaceholders: Record<string, string> = {
  jules: 'https://api.jules.google.com/v1',
  localllm: 'http://localhost:11434/v1',
  freemodel: 'https://api.freemodel.dev/v1',
  opencode: 'https://api.opencode.ai/v1',
  mistral: 'https://api.mistral.ai/v1',
  nvidia: 'https://integrate.api.nvidia.com/v1',
  cloudflare: 'https://gateway.ai.cloudflare.com/v1/YOUR_ACCOUNT_ID/default/workers-ai',
  kimi: 'https://api.moonshot.ai/v1',
  orcarouter: 'https://api.orcarouter.com/v1',
  zenmax: 'https://api.zenmax.ai/v1',
  agentrouter: 'https://api.agentrouter.com/v1',
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

  // Config State
  const [configOpen, setConfigOpen] = useState(false);
  const [configData, setConfigData] = useState<Record<string, string>>({});
  const [fetchedModels, setFetchedModels] = useState<Record<string, string[]>>({});
  const [fetchingModelsId, setFetchingModelsId] = useState<string | null>(null);

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

  const fetchData = useCallback(async () => {
    try {
      const withTimeout = <T,>(promise: Promise<T>, ms: number, fallback: T): Promise<T> =>
        Promise.race([
          promise,
          new Promise<T>((resolve) => setTimeout(() => resolve(fallback), ms)),
        ]);

      const [prov, deps, anoms, rep, svcs, autoStatus, svrs] = await Promise.all([
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
      ]);
      setProviders(prov);
      setAnomalies(anoms);
      setReport(rep);
      setAllServices(svcs);
      setAutoscalerStatus(autoStatus);
      setServers(svrs);

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

  const handleUpdateProviders = async () => {
    try {
      await aiApi.updateProviders(configData);
      toast({ title: "Providers Updated", description: "AI settings saved successfully." });
      setConfigOpen(false);
      setConfigData({});
      fetchData();
    } catch (err) {
      toast({ title: "Update Failed", description: "Could not save AI settings.", variant: "destructive" });
    }
  };

  const handleFetchProviderModels = async (providerId: string) => {
    setFetchingModelsId(providerId);
    try {
      const apiKey = configData[`${providerId}_api_key`] || '';
      const baseUrl = configData[`${providerId}_base_url`] || '';
      const result = await aiApi.fetchModels(providerId, apiKey, baseUrl);
      if (result.models && result.models.length > 0) {
        setFetchedModels(prev => ({ ...prev, [providerId]: result.models }));
        setConfigData(prev => ({ ...prev, [`${providerId}_model`]: result.models[0] }));
        toast({ title: `${providerId} models loaded`, description: `Found ${result.models.length} models.` });
      } else {
        toast({ title: "No models found", description: "Check your API key and base URL.", variant: "destructive" });
      }
    } catch (err: any) {
      toast({ title: "Fetch failed", description: err?.response?.data?.error || err.message || "Could not fetch models.", variant: "destructive" });
    } finally {
      setFetchingModelsId(null);
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
              <Button variant="outline" onClick={() => setConfigOpen(!configOpen)}>
                <Settings className="w-4 h-4 mr-2" /> Configure
              </Button>
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

          {/* ── Configuration Panel ────────────────────────────────── */}
          <AnimatePresence>
            {configOpen && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: 'auto', opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                className="overflow-hidden"
              >
                <Card className="bg-muted/20 border-purple-500/20 mb-8">
                  <CardContent className="p-6">
                    <h3 className="font-bold text-lg mb-4 flex items-center gap-2">
                       <Shield className="text-purple-500" size={18} /> Configure AI Providers
                    </h3>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                       {[
                         { id: "openai", label: "OpenAI" },
                         { id: "gemini", label: "Gemini" },
                         { id: "claude", label: "Claude" },
                         { id: "openrouter", label: "OpenRouter" },
                         { id: "groq", label: "Groq" },
                         { id: "alibaba", label: "Alibaba" },
                         { id: "grok", label: "xAI Grok" },
                         { id: "deepseek", label: "DeepSeek" },
                         { id: "jules", label: "Jules", hasUrl: true },
                         { id: "localllm", label: "Local LLM", hasUrl: true },
                          { id: "smslycloud", label: "Trulay Cloud" },
                         { id: "freemodel", label: "FreeModel.dev", hasUrl: true },
                         { id: "opencode", label: "OpenCode", hasUrl: true },
                          { id: "mistral", label: "Mistral", hasUrl: true },
                          { id: "nvidia", label: "NVIDIA NIM", hasUrl: true },
                          { id: "cloudflare", label: "Cloudflare AI", hasUrl: true },
                          { id: "kimi", label: "Kimi", hasUrl: true },
                          { id: "orcarouter", label: "Orca Router", hasUrl: true },
                          { id: "zenmax", label: "ZenMax", hasUrl: true },
                          { id: "agentrouter", label: "Agent Router", hasUrl: true }
                       ].map(p => (
                           <div key={p.id} className="space-y-3 p-4 rounded-xl bg-muted/30 border border-border/50">
                             <div className="flex items-center justify-between">
                               <label className="text-sm font-bold uppercase tracking-tight text-foreground">{p.label}</label>
                               {providers?.providers?.find(pp => pp.id === p.id)?.configured && (
                                 <span className="text-[10px] bg-emerald-500/20 text-emerald-500 px-2 py-0.5 rounded-full font-bold">ACTIVE</span>
                               )}
                             </div>
                             <Input
                               type="password"
                               placeholder={`Enter ${p.label} API Key...`}
                               onChange={e => setConfigData({...configData, [`${p.id}_api_key`]: e.target.value})}
                             />
                             <div className="grid grid-cols-2 gap-2">
                               {fetchedModels[p.id] && fetchedModels[p.id].length > 0 ? (
                                 <select
                                   className="text-xs h-8 px-2 border rounded-md bg-background"
                                   value={configData[`${p.id}_model`] || ''}
                                   onChange={e => setConfigData({...configData, [`${p.id}_model`]: e.target.value})}
                                 >
                                   {fetchedModels[p.id].map((m: string) => (
                                     <option key={m} value={m}>{m}</option>
                                   ))}
                                 </select>
                               ) : (
                                 <div className="flex gap-1">
                                   <Input
                                     placeholder={`Model ID`}
                                     className="text-xs h-8 flex-1"
                                     value={configData[`${p.id}_model`] || ''}
                                     onChange={e => setConfigData({...configData, [`${p.id}_model`]: e.target.value})}
                                   />
                                   <button
                                     onClick={() => handleFetchProviderModels(p.id)}
                                     disabled={fetchingModelsId === p.id}
                                     className="px-2 h-8 text-[10px] bg-blue-500/10 text-blue-500 rounded border border-blue-500/20 hover:bg-blue-500/20 disabled:opacity-50 shrink-0"
                                   >
                                     {fetchingModelsId === p.id ? '...' : 'Fetch'}
                                   </button>
                                 </div>
                               )}
                               {p.hasUrl && (
                                 <Input
                                   placeholder={baseUrlPlaceholders[p.id] || 'Base URL'}
                                   className="text-xs h-8"
                                   onChange={e => setConfigData({...configData, [`${p.id}_base_url`]: e.target.value})}
                                 />
                               )}
                             </div>
                           </div>
                       ))}
                    </div>
                    <div className="flex justify-end mt-4">
                      <Button onClick={handleUpdateProviders} className="bg-purple-600 hover:bg-purple-700">
                        <Save className="w-4 h-4 mr-2" /> Save Configuration
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              </motion.div>
            )}
          </AnimatePresence>

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
              <TabsList className="inline-flex sm:grid w-max sm:w-full sm:grid-cols-8 bg-muted/20 gap-2 sm:gap-0 p-1">
                <TabsTrigger value="dashboard" className="rounded-full sm:rounded-sm px-4">Dashboard</TabsTrigger>
                <TabsTrigger value="anomalies" className="rounded-full sm:rounded-sm px-4">Anomalies</TabsTrigger>
                <TabsTrigger value="services" className="rounded-full sm:rounded-sm px-4">Services</TabsTrigger>
                <TabsTrigger value="autoscaler" className="rounded-full sm:rounded-sm px-4">Autoscaler</TabsTrigger>
                <TabsTrigger value="cost" className="rounded-full sm:rounded-sm px-4">Cost Intel</TabsTrigger>
                <TabsTrigger value="codemap" className="rounded-full sm:rounded-sm px-4"><Code2 className="w-3.5 h-3.5 mr-1 inline" />Code Map</TabsTrigger>
                <TabsTrigger value="chat" className="rounded-full sm:rounded-sm px-4">AI Chat</TabsTrigger>
                <TabsTrigger value="servers" className="rounded-full sm:rounded-sm px-4"><Siren className="w-3.5 h-3.5 mr-1 inline" />Servers</TabsTrigger>
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
