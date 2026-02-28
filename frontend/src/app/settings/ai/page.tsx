'use client';

import { useState, useEffect, useCallback } from 'react';
import { Brain, RefreshCw, CheckCircle2, XCircle, Loader2, Send, Zap, Shield, DollarSign, Bot } from 'lucide-react';
import { aiApi, AIProvidersResponse, AIProviderInfo } from '@/lib/api';

/** Provider brand colors */
const BRAND: Record<string, { gradient: string; icon: string }> = {
  openai:  { gradient: 'from-emerald-500 to-teal-600', icon: '🟢' },
  grok:    { gradient: 'from-blue-500 to-indigo-600',  icon: '🔵' },
  gemini:  { gradient: 'from-purple-500 to-pink-600',  icon: '🟣' },
  claude:  { gradient: 'from-orange-500 to-amber-600', icon: '🟠' },
  jules:   { gradient: 'from-fuchsia-500 to-rose-600', icon: '🔴' },
};

const MODE_BADGE: Record<string, { label: string; color: string; icon: React.ReactNode }> = {
  mock:             { label: 'Mock Mode',        color: 'bg-gray-500/20 text-gray-400',     icon: <Bot className="w-4 h-4" /> },
  solo:             { label: 'Solo Mode',         color: 'bg-blue-500/20 text-blue-400',     icon: <Zap className="w-4 h-4" /> },
  senate_committee: { label: 'Senate Committee',  color: 'bg-purple-500/20 text-purple-400', icon: <Shield className="w-4 h-4" /> },
};

