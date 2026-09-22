"use client";

import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { ShieldCheck } from "lucide-react";

interface EdgeShieldCardProps {
  config: any;
  onChange: (field: string, value: any) => void;
}

export function EdgeShieldCard({ config, onChange }: EdgeShieldCardProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center space-x-2">
          <ShieldCheck className="h-5 w-5" />
          <span>Edge Shield (Cloudflare Proxy)</span>
        </CardTitle>
        <CardDescription>
          Route DNS records through the Cloudflare proxy instead of DNS-only to the origin.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center justify-between rounded-lg border border-border p-3">
          <div className="space-y-0.5">
            <Label>Proxy DNS records (orange cloud)</Label>
            <p className="text-xs text-muted-foreground">
              Absorbs origin-prefix hijack and L3–L4 DDoS. The platform never
              downgrades an orange record automatically — turning this off only
              affects newly reconciled records.
            </p>
          </div>
          <Switch
            checked={!!config.edge_proxy_records}
            onCheckedChange={(v) => onChange("edge_proxy_records", v)}
          />
        </div>
      </CardContent>
    </Card>
  );
}
