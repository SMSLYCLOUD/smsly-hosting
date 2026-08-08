"use client";

import { useEffect, useState, useCallback } from "react";
import {
  Github,
  Link2,
  Webhook,
  CheckCircle2,
  Circle,
  Copy,
  ExternalLink,
  Loader2,
  Shield,
  Eye,
  EyeOff,
  AlertTriangle,
  Settings,
  GitBranch,
  GitMerge,
} from "lucide-react";
import { useToast } from "@/components/ui/use-toast";
import api from "@/lib/api";

type IntegrationStatus = {
  github_app: { configured: boolean; app_id: string | null };
  github_oauth: { configured: boolean; client_id: string | null };
  github_connected: boolean;
  github_account: { login: string; avatar_url: string } | null;
  github_installations: Array<{
    installation_id: number;
    account_login: string;
    account_type: string;
    repo_count: number;
  }>;
  gitlab: {
    configured: boolean;
    connected: boolean;
    account: { login: string; avatar_url: string } | null;
  };
  bitbucket: {
    configured: boolean;
    connected: boolean;
    account: { login: string; avatar_url: string } | null;
  };
  webhook_secret_set: boolean;
  webhook_url: string;
};

function StatusDot({ active }: { active: boolean }) {
  return active ? (
    <CheckCircle2 className="h-4 w-4 text-emerald-500" />
  ) : (
    <Circle className="h-4 w-4 text-slate-400" />
  );
}

function CopyButton({ text, label }: { text: string; label?: string }) {
  const { toast } = useToast();
  return (
    <button
      onClick={() => {
        navigator.clipboard.writeText(text);
        toast({ title: "Copied", description: `${label || "Value"} copied to clipboard` });
      }}
      className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
    >
      <Copy className="h-3 w-3" />
      {label || "Copy"}
    </button>
  );
}

function StepIndicator({ step, total, label, active, done }: {
  step: number; total: number; label: string; active: boolean; done: boolean;
}) {
  return (
    <div className="flex items-center gap-3">
      <div className={`flex items-center justify-center w-7 h-7 rounded-full text-xs font-medium transition-colors ${
        done ? "bg-emerald-500 text-white" : active ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground"
      }`}>
        {done ? <CheckCircle2 className="h-4 w-4" /> : step}
      </div>
      <span className={`text-sm ${active ? "font-medium" : done ? "text-muted-foreground" : "text-muted-foreground"}`}>
        {label}
      </span>
    </div>
  );
}

