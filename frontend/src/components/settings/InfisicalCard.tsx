"use client";

import React, { useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Loader2, Shield } from "lucide-react";
import { useToast } from "@/components/ui/use-toast";
import { infisicalApi } from "@/lib/api";

export function InfisicalCard() {
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
      <CardContent className="flex items-center justify-between">
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
      </CardContent>
    </Card>
  );
}
