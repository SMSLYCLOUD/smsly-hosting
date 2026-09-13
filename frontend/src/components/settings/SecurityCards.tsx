"use client";

import React, { useCallback, useEffect, useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Shield, AlertTriangle, Ban, Loader2, RefreshCw, ShieldCheck } from "lucide-react";
import { crowdsecApi, CrowdSecDecision } from "@/lib/api";

interface SecurityCardProps {
  config: any;
  onChange: (field: string, value: any) => void;
}

export function SecurityScanningCard({ config, onChange }: SecurityCardProps) {
  return (
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
            onCheckedChange={(v) => onChange("trivy_enabled", v)}
          />
        </div>
        {config.trivy_enabled !== false && (
          <div className="space-y-2">
            <Label>Fail Build On Severity</Label>
            <select
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              value={config.trivy_fail_on_severity || "CRITICAL"}
              onChange={(e) => onChange("trivy_fail_on_severity", e.target.value)}
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
              onCheckedChange={(v) => onChange("cosign_enabled", v)}
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
                onCheckedChange={(v) => onChange("cosign_require_verification", v)}
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
              onCheckedChange={(v) => onChange("enable_crowdsec_waf", v)}
            />
          </div>
          {config.enable_crowdsec_waf && (
            <div className="space-y-4 mt-3 ml-1">
              <div className="space-y-2">
                <Label>CrowdSec Bouncer API Key</Label>
                <Input
                  type="password"
                  placeholder={config.crowdsec_bouncer_key_set ? "•••••••• (Saved)" : "64-char hex token from cscli bouncers add"}
                  onChange={(e) => onChange("crowdsec_bouncer_key", e.target.value)}
                />
                <p className="text-xs text-muted-foreground">
                  The bouncer key authenticates Traefik with CrowdSec.
                  Generate with: <code className="bg-muted px-1 rounded">cscli bouncers add traefik_bouncer -o raw</code>
                </p>
              </div>
              <div className="space-y-2">
                <Label>CrowdSec Enrollment Key (optional)</Label>
                <Input
                  type="password"
                  placeholder={config.crowdsec_enroll_key_set ? "•••••••• (Saved)" : "Console enrollment key (optional)"}
                  onChange={(e) => onChange("crowdsec_enroll_key", e.target.value)}
                />
                <p className="text-xs text-muted-foreground">
                  Optional. Enroll in CrowdSec console for community threat intelligence.
                </p>
              </div>
            </div>
          )}
        </div>

        <div className="border-t pt-4 mt-4">
          <div className="flex items-center justify-between rounded-lg border p-4">
            <div className="space-y-0.5">
              <Label className="text-base">Require Backup Encryption</Label>
              <p className="text-sm text-muted-foreground">Force encryption for all server backups. Auto-enabled in production.</p>
            </div>
            <Switch
              checked={config.backup_require_encryption ?? false}
              onCheckedChange={(v) => onChange("backup_require_encryption", v)}
            />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

export function DeviceTrustCard({ config, onChange }: SecurityCardProps) {
  return (
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
            onCheckedChange={(v) => onChange("enforce_device_trust", v)}
          />
        </div>
      </CardContent>
    </Card>
  );
}

export function CrowdSecBlocksCard() {
  const [decisions, setDecisions] = useState<CrowdSecDecision[]>([]);
  const [alertCount, setAlertCount] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [unbanningIp, setUnbanningIp] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchBlocks = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [decRes, alertRes] = await Promise.all([
        crowdsecApi.decisions({ limit: 100 }),
        crowdsecApi.alerts({ limit: 1 }).catch(() => null),
      ]);
      setDecisions(decRes.results || []);
      setAlertCount(alertRes ? alertRes.count : null);
    } catch (err: any) {
      setError(err?.response?.data?.error || err?.message || "Failed to load threat blocks");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchBlocks();
  }, [fetchBlocks]);

  const handleUnban = async (ip: string) => {
    setUnbanningIp(ip);
    setError(null);
    try {
      await crowdsecApi.unban(ip);
      await fetchBlocks();
    } catch (err: any) {
      setError(err?.response?.data?.error || err?.message || `Failed to unblock ${ip}`);
    } finally {
      setUnbanningIp(null);
    }
  };

  return (
    <Card className="md:col-span-2">
      <CardHeader>
        <CardTitle className="flex items-center space-x-2">
          <Ban className="h-5 w-5" />
          <span>CrowdSec Threat Blocks</span>
          {!loading && !error && (
            <Badge variant={decisions.length > 0 ? "destructive" : "outline"} className="text-[10px] ml-1">
              {decisions.length} active
            </Badge>
          )}
        </CardTitle>
        <CardDescription>
          IPs currently blocked platform-wide, why they were blocked, and one-click unblock.
          {alertCount !== null && alertCount > 0 && ` ${alertCount} threat alerts on record.`}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="flex justify-end mb-2">
          <Button onClick={fetchBlocks} variant="ghost" size="sm" disabled={loading}>
            <RefreshCw className={`h-3 w-3 mr-1 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </Button>
        </div>
        {loading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        ) : error ? (
          <p className="text-sm text-red-400">{error}</p>
        ) : decisions.length === 0 ? (
          <p className="text-sm text-muted-foreground">No active blocks. CrowdSec re-blocks reoffending IPs automatically.</p>
        ) : (
          <div className="space-y-1 max-h-72 overflow-y-auto">
            {decisions.map((d) => (
              <div key={d.id || d.value} className="flex items-center gap-2 p-2 rounded-lg bg-black/20 text-xs">
                <code className="text-foreground font-semibold">{d.value}</code>
                <Badge variant="destructive" className="text-[9px]">{d.type || d.scope}</Badge>
                <span className="text-muted-foreground truncate flex-1" title={d.scenario}>
                  {d.scenario || "unknown scenario"} · {d.events_count} events
                </span>
                {d.service_name && (
                  <Badge variant="outline" className="text-[9px] hidden md:inline-flex">{d.service_name}</Badge>
                )}
                {d.end_time && (
                  <span className="text-muted-foreground hidden sm:inline">
                    until {new Date(d.end_time).toLocaleString()}
                  </span>
                )}
                <Button
                  variant="outline"
                  size="sm"
                  className="h-6 text-[10px]"
                  disabled={unbanningIp === d.value}
                  onClick={() => handleUnban(d.value)}
                >
                  {unbanningIp === d.value ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    <><ShieldCheck className="h-3 w-3 mr-1" /> Unblock</>
                  )}
                </Button>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