export default function IntegrationsPage() {
  const { toast } = useToast();
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState<IntegrationStatus | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [installing, setInstalling] = useState(false);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await api.get("/integrations/overview/");
      setStatus(res.data);
    } catch (e) {
      console.error("Failed to fetch integration status", e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchStatus(); }, [fetchStatus]);

  const connectGitHub = async () => {
    setConnecting(true);
    try {
      const res = await api.get("/integrations/github/oauth-url/");
      const url = res.data?.url;
      if (url) window.location.assign(url);
    } catch (e: any) {
      toast({ title: "Error", description: e?.response?.data?.error || "Failed to start GitHub connection", variant: "destructive" });
    } finally {
      setConnecting(false);
    }
  };

  const installApp = async () => {
    setInstalling(true);
    try {
      const endpoint = status?.github_connected
        ? "/integrations/github/app/install-url/"
        : "/integrations/github/app/install/";
      const res = await api.get(endpoint);
      const url = res.data?.url;
      if (url) window.location.assign(url);
    } catch (e: any) {
      toast({ title: "Error", description: e?.response?.data?.error || "Failed to start installation", variant: "destructive" });
    } finally {
      setInstalling(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  // Calculate setup progress
  const steps = [
    { label: "GitHub App created", done: !!status?.github_app.configured },
    { label: "GitHub account connected", done: !!status?.github_connected },
    { label: "App installed on repo", done: (status?.github_installations.length || 0) > 0 },
    { label: "Webhook secret set", done: !!status?.webhook_secret_set },
  ];
  const completedSteps = steps.filter(s => s.done).length;
  const activeStep = steps.findIndex(s => !s.done);

  return (
    <div className="max-w-3xl space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <Settings className="h-6 w-6" />
          Integrations
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Connect your Git providers to enable push-to-deploy, commit statuses, and PR previews.
        </p>
      </div>

      {/* Progress Bar */}
      <div className="rounded-xl border bg-card p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-semibold">Setup Progress</h2>
          <span className="text-xs text-muted-foreground">{completedSteps}/{steps.length} complete</span>
        </div>
        <div className="w-full h-1.5 bg-muted rounded-full mb-4">
          <div
            className="h-full bg-emerald-500 rounded-full transition-all duration-500"
            style={{ width: `${(completedSteps / steps.length) * 100}%` }}
          />
        </div>
        <div className="grid grid-cols-2 gap-3">
          {steps.map((s, i) => (
            <StepIndicator key={i} step={i + 1} total={steps.length} label={s.label} active={i === activeStep} done={s.done} />
          ))}
        </div>
      </div>

      {/* GitHub Account Connection */}
      <div className="rounded-xl border bg-card">
        <div className="p-6">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-slate-100 dark:bg-slate-800">
                <Github className="h-5 w-5" />
              </div>
              <div>
                <h3 className="font-semibold">GitHub Account</h3>
                <p className="text-xs text-muted-foreground">OAuth connection for user authentication</p>
              </div>
            </div>
            {status?.github_connected ? (
              <div className="flex items-center gap-2">
                {status.github_account?.avatar_url && (
                  <img src={status.github_account.avatar_url} alt="" className="w-6 h-6 rounded-full" />
                )}
                <span className="text-sm font-medium">{status.github_account?.login}</span>
                <span className="inline-flex items-center gap-1 text-xs text-emerald-600 bg-emerald-50 dark:bg-emerald-950/30 px-2 py-0.5 rounded-full">
                  <CheckCircle2 className="h-3 w-3" /> Connected
                </span>
              </div>
            ) : (
              <button
                onClick={connectGitHub}
                disabled={connecting}
                className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-lg bg-foreground text-background hover:opacity-90 transition-opacity disabled:opacity-50"
              >
                {connecting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Link2 className="h-4 w-4" />}
                Connect
              </button>
            )}
          </div>

          {status?.github_connected && (
            <div className="mt-4 p-3 rounded-lg bg-muted/50 text-xs text-muted-foreground">
              <p>Enables: private repo access, user authentication, repository listing</p>
            </div>
          )}
        </div>
      </div>

      {/* GitHub App */}
      <div className="rounded-xl border bg-card">
        <div className="p-6">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-slate-100 dark:bg-slate-800">
                <Shield className="h-5 w-5" />
              </div>
              <div>
                <h3 className="font-semibold">GitHub App</h3>
                <p className="text-xs text-muted-foreground">Server-to-server auth for webhooks, commit statuses, PR comments</p>
              </div>
            </div>
            {status?.github_app.configured ? (
              <span className="inline-flex items-center gap-1 text-xs text-emerald-600 bg-emerald-50 dark:bg-emerald-950/30 px-2 py-0.5 rounded-full">
                <CheckCircle2 className="h-3 w-3" /> Configured
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 text-xs text-amber-600 bg-amber-50 dark:bg-amber-950/30 px-2 py-0.5 rounded-full">
                <AlertTriangle className="h-3 w-3" /> Not configured
              </span>
            )}
          </div>

          {status?.github_app.configured ? (
            <div className="mt-4 space-y-3">
              <div className="flex items-center justify-between p-3 rounded-lg bg-muted/50">
                <span className="text-xs text-muted-foreground">App ID</span>
                <code className="text-xs font-mono">{status.github_app.app_id}</code>
              </div>

              {/* Installations */}
              {status.github_installations.length > 0 ? (
                <div className="space-y-2">
                  <p className="text-xs font-medium text-muted-foreground">Installations</p>
                  {status.github_installations.map((inst) => (
                    <div key={inst.installation_id} className="flex items-center justify-between p-3 rounded-lg bg-muted/50">
                      <div className="flex items-center gap-2">
                        <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                        <span className="text-sm font-medium">{inst.account_login}</span>
                        <span className="text-xs text-muted-foreground">({inst.account_type})</span>
                      </div>
                      <span className="text-xs text-muted-foreground">{inst.repo_count} repos</span>
                    </div>
                  ))}
                </div>
              ) : (
                <button
                  onClick={installApp}
                  disabled={installing}
                  className="w-full flex items-center justify-center gap-2 px-4 py-2.5 text-sm font-medium rounded-lg border hover:bg-muted/50 transition-colors disabled:opacity-50"
                >
                  {installing ? <Loader2 className="h-4 w-4 animate-spin" /> : <ExternalLink className="h-4 w-4" />}
                  Install GitHub App
                </button>
              )}
            </div>
          ) : (
            <div className="mt-4 p-4 rounded-lg bg-muted/30 border border-dashed">
              <p className="text-sm text-muted-foreground mb-3">
                The GitHub App enables push-to-deploy, commit deployment statuses, and PR preview environments.
              </p>
              <ol className="text-xs text-muted-foreground space-y-2 list-decimal list-inside">
                <li>Go to <a href="https://github.com/organizations/SMSLYCLOUD/settings/apps/new" target="_blank" rel="noreferrer" className="text-primary hover:underline inline-flex items-center gap-1">GitHub Apps <ExternalLink className="h-3 w-3" /></a></li>
                <li>Name: <code className="bg-muted px-1 rounded">smsly-paas-builder</code></li>
                <li>Webhook URL: <code className="bg-muted px-1 rounded">{status?.webhook_url || "https://your-domain/webhooks/github/"}</code></li>
                <li>Permissions: Contents (read), Commit statuses (write), Pull requests (write), Deployments (write)</li>
                <li>Events: Push, Pull request, Installation, Installation repositories</li>
                <li>Create app, note App ID, generate &amp; download private key</li>
                <li>Run: <code className="bg-muted px-1 rounded">python manage.py setup_github --app-id ID --app-private-key key.pem</code></li>
              </ol>
            </div>
          )}
        </div>
      </div>

      {/* GitLab */}
      <div className="rounded-xl border bg-card">
        <div className="p-6">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-slate-100 dark:bg-slate-800">
                <GitBranch className="h-5 w-5 text-orange-500" />
              </div>
              <div>
                <h3 className="font-semibold">GitLab</h3>
                <p className="text-xs text-muted-foreground">OAuth connection for repository access</p>
              </div>
            </div>
            {status?.gitlab?.connected ? (
              <div className="flex items-center gap-2">
                {status.gitlab.account?.avatar_url && (
                  <img src={status.gitlab.account.avatar_url} alt="" className="w-6 h-6 rounded-full" />
                )}
                <span className="text-sm font-medium">{status.gitlab.account?.login}</span>
                <span className="inline-flex items-center gap-1 text-xs text-emerald-600 bg-emerald-50 dark:bg-emerald-950/30 px-2 py-0.5 rounded-full">
                  <CheckCircle2 className="h-3 w-3" /> Connected
                </span>
              </div>
            ) : status?.gitlab?.configured ? (
              <a
                href="/settings"
                className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-lg border hover:bg-muted/50 transition-opacity"
              >
                <Link2 className="h-4 w-4" />
                Connect in Settings
              </a>
            ) : (
              <span className="inline-flex items-center gap-1 text-xs text-muted-foreground bg-muted px-2 py-0.5 rounded-full">
                Not configured
              </span>
            )}
          </div>
          {status?.gitlab?.connected && (
            <div className="mt-4 p-3 rounded-lg bg-muted/50 text-xs text-muted-foreground">
              <p>Enables: GitLab repository listing, push-to-deploy, branch/commit browsing</p>
            </div>
          )}
        </div>
      </div>

      {/* Bitbucket */}
      <div className="rounded-xl border bg-card">
        <div className="p-6">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-slate-100 dark:bg-slate-800">
                <GitMerge className="h-5 w-5 text-blue-500" />
              </div>
              <div>
                <h3 className="font-semibold">Bitbucket</h3>
                <p className="text-xs text-muted-foreground">OAuth connection for repository access</p>
              </div>
            </div>
            {status?.bitbucket?.connected ? (
              <div className="flex items-center gap-2">
                {status.bitbucket.account?.avatar_url && (
                  <img src={status.bitbucket.account.avatar_url} alt="" className="w-6 h-6 rounded-full" />
                )}
                <span className="text-sm font-medium">{status.bitbucket.account?.login}</span>
                <span className="inline-flex items-center gap-1 text-xs text-emerald-600 bg-emerald-50 dark:bg-emerald-950/30 px-2 py-0.5 rounded-full">
                  <CheckCircle2 className="h-3 w-3" /> Connected
                </span>
              </div>
            ) : status?.bitbucket?.configured ? (
              <a
                href="/settings"
                className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-lg border hover:bg-muted/50 transition-opacity"
              >
                <Link2 className="h-4 w-4" />
                Connect in Settings
              </a>
            ) : (
              <span className="inline-flex items-center gap-1 text-xs text-muted-foreground bg-muted px-2 py-0.5 rounded-full">
                Not configured
              </span>
            )}
          </div>
          {status?.bitbucket?.connected && (
            <div className="mt-4 p-3 rounded-lg bg-muted/50 text-xs text-muted-foreground">
              <p>Enables: Bitbucket repository listing, push-to-deploy, branch/commit browsing</p>
            </div>
          )}
        </div>
      </div>

      {/* Webhooks */}
      <div className="rounded-xl border bg-card">
        <div className="p-6">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-slate-100 dark:bg-slate-800">
                <Webhook className="h-5 w-5" />
              </div>
              <div>
                <h3 className="font-semibold">Webhooks</h3>
                <p className="text-xs text-muted-foreground">Receive push, PR, and installation events from GitHub</p>
              </div>
            </div>
            {status?.webhook_secret_set ? (
              <span className="inline-flex items-center gap-1 text-xs text-emerald-600 bg-emerald-50 dark:bg-emerald-950/30 px-2 py-0.5 rounded-full">
                <CheckCircle2 className="h-3 w-3" /> Configured
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 text-xs text-amber-600 bg-amber-50 dark:bg-amber-950/30 px-2 py-0.5 rounded-full">
                <AlertTriangle className="h-3 w-3" /> Not configured
              </span>
            )}
          </div>

          <div className="mt-4 space-y-3">
            <div className="flex items-center justify-between p-3 rounded-lg bg-muted/50">
              <div>
                <p className="text-xs font-medium">Webhook URL</p>
                <code className="text-xs font-mono text-muted-foreground">{status?.webhook_url}</code>
              </div>
              <CopyButton text={status?.webhook_url || ""} label="URL" />
            </div>
            <p className="text-xs text-muted-foreground">
              Paste this URL in your GitHub App&apos;s webhook configuration. The secret is auto-generated during installation.
            </p>
          </div>
        </div>
      </div>

      {/* Quick Links */}
      <div className="rounded-xl border bg-card p-6">
        <h3 className="font-semibold mb-4">Quick Links</h3>
        <div className="grid grid-cols-2 gap-3">
          <a
            href="https://github.com/settings/developers"
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-2 p-3 rounded-lg border hover:bg-muted/50 transition-colors text-sm"
          >
            <ExternalLink className="h-4 w-4 text-muted-foreground" />
            GitHub Developer Settings
          </a>
          <a
            href="https://github.com/apps/smsly-paas-builder"
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-2 p-3 rounded-lg border hover:bg-muted/50 transition-colors text-sm"
          >
            <ExternalLink className="h-4 w-4 text-muted-foreground" />
            Your GitHub App
          </a>
          <a
            href="/docs/github-app"
            className="flex items-center gap-2 p-3 rounded-lg border hover:bg-muted/50 transition-colors text-sm"
          >
            <ExternalLink className="h-4 w-4 text-muted-foreground" />
            Setup Guide
          </a>
          <a
            href="/settings"
            className="flex items-center gap-2 p-3 rounded-lg border hover:bg-muted/50 transition-colors text-sm"
          >
            <Settings className="h-4 w-4 text-muted-foreground" />
            Back to Settings
          </a>
        </div>
      </div>
    </div>
  );
}
