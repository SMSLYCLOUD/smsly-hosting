"use client";

import React, { useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Loader2, Shield, CheckCircle2, XCircle } from "lucide-react";
import { useToast } from "@/components/ui/use-toast";
import { infisicalApi } from "@/lib/api";

export function InfisicalCard({ config, onChange }: { config?: any; onChange?: (field: string, value: any) => void }) {
  const { toast } = useToast();
  const [syncing, setSyncing] = useState(false);

  const handleSync = async () => {
    try {
      setSyncing(true);
      const res = await infisicalApi.sync({ direction: "push", workspace: "smsly-platform" });
      toast({ title: "Infisical Sync Success", description: res.message || `Synced ${res.synced_count || 0} secrets.` });
    } catch (err: any) {
      toast({
        title: "Infisical Sync Failed",
        description: err?.response?.data?.message || err?.response?.data?.error || "Failed to sync secrets with Infisical.",
        variant: "destructive",
      });
    } finally {
      setSyncing(false);
    }
  };

  return (
    <Card className="border-border">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Shield className="h-5 w-5 text-purple-500" />
          Infisical Secret Synchronization
        </CardTitle>
        <CardDescription>
          Synchronize platform configuration and environment variables with Infisical secret management service.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {onChange && (
          <div className="space-y-2">
            <Label className="flex items-center gap-2">
              Service Token
              {config?.infisical_service_token_set ? (
                <span className="flex items-center gap-1 text-xs font-normal text-green-500">
                  <CheckCircle2 className="h-3.5 w-3.5" /> Saved
                </span>
              ) : (
                <span className="flex items-center gap-1 text-xs font-normal text-yellow-500">
                  <XCircle className="h-3.5 w-3.5" /> Missing — vault injection disabled
                </span>
              )}
            </Label>
            <Input
              type="password"
              placeholder={config?.infisical_service_token_set ? "•••••••• (Saved — blank keeps it)" : "Paste from Organization Settings → Service Tokens"}
              onChange={(e) => onChange("infisical_service_token", e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              Stored encrypted, live immediately — no restart. Mint at your secrets UI
              (Organization Settings → Service Tokens). Auto-mint runs when admin bootstrap exists.
            </p>
          </div>
        )}
        <div className="flex items-center justify-between">
          <div className="space-y-0.5">
            <Label className="text-base font-medium">Sync Platform Secrets</Label>
            <p className="text-sm text-muted-foreground">Push active platform configuration values and encryption keys to Infisical.</p>
          </div>
          <Button
            onClick={handleSync}
            disabled={syncing}
            className="bg-purple-600 hover:bg-purple-700 text-white"
          >
            {syncing && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            Sync Secrets Now
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
