"use client";

import React, { useState, useEffect, useCallback } from "react";
import { Loader2 } from "lucide-react";
import { useToast } from "@/components/ui/use-toast";
import { alertsApi, systemApi, AlertRule, NotificationChannel } from "@/lib/api";
import { SmtpConfigCard } from "./SmtpConfigCard";
import { NotificationChannelsCard } from "./NotificationChannelsCard";
import { AlertRulesCard } from "./AlertRulesCard";

export function AlertsTab() {
  const { toast } = useToast();
  const [channels, setChannels] = useState<NotificationChannel[]>([]);
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [smtpConfig, setSmtpConfig] = useState<any>({});
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    try {
      const [channelsData, rulesData, smtpData] = await Promise.all([
        alertsApi.listChannels(),
        alertsApi.listRules(),
        systemApi.getDomainConfig(),
      ]);
      setChannels(channelsData);
      setRules(rulesData);
      setSmtpConfig(smtpData);
    } catch {
      toast({ title: "Failed to load alert configuration", variant: "destructive" });
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => { fetchData(); }, [fetchData]);

  if (loading) {
    return (
      <div className="flex h-48 items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <SmtpConfigCard config={smtpConfig} onConfigChange={setSmtpConfig} />
      <NotificationChannelsCard channels={channels} onRefresh={fetchData} />
      <AlertRulesCard rules={rules} channels={channels} onRefresh={fetchData} />
    </div>
  );
}
