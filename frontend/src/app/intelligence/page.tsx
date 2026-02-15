'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Brain, Cpu, Zap, Shield, Eye, Activity, BarChart3, Sparkles,
  RefreshCw, Send, CheckCircle2, XCircle, Loader2, TrendingUp,
  Gauge, CircuitBoard, Bot, MessageSquare, AlertTriangle, Flame,
  Target, Lightbulb, DollarSign, Clock, ArrowUpRight
} from 'lucide-react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { aiApi, type AIProvidersResponse } from '@/lib/api';
import api from '@/lib/api';

// ─── Types ──────────────────────────────────────────────────────────────────

interface DeploymentInsight {
  id: string;
  service_name: string;
  status: string;
  ai_diagnosis: string | null;
  created_at: string;
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
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [chatInput, setChatInput] = useState('');
  const [chatResponse, setChatResponse] = useState<string | null>(null);
  const [chatLoading, setChatLoading] = useState(false);
  const [chatProvider, setChatProvider] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const [prov, deps] = await Promise.all([
        aiApi.getProviders(true),
        api.get('/deployments/', { params: { page_size: 20 } }).then(r => r.data?.results || r.data || []).catch(() => []),
      ]);
      setProviders(prov);
      // Filter deployments that have AI diagnosis or are failed
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
    const interval = setInterval(fetchData, 15000);
    return () => clearInterval(interval);
  }, [fetchData]);

  const handleRefresh = () => {
    setRefreshing(true);
    fetchData();
  };

  const handleChat = async () => {
    if (!chatInput.trim()) return;
    setChatLoading(true);
    setChatResponse(null);
    try {
      const result = await aiApi.testPrompt(
        chatInput,
        'You are the SMSLY Hosting AI assistant. Help users with deployment, infrastructure, and DevOps questions. Be concise and actionable.'
      );
      setChatResponse(result.response);
      setChatProvider(result.provider);
    } catch (err: any) {
      setChatResponse(`Error: ${err.message || 'AI request failed'}`);
    } finally {
      setChatLoading(false);
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
      <div className="flex-1 p-8 relative z-10">
        <motion.div
          className="max-w-6xl mx-auto space-y-8"
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
                AI operations dashboard — monitor providers, insights, and platform intelligence
              </p>
            </div>
            <button
              onClick={handleRefresh}
              disabled={refreshing}
              className="px-4 py-2 rounded-lg border border-border text-sm flex items-center gap-2 hover:bg-muted/50 transition-colors"
            >
              <RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />
              Refresh
            </button>
          </div>

          {/* ── Stats Row ──────────────────────────────────────────── */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <motion.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.05 }}
              className="bg-card border border-border rounded-xl p-4"
            >
              <div className="flex items-center gap-3 mb-2">
                <div className="w-9 h-9 rounded-lg bg-purple-500/10 flex items-center justify-center">
                  <CircuitBoard className="text-purple-500" size={18} />
                </div>
                <span className="text-xs text-muted-foreground uppercase tracking-wider font-semibold">AI Mode</span>
              </div>
              <p className="text-lg font-bold">{modeConfig.label}</p>
              <p className="text-xs text-muted-foreground mt-0.5">{modeConfig.description}</p>
            </motion.div>

            <motion.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.1 }}
              className="bg-card border border-border rounded-xl p-4"
            >
              <div className="flex items-center gap-3 mb-2">
                <div className="w-9 h-9 rounded-lg bg-emerald-500/10 flex items-center justify-center">
                  <Zap className="text-emerald-500" size={18} />
                </div>
                <span className="text-xs text-muted-foreground uppercase tracking-wider font-semibold">Providers</span>
              </div>
              <p className="text-lg font-bold">{providers?.active_count || 0} <span className="text-sm text-muted-foreground font-normal">/ {providers?.total_available || 0}</span></p>
              <p className="text-xs text-muted-foreground mt-0.5">{activeProviders.length} configured and ready</p>
            </motion.div>

            <motion.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.15 }}
              className="bg-card border border-border rounded-xl p-4"
            >
              <div className="flex items-center gap-3 mb-2">
                <div className="w-9 h-9 rounded-lg bg-amber-500/10 flex items-center justify-center">
                  <Lightbulb className="text-amber-500" size={18} />
                </div>
                <span className="text-xs text-muted-foreground uppercase tracking-wider font-semibold">Diagnoses</span>
              </div>
              <p className="text-lg font-bold">{diagnosedDeploys.length}</p>
              <p className="text-xs text-muted-foreground mt-0.5">AI-diagnosed deployments</p>
            </motion.div>

            <motion.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.2 }}
              className="bg-card border border-border rounded-xl p-4"
            >
              <div className="flex items-center gap-3 mb-2">
                <div className="w-9 h-9 rounded-lg bg-red-500/10 flex items-center justify-center">
                  <AlertTriangle className="text-red-500" size={18} />
                </div>
                <span className="text-xs text-muted-foreground uppercase tracking-wider font-semibold">Failures</span>
              </div>
              <p className="text-lg font-bold">{failedDeploys.length}</p>
              <p className="text-xs text-muted-foreground mt-0.5">Recent failed deploys</p>
            </motion.div>
          </div>

          {/* ── Main Grid ──────────────────────────────────────────── */}
          <div className="grid grid-cols-3 gap-6">

            {/* AI Providers */}
            <div className="col-span-2 bg-card border border-border rounded-xl p-5 space-y-4">
              <div className="flex items-center justify-between">
                <h2 className="font-bold text-lg flex items-center gap-2">
                  <Cpu size={18} className="text-purple-500" />
                  AI Providers
                </h2>
                <a href="/settings/ai" className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1 transition">
                  Configure <ArrowUpRight size={12} />
                </a>
              </div>

              {providers?.providers?.length ? (
                <div className="space-y-3">
                  {providers.providers.map(prov => (
                    <div key={prov.id} className="flex items-center gap-3 p-3 rounded-lg bg-muted/30 border border-border/50">
                      <div className={`w-8 h-8 rounded-lg flex items-center justify-center ${prov.configured ? 'bg-emerald-500/10' : 'bg-zinc-500/10'}`}>
                        {prov.configured
                          ? <CheckCircle2 size={16} className="text-emerald-500" />
                          : <XCircle size={16} className="text-zinc-500" />
                        }
                      </div>
                      <div className="flex-1">
                        <p className="font-semibold text-sm">{prov.name}</p>
                        <p className="text-xs text-muted-foreground">
                          {prov.configured ? `Model: ${prov.model}` : 'Not configured'}
                        </p>
                      </div>
                      {prov.balance && (
                        <div className="text-right">
                          <p className="text-sm font-bold flex items-center gap-1">
                            <DollarSign size={12} className="text-emerald-500" />
                            {prov.balance.balance}
                          </p>
                          <p className="text-[10px] text-muted-foreground">{prov.balance.currency}</p>
                        </div>
                      )}
                      <span className={`text-[10px] px-2 py-0.5 rounded-full font-bold uppercase ${prov.configured ? 'bg-emerald-500/10 text-emerald-500' : 'bg-zinc-500/10 text-zinc-500'}`}>
                        {prov.configured ? 'Active' : 'Inactive'}
                      </span>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">No AI providers detected.</p>
              )}
            </div>

            {/* AI Mode Card */}
            <div className="bg-card border border-border rounded-xl p-5 space-y-4">
              <h2 className="font-bold text-lg flex items-center gap-2">
                <Gauge size={18} className="text-blue-500" />
                AI Mode
              </h2>
              <div className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-full ${modeConfig.color} text-sm font-bold`}>
                {modeConfig.icon}
                {modeConfig.label}
              </div>
              <p className="text-sm text-muted-foreground">{modeConfig.description}</p>

              <div className="border-t border-border pt-3 space-y-2">
                <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Capabilities</h3>
                <div className="space-y-1.5">
                  {[
                    { icon: <Eye size={14} />, label: 'Build failure diagnosis', active: true },
                    { icon: <Target size={14} />, label: 'Repo analysis & stack detection', active: true },
                    { icon: <Sparkles size={14} />, label: 'Ecosystem AI scanning', active: true },
                    { icon: <BarChart3 size={14} />, label: 'Deployment recommendations', active: providers?.active_count ? true : false },
                    { icon: <Flame size={14} />, label: 'Multi-provider consensus', active: providers?.mode === 'senate_committee' },
                  ].map((cap, i) => (
                    <div key={i} className="flex items-center gap-2 text-sm">
                      <span className={cap.active ? 'text-emerald-500' : 'text-zinc-600'}>{cap.icon}</span>
                      <span className={cap.active ? '' : 'text-muted-foreground line-through'}>{cap.label}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>

          {/* ── AI Chat ──────────────────────────────────────────── */}
          <div className="bg-card border border-border rounded-xl p-5 space-y-4">
            <h2 className="font-bold text-lg flex items-center gap-2">
              <MessageSquare size={18} className="text-cyan-500" />
              Ask the AI
              <span className="text-xs text-muted-foreground font-normal ml-1">— infrastructure & deployment questions</span>
            </h2>

            <div className="flex gap-3">
              <input
                value={chatInput}
                onChange={e => setChatInput(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && handleChat()}
                placeholder="How do I set up a reverse proxy for my Django app?"
                className="flex-1 px-4 py-2.5 rounded-lg bg-background border border-border text-sm focus:outline-none focus:ring-2 focus:ring-purple-500/30"
              />
              <button
                onClick={handleChat}
                disabled={chatLoading || !chatInput.trim()}
                className="px-5 py-2.5 rounded-lg bg-gradient-to-r from-purple-500 to-pink-600 text-white text-sm font-semibold flex items-center gap-2 shadow-lg shadow-purple-500/25 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {chatLoading ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
                Ask
              </button>
            </div>

            <AnimatePresence>
              {chatResponse && (
                <motion.div
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: 'auto' }}
                  exit={{ opacity: 0, height: 0 }}
                  className="p-4 rounded-lg bg-muted/30 border border-border/50"
                >
                  <div className="flex items-center gap-2 mb-2">
                    <Bot size={14} className="text-purple-500" />
                    <span className="text-xs font-semibold text-muted-foreground">
                      AI Response {chatProvider && `(${chatProvider})`}
                    </span>
                  </div>
                  <div className="text-sm whitespace-pre-wrap leading-relaxed">{chatResponse}</div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>

          {/* ── Deployment Insights ──────────────────────────────── */}
          {deployments.length > 0 && (
            <div className="bg-card border border-border rounded-xl p-5 space-y-4">
              <h2 className="font-bold text-lg flex items-center gap-2">
                <Activity size={18} className="text-amber-500" />
                Deployment Insights
                <span className="text-xs text-muted-foreground font-normal ml-1">— recent AI diagnoses</span>
              </h2>

              <div className="space-y-3">
                {deployments.map(dep => (
                  <div key={dep.id} className="p-3 rounded-lg bg-muted/30 border border-border/50">
                    <div className="flex items-center gap-2 mb-1">
                      {dep.status === 'FAILED'
                        ? <XCircle size={14} className="text-red-500" />
                        : <CheckCircle2 size={14} className="text-emerald-500" />
                      }
                      <span className="font-semibold text-sm">{dep.service_name || 'Unknown Service'}</span>
                      <span className={`text-[10px] px-1.5 py-0.5 rounded font-bold uppercase ${
                        dep.status === 'FAILED' ? 'bg-red-500/10 text-red-500' :
                        dep.status === 'SUCCESS' ? 'bg-emerald-500/10 text-emerald-500' :
                        'bg-yellow-500/10 text-yellow-500'
                      }`}>
                        {dep.status}
                      </span>
                      <span className="text-[10px] text-muted-foreground ml-auto flex items-center gap-1">
                        <Clock size={10} />
                        {new Date(dep.created_at).toLocaleString()}
                      </span>
                    </div>
                    {dep.ai_diagnosis && (
                      <div className="mt-2 pl-5 text-xs text-muted-foreground border-l-2 border-purple-500/30">
                        <span className="flex items-center gap-1 mb-0.5 text-purple-400 font-semibold">
                          <Brain size={10} /> AI Diagnosis
                        </span>
                        {dep.ai_diagnosis}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

        </motion.div>
      </div>
    </DashboardShell>
  );
}
