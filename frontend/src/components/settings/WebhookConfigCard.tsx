"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Webhook, Loader2, Check, Copy, Eye, EyeOff } from "lucide-react";
import { useToast } from "@/components/ui/use-toast";
import api from "@/lib/api";

interface WebhookConfig {
  github_webhook_secret_set: boolean;
  gitlab_webhook_secret_set: boolean;
  bitbucket_webhook_secret_set: boolean;
  domain: string;
}

export function WebhookConfigCard({ provider }: { provider?: "github" | "gitlab" | "bitbucket" }) {
  const { toast } = useToast();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [config, setConfig] = useState<WebhookConfig | null>(null);

  const [githubSecret, setGithubSecret] = useState("");
  const [gitlabSecret, setGitlabSecret] = useState("");
  const [bitbucketSecret, setBitbucketSecret] = useState("");

  const [showGithub, setShowGithub] = useState(false);
  const [showGitlab, setShowGitlab] = useState(false);
  const [showBitbucket, setShowBitbucket] = useState(false);

  useEffect(() => {
    fetchConfig();
  }, []);

  const fetchConfig = async () => {
    setLoading(true);
    try {
      const res = await api.get("/system/domain-config/");
      setConfig(res.data);
    } catch (e) {
      console.error("Failed to fetch webhook config", e);
    } finally {
      setLoading(false);
    }
  };

  const getWebhookUrl = (provider: string) => {
    if (typeof window === "undefined") return "";
    const base = window.location.origin;
    return `${base}/api/v1/webhooks/${provider}/`;
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text).then(() => {
      toast({ title: "Copied", description: "Webhook URL copied to clipboard." });
    });
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const payload: Record<string, string> = {};
      if (!provider || provider === "github") {
        if (githubSecret.trim()) payload.github_webhook_secret = githubSecret.trim();
      }
      if (!provider || provider === "gitlab") {
        if (gitlabSecret.trim()) payload.gitlab_webhook_secret = gitlabSecret.trim();
      }
      if (!provider || provider === "bitbucket") {
        if (bitbucketSecret.trim()) payload.bitbucket_webhook_secret = bitbucketSecret.trim();
      }

      if (Object.keys(payload).length === 0) {
        toast({ title: "No changes", description: "Enter a new secret to save." });
        setSaving(false);
        return;
      }

      await api.put("/system/domain-config/", payload);
      toast({ title: "Webhook secrets saved", description: "Git provider webhook secrets updated." });
      setGithubSecret("");
      setGitlabSecret("");
      setBitbucketSecret("");
      fetchConfig();
    } catch (e: any) {
      const detail = e?.response?.data?.detail || e?.response?.data?.error;
      toast({ title: "Error", description: detail || "Failed to save webhook secrets.", variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center py-8">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Webhook className="h-6 w-6 text-indigo-500" />
            <div>
              <CardTitle className="text-lg">
                {provider ? `${provider.charAt(0).toUpperCase() + provider.slice(1)} Webhook` : "Webhook Configuration"}
              </CardTitle>
              <CardDescription>
                {provider
                  ? `Configure the webhook secret for ${provider.charAt(0).toUpperCase() + provider.slice(1)} push events.`
                  : "Configure secrets for Git provider push webhooks. These verify incoming webhook payloads."}
              </CardDescription>
            </div>
          </div>
        </div>
      </CardHeader>

      <CardContent className="space-y-6">
        {/* GitHub Webhook */}
        {(!provider || provider === "github") && (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <svg className="h-5 w-5" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M12 0c-6.626 0-12 5.373-12 12 0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23.957-.266 1.983-.399 3.003-.404 1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576 4.765-1.589 8.199-6.086 8.199-11.386 0-6.627-5.373-12-12-12z"/>
                </svg>
                <span className="text-sm font-medium">GitHub</span>
              </div>
              <Badge variant={config?.github_webhook_secret_set ? "default" : "secondary"} className={config?.github_webhook_secret_set ? "bg-green-500/10 text-green-500 border-green-500/30" : ""}>
                {config?.github_webhook_secret_set ? "Secret Set" : "Not Set"}
              </Badge>
            </div>
            <div className="flex items-center gap-2">
              <code className="flex-1 text-[11px] bg-muted px-2 py-1.5 rounded font-mono text-muted-foreground truncate">{getWebhookUrl("github")}</code>
              <Button variant="outline" size="sm" className="h-7 px-2" onClick={() => copyToClipboard(getWebhookUrl("github"))}>
                <Copy className="w-3 h-3" />
              </Button>
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Webhook Secret</Label>
              <div className="relative">
                <Input
                  type={showGithub ? "text" : "password"}
                  placeholder={config?.github_webhook_secret_set ? "Enter new secret to update" : "Enter webhook secret"}
                  value={githubSecret}
                  onChange={(e) => setGithubSecret(e.target.value)}
                  className="pr-9"
                />
                <button type="button" onClick={() => setShowGithub(!showGithub)} className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground">
                  {showGithub ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                </button>
              </div>
            </div>
          </div>
        )}

        {(!provider || provider === "github") && (!provider || provider === "gitlab") && <hr className="border-zinc-800" />}

        {/* GitLab Webhook */}
        {(!provider || provider === "gitlab") && (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <svg className="h-5 w-5 text-orange-500" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M22.65 14.39L12 22.13 1.35 14.39a.84.84 0 01-.3-.94l1.22-3.78 2.44-7.51A.42.42 0 014.93 2a.43.43 0 01.24.16L12 9.32l6.83-7.13a.43.43 0 01.24-.16.42.42 0 01.22.19l2.44 7.51 1.22 3.78a.84.84 0 01-.3.88z"/>
                </svg>
                <span className="text-sm font-medium">GitLab</span>
              </div>
              <Badge variant={config?.gitlab_webhook_secret_set ? "default" : "secondary"} className={config?.gitlab_webhook_secret_set ? "bg-green-500/10 text-green-500 border-green-500/30" : ""}>
                {config?.gitlab_webhook_secret_set ? "Secret Set" : "Not Set"}
              </Badge>
            </div>
            <div className="flex items-center gap-2">
              <code className="flex-1 text-[11px] bg-muted px-2 py-1.5 rounded font-mono text-muted-foreground truncate">{getWebhookUrl("gitlab")}</code>
              <Button variant="outline" size="sm" className="h-7 px-2" onClick={() => copyToClipboard(getWebhookUrl("gitlab"))}>
                <Copy className="w-3 h-3" />
              </Button>
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Secret Token</Label>
              <div className="relative">
                <Input
                  type={showGitlab ? "text" : "password"}
                  placeholder={config?.gitlab_webhook_secret_set ? "Enter new secret to update" : "Enter secret token"}
                  value={gitlabSecret}
                  onChange={(e) => setGitlabSecret(e.target.value)}
                  className="pr-9"
                />
                <button type="button" onClick={() => setShowGitlab(!showGitlab)} className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground">
                  {showGitlab ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                </button>
              </div>
            </div>
          </div>
        )}

        {(!provider || provider === "gitlab") && (!provider || provider === "bitbucket") && <hr className="border-zinc-800" />}

        {/* Bitbucket Webhook */}
        {(!provider || provider === "bitbucket") && (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <svg className="h-5 w-5 text-blue-500" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M.778 1.213a.768.768 0 00-.768.892l3.263 19.79c.084.5.515.868 1.022.873H20.95a.772.772 0 00.77-.646l3.27-20.03a.768.768 0 00-.768-.891zM14.52 15.53H9.522L8.17 8.466h7.561z"/>
                </svg>
                <span className="text-sm font-medium">Bitbucket</span>
              </div>
              <Badge variant={config?.bitbucket_webhook_secret_set ? "default" : "secondary"} className={config?.bitbucket_webhook_secret_set ? "bg-green-500/10 text-green-500 border-green-500/30" : ""}>
                {config?.bitbucket_webhook_secret_set ? "Secret Set" : "Not Set"}
              </Badge>
            </div>
            <div className="flex items-center gap-2">
              <code className="flex-1 text-[11px] bg-muted px-2 py-1.5 rounded font-mono text-muted-foreground truncate">{getWebhookUrl("bitbucket")}</code>
              <Button variant="outline" size="sm" className="h-7 px-2" onClick={() => copyToClipboard(getWebhookUrl("bitbucket"))}>
                <Copy className="w-3 h-3" />
              </Button>
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Secret</Label>
              <div className="relative">
                <Input
                  type={showBitbucket ? "text" : "password"}
                  placeholder={config?.bitbucket_webhook_secret_set ? "Enter new secret to update" : "Enter webhook secret"}
                  value={bitbucketSecret}
                  onChange={(e) => setBitbucketSecret(e.target.value)}
                  className="pr-9"
                />
                <button type="button" onClick={() => setShowBitbucket(!showBitbucket)} className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground">
                  {showBitbucket ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                </button>
              </div>
            </div>
          </div>
        )}

        <div className="flex justify-end pt-2">
          <Button onClick={handleSave} disabled={saving}>
            {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Check className="mr-2 h-4 w-4" />}
            Save Webhook Secrets
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
