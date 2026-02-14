"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Github, Loader2, Link as LinkIcon } from "lucide-react";
import api from "@/lib/api";

type GitHubConnection = {
  connected: boolean;
  has_token: boolean;
  connect_url: string;
  account: null | {
    uid: string;
    login: string | null;
    avatar_url: string | null;
  };
  warning?: string;
};

export function GitHubIntegrationCard() {
  const [loading, setLoading] = useState(true);
  const [data, setData] = useState<GitHubConnection | null>(null);

  const fetchStatus = async () => {
    setLoading(true);
    try {
      const res = await api.get("/integrations/github/");
      setData(res.data);
    } catch (e) {
      setData(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchStatus();
  }, []);

  const connectUrl = data?.connect_url || "/accounts/github/login/?process=connect&next=/auth/callback";

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <Github className="h-6 w-6" />
            <div>
              <CardTitle className="text-lg">GitHub Connection</CardTitle>
              <CardDescription>Link your GitHub account to deploy private repositories.</CardDescription>
            </div>
          </div>
          <Badge
            variant={data?.connected ? "default" : "secondary"}
            className={data?.connected ? "bg-green-500/10 text-green-600 border-green-500/30" : ""}
          >
            {data?.connected ? "Connected" : "Not Connected"}
          </Badge>
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        {loading ? (
          <div className="flex items-center gap-2 text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Checking GitHub connection...
          </div>
        ) : (
          <>
            {data?.warning ? (
              <p className="text-sm text-muted-foreground">{data.warning}</p>
            ) : null}

            {data?.connected ? (
              <div className="text-sm text-muted-foreground">
                Connected as <span className="font-medium text-foreground">{data.account?.login || "GitHub user"}</span>.
                {!data.has_token ? (
                  <span className="block mt-1 text-amber-600">
                    Token not available. Reconnect to grant the required permissions.
                  </span>
                ) : null}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                Private GitHub repositories require an authenticated connection with <code>repo</code> scope.
              </p>
            )}

            <div className="flex items-center gap-3">
              <Button asChild>
                <a href={connectUrl}>
                  <LinkIcon className="mr-2 h-4 w-4" />
                  {data?.connected ? "Reconnect GitHub" : "Connect GitHub"}
                </a>
              </Button>
              <Button variant="outline" onClick={fetchStatus}>
                Refresh
              </Button>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

