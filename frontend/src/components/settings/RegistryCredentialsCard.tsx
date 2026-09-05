"use client";

import { useState, useEffect, useCallback } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { projectRegistryApi, ProjectRegistryInfo } from "@/lib/api";
import { useToast } from "@/components/ui/use-toast";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { Copy, Eye, EyeOff, KeyRound, Loader2, RefreshCw, Server, ShieldCheck } from "lucide-react";

interface RegistryCredentialsCardProps {
  projectId: string;
}

export function RegistryCredentialsCard({ projectId }: RegistryCredentialsCardProps) {
  const { toast } = useToast();
  const confirm = useConfirm();
  const [info, setInfo] = useState<ProjectRegistryInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [showPassword, setShowPassword] = useState(false);
  const [rotating, setRotating] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await projectRegistryApi.get(projectId);
      setInfo(data);
    } catch {
      // Non-fatal — the scoped registry editor below still works
      setInfo(null);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => { load(); }, [load]);

  const copy = (label: string, value: string) => {
    navigator.clipboard.writeText(value);
    toast({ title: `${label} copied` });
  };

  const rotate = async () => {
    const ok = await confirm({
      title: "Rotate registry credentials?",
      message: "The current password stops working immediately. Any node that pulled with the old credential must re-login (re-provision the node or run docker login on it).",
      confirmText: "Rotate",
    });
    if (!ok) return;
    setRotating(true);
    try {
      await projectRegistryApi.rotate(projectId);
      await load();
      setShowPassword(true);
      toast({ title: "Credentials rotated", description: "New password generated and shown below." });
    } catch {
      toast({ title: "Rotation failed", variant: "destructive" });
    } finally {
      setRotating(false);
    }
  };

  if (loading) {
    return (
      <Card>
        <CardContent className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading registry credentials…
        </CardContent>
      </Card>
    );
  }

  if (!info?.auth) {
    return (
      <Card>
        <CardContent className="py-6 text-sm text-muted-foreground">
          Registry credentials unavailable on this install.
        </CardContent>
      </Card>
    );
  }

  const auth = info.auth;
  const primaryUrl = info.effective_url || auth.urls[0];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <KeyRound className="h-4 w-4 text-emerald-500" />
          Registry Access
          <Badge variant={auth.per_project ? "default" : "secondary"} className="text-[10px]">
            {auth.per_project ? "per-project credential" : "platform credential"}
          </Badge>
        </CardTitle>
        <CardDescription>
          Auto-generated credentials for pushing and pulling this project&apos;s images.
          Nodes pull via <code className="text-xs">{auth.node_url || "the master registry"}</code>.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid gap-2">
          <div className="flex items-center justify-between gap-2 rounded-md border border-border bg-muted/40 px-3 py-2">
            <div className="min-w-0">
              <div className="text-[10px] uppercase text-muted-foreground">Registry URL (master)</div>
              <div className="truncate font-mono text-xs">{primaryUrl}</div>
            </div>
            <Button size="sm" variant="ghost" onClick={() => copy("URL", primaryUrl)}>
              <Copy className="h-3.5 w-3.5" />
            </Button>
          </div>

          {auth.node_url && auth.node_url !== primaryUrl && (
            <div className="flex items-center justify-between gap-2 rounded-md border border-border bg-muted/40 px-3 py-2">
              <div className="min-w-0">
                <div className="text-[10px] uppercase text-muted-foreground">Registry URL (nodes)</div>
                <div className="truncate font-mono text-xs">{auth.node_url}</div>
              </div>
              <Button size="sm" variant="ghost" onClick={() => copy("Node URL", auth.node_url)}>
                <Copy className="h-3.5 w-3.5" />
              </Button>
            </div>
          )}

          <div className="flex items-center justify-between gap-2 rounded-md border border-border bg-muted/40 px-3 py-2">
            <div className="min-w-0">
              <div className="text-[10px] uppercase text-muted-foreground">Username</div>
              <div className="truncate font-mono text-xs">{auth.username}</div>
            </div>
            <Button size="sm" variant="ghost" onClick={() => copy("Username", auth.username)}>
              <Copy className="h-3.5 w-3.5" />
            </Button>
          </div>

          <div className="flex items-center justify-between gap-2 rounded-md border border-border bg-muted/40 px-3 py-2">
            <div className="min-w-0">
              <div className="text-[10px] uppercase text-muted-foreground">Password</div>
              <div className="truncate font-mono text-xs">
                {showPassword ? auth.password : "•".repeat(24)}
              </div>
            </div>
            <div className="flex gap-1">
              <Button size="sm" variant="ghost" onClick={() => setShowPassword(!showPassword)}>
                {showPassword ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => copy("Password", auth.password)}>
                <Copy className="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>
        </div>

        <div className="flex items-center justify-between pt-1">
          <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <ShieldCheck className="h-3.5 w-3.5 text-emerald-500" />
            Stored encrypted · visible to project owners only
          </p>
          <Button size="sm" variant="outline" onClick={rotate} disabled={rotating || !auth.per_project}>
            {rotating ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
            Rotate
          </Button>
        </div>

        {auth.per_project && (
          <div className="rounded-md border border-border/60 bg-muted/20 p-2.5 text-[11px] leading-relaxed text-muted-foreground">
            <div className="mb-1 flex items-center gap-1.5 font-medium text-foreground">
              <Server className="h-3.5 w-3.5" /> Use on a node
            </div>
            <code className="block break-all font-mono">
              docker login {auth.node_url || primaryUrl} -u {auth.username}
            </code>
            then pull:{" "}
            <code className="break-all font-mono">
              {(auth.node_url || primaryUrl)}/smsly/&lt;service&gt;:&lt;tag&gt;
            </code>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
