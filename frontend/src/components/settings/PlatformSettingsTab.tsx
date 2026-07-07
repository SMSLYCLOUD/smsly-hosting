"use client";

import React, { useEffect, useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { useToast } from "@/components/ui/use-toast";
import { Loader2, Save, Server, Shield, Globe, Database, Activity, CreditCard, AlertTriangle, CheckCircle2, XCircle } from "lucide-react";
import api, { infisicalApi } from "@/lib/api";

export function PlatformSettingsTab() {
  const { toast } = useToast();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [syncingInfisical, setSyncingInfisical] = useState(false);
  const [config, setConfig] = useState<any>({});

  const fetchConfig = async () => {
    try {
      setLoading(true);
      const res = await api.get("/system/domain-config/");
      setConfig(res.data);
    } catch (err) {
      toast({ title: "Error fetching platform config", variant: "destructive" });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchConfig();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleChange = (field: string, value: any) => {
    setConfig((prev: any) => ({ ...prev, [field]: value }));
  };

  const handleSave = async () => {
    try {
      setSaving(true);
      await api.put("/system/domain-config/", config);
      toast({ title: "Platform configuration updated successfully" });
      await fetchConfig();
    } catch (err: any) {
      toast({
        title: "Failed to update platform configuration",
        description: err.response?.data?.error || err.message,
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  };

  const handleSyncInfisical = async () => {
    try {
      setSyncingInfisical(true);
      const res = await infisicalApi.sync({ direction: "push", workspace: "smsly-platform" });
      toast({ title: "Infisical Sync Success", description: res.message || `Synced ${res.synced_count || 0} secrets.` });
    } catch (err: any) {
      toast({
        title: "Infisical Sync Failed",
        description: err?.response?.data?.message || err?.response?.data?.error || "Failed to sync secrets with Infisical.",
        variant: "destructive",
      });
    } finally {
      setSyncingInfisical(false);
    }
  };

  if (loading) {
    return (
      <div className="flex h-48 items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-4xl">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-medium">Platform Global Settings</h3>
          <p className="text-sm text-muted-foreground">
            Configure global properties that affect the entire SMSLY hosting platform.
          </p>
        </div>
        <Button onClick={handleSave} disabled={saving}>
          {saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          <Save className="mr-2 h-4 w-4" />
          Save Changes
        </Button>
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        {/* Ecosystem Build Settings */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center space-x-2">
              <Server className="h-5 w-5" />
              <span>Ecosystem Pipeline</span>
            </CardTitle>
            <CardDescription>Configure concurrency and wave settings for multi-service deployments.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label>Global Max Concurrent Builds (1-10)</Label>
              <Input
                type="number"
                min="1"
                max="10"
                value={config.max_concurrent_builds || 1}
                onChange={(e) => handleChange("max_concurrent_builds", parseInt(e.target.value))}
              />
            </div>
            <div className="space-y-2">
              <Label>Ecosystem Max Concurrent Builds (1-10)</Label>
              <Input
                type="number"
                min="1"
                max="10"
                value={config.ecosystem_max_concurrent_builds || 2}
                onChange={(e) => handleChange("ecosystem_max_concurrent_builds", parseInt(e.target.value))}
              />
            </div>
            <div className="space-y-2">
              <Label>Ecosystem Build Stagger Seconds (0-300)</Label>
              <Input
                type="number"
                min="0"
                max="300"
                value={config.ecosystem_build_stagger_seconds || 30}
                onChange={(e) => handleChange("ecosystem_build_stagger_seconds", parseInt(e.target.value))}
              />
            </div>
            <div className="space-y-2">
              <Label>Default Wave Size (1-5)</Label>
              <Input
                type="number"
                min="1"
                max="5"
                value={config.ecosystem_default_wave_size || 5}
                onChange={(e) => handleChange("ecosystem_default_wave_size", parseInt(e.target.value))}
              />
            </div>
            <div className="space-y-2">
              <Label>Wave Recheck Seconds (5-300)</Label>
              <Input
                type="number"
                min="5"
                max="300"
                value={config.ecosystem_wave_recheck_seconds || 15}
                onChange={(e) => handleChange("ecosystem_wave_recheck_seconds", parseInt(e.target.value))}
              />
            </div>
          </CardContent>
        </Card>

        {/* Docker Registry Settings */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center space-x-2">
              <Database className="h-5 w-5" />
              <span>Container Registry</span>
              {config.container_registry_url ? (
                (() => {
                  const url = config.container_registry_url || '';
                  const isExternal = !url.startsWith('registry:') && !url.startsWith('127.') && !url.startsWith('localhost');
                  const hasCreds = config.REGISTRY_PASSWORD_SET;
                  return (
                    <span className="ml-2 flex items-center gap-1 text-xs font-normal">
                      {hasCreds ? (
                        <CheckCircle2 className="h-3.5 w-3.5 text-green-500" />
                      ) : (
                        <XCircle className="h-3.5 w-3.5 text-yellow-500" />
                      )}
                      <span className={isExternal ? "text-blue-500" : "text-muted-foreground"}>
                        {isExternal ? "External" : "Internal"}
                      </span>
                    </span>
                  );
                })()
              ) : null}
            </CardTitle>
            <CardDescription>
              Docker image registry for deployments. Supports internal (registry:5000) and external (Docker Hub, GHCR, ECR, etc.) registries.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label>Registry URL</Label>
              <Input
                placeholder="registry:5000 (internal) or docker.io/ghcr.io (external)"
                value={config.container_registry_url || ""}
                onChange={(e) => handleChange("container_registry_url", e.target.value)}
              />
              <p className="text-xs text-muted-foreground">
                Internal: registry:5000, 127.0.0.1:5000 · External: docker.io, ghcr.io, &lt;your-registry&gt;:5000
              </p>
            </div>
            <div className="space-y-2">
              <Label>Registry User</Label>
              <Input
                placeholder="smsly-registry"
                value={config.registry_user || ""}
                onChange={(e) => handleChange("registry_user", e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label>Registry Password</Label>
              <Input
                type="password"
                placeholder={config.registry_password_set ? "•••••••• (Saved)" : "Leave blank to keep unchanged"}
                onChange={(e) => handleChange("registry_password", e.target.value)}
              />
            </div>
          </CardContent>
        </Card>

        {/* Observability */}
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
                onChange={(e) => handleChange("sentry_dsn", e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label>Sentry Environment</Label>
              <Input
                placeholder="production"
                value={config.sentry_environment || ""}
                onChange={(e) => handleChange("sentry_environment", e.target.value)}
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
                onChange={(e) => handleChange("sentry_traces_sample_rate", parseFloat(e.target.value))}
              />
            </div>
          </CardContent>
        </Card>

        {/* Billing & Alerts */}
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
                onChange={(e) => handleChange("billing_currency", e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label>Pro Plan Amount</Label>
              <Input
                placeholder="29.00"
                value={config.billing_pro_amount || "29.00"}
                onChange={(e) => handleChange("billing_pro_amount", e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label>Pro Plan Period Days</Label>
              <Input
                type="number"
                min="1"
                value={config.billing_pro_period_days || 30}
                onChange={(e) => handleChange("billing_pro_period_days", parseInt(e.target.value))}
              />
            </div>
            <div className="space-y-2">
              <Label>Alert Phone Number</Label>
              <Input
                placeholder="+1234567890"
                value={config.alert_phone_number || ""}
                onChange={(e) => handleChange("alert_phone_number", e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label>Critical Alert Phone Number</Label>
              <Input
                placeholder="+1234567890"
                value={config.critical_alert_phone || ""}
                onChange={(e) => handleChange("critical_alert_phone", e.target.value)}
              />
            </div>
          </CardContent>
        </Card>

        {/* Feature Flags */}
        <Card className="md:col-span-2">
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
                <Label className="text-base">Disable Tier Gates</Label>
                <p className="text-sm text-muted-foreground">Allow all users to access premium features.</p>
              </div>
              <Switch
                checked={config.smsly_disable_tier_gates || false}
                onCheckedChange={(v) => handleChange("smsly_disable_tier_gates", v)}
              />
            </div>
            <div className="flex items-center justify-between rounded-lg border p-4">
              <div className="space-y-0.5">
                <Label className="text-base">Legacy Tunnel API</Label>
                <p className="text-sm text-muted-foreground">Enable the legacy reverse-proxy tunnel endpoints.</p>
              </div>
              <Switch
                checked={config.enable_legacy_tunnel_api || false}
                onCheckedChange={(v) => handleChange("enable_legacy_tunnel_api", v)}
              />
            </div>
            <div className="flex items-center justify-between rounded-lg border p-4">
              <div className="space-y-0.5">
                <Label className="text-base">Strict SSH Host Key Check</Label>
                <p className="text-sm text-muted-foreground">Require strict host key checking for nodes.</p>
              </div>
              <Switch
                checked={config.smsly_strict_ssh_host_key_check || false}
                onCheckedChange={(v) => handleChange("smsly_strict_ssh_host_key_check", v)}
              />
            </div>
          </CardContent>
        </Card>

        {/* Security Scanning */}
        <Card className="md:col-span-2">
          <CardHeader>
            <CardTitle className="flex items-center space-x-2">
              <Shield className="h-5 w-5" />
              <span>Security Scanning</span>
            </CardTitle>
            <CardDescription>Configure container image vulnerability scanning, signing, and runtime protection.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center justify-between rounded-lg border p-4">
              <div className="space-y-0.5">
                <Label className="text-base">Trivy Scanning Enabled</Label>
                <p className="text-sm text-muted-foreground">Scan container images for vulnerabilities during build.</p>
              </div>
              <Switch
                checked={config.trivy_enabled ?? true}
                onCheckedChange={(v) => handleChange("trivy_enabled", v)}
              />
            </div>
            {config.trivy_enabled !== false && (
              <div className="space-y-2">
                <Label>Fail Build On Severity</Label>
                <select
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  value={config.trivy_fail_on_severity || "CRITICAL"}
                  onChange={(e) => handleChange("trivy_fail_on_severity", e.target.value)}
                >
                  <option value="LOW">Low</option>
                  <option value="MEDIUM">Medium</option>
                  <option value="HIGH">High</option>
                  <option value="CRITICAL">Critical</option>
                </select>
                <p className="text-sm text-muted-foreground">
                  Builds are blocked when vulnerabilities at or above this severity are found.
                </p>
              </div>
            )}

            <div className="border-t pt-4 mt-4">
              <div className="flex items-center justify-between rounded-lg border p-4">
                <div className="space-y-0.5">
                  <Label className="text-base">Cosign Image Signing</Label>
                  <p className="text-sm text-muted-foreground">Sign container images with Cosign after build for supply-chain integrity.</p>
                </div>
                <Switch
                  checked={config.cosign_enabled ?? true}
                  onCheckedChange={(v) => handleChange("cosign_enabled", v)}
                />
              </div>
              {config.cosign_enabled !== false && (
                <div className="flex items-center justify-between rounded-lg border p-4 mt-2">
                  <div className="space-y-0.5">
                    <Label className="text-base">Require Signature Verification</Label>
                    <p className="text-sm text-muted-foreground">Block deployments if the image is unsigned or Cosign verification fails.</p>
                  </div>
                  <Switch
                    checked={config.cosign_require_verification ?? false}
                    onCheckedChange={(v) => handleChange("cosign_require_verification", v)}
                  />
                </div>
              )}
            </div>

            <div className="border-t pt-4 mt-4">
              <div className="flex items-center justify-between rounded-lg border p-4">
                <div className="space-y-0.5">
                  <Label className="text-base">CrowdSec WAF</Label>
                  <p className="text-sm text-muted-foreground">Enable CrowdSec to automatically block malicious traffic.</p>
                </div>
                <Switch
                  checked={config.enable_crowdsec_waf ?? false}
                  onCheckedChange={(v) => handleChange("enable_crowdsec_waf", v)}
                />
              </div>
            </div>

            <div className="border-t pt-4 mt-4">
              <div className="flex items-center justify-between rounded-lg border p-4">
                <div className="space-y-0.5">
                  <Label className="text-base">Require Backup Encryption</Label>
                  <p className="text-sm text-muted-foreground">Force encryption for all server backups. Auto-enabled in production.</p>
                </div>
                <Switch
                  checked={config.backup_require_encryption ?? false}
                  onCheckedChange={(v) => handleChange("backup_require_encryption", v)}
                />
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className="border-border">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Shield className="h-5 w-5" />
              <span>Device Trust</span>
              <span className="text-xs font-normal px-2 py-0.5 rounded bg-yellow-500/10 text-yellow-600 border border-yellow-500/20">Beta</span>
            </CardTitle>
            <CardDescription>Require device fingerprint registration for API access (Beta feature).</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-start gap-3 p-3 rounded-lg bg-yellow-500/5 border border-yellow-500/20">
              <AlertTriangle className="h-4 w-4 text-yellow-600 mt-0.5 shrink-0" />
              <div className="text-sm text-yellow-600/80">
                <p className="font-medium text-yellow-600">Beta — Use with caution</p>
                <p className="mt-1">
                  When enabled, all API requests must include a valid device token.
                  If you lose access to your registered devices, you may be locked out.
                  Test thoroughly before enabling in production.
                </p>
              </div>
            </div>
            <div className="flex items-center justify-between rounded-lg border p-4">
              <div className="space-y-0.5">
                <Label className="text-base">Enforce Device Trust</Label>
                <p className="text-sm text-muted-foreground">
                  Require hardware fingerprint registration before API access.
                </p>
              </div>
              <Switch
                checked={config.enforce_device_trust ?? false}
                onCheckedChange={(v) => handleChange("enforce_device_trust", v)}
              />
            </div>
          </CardContent>
        </Card>

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
              onClick={handleSyncInfisical}
              disabled={syncingInfisical}
              className="bg-purple-600 hover:bg-purple-700 text-white"
            >
              {syncingInfisical && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Sync Secrets Now
            </Button>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
