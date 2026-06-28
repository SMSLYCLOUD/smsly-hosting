"use client";

import React, { useState, useEffect } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { useToast } from "@/components/ui/use-toast";
import { notificationsApi } from "@/lib/api";
import { Loader2, Bell, Mail, MessageSquare } from "lucide-react";

export function PreferencesTab() {
  const { toast } = useToast();
  const [loading, setLoading] = useState(true);
  const [preferences, setPreferences] = useState<any[]>([]);

  const fetchPreferences = async () => {
    try {
      const data = await notificationsApi.preferences();
      setPreferences(data);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchPreferences();
  }, []);

  const handleToggle = async (id: string, field: string, currentValue: boolean) => {
    try {
      await notificationsApi.updatePreference(id, { [field]: !currentValue });
      setPreferences(preferences.map(p => p.id === id ? { ...p, [field]: !currentValue } : p));
      toast({ title: "Preference updated" });
    } catch (e: any) {
      toast({ title: "Error", description: e.message, variant: "destructive" });
    }
  };

  if (loading) return <div className="flex justify-center p-8"><Loader2 className="h-8 w-8 animate-spin text-muted-foreground" /></div>;

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Bell className="h-5 w-5" />
            Notification Preferences
          </CardTitle>
          <CardDescription>Manage how you receive notifications.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          {preferences.length === 0 ? (
            <div className="p-4 bg-muted/20 text-muted-foreground rounded-md text-sm">
              No preferences configured yet.
            </div>
          ) : (
            preferences.map((pref) => (
              <div key={pref.id} className="flex flex-col space-y-4 p-4 border border-white/10 rounded-lg">
                <div className="flex items-center justify-between">
                  <div>
                    <h4 className="font-medium text-sm capitalize">{pref.event_type.replace(/_/g, ' ')}</h4>
                  </div>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 pt-2 border-t border-white/5">
                  <div className="flex items-center space-x-2">
                    <Switch 
                      id={`in_app-${pref.id}`} 
                      checked={pref.in_app_enabled} 
                      onCheckedChange={() => handleToggle(pref.id, 'in_app_enabled', pref.in_app_enabled)}
                    />
                    <Label htmlFor={`in_app-${pref.id}`} className="flex items-center gap-2">
                      <Bell className="h-4 w-4" /> In-App
                    </Label>
                  </div>
                  <div className="flex items-center space-x-2">
                    <Switch 
                      id={`email-${pref.id}`} 
                      checked={pref.email_enabled} 
                      onCheckedChange={() => handleToggle(pref.id, 'email_enabled', pref.email_enabled)}
                    />
                    <Label htmlFor={`email-${pref.id}`} className="flex items-center gap-2">
                      <Mail className="h-4 w-4" /> Email
                    </Label>
                  </div>
                  <div className="flex items-center space-x-2">
                    <Switch 
                      id={`slack-${pref.id}`} 
                      checked={pref.slack_enabled} 
                      onCheckedChange={() => handleToggle(pref.id, 'slack_enabled', pref.slack_enabled)}
                    />
                    <Label htmlFor={`slack-${pref.id}`} className="flex items-center gap-2">
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
