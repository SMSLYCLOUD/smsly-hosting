"use client";

import { useEffect, useState, useCallback } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Github, Loader2, Link as LinkIcon, GitBranch, GitMerge } from "lucide-react";
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
};

interface GitIntegrationCardProps {
  provider: "github" | "gitlab" | "bitbucket";
}

export function GitIntegrationCard({ provider }: GitIntegrationCardProps) {
  const [loading, setLoading] = useState(true);
  const [connecting, setConnecting] = useState(false);
  const [data, setData] = useState<GitConnection | null>(null);
  const [connectError, setConnectError] = useState<string | null>(null);

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

  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

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

  const getProviderDetails = () => {
    switch (provider) {
      case "github":
        return { name: "GitHub", icon: Github, color: "text-slate-900 dark:text-white" };
      case "gitlab":
        return { name: "GitLab", icon: GitBranch, color: "text-orange-500" };
      case "bitbucket":
        return { name: "Bitbucket", icon: GitMerge, color: "text-blue-500" };
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
                  <Button variant="outline" size="sm" className="text-red-500 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-950/30">
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
          </div>
        </CardContent>
      )}
    </Card>
  );
}
