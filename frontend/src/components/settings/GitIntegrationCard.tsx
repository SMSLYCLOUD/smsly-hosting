"use client";

import { useEffect, useState, useCallback } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Github, Loader2, Link as LinkIcon, GitBranch, GitMerge, Package, ExternalLink, Trash2, Chrome } from "lucide-react";
import api from "@/lib/api";

type GitConnection = {
  connected: boolean;
  has_token: boolean;
  connect_url?: string;
  account: null | {
    uid: string;
    login: string | null;
    avatar_url: string | null;
  };
  warning?: string;
  github_app_configured?: boolean;
};

type GitHubInstallation = {
  id: string;
  installation_id: number;
  account_login: string;
  account_type: string;
  account_avatar_url: string;
  repository_selection: string;
  repo_count: number;
  created_at: string;
};

interface GitIntegrationCardProps {
  provider: "github" | "gitlab" | "bitbucket" | "google";
}

export function GitIntegrationCard({ provider }: GitIntegrationCardProps) {
  const [loading, setLoading] = useState(true);
  const [connecting, setConnecting] = useState(false);
  const [data, setData] = useState<GitConnection | null>(null);
  const [connectError, setConnectError] = useState<string | null>(null);

  // GitHub App installation state
  const [installations, setInstallations] = useState<GitHubInstallation[]>([]);
  const [installLoading, setInstallLoading] = useState(false);
  const [installError, setInstallError] = useState<string | null>(null);

  const fetchStatus = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get(`/integrations/${provider}/`);
      setData(res.data);
    } catch (e) {
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [provider]);

  const fetchInstallations = useCallback(async () => {
    if (provider !== "github") return;
    setInstallError(null);
    try {
      const res = await api.get("/integrations/github/app/installations/");
      setInstallations(res.data?.installations || []);
    } catch {
      setInstallError("Could not load installations.");
    }
  }, [provider]);

  useEffect(() => {
    fetchStatus();
    fetchInstallations();
  }, [fetchStatus, fetchInstallations]);

  const startConnectFlow = async () => {
    setConnectError(null);
    setConnecting(true);
    try {
      const res = await api.get(`/integrations/${provider}/oauth-url/`);
      const target = res.data?.url;
      if (!target) throw new Error("No OAuth URL returned");
      window.location.assign(target);
    } catch (e: any) {
      const message =
        e?.response?.data?.detail ||
        e?.response?.data?.error ||
        `Unable to start ${provider} connection flow.`;
      setConnectError(String(message));
    } finally {
      setConnecting(false);
    }
  };

  const startGitHubAppInstall = async () => {
    setInstallLoading(true);
    try {
      // Use combined OAuth+install flow when user is not yet connected
      const endpoint = data?.connected
        ? "/integrations/github/app/install-url/"
        : "/integrations/github/app/install/";
      const res = await api.get(endpoint);
      const target = res.data?.url;
      if (!target) throw new Error("No install URL returned");
      window.location.assign(target);
    } catch (e: any) {
      const message =
        e?.response?.data?.error || "Unable to start GitHub App installation.";
      setConnectError(String(message));
    } finally {
      setInstallLoading(false);
    }
  };

  const removeInstallation = async (installationId: number) => {
    const prev = installations;
    setInstallations((p) => p.filter((i) => i.installation_id !== installationId));
    try {
      await api.delete(`/integrations/github/app/installations/${installationId}/`);
    } catch {
      setInstallations(prev);
    }
  };

  const disconnectProvider = async () => {
    try {
      await api.delete(`/integrations/${provider}/disconnect/`);
      setData(null);
      fetchStatus();
    } catch (e: any) {
      const message = e?.response?.data?.error || "Failed to disconnect.";
      setConnectError(String(message));
    }
  };

  const getProviderDetails = () => {
    switch (provider) {
      case "github":
        return { name: "GitHub", icon: Github, color: "text-slate-900 dark:text-white" };
      case "gitlab":
        return { name: "GitLab", icon: GitBranch, color: "text-orange-500" };
      case "bitbucket":
        return { name: "Bitbucket", icon: GitMerge, color: "text-blue-500" };
      case "google":
        return { name: "Google", icon: Chrome, color: "text-red-500" };
    }
  };

  const details = getProviderDetails();
  const Icon = details.icon;

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <Icon className={`h-6 w-6 ${details.color}`} />
            <div>
              <CardTitle className="text-lg">{details.name} Connection</CardTitle>
              <CardDescription>Link your {details.name} account to deploy private repositories.</CardDescription>
            </div>
          </div>
          {loading ? (
            <Loader2 className="h-5 w-5 animate-spin text-slate-400" />
          ) : data?.connected ? (
            <Badge variant="default" className="bg-emerald-500/10 text-emerald-500 hover:bg-emerald-500/20 shadow-none border-none">
              Connected
            </Badge>
          ) : (
            <Badge variant="outline" className="text-slate-500">Not connected</Badge>
          )}
        </div>
      </CardHeader>
      
      {!loading && (
        <CardContent>
          <div className="space-y-4">
            {data?.connected && data.account ? (
              <div className="flex flex-col gap-4">
                <div className="flex items-center gap-3 p-3 bg-slate-50 dark:bg-slate-900 rounded-lg border border-slate-100 dark:border-slate-800">
                  {data.account.avatar_url ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={data.account.avatar_url} alt="Avatar" className="w-10 h-10 rounded-full bg-slate-200" />
                  ) : (
                    <div className="w-10 h-10 rounded-full bg-slate-200 dark:bg-slate-800 flex items-center justify-center">
                      <Icon className="h-5 w-5 text-slate-400" />
                    </div>
                  )}
                  <div>
                    <p className="text-sm font-medium">Connected as {data.account.login || data.account.uid}</p>
                    <p className="text-xs text-muted-foreground font-mono">{data.account.uid}</p>
                  </div>
                </div>
                
                {data.warning && (
                  <div className="p-3 bg-amber-50 dark:bg-amber-950/30 text-amber-600 dark:text-amber-400 text-sm rounded-lg border border-amber-200 dark:border-amber-900">
                    <p className="font-semibold mb-1">Warning</p>
                    {data.warning}
                  </div>
                )}

                <div className="pt-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={disconnectProvider}
                    className="text-red-500 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-950/30"
                  >
                    Disconnect Account
                  </Button>
                </div>
              </div>
            ) : (
              <div className="flex flex-col gap-3 items-start">
                <p className="text-sm text-slate-600 dark:text-slate-400">
                  Connect your {details.name} account to automatically sync repositories, enable push-to-deploy, and configure PR previews.
                </p>
                {connectError && (
                  <p className="text-sm text-red-500 font-medium">{connectError}</p>
                )}
                <Button 
                  onClick={startConnectFlow}
                  disabled={connecting}
                  className="gap-2"
                >
                  {connecting ? <Loader2 className="w-4 h-4 animate-spin" /> : <LinkIcon className="w-4 h-4" />}
                  Connect {details.name} Account
                </Button>
              </div>
            )}

            {/* GitHub App Installation Section */}
            {provider === "github" && (
              <div className="pt-4 mt-4 border-t border-slate-100 dark:border-slate-800">
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-center gap-2">
                    <Package className="h-4 w-4 text-slate-500" />
                    <h4 className="text-sm font-medium">GitHub App</h4>
                    <Badge variant="outline" className="text-xs font-normal">Recommended</Badge>
                  </div>
                  {data?.github_app_configured ? (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={startGitHubAppInstall}
                      disabled={installLoading}
                      className="gap-1.5"
                    >
                      {installLoading ? (
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      ) : (
                        <ExternalLink className="w-3.5 h-3.5" />
                      )}
                      Install App
                    </Button>
                  ) : (
                    <Badge variant="secondary" className="text-xs">Not configured</Badge>
                  )}
                </div>

                <p className="text-xs text-muted-foreground mb-3">
                  Install the GitHub App for automatic webhooks, commit deployment statuses, and PR preview environments.{' '}
                  <a href="/docs/github-app" target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">
                    Setup guide
                  </a>
                </p>

                {data?.github_app_configured ? (
                  installError ? (
                    <p className="text-xs text-red-500">{installError}</p>
                  ) : installations.length > 0 ? (
                    <div className="space-y-2">
                      {installations.map((inst) => (
                        <div
                          key={inst.installation_id}
                          className="flex items-center justify-between p-2.5 bg-slate-50 dark:bg-slate-900 rounded-lg border border-slate-100 dark:border-slate-800"
                        >
                          <div className="flex items-center gap-2.5">
                            {inst.account_avatar_url ? (
                              // eslint-disable-next-line @next/next/no-img-element
                              <img src={inst.account_avatar_url} alt="" className="w-7 h-7 rounded-full bg-slate-200" />
                            ) : (
                              <div className="w-7 h-7 rounded-full bg-slate-200 dark:bg-slate-800 flex items-center justify-center">
                                <Github className="h-3.5 w-3.5 text-slate-400" />
                              </div>
                            )}
                            <div>
                              <p className="text-sm font-medium">{inst.account_login}</p>
                              <p className="text-xs text-muted-foreground">
                                {inst.repo_count} {inst.repo_count === 1 ? "repo" : "repos"} · {inst.account_type}
                              </p>
                            </div>
                          </div>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => removeInstallation(inst.installation_id)}
                            className="h-7 w-7 p-0 text-slate-400 hover:text-red-500"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </Button>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-xs text-muted-foreground italic">
                      No installations linked yet. Click &quot;Install App&quot; to get started.
                    </p>
                  )
                ) : (
                  <ol className="text-xs text-muted-foreground space-y-1.5 list-decimal list-inside">
                    <li>Create a GitHub App at <a href="https://github.com/organizations/SMSLYCLOUD/settings/apps/new" target="_blank" rel="noreferrer" className="text-primary hover:underline">GitHub Apps</a></li>
                    <li>Name: <code className="bg-muted px-1 rounded">smsly-paas-builder</code></li>
                    <li>Webhook URL: <code className="bg-muted px-1 rounded">https://your-domain/webhooks/github/</code></li>
                    <li>Run: <code className="bg-muted px-1 rounded">python manage.py setup_github --app-id ID --private-key key.pem</code></li>
                  </ol>
                )}
              </div>
            )}
          </div>
        </CardContent>
      )}
    </Card>
  );
}
