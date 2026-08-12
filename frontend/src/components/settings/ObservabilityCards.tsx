"use client";

import React from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Activity, CreditCard } from "lucide-react";

interface ObservabilityCardProps {
  config: any;
  onChange: (field: string, value: any) => void;
}

export function ObservabilityCard({ config, onChange }: ObservabilityCardProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center space-x-2">
          <Activity className="h-5 w-5" />
          <span>Observability & Sentry</span>
        </CardTitle>
        <CardDescription>Configure external telemetry and monitoring services.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <Label>Sentry DSN</Label>
          <Input
            type="password"
            placeholder={config.sentry_dsn_set ? "•••••••• (Saved)" : "https://..."}
            onChange={(e) => onChange("sentry_dsn", e.target.value)}
          />
        </div>
        <div className="space-y-2">
          <Label>Sentry Environment</Label>
          <Input
            placeholder="production"
            value={config.sentry_environment || ""}
            onChange={(e) => onChange("sentry_environment", e.target.value)}
          />
        </div>
        <div className="space-y-2">
          <Label>Traces Sample Rate (0.0 - 1.0)</Label>
          <Input
            type="number"
            step="0.1"
            min="0"
            max="1"
            value={config.sentry_traces_sample_rate || 0.1}
            onChange={(e) => onChange("sentry_traces_sample_rate", parseFloat(e.target.value))}
          />
        </div>
      </CardContent>
    </Card>
  );
}

export function BillingSmsCard({ config, onChange }: ObservabilityCardProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center space-x-2">
          <CreditCard className="h-5 w-5" />
          <span>Billing & SMS Alerts</span>
        </CardTitle>
        <CardDescription>Configure the underlying billing mechanics and SMS alerts.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <Label>Billing Currency</Label>
          <Input
            placeholder="USD"
            value={config.billing_currency || "USD"}
            onChange={(e) => onChange("billing_currency", e.target.value)}
          />
        </div>
        <div className="space-y-2">
          <Label>Pro Plan Amount</Label>
          <Input
            placeholder="29.00"
            value={config.billing_pro_amount || "29.00"}
            onChange={(e) => onChange("billing_pro_amount", e.target.value)}
          />
        </div>
        <div className="space-y-2">
          <Label>Pro Plan Period Days</Label>
          <Input
            type="number"
            min="1"
            value={config.billing_pro_period_days || 30}
            onChange={(e) => onChange("billing_pro_period_days", parseInt(e.target.value))}
          />
        </div>
        <div className="space-y-2">
          <Label>Alert Phone Number</Label>
          <Input
            placeholder="+1234567890"
            value={config.alert_phone_number || ""}
            onChange={(e) => onChange("alert_phone_number", e.target.value)}
          />
        </div>
        <div className="space-y-2">
          <Label>Critical Alert Phone Number</Label>
          <Input
            placeholder="+1234567890"
            value={config.critical_alert_phone || ""}
            onChange={(e) => onChange("critical_alert_phone", e.target.value)}
          />
        </div>
      </CardContent>
    </Card>
  );
}
