"use client";

import { useState, useEffect, useCallback } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Loader2, Sparkles, Send, Download } from "lucide-react";
import { aiApi } from "@/lib/api";
import { useToast } from "@/components/ui/use-toast";
import { Badge } from "@/components/ui/badge";

export function AiTab() {
  const { toast } = useToast();
  const [aiData, setAiData] = useState<any>(null);
  const [loadingAI, setLoadingAI] = useState(true);
  const [testingAI, setTestingAI] = useState(false);
  const [saving, setSaving] = useState(false);
  const [aiKeys, setAiKeys] = useState<Record<string, string>>({});
  const [aiModels, setAiModels] = useState<Record<string, string>>({});
  const [aiUrls, setAiUrls] = useState<Record<string, string>>({});
  const [testPrompt, setTestPrompt] = useState("");
  const [testResult, setTestResult] = useState<{ response: string; provider: string; mode: string } | null>(null);
  const [testingPrompt, setTestingPrompt] = useState(false);
  const [fetchingModels, setFetchingModels] = useState<string | null>(null);

  const fetchAIConfig = useCallback(async () => {
    try {
      const result = await aiApi.getProviders(true);
      setAiData(result);
    } catch {
      console.error("Failed to fetch AI config");
    } finally {
      setLoadingAI(false);
    }
  }, []);

  useEffect(() => {
    fetchAIConfig();
  }, [fetchAIConfig]);

  useEffect(() => {
    if (aiData?.providers) {
      const urls: Record<string, string> = {};
      const models: Record<string, string> = {};
      aiData.providers.forEach((p: any) => {
        if (p.base_url) urls[p.id] = p.base_url;
        if (p.model) models[p.id] = p.model;
      });
      setAiUrls((prev) => {
        const next = { ...urls };
        Object.keys(prev).forEach((key) => {
          if (prev[key]) next[key] = prev[key];
        });
        return next;
      });
      setAiModels((prev) => {
        const next = { ...models };
        Object.keys(prev).forEach((key) => {
          if (prev[key]) next[key] = prev[key];
        });
        return next;
      });
      // Auto-fetch models for configured providers
      aiData.providers
        .filter((p: any) => p.configured)
        .forEach((p: any) => {
          handleFetchModels(p.id);
        });
    }
  }, [aiData]);

  const handleTestAI = async () => {
    setTestingAI(true);
    try {
      const result = await aiApi.testPrompt("Hello, confirm you are working.");
      toast({ title: `${result.provider} responded`, description: result.response?.substring(0, 100) + "..." });
    } catch {
      toast({ title: "Test failed", description: "Could not reach AI provider.", variant: "destructive" });
    } finally {
      setTestingAI(false);
    }
  };

  const handleTestPrompt = async () => {
    if (!testPrompt.trim()) return;
    setTestingPrompt(true);
    setTestResult(null);
    try {
      const result = await aiApi.testPrompt(testPrompt);
      setTestResult(result);
    } catch (err: any) {
      setTestResult({ response: `Error: ${err?.response?.data?.error || err.message}`, provider: "Error", mode: "error" });
    } finally {
      setTestingPrompt(false);
    }
  };

  const handleFetchModels = async (providerId: string) => {
    setFetchingModels(providerId);
    try {
      const result = await aiApi.fetchModels(providerId, aiKeys[providerId], aiUrls[providerId]);
      if (result.models && result.models.length > 0) {
        setAiModels(prev => ({ ...prev, [providerId]: result.models[0] }));
        setModelOptions(prev => ({ ...prev, [providerId]: result.models }));
        toast({ title: `${providerId} models loaded`, description: `Found ${result.models.length} models. First one selected.` });
      } else {
        toast({ title: "No models found", description: "The provider returned no models. Check your API key and base URL.", variant: "destructive" });
      }
    } catch (err: any) {
      toast({ title: "Fetch failed", description: err?.response?.data?.error || err.message || "Could not fetch models.", variant: "destructive" });
    } finally {
      setFetchingModels(null);
    }
  };

  const [modelOptions, setModelOptions] = useState<Record<string, string[]>>({});

  const hasUrl = (id: string) => ["jules", "localllm", "freemodel", "opencode", "mistral", "nvidia", "cloudflare", "kimi", "orcarouter", "zenmax", "agentrouter"].includes(id);

  const baseUrlPlaceholders: Record<string, string> = {
    jules: "https://api.jules.google.com/v1",
    localllm: "http://localhost:11434/v1",
    freemodel: "https://api.freemodel.dev/v1",
    opencode: "https://api.opencode.ai/v1",
    mistral: "https://api.mistral.ai/v1",
    nvidia: "https://integrate.api.nvidia.com/v1",
    cloudflare: "https://gateway.ai.cloudflare.com/v1/YOUR_ACCOUNT_ID/default/workers-ai",
    kimi: "https://api.moonshot.ai/v1",
    orcarouter: "https://api.orcarouter.com/v1",
    zenmax: "https://api.zenmax.ai/v1",
    agentrouter: "https://api.agentrouter.com/v1",
  };

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Sparkles className="h-5 w-5 text-emerald-500" /> AI Engine
          </CardTitle>
          <CardDescription>
            {aiData?.mode_label || "Loading..."}
            {aiData?.mode === "senate_committee" && " - Providers debate and vote on each answer."}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center gap-3">
            <Badge variant={aiData?.mode === "senate_committee" ? "default" : aiData?.mode === "solo" ? "secondary" : "outline"}>
              {aiData?.mode === "senate_committee" ? "Senate Committee" : aiData?.mode === "solo" ? "Solo Mode" : "Mock Mode"}
            </Badge>
            <span className="text-sm text-muted-foreground">
              {aiData?.active_count || 0} of {aiData?.total_available || 0} active
            </span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {aiData?.providers?.map((p: any) => (
              <div key={p.id} className={`p-4 rounded-xl border-2 transition-all ${p.configured ? "border-emerald-500/50 bg-emerald-500/5 shadow-sm" : "border-border bg-card"}`}>
                <div className="flex items-center justify-between mb-3">
                  <div className="font-bold text-sm uppercase tracking-tight">{p.name}</div>
                  {p.configured ? (
                    <Badge variant="outline" className="text-[10px] bg-emerald-500/10 text-emerald-500 border-emerald-500/30 font-bold uppercase">ACTIVE</Badge>
                  ) : (
                    <Badge variant="outline" className="text-[10px] font-bold uppercase opacity-50">Inactive</Badge>
                  )}
                </div>
                <div className="space-y-1.5 mb-3">
                  <Label className="text-[10px] font-bold uppercase text-muted-foreground tracking-wider">API Key</Label>
                  <Input type="password" placeholder={p.configured ? "Configured key (hidden)" : "Enter API key..."} className="h-9 text-xs" value={aiKeys[p.id] || ""} onChange={(e) => setAiKeys((prev) => ({ ...prev, [p.id]: e.target.value }))} />
                </div>
                <div className="grid grid-cols-1 gap-3">
                  <div className="space-y-1.5">
                    <div className="flex items-center justify-between">
                      <Label className="text-[10px] font-bold uppercase text-muted-foreground tracking-wider">Model</Label>
                      <button
                        onClick={() => handleFetchModels(p.id)}
                        disabled={fetchingModels === p.id}
                        className="text-[10px] text-blue-500 hover:text-blue-400 flex items-center gap-1 disabled:opacity-50"
                      >
                        {fetchingModels === p.id ? <Loader2 size={10} className="animate-spin" /> : <Download size={10} />}
                        {fetchingModels === p.id ? "Fetching..." : "Fetch models"}
                      </button>
                    </div>
                    <div className="flex gap-2">
                      {modelOptions[p.id] && modelOptions[p.id].length > 0 ? (
                        <select className="flex-1 h-9 px-2 text-xs border rounded-md bg-background" value={aiModels[p.id] || p.model || ""} onChange={(e) => setAiModels((prev) => ({ ...prev, [p.id]: e.target.value }))}>
                          {modelOptions[p.id].map((m: string) => (
                            <option key={m} value={m}>{m}</option>
                          ))}
                        </select>
                      ) : (
                        <Input
                          placeholder={p.model || "Enter model ID or click Fetch models"}
                          className="flex-1 h-9 text-xs"
                          value={aiModels[p.id] || ""}
                          onChange={(e) => setAiModels((prev) => ({ ...prev, [p.id]: e.target.value }))}
                        />
                      )}
                      <Input placeholder="Override" className="w-1/2 h-9 text-xs" value={aiModels[p.id] || ""} onChange={(e) => setAiModels((prev) => ({ ...prev, [p.id]: e.target.value }))} />
                    </div>
                  </div>
                  {hasUrl(p.id) && (
                    <div className="space-y-1.5">
                      <Label className="text-[10px] font-bold uppercase text-muted-foreground tracking-wider">Base URL</Label>
                      <Input placeholder={baseUrlPlaceholders[p.id] || "https://api.example.com/v1"} className="h-9 text-xs font-mono" value={aiUrls[p.id] || ""} onChange={(e) => setAiUrls((prev) => ({ ...prev, [p.id]: e.target.value }))} />
                    </div>
                  )}
                </div>
                {p.balance && (
                  <div className="mt-3 pt-3 border-t border-border/50 flex justify-between items-center">
                    <span className="text-[10px] font-bold uppercase text-muted-foreground">Credits</span>
                    <span className="text-[11px] text-emerald-500 font-bold">{p.balance.balance}</span>
                  </div>
                )}
              </div>
            ))}
          </div>

          <div className="flex gap-3 flex-wrap pt-4 border-t border-border/50">
            <Button
              variant="default"
              onClick={async () => {
                setSaving(true);
                try {
                  const data: Record<string, string> = {};
                  const allIds = ["openai", "grok", "gemini", "claude", "openrouter", "groq", "alibaba", "deepseek", "jules", "localllm", "smslycloud", "freemodel", "opencode", "mistral", "nvidia", "cloudflare", "kimi", "orcarouter", "zenmax", "agentrouter"];
                  allIds.forEach((id) => {
                    if (aiKeys[id]) data[`${id}_api_key`] = aiKeys[id];
                    if (aiModels[id]) data[`${id}_model`] = aiModels[id];
                    if (aiUrls[id]) data[`${id}_base_url`] = aiUrls[id];
                  });
                  await aiApi.updateProviders(data);
                  toast({ title: "AI Config Saved", description: "The Intelligence Senate has been updated." });
                  fetchAIConfig();
                } catch {
                  toast({ title: "Error", description: "Failed to update the Senate.", variant: "destructive" });
                } finally {
                  setSaving(false);
                }
              }}
              disabled={saving}
              className="bg-primary hover:bg-primary/90 text-primary-foreground font-bold"
            >
              {saving ? <><Loader2 className="mr-2 h-4 w-4 animate-spin" /> Saving...</> : "Apply Senate Changes"}
            </Button>
            <Button variant="outline" onClick={handleTestAI} disabled={testingAI} className="font-bold">
              {testingAI ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Sparkles className="mr-2 h-4 w-4" />}
              Test Committee
            </Button>
            <Button variant="ghost" onClick={fetchAIConfig} disabled={loadingAI} className="font-bold">
              <Loader2 className={`mr-2 h-4 w-4 ${loadingAI ? "animate-spin" : ""}`} />
              Sync
            </Button>
          </div>

          <p className="text-xs text-muted-foreground">
            Set keys and models above, or via env vars (OPENAI_API_KEY, GROK_API_KEY, GEMINI_API_KEY, CLAUDE_API_KEY, JULES_API_KEY, FREEMODEL_API_KEY, OPENCODE_API_KEY, MISTRAL_API_KEY, NVIDIA_API_KEY, CLOUDFLARE_API_KEY, KIMI_API_KEY, ORCAROUTER_API_KEY, ZENMAX_API_KEY, AGENTROUTER_API_KEY), or admin panel.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Send className="h-5 w-5 text-primary" /> Test AI Engine
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
              onKeyDown={(e) => e.key === "Enter" && handleTestPrompt()}
              placeholder="Ask something... e.g. 'What stack does a Django app use?'"
              className="flex-1"
            />
            <Button
              onClick={handleTestPrompt}
              disabled={testingPrompt || !testPrompt.trim()}
              className="sm:w-32"
            >
              {testingPrompt ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Send className="w-4 h-4 mr-2" />}
              {testingPrompt ? "Testing..." : "Send"}
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
      </Card>
    </div>
  );
}