export default function AISettingsPage() {
  const [data, setData] = useState<AIProvidersResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [testPrompt, setTestPrompt] = useState('');
  const [testResult, setTestResult] = useState<{ response: string; provider: string; mode: string } | null>(null);
  const [testing, setTesting] = useState(false);

  const fetchProviders = useCallback(async (withBalance = false) => {
    try {
      const result = await aiApi.getProviders(withBalance);
      setData(result);
    } catch (err) {
      console.error('Failed to fetch AI providers:', err);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    fetchProviders(false);
  }, [fetchProviders]);

  const handleRefreshBalance = async () => {
    setRefreshing(true);
    await fetchProviders(true);
  };

  const handleTest = async () => {
    if (!testPrompt.trim()) return;
    setTesting(true);
    setTestResult(null);
    try {
      const result = await aiApi.testPrompt(testPrompt);
      setTestResult(result);
    } catch (err: any) {
      setTestResult({ response: `Error: ${err?.response?.data?.error || err.message}`, provider: 'Error', mode: 'error' });
    } finally {
      setTesting(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <Loader2 className="w-8 h-8 animate-spin text-purple-500" />
      </div>
    );
  }

  const mode = data?.mode || 'mock';
  const badge = MODE_BADGE[mode] || MODE_BADGE.mock;

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="p-3 rounded-xl bg-gradient-to-br from-purple-500/20 to-pink-500/20">
            <Brain className="w-7 h-7 text-purple-400" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-white">AI Engine</h1>
            <p className="text-sm text-gray-400">
              {data?.active_count || 0} of {data?.total_available || 0} providers active
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          {/* Mode Badge */}
          <div className={`flex items-center gap-2 px-3 py-1.5 rounded-full text-sm font-medium ${badge.color}`}>
            {badge.icon}
            {badge.label}
          </div>
          {/* Refresh Balance */}
          <button
            onClick={handleRefreshBalance}
            disabled={refreshing}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-white/5 hover:bg-white/10 text-gray-300 text-sm transition-all border border-white/10"
          >
            <RefreshCw className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`} />
            {refreshing ? 'Checking...' : 'Check Balance'}
          </button>
        </div>
      </div>

      {/* Mode Description */}
      <div className="p-4 rounded-xl bg-white/5 border border-white/10">
        <p className="text-sm text-gray-300">
          <span className="font-semibold text-white">{data?.mode_label}</span>
          {mode === 'senate_committee' && (
            <span className="ml-2 text-gray-400">
              — All active providers debate and vote on each answer through a 3-phase deliberation process (Propose → Review & Vote → Chair&apos;s Resolution).
            </span>
          )}
          {mode === 'solo' && (
            <span className="ml-2 text-gray-400">
              — Single provider handles all requests. Add more API keys to enable Senate Committee mode.
            </span>
          )}
          {mode === 'mock' && (
            <span className="ml-2 text-gray-400">
              — No API keys configured. Set at least one provider key in your environment or admin panel.
            </span>
          )}
        </p>
      </div>

      {/* Provider Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {data?.providers.map((provider: AIProviderInfo) => {
          const brand = BRAND[provider.id] || { gradient: 'from-gray-500 to-gray-600', icon: '⚪' };
          return (
            <div
              key={provider.id}
              className={`relative rounded-xl border transition-all ${
                provider.configured
                  ? 'border-white/20 bg-white/5 hover:bg-white/8'
                  : 'border-white/5 bg-white/[0.02] opacity-60'
              }`}
            >
              {/* Status indicator */}
              <div className="absolute top-4 right-4">
                {provider.configured ? (
                  <CheckCircle2 className="w-5 h-5 text-green-400" />
                ) : (
                  <XCircle className="w-5 h-5 text-gray-600" />
                )}
              </div>

              <div className="p-5 space-y-3">
                {/* Provider header */}
                <div className="flex items-center gap-3">
                  <div className={`w-10 h-10 rounded-lg bg-gradient-to-br ${brand.gradient} flex items-center justify-center text-lg`}>
                    {brand.icon}
                  </div>
                  <div>
                    <h3 className="font-semibold text-white">{provider.name}</h3>
                    <p className="text-xs text-gray-500 capitalize">{provider.id}</p>
                  </div>
                </div>

                {/* Model */}
                <div className="flex items-center justify-between text-sm">
                  <span className="text-gray-400">Model</span>
                  <code className="text-xs px-2 py-0.5 rounded bg-white/10 text-gray-300">
                    {provider.model || 'Not set'}
                  </code>
                </div>

                {/* Status */}
                <div className="flex items-center justify-between text-sm">
                  <span className="text-gray-400">Status</span>
                  <span className={provider.configured ? 'text-green-400' : 'text-red-400'}>
                    {provider.configured ? 'Configured' : 'Not configured'}
                  </span>
                </div>

                {/* Balance */}
                {provider.balance && (
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-gray-400 flex items-center gap-1">
                      <DollarSign className="w-3 h-3" /> Balance
                    </span>
                    <span className="text-yellow-300 font-medium">
                      {provider.balance.balance}
                    </span>
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Test Prompt */}
      <div className="rounded-xl border border-white/10 bg-white/5 p-5 space-y-4">
        <h2 className="text-lg font-semibold text-white flex items-center gap-2">
          <Send className="w-5 h-5 text-purple-400" />
          Test AI Engine
        </h2>
        <p className="text-sm text-gray-400">
          Send a test prompt to see which mode and provider handles it.
        </p>

        <div className="flex gap-3">
          <input
            type="text"
            value={testPrompt}
            onChange={(e) => setTestPrompt(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleTest()}
            placeholder="Ask something... e.g. 'What stack does a Django app use?'"
            className="flex-1 px-4 py-2.5 rounded-lg bg-white/10 border border-white/10 text-white placeholder:text-gray-500 focus:outline-none focus:ring-2 focus:ring-purple-500/50 text-sm"
          />
          <button
            onClick={handleTest}
            disabled={testing || !testPrompt.trim()}
            className="px-5 py-2.5 rounded-lg bg-gradient-to-r from-purple-600 to-pink-600 text-white font-medium text-sm hover:from-purple-500 hover:to-pink-500 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2 transition-all"
          >
            {testing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
            {testing ? 'Thinking...' : 'Send'}
          </button>
        </div>

        {testResult && (
          <div className="mt-4 p-4 rounded-lg bg-black/30 border border-white/10 space-y-2">
            <div className="flex items-center gap-2 text-xs text-gray-400">
              <span className="px-2 py-0.5 rounded-full bg-purple-500/20 text-purple-300">{testResult.provider}</span>
              <span className="px-2 py-0.5 rounded-full bg-blue-500/20 text-blue-300">{testResult.mode}</span>
            </div>
            <div className="text-sm text-gray-200 whitespace-pre-wrap leading-relaxed">
              {testResult.response}
            </div>
          </div>
        )}
      </div>

      {/* Configuration Note */}
      <div className="text-xs text-gray-500 text-center pb-4">
        Configure API keys via environment variables (OPENAI_API_KEY, GROK_API_KEY, GEMINI_API_KEY, CLAUDE_API_KEY, JULES_API_KEY) 
        or the admin panel. Models are set via OPENAI_MODEL, GROK_MODEL, etc.
      </div>
    </div>
  );
}
