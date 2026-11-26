"use client";

import React, { useState, useEffect } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { useToast } from "@/components/ui/use-toast";
import { notificationsApi } from "@/lib/api";
import { Loader2, Bell, Mail, MessageSquare } from "lucide-react";

const EVENT_LABELS: Record<string, string> = {
  deploy_success: "Deployment Succeeded",
  deploy_failed: "Deployment Failed",
  health_alert: "Health & Resource Alerts",
  billing_due: "Billing & Invoice Reminders",
  ssl_expiring: "SSL Certificate Expiring",
  backup_completed: "Backup Completed",
  replication_lag: "Replication Lag Warning",
  replication_node_down: "Replication Node Down",
};

const CHANNELS = ["in_app", "email", "slack"] as const;
type NotificationChannelType = (typeof CHANNELS)[number];

export function PreferencesTab() {
  const { toast } = useToast();
  const [loading, setLoading] = useState(true);
  const [preferences, setPreferences] = useState<any[]>([]);

  const fetchPreferences = async () => {
    try {
      const data = await notificationsApi.preferences();
      setPreferences(Array.isArray(data) ? data : []);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchPreferences();
  }, []);

  const hasChannel = (pref: any, channel: NotificationChannelType): boolean => {
    const channels = Array.isArray(pref?.channels) ? pref.channels : [];
    return channels.includes(channel);
  };

  const handleToggle = async (pref: any, channel: NotificationChannelType) => {
    const currentChannels: string[] = Array.isArray(pref?.channels) ? pref.channels : [];
    const enabled = currentChannels.includes(channel);
    const nextChannels = enabled
      ? currentChannels.filter((c) => c !== channel)
      : [...currentChannels, channel];

    setPreferences((prev) =>
      prev.map((p) => (p.id === pref.id ? { ...p, channels: nextChannels } : p))
    );
    try {
      await notificationsApi.updatePreference(String(pref.id), { channels: nextChannels });
      toast({ title: "Preference updated" });
    } catch (e: any) {
      setPreferences((prev) =>
        prev.map((p) => (p.id === pref.id ? { ...p, channels: currentChannels } : p))
      );
      toast({
        title: "Error",
        description: e?.response?.data?.detail || e?.message || "Failed to update preference.",
        variant: "destructive",
      });
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center p-8">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Bell className="h-5 w-5" />
            Notification Preferences
          </CardTitle>
          <CardDescription>Choose which delivery channels are active for each event type.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {preferences.length === 0 ? (
            <div className="p-4 bg-muted/20 text-muted-foreground rounded-md text-sm">
              No preferences configured yet.
            </div>
          ) : (
            preferences.map((pref) => (
              <div key={pref.id} className="flex flex-col space-y-3 p-4 border border-border rounded-lg">
                <div className="flex items-center justify-between">
                  <h4 className="font-medium text-sm">
                    {EVENT_LABELS[pref.event_type] || String(pref.event_type || "").replace(/_/g, " ")}
                  </h4>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 pt-2 border-t border-border/50">
                  <div className="flex items-center space-x-2">
                    <Switch
                      id={`in_app-${pref.id}`}
                      checked={hasChannel(pref, "in_app")}
                      onCheckedChange={() => handleToggle(pref, "in_app")}
                    />
                    <Label htmlFor={`in_app-${pref.id}`} className="flex items-center gap-2 cursor-pointer">
                      <Bell className="h-4 w-4" /> In-App
                    </Label>
                  </div>
                  <div className="flex items-center space-x-2">
                    <Switch
                      id={`email-${pref.id}`}
                      checked={hasChannel(pref, "email")}
                      onCheckedChange={() => handleToggle(pref, "email")}
                    />
                    <Label htmlFor={`email-${pref.id}`} className="flex items-center gap-2 cursor-pointer">
                      <Mail className="h-4 w-4" /> Email
                    </Label>
                  </div>
                  <div className="flex items-center space-x-2">
                    <Switch
                      id={`slack-${pref.id}`}
                      checked={hasChannel(pref, "slack")}
                      onCheckedChange={() => handleToggle(pref, "slack")}
                    />
                    <Label htmlFor={`slack-${pref.id}`} className="flex items-center gap-2 cursor-pointer">
                      <MessageSquare className="h-4 w-4" /> Slack
                    </Label>
                  </div>
                </div>
              </div>
            ))
          )}
        </CardContent>
      </Card>
    </div>
  );
}
