'use client';

import { useState, useEffect, useCallback } from 'react';
import { Brain, RefreshCw, CheckCircle2, XCircle, Loader2, Send, Zap, Shield, DollarSign, Bot, AlertCircle } from 'lucide-react';
import { aiApi, AIProvidersResponse, AIProviderInfo } from '@/lib/api';
import { Card, CardContent, CardHeader, CardTitle, CardDescription, CardFooter } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';

const MODE_BADGE: Record<string, { label: string; variant: "default" | "secondary" | "outline" | "destructive"; icon: React.ReactNode }> = {
  mock:             { label: 'Mock Mode',        variant: 'secondary', icon: <Bot className="w-3 h-3 mr-1" /> },
  solo:             { label: 'Solo Mode',         variant: 'default',   icon: <Zap className="w-3 h-3 mr-1" /> },
  senate_committee: { label: 'Senate Committee',  variant: 'default',   icon: <Shield className="w-3 h-3 mr-1" /> },
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
        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const mode = data?.mode || 'mock';
  const badgeInfo = MODE_BADGE[mode] || MODE_BADGE.mock;

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight flex items-center gap-2">
            <Brain className="w-8 h-8 text-primary" />
            AI Engine
          </h1>
          <p className="text-muted-foreground mt-1 text-sm">
            Configure providers, monitor balances, and test AI orchestration.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Badge variant={badgeInfo.variant} className="py-1 px-3">
            {badgeInfo.icon}
            {badgeInfo.label}
          </Badge>
          <Button
            variant="outline"
            size="sm"
            onClick={handleRefreshBalance}
            disabled={refreshing}
          >
            <RefreshCw className={`w-4 h-4 mr-2 ${refreshing ? 'animate-spin' : ''}`} />
            {refreshing ? 'Checking...' : 'Check Balance'}
          </Button>
        </div>
      </div>

      <Alert>
        <AlertCircle className="h-4 w-4" />
        <AlertTitle>{data?.mode_label} Status</AlertTitle>
        <AlertDescription>
          {mode === 'senate_committee' && (
            <span>All active providers debate and vote on each answer through a 3-phase deliberation process (Propose → Review & Vote → Chair&apos;s Resolution).</span>
          )}
          {mode === 'solo' && (
            <span>Single provider handles all requests. Add more API keys to enable Senate Committee mode.</span>
          )}
          {mode === 'mock' && (
            <span>No API keys configured. Set at least one provider key in your environment or admin panel to activate AI functions.</span>
          )}
        </AlertDescription>
      </Alert>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {data?.providers.map((provider: AIProviderInfo) => (
          <Card key={provider.id} className={`${!provider.configured ? 'opacity-70' : ''}`}>
            <CardHeader className="pb-3 flex flex-row items-start justify-between space-y-0">
              <div>
                <CardTitle className="text-lg">{provider.name}</CardTitle>
                <CardDescription className="uppercase text-xs mt-1">{provider.id}</CardDescription>
              </div>
              {provider.configured ? (
                <CheckCircle2 className="w-5 h-5 text-green-500" />
              ) : (
                <XCircle className="w-5 h-5 text-muted-foreground" />
              )}
            </CardHeader>
            <CardContent className="space-y-3 pb-4">
              <div className="flex items-center justify-between text-sm">
                <span className="text-muted-foreground">Model</span>
                <Badge variant="secondary" className="font-mono text-xs">
                  {provider.model || 'Not set'}
                </Badge>
              </div>
              <div className="flex items-center justify-between text-sm">
                <span className="text-muted-foreground">Status</span>
                <span className={provider.configured ? 'text-green-600 font-medium dark:text-green-400' : 'text-muted-foreground'}>
                  {provider.configured ? 'Configured' : 'Not configured'}
                </span>
              </div>
              {provider.balance && (
                <div className="flex items-center justify-between text-sm pt-2 border-t">
                  <span className="text-muted-foreground flex items-center gap-1">
                    <DollarSign className="w-3 h-3" /> Balance
                  </span>
                  <span className="font-semibold text-primary">
                    {provider.balance.balance}
                  </span>
                </div>
              )}
            </CardContent>
          </Card>
        ))}
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Send className="w-5 h-5 text-primary" />
            Test AI Engine
          </CardTitle>
          <CardDescription>
            Send a test prompt to verify your configured providers and see how the active mode handles it.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-col sm:flex-row gap-3">
            <Input
              type="text"
              value={testPrompt}
              onChange={(e) => setTestPrompt(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleTest()}
              placeholder="Ask something... e.g. 'What stack does a Django app use?'"
              className="flex-1"
            />
            <Button
              onClick={handleTest}
              disabled={testing || !testPrompt.trim()}
              className="sm:w-32"
            >
              {testing ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Send className="w-4 h-4 mr-2" />}
              {testing ? 'Testing...' : 'Send'}
            </Button>
          </div>

          {testResult && (
            <div className="rounded-md bg-muted p-4 mt-4 border">
              <div className="flex items-center gap-2 mb-3">
                <Badge variant="outline">{testResult.provider}</Badge>
                <Badge variant="secondary">{testResult.mode}</Badge>
              </div>
              <p className="text-sm text-foreground whitespace-pre-wrap leading-relaxed">
                {testResult.response}
              </p>
            </div>
          )}
        </CardContent>
        <CardFooter className="border-t pt-4 text-xs text-muted-foreground">
          Configure API keys via environment variables (OPENAI_API_KEY, GROK_API_KEY, GEMINI_API_KEY, etc.) or the admin panel.
        </CardFooter>
      </Card>
    </div>
  );
}
