"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { GitBranch, Loader2, Link as LinkIcon } from "lucide-react";
import api from "@/lib/api";

type GitLabConnection = {
  connected: boolean;
  has_token: boolean;
  account: null | { uid: string; login: string | null; avatar_url: string | null };
  warning?: string;
};

export function GitLabIntegrationCard() {
  const [loading, setLoading] = useState(true);
  const [connecting, setConnecting] = useState(false);
  const [data, setData] = useState<GitLabConnection | null>(null);
  const [connectError, setConnectError] = useState<string | null>(null);

  const fetchStatus = async () => {
    setLoading(true);
    try {
      const res = await api.get("/integrations/gitlab/");
      setData(res.data);
    } catch (e) {
      setData(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchStatus(); }, []);

  const startConnectFlow = async () => {
    setConnectError(null);
    setConnecting(true);
    try {
      const res = await api.get("/integrations/gitlab/oauth-url/");
      const target = res.data?.url;
      if (!target) throw new Error("No OAuth URL returned");
      window.location.assign(target);
    } catch (e: any) {
      setConnectError(String(e?.response?.data?.error || "Unable to start GitLab connection flow."));
    } finally {
      setConnecting(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <GitBranch className="h-6 w-6 text-orange-500" />
            <div>
              <CardTitle className="text-lg">GitLab Connection</CardTitle>
              <CardDescription>Link your GitLab account to deploy private repositories.</CardDescription>
            </div>
          </div>
          <Badge variant={data?.connected ? "default" : "secondary"} className={data?.connected ? "bg-green-500/10 text-green-600 border-green-500/30" : ""}>
            {data?.connected ? "Connected" : "Not Connected"}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {loading ? (
          <div className="flex items-center gap-2 text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" /> Checking GitLab connection...</div>
        ) : (
          <>
            {data?.warning ? <p className="text-sm text-muted-foreground">{data.warning}</p> : null}
            {data?.connected ? (
              <div className="text-sm text-muted-foreground">
                Connected as <span className="font-medium text-foreground">{data.account?.login || "GitLab user"}</span>.
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                Requires an authenticated connection to gitlab.com (or your self-hosted instance).
              </p>
            )}
            <div className="flex items-center gap-3">
              <Button onClick={startConnectFlow} disabled={connecting}>
                {connecting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <LinkIcon className="mr-2 h-4 w-4" />}
                {connecting ? "Redirecting..." : data?.connected ? "Reconnect GitLab" : "Connect GitLab"}
              </Button>
              <Button variant="outline" onClick={fetchStatus}>Refresh</Button>
            </div>
            {connectError ? <p className="text-sm text-destructive">{connectError}</p> : null}
          </>
        )}
      </CardContent>
    </Card>
  );
}
