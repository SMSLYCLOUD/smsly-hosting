"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Code2, Loader2, Link as LinkIcon } from "lucide-react";
import api from "@/lib/api";

type BitbucketConnection = {
  connected: boolean;
  has_token: boolean;
  account: null | { uid: string; login: string | null; avatar_url: string | null };
  warning?: string;
};

export function BitbucketIntegrationCard() {
  const [loading, setLoading] = useState(true);
  const [connecting, setConnecting] = useState(false);
  const [data, setData] = useState<BitbucketConnection | null>(null);
  const [connectError, setConnectError] = useState<string | null>(null);

  const fetchStatus = async () => {
    setLoading(true);
    try {
      const res = await api.get("/integrations/bitbucket/");
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
      const res = await api.get("/integrations/bitbucket/oauth-url/");
      const target = res.data?.url;
      if (!target) throw new Error("No OAuth URL returned");
      window.location.assign(target);
    } catch (e: any) {
      setConnectError(String(e?.response?.data?.error || "Unable to start Bitbucket connection flow."));
    } finally {
      setConnecting(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <Code2 className="h-6 w-6 text-blue-500" />
            <div>
              <CardTitle className="text-lg">Bitbucket Connection</CardTitle>
              <CardDescription>Link your Bitbucket account to deploy private repositories.</CardDescription>
            </div>
          </div>
          <Badge variant={data?.connected ? "default" : "secondary"} className={data?.connected ? "bg-green-500/10 text-green-600 border-green-500/30" : ""}>
            {data?.connected ? "Connected" : "Not Connected"}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {loading ? (
          <div className="flex items-center gap-2 text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" /> Checking Bitbucket connection...</div>
        ) : (
          <>
            {data?.warning ? <p className="text-sm text-muted-foreground">{data.warning}</p> : null}
            {data?.connected ? (
              <div className="text-sm text-muted-foreground">
                Connected as <span className="font-medium text-foreground">{data.account?.login || "Bitbucket user"}</span>.
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                Requires an authenticated connection to bitbucket.org.
              </p>
            )}
            <div className="flex items-center gap-3">
              <Button onClick={startConnectFlow} disabled={connecting}>
                {connecting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <LinkIcon className="mr-2 h-4 w-4" />}
                {connecting ? "Redirecting..." : data?.connected ? "Reconnect Bitbucket" : "Connect Bitbucket"}
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
