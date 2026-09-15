"use client";

import React, { useCallback, useEffect, useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Shield, AlertTriangle, Ban, Loader2, RefreshCw } from "lucide-react";
import { crowdsecApi, CrowdSecDecision } from "@/lib/api";
import { ThreatDecisionCard } from "@/components/crowdsec/ThreatDecisionCard";

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
              <div className="flex items-center justify-between rounded-lg border p-4">
                <div className="space-y-0.5">
                  <Label className="text-base">First-strike blocking</Label>
                  <p className="text-sm text-muted-foreground">
                    Ban on the first exploit probe (sensitive files, path traversal,
                    admin-interface scans) instead of waiting for the scenario bucket
                    to fill. 404/crawler/bruteforce buckets keep their thresholds
                    to avoid false positives.
                  </p>
                </div>
                <Switch
                  checked={config.crowdsec_first_strike_enabled ?? true}
                  onCheckedChange={(v) => onChange("crowdsec_first_strike_enabled", v)}
                />
              </div>
              <div className="flex items-center justify-between rounded-lg border p-4">
                <div className="space-y-0.5">
                  <Label className="text-base">Automatic unblocking</Label>
                  <p className="text-sm text-muted-foreground">
                    Automatically remove bans older than the retention window below.
                    Turn off to require a manual Unblock click for every ban.
                  </p>
                </div>
                <Switch
                  checked={config.crowdsec_auto_unblock_enabled ?? true}
                  onCheckedChange={(v) => onChange("crowdsec_auto_unblock_enabled", v)}
                />
              </div>
              <div className="space-y-2">
                <Label>Auto-unblock after (hours)</Label>
                <Input
                  type="number"
                  min={1}
                  max={8760}
                  placeholder="24"
                  value={config.crowdsec_auto_unblock_after_hours ?? 24}
                  onChange={(e) => onChange("crowdsec_auto_unblock_after_hours", Number(e.target.value))}
                />
                <p className="text-xs text-muted-foreground">
                  Bans older than this are removed by the periodic sweeper.
                  Simulated (non-enforcing) decisions are never auto-removed.
                </p>
              </div>
              <div className="flex items-center justify-between rounded-lg border p-4">
                <div className="space-y-0.5">
                  <Label className="text-base">Cloudflare edge blocking</Label>
                  <p className="text-sm text-muted-foreground">
                    Push bans to Cloudflare account IP lists so attackers are
                    dropped at the CDN before reaching the origin. The Traefik
                    bouncer keeps enforcing locally as backup.
                  </p>
                </div>
                <Switch
                  checked={config.crowdsec_cf_enabled ?? false}
                  onCheckedChange={(v) => onChange("crowdsec_cf_enabled", v)}
                />
              </div>
              {config.crowdsec_cf_enabled && (
                <div className="space-y-4 mt-3 ml-1">
                  <div className="flex items-center justify-between rounded-lg border p-3">
                    <span className="text-sm text-muted-foreground">Edge bouncer status</span>
                    <Badge variant="outline" className="text-[10px]">
                      {config.crowdsec_cf_bouncer_running
                        ? "Running — bans enforced at Cloudflare"
                        : config.crowdsec_cf_api_token_set && config.crowdsec_cf_account_id
                          ? "Configured — starts on next update"
                          : "Idle — needs API token + account ID"}
                    </Badge>
                  </div>
                  <div className="space-y-2">
                    <Label>Cloudflare Account ID</Label>
                    <Input
                      placeholder="32 hex chars from the dashboard URL"
                      value={config.crowdsec_cf_account_id ?? ""}
                      onChange={(e) => onChange("crowdsec_cf_account_id", e.target.value)}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label>Cloudflare API Token</Label>
                    <Input
                      type="password"
                      placeholder={config.crowdsec_cf_api_token_set ? "•••••••• (Saved)" : "Custom token with account Firewall/IP List write access"}
                      onChange={(e) => onChange("crowdsec_cf_api_token", e.target.value)}
                    />
                    <p className="text-xs text-muted-foreground">
                      Custom token scoped to your account with Firewall and IP
                      List write access. Never shown again after saving.
                    </p>
                  </div>
                  <div className="space-y-2">
                    <Label>Edge action</Label>
                    <Select
                      value={config.crowdsec_cf_action ?? "block"}
                      onValueChange={(v) => onChange("crowdsec_cf_action", v)}
                    >
                      <SelectTrigger>
                        <SelectValue placeholder="block" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="block">Block (harshest)</SelectItem>
                        <SelectItem value="managed_challenge">Managed challenge</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </div>
              )}
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
              <ThreatDecisionCard
                key={d.id || d.value}
                decision={d}
                unbanningIp={unbanningIp}
                onUnban={handleUnban}
                showService
              />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
