"use client";

import { useState, useEffect, useCallback } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Loader2, Sparkles, Send } from "lucide-react";
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

  const modelOptions: Record<string, string[]> = {
    openai: ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo", "o1-mini", "o1-preview"],
    grok: ["grok-3-mini", "grok-3", "grok-2", "grok-beta"],
    gemini: ["gemini-2.0-flash", "gemini-2.0-pro", "gemini-1.5-flash", "gemini-1.5-pro"],
    claude: ["claude-sonnet-4-20250514", "claude-opus-4-20250514", "claude-3-5-sonnet-20241022", "claude-3-haiku-20240307"],
    openrouter: ["openrouter/auto", "openai/gpt-4o", "anthropic/claude-3.5-sonnet"],
    groq: ["llama-3.3-70b-versatile", "llama3-70b-8192", "mixtral-8x7b-32768"],
    alibaba: ["qwen-max", "qwen-plus", "qwen-turbo"],
    deepseek: ["deepseek-coder", "deepseek-chat"],
    jules: ["jules-latest", "jules-pro"],
    localllm: ["local-model"],
    smslycloud: ["smsly-latest"],
    freemodel: ["gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo", "claude-3.5-sonnet", "llama-3.1-70b"],
    opencode: ["opencode-latest", "gpt-4o", "claude-sonnet-4-20250514"],
    mistral: ["mistral-small-latest", "mistral-medium-latest", "mistral-large-latest", "codestral-latest", "pixtral-large-latest", "ministral-8b-latest"],
    nvidia: ["nvidia/llama-3.1-nemotron-70b-instruct", "nvidia/nemotron-4-340b-instruct", "meta/llama-3.1-8b-instruct", "mistralai/mixtral-8x22b-instruct-v0.1"],
    cloudflare: ["@cf/meta/llama-3.1-8b-instruct", "@cf/meta/llama-3.3-70b-instruct", "@cf/qwen/qwen3-30b-a3b-fp8", "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b", "@cf/mistral/mistral-small-3.1-24b-instruct"],
  };

  const hasUrl = (id: string) => ["jules", "localllm", "freemodel", "opencode", "mistral", "nvidia", "cloudflare"].includes(id);

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
                    <Label className="text-[10px] font-bold uppercase text-muted-foreground tracking-wider">Model</Label>
                    <div className="flex gap-2">
                      <select className="flex-1 h-9 px-2 text-xs border rounded-md bg-background" value={aiModels[p.id] || p.model || ""} onChange={(e) => setAiModels((prev) => ({ ...prev, [p.id]: e.target.value }))}>
                        {(modelOptions[p.id] || [p.model]).map((m: string) => (
                          <option key={m} value={m}>{m}</option>
                        ))}
                      </select>
                      <Input placeholder="Custom Model" className="w-1/2 h-9 text-xs" onChange={(e) => setAiModels((prev) => ({ ...prev, [p.id]: e.target.value }))} />
                    </div>
                  </div>
                  {hasUrl(p.id) && (
                    <div className="space-y-1.5">
                      <Label className="text-[10px] font-bold uppercase text-muted-foreground tracking-wider">Base URL</Label>
                      <Input placeholder="https://api.example.com/v1" className="h-9 text-xs font-mono" value={aiUrls[p.id] || ""} onChange={(e) => setAiUrls((prev) => ({ ...prev, [p.id]: e.target.value }))} />
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
                  const allIds = ["openai", "grok", "gemini", "claude", "openrouter", "groq", "alibaba", "deepseek", "jules", "localllm", "smslycloud", "freemodel", "opencode", "mistral", "nvidia", "cloudflare"];
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
            Set keys and models above, or via env vars (OPENAI_API_KEY, GROK_API_KEY, GEMINI_API_KEY, CLAUDE_API_KEY, JULES_API_KEY, FREEMODEL_API_KEY, OPENCODE_API_KEY, MISTRAL_API_KEY, NVIDIA_API_KEY, CLOUDFLARE_API_KEY), or admin panel.
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
