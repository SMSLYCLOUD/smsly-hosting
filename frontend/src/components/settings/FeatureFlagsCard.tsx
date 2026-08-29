"use client";

import React from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import { Shield, X } from "lucide-react";

interface FeatureFlagsCardProps {
  config: any;
  onChange: (field: string, value: any) => void;
}

export function FeatureFlagsCard({ config, onChange }: FeatureFlagsCardProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center space-x-2">
          <Shield className="h-5 w-5" />
          <span>Feature Flags</span>
        </CardTitle>
        <CardDescription>Toggle global platform capabilities.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center justify-between rounded-lg border p-4">
          <div className="space-y-0.5">
            <Label className="text-base">Traffic Geo Collection</Label>
            <p className="text-sm text-muted-foreground">Collect Traefik access logs and resolve IP geolocations for the traffic world map.</p>
          </div>
          <Switch
            checked={config.traffic_geo_enabled ?? true}
            onCheckedChange={(v) => onChange("traffic_geo_enabled", v)}
          />
        </div>
        {config.traffic_geo_enabled && (
          <div className="space-y-2 ml-1">
            <Label>Mapbox Token (optional)</Label>
            <div className="flex gap-2">
              <Input
                type="password"
                placeholder={config.mapbox_token_set ? "•••••••• (Saved)" : "Leave empty to use free OpenFreeMap tiles"}
                onChange={(e) => onChange("mapbox_token", e.target.value)}
                className="flex-1"
              />
              {config.mapbox_token_set && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => onChange("mapbox_token", "")}
                  className="shrink-0 text-destructive hover:text-destructive hover:bg-destructive/10"
                  title="Clear saved Mapbox token"
                >
                  <X className="h-4 w-4" />
                </Button>
              )}
            </div>
            <p className="text-xs text-muted-foreground">
              Optional. When empty, the map uses free OpenFreeMap tiles (no account needed).
              Get a token at <a href="https://mapbox.com" target="_blank" rel="noopener noreferrer" className="underline">mapbox.com</a> for premium styles.
            </p>
          </div>
        )}
        <div className="flex items-center justify-between rounded-lg border p-4">
          <div className="space-y-0.5">
            <Label className="text-base">Disable Tier Gates</Label>
            <p className="text-sm text-muted-foreground">Allow all users to access premium features.</p>
          </div>
          <Switch
            checked={config.smsly_disable_tier_gates || false}
            onCheckedChange={(v) => onChange("smsly_disable_tier_gates", v)}
          />
        </div>
        <div className="flex items-center justify-between rounded-lg border p-4">
          <div className="space-y-0.5">
            <Label className="text-base">Legacy Tunnel API</Label>
            <p className="text-sm text-muted-foreground">Enable the legacy reverse-proxy tunnel endpoints.</p>
          </div>
          <Switch
            checked={config.enable_legacy_tunnel_api || false}
            onCheckedChange={(v) => onChange("enable_legacy_tunnel_api", v)}
          />
        </div>
        <div className="flex items-center justify-between rounded-lg border p-4">
          <div className="space-y-0.5">
            <Label className="text-base">Strict SSH Host Key Check</Label>
            <p className="text-sm text-muted-foreground">Require strict host key checking for nodes.</p>
          </div>
          <Switch
            checked={config.smsly_strict_ssh_host_key_check || false}
            onCheckedChange={(v) => onChange("smsly_strict_ssh_host_key_check", v)}
          />
        </div>
      </CardContent>
    </Card>
  );
}
