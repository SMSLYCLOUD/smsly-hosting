"use client";

import { useState, useEffect, useCallback } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { useToast } from "@/components/ui/use-toast";
import { Cloud } from "lucide-react";
import { systemApi } from "@/lib/api";
import { ScrollReveal } from "@/components/ui/ScrollReveal";

export function PlatformConfigTab() {
  const { toast } = useToast();
  const [config, setConfig] = useState<any>(null);

  const fetchConfig = useCallback(async () => {
    try {
      const result = await systemApi.getConfig();
      setConfig(result);
    } catch {
      console.error("Failed to fetch system config");
    }
  }, []);

  useEffect(() => {
    fetchConfig();
  }, [fetchConfig]);

  if (!config) return <div className="flex items-center justify-center py-8">Loading...</div>;

  return (
    <div className="space-y-6">
      {/* Auto-Scaling — scaleIn */}
      <ScrollReveal variant="scaleIn">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2"><Cloud className="h-5 w-5 text-sky-500" /> Auto-Scaling Configuration</CardTitle>
            <CardDescription>Control how the SMSLY autoscaler adjusts replicas across your services.</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-6">
              <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                <div className="space-y-2">
                  <label className="text-sm font-medium">Max Replicas</label>
                  <p className="text-xs text-muted-foreground">Maximum replica containers per service</p>
                  <input type="number" min={1} max={50} value={config.SCALE_MAX_REPLICAS ?? 5} onChange={(e) => setConfig({ ...config, SCALE_MAX_REPLICAS: parseInt(e.target.value) || 5 })} className="w-full px-3 py-2 text-sm rounded-lg bg-background border border-border font-mono" />
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium">CPU High (%)</label>
                  <p className="text-xs text-muted-foreground">CPU% above which a new replica spawns</p>
                  <input type="number" min={10} max={100} value={config.SCALE_CPU_HIGH ?? 80} onChange={(e) => setConfig({ ...config, SCALE_CPU_HIGH: parseInt(e.target.value) || 80 })} className="w-full px-3 py-2 text-sm rounded-lg bg-background border border-border font-mono" />
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium">Cooldown (min)</label>
                  <p className="text-xs text-muted-foreground">Minutes between scale-up operations</p>
                  <input type="number" min={1} max={60} value={config.SCALE_COOLDOWN_MIN ?? 5} onChange={(e) => setConfig({ ...config, SCALE_COOLDOWN_MIN: parseInt(e.target.value) || 5 })} className="w-full px-3 py-2 text-sm rounded-lg bg-background border border-border font-mono" />
                </div>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div className="space-y-2">
                  <label className="text-sm font-medium">Node Min Score</label>
                  <p className="text-xs text-muted-foreground">Minimum weighted resource score (0-100) for a node to be accepted for spawning. Composite of 40% free mem + 40% free CPU + 20% free disk.</p>
                  <input type="number" min={0} max={100} value={config.NODE_SCORER_MIN_SCORE ?? 20} onChange={(e) => setConfig({ ...config, NODE_SCORER_MIN_SCORE: parseInt(e.target.value) || 20 })} className="w-full px-3 py-2 text-sm rounded-lg bg-background border border-border font-mono" />
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium">Node Min Free RAM (%)</label>
                  <p className="text-xs text-muted-foreground">Minimum free RAM percentage on a node before refusing to spawn a replica</p>
                  <input type="number" min={0} max={100} value={config.NODE_MIN_FREE_RAM_PCT ?? 20} onChange={(e) => setConfig({ ...config, NODE_MIN_FREE_RAM_PCT: parseInt(e.target.value) || 20 })} className="w-full px-3 py-2 text-sm rounded-lg bg-background border border-border font-mono" />
                </div>
              </div>
              <div className="flex justify-end">
                <Button onClick={async () => {
                  try {
                    const result = await systemApi.updateConfig({ SCALE_MAX_REPLICAS: config.SCALE_MAX_REPLICAS, SCALE_CPU_HIGH: config.SCALE_CPU_HIGH, SCALE_COOLDOWN_MIN: config.SCALE_COOLDOWN_MIN, NODE_SCORER_MIN_SCORE: config.NODE_SCORER_MIN_SCORE, NODE_MIN_FREE_RAM_PCT: config.NODE_MIN_FREE_RAM_PCT });
                    setConfig(result);
                    toast({ title: "Saved", description: "Auto-scaling config updated." });
                  } catch { toast({ title: "Failed", description: "Could not save config.", variant: "destructive" }); }
                }}>Save Changes</Button>
              </div>
            </div>
          </CardContent>
        </Card>
      </ScrollReveal>

      {/* Limits — slideRight */}
      <ScrollReveal variant="slideRight" delay={0.15}>
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">Limits & Rate Limiting</CardTitle>
            <CardDescription>Control upload sizes, cert caps, and API rate limits.</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div className="space-y-2"><Label>Max Upload Size (bytes)</Label><Input type="number" value={config.MAX_UPLOAD_SIZE ?? 104857600} onChange={(e) => setConfig({ ...config, MAX_UPLOAD_SIZE: parseInt(e.target.value) || 104857600 })} /><p className="text-xs text-muted-foreground">Default 100 MB</p></div>
                <div className="space-y-2"><Label>Max File Read (bytes)</Label><Input type="number" value={config.SMSLY_MAX_FILE_READ_SIZE ?? 10485760} onChange={(e) => setConfig({ ...config, SMSLY_MAX_FILE_READ_SIZE: parseInt(e.target.value) || 10485760 })} /><p className="text-xs text-muted-foreground">Default 10 MB</p></div>
                <div className="space-y-2"><Label>Daily Cert Cap</Label><Input type="number" value={config.CADDY_DAILY_CERT_CAP ?? 20} onChange={(e) => setConfig({ ...config, CADDY_DAILY_CERT_CAP: parseInt(e.target.value) || 20 })} /></div>
                <div className="space-y-2"><Label>API Rate Limit (req/min per IP)</Label><Input type="number" value={config.API_RATE_LIMIT ?? 10000} onChange={(e) => setConfig({ ...config, API_RATE_LIMIT: parseInt(e.target.value) || 10000 })} /></div>
              </div>
              <div className="flex items-center gap-3">
                <Switch checked={config.API_RATE_LIMIT_FAIL_CLOSED ?? false} onCheckedChange={(v) => setConfig({ ...config, API_RATE_LIMIT_FAIL_CLOSED: v })} />
                <Label>Fail closed on rate-limit error</Label>
              </div>
              <div className="flex justify-end">
                <Button onClick={async () => {
                  try {
                    const result = await systemApi.updateConfig({ MAX_UPLOAD_SIZE: config.MAX_UPLOAD_SIZE, SMSLY_MAX_FILE_READ_SIZE: config.SMSLY_MAX_FILE_READ_SIZE, CADDY_DAILY_CERT_CAP: config.CADDY_DAILY_CERT_CAP, API_RATE_LIMIT: config.API_RATE_LIMIT, API_RATE_LIMIT_FAIL_CLOSED: config.API_RATE_LIMIT_FAIL_CLOSED });
                    setConfig(result);
                    toast({ title: "Saved", description: "Limits config updated." });
                  } catch { toast({ title: "Failed", variant: "destructive" }); }
                }}>Save Limits</Button>
              </div>
            </div>
          </CardContent>
        </Card>
      </ScrollReveal>

      {/* Rollback Grace Period */}
      <ScrollReveal variant="slideRight" delay={0.18}>
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">Blue-Green Rollback</CardTitle>
            <CardDescription>How long to preserve rollback backup containers before cleanup.</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label>Rollback Grace Period (minutes)</Label>
                  <Input type="number" min={0} max={1440} value={config.ROLLBACK_GRACE_MINUTES ?? 10} onChange={(e) => setConfig({ ...config, ROLLBACK_GRACE_MINUTES: parseInt(e.target.value) || 0 })} />
                  <p className="text-xs text-muted-foreground">Minutes to keep rollback containers. Set 0 to clean immediately after promote.</p>
                </div>
              </div>
              <div className="flex justify-end">
                <Button onClick={async () => {
                  try {
                    const result = await systemApi.updateConfig({ ROLLBACK_GRACE_MINUTES: config.ROLLBACK_GRACE_MINUTES });
                    setConfig(result);
                    toast({ title: "Saved", description: "Rollback config updated." });
                  } catch { toast({ title: "Failed", variant: "destructive" }); }
                }}>Save Rollback</Button>
              </div>
            </div>
          </CardContent>
        </Card>
      </ScrollReveal>

      {/* Security — blurReveal */}
      <ScrollReveal variant="blurReveal" delay={0.2}>
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">Logging & Security Toggles</CardTitle>
            <CardDescription>Control log verbosity and security feature flags.</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              <div className="space-y-2">
                <Label>Django Log Level</Label>
                <select value={config.DJANGO_LOG_LEVEL ?? "INFO"} onChange={(e) => setConfig({ ...config, DJANGO_LOG_LEVEL: e.target.value })} className="w-full px-3 py-2 text-sm rounded-lg bg-background border border-border">
                  <option value="DEBUG">DEBUG</option>
                  <option value="INFO">INFO</option>
                  <option value="WARNING">WARNING</option>
                  <option value="ERROR">ERROR</option>
                  <option value="CRITICAL">CRITICAL</option>
                </select>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {([
                  ["GRID_ALLOW_CONTROL_PLANE_WORKLOADS", "Allow workloads on primary node"],
                  ["SMSLY_DISABLE_SIGNATURE_CHECK", "Disable HMAC signature check"],
                  ["SMSLY_DISABLE_TIER_GATES", "Disable billing tier gates"],
                  ["ENABLE_LEGACY_TUNNEL_API", "Enable legacy tunnel API"],
                  ["SMSLY_STRICT_SSH_HOST_KEY_CHECK", "Strict SSH host-key check"],
                  ["ENFORCE_DEVICE_TRUST", "Enforce device trust (beta)"],
                  ["ALLOW_INSECURE_INTER_NODE_TLS", "Allow insecure inter-node TLS"],
                ] as [string, string][]).map(([k, label]) => (
                  <div key={k} className="flex items-center gap-3">
                    <Switch checked={!!config[k]} onCheckedChange={(v) => setConfig({ ...config, [k]: v })} />
                    <Label className="text-sm">{label}</Label>
                  </div>
                ))}
              </div>
              <div className="flex justify-end">
                <Button onClick={async () => {
                  try {
                    const result = await systemApi.updateConfig({
                      DJANGO_LOG_LEVEL: config.DJANGO_LOG_LEVEL,
                      GRID_ALLOW_CONTROL_PLANE_WORKLOADS: config.GRID_ALLOW_CONTROL_PLANE_WORKLOADS,
                      SMSLY_DISABLE_SIGNATURE_CHECK: config.SMSLY_DISABLE_SIGNATURE_CHECK,
                      SMSLY_DISABLE_TIER_GATES: config.SMSLY_DISABLE_TIER_GATES,
                      ENABLE_LEGACY_TUNNEL_API: config.ENABLE_LEGACY_TUNNEL_API,
                      SMSLY_STRICT_SSH_HOST_KEY_CHECK: config.SMSLY_STRICT_SSH_HOST_KEY_CHECK,
                      ENFORCE_DEVICE_TRUST: config.ENFORCE_DEVICE_TRUST,
                      ALLOW_INSECURE_INTER_NODE_TLS: config.ALLOW_INSECURE_INTER_NODE_TLS,
                    });
                    setConfig(result);
                    toast({ title: "Saved", description: "Security toggles updated." });
                  } catch { toast({ title: "Failed", variant: "destructive" }); }
                }}>Save Security</Button>
              </div>
            </div>
          </CardContent>
        </Card>
      </ScrollReveal>
    </div>
  );
}
