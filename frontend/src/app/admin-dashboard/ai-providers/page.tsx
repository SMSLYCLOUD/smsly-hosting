"use client";

import React, { useState, useEffect } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle, CardFooter } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useToast } from "@/components/ui/use-toast";
import { aiAdminApi } from "@/lib/api";
import { Loader2, Brain, CheckCircle2, XCircle } from "lucide-react";

export default function AIProvidersPage() {
  const { toast } = useToast();
  const [loading, setLoading] = useState(true);
  const [providers, setProviders] = useState<any>({});
  
  const [openaiKey, setOpenaiKey] = useState("");
  const [anthropicKey, setAnthropicKey] = useState("");
  const [saving, setSaving] = useState(false);

  const fetchProviders = async () => {
    try {
      const data = await aiAdminApi.getProviders();
      setProviders(data);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchProviders();
  }, []);

  const handleUpdate = async (providerName: string, key: string) => {
    if (!key.trim()) return;
    try {
      setSaving(true);
      await aiAdminApi.updateProvider(providerName, { api_key: key });
      await fetchProviders();
      if (providerName === 'openai') setOpenaiKey("");
      if (providerName === 'anthropic') setAnthropicKey("");
      toast({ title: `${providerName} provider updated successfully` });
    } catch (e: unknown) {
      toast({ title: `Error updating ${providerName}`, description: e instanceof Error ? e.message : 'Unknown error', variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <div className="flex justify-center p-8"><Loader2 className="h-8 w-8 animate-spin text-muted-foreground" /></div>;

  return (
    <div className="space-y-6 max-w-4xl mx-auto p-6">
      <div>
        <h2 className="text-2xl font-bold tracking-tight">AI Providers</h2>
        <p className="text-muted-foreground">Configure the global AI models and API keys used by Jules.</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* OpenAI */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Brain className="h-5 w-5" />
              OpenAI
            </CardTitle>
            <CardDescription>Configure GPT-4 models.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center gap-2 text-sm">
              Status: 
              {providers?.openai?.is_configured ? (
                <span className="flex items-center text-emerald-500 font-medium"><CheckCircle2 className="h-4 w-4 mr-1" /> Configured</span>
              ) : (
                <span className="flex items-center text-zinc-500 font-medium"><XCircle className="h-4 w-4 mr-1" /> Missing Key</span>
              )}
            </div>
            <div className="space-y-2">
              <Label>API Key</Label>
              <Input 
                value={openaiKey}
                onChange={(e) => setOpenaiKey(e.target.value)}
                placeholder="sk-proj-..."
                type="password"
              />
            </div>
          </CardContent>
          <CardFooter>
            <Button onClick={() => handleUpdate('openai', openaiKey)} disabled={!openaiKey.trim() || saving}>
              Save OpenAI Key
            </Button>
          </CardFooter>
        </Card>

        {/* Anthropic */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Brain className="h-5 w-5" />
              Anthropic
            </CardTitle>
            <CardDescription>Configure Claude 3 models.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center gap-2 text-sm">
              Status: 
              {providers?.anthropic?.is_configured ? (
                <span className="flex items-center text-emerald-500 font-medium"><CheckCircle2 className="h-4 w-4 mr-1" /> Configured</span>
              ) : (
                <span className="flex items-center text-zinc-500 font-medium"><XCircle className="h-4 w-4 mr-1" /> Missing Key</span>
              )}
            </div>
            <div className="space-y-2">
              <Label>API Key</Label>
              <Input 
                value={anthropicKey}
                onChange={(e) => setAnthropicKey(e.target.value)}
                placeholder="sk-ant-..."
                type="password"
              />
            </div>
          </CardContent>
          <CardFooter>
            <Button onClick={() => handleUpdate('anthropic', anthropicKey)} disabled={!anthropicKey.trim() || saving}>
              Save Anthropic Key
            </Button>
          </CardFooter>
        </Card>
      </div>
    </div>
  );
}
