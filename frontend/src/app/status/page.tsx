"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DashboardShell } from "@/components/layout/DashboardShell";
import { systemApi } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Server, Loader2, CheckCircle2, AlertTriangle, RefreshCw, Shield, Activity, HardDrive, Cpu } from "lucide-react";
import { useState, useEffect, useCallback } from "react";

const SERVICE_GROUPS = [
  { label: "Core", keys: ["backend", "frontend", "celery", "celery-beat", "celery-fast", "celery-deploy"] },
  { label: "Database", keys: ["db", "db-replica", "postgres-primary", "postgres-replica", "pgcat", "pgbouncer", "pgbouncer-readonly"] },
  { label: "Redis HA", keys: ["redis", "redis-primary", "redis-replica", "redis-sentinel-1", "redis-sentinel-2", "redis-sentinel-3"] },
  { label: "Queue", keys: ["rabbitmq"] },
  { label: "Proxy", keys: ["traefik", "caddy", "route-fallback", "socket-proxy", "frps"] },
  { label: "Observability", keys: ["grafana", "loki", "promtail", "prometheus", "alertmanager", "cadvisor", "node-exporter"] },
  { label: "Security", keys: ["crowdsec", "smsly-falco", "infisical"] },
  { label: "Registry & Build", keys: ["registry", "docker-mirror", "verdaccio", "buildkitd"] },
  { label: "Other", keys: ["apt-cacher", "docker-labels"] },
];

const CORE_REQUIRED_SERVICES = new Set([
  "backend", "frontend", "celery", "celery-beat", "db", "postgres-primary", "redis", "redis-primary", "rabbitmq", "traefik"
]);

const OPTIONAL_SERVICES = new Set([
  "db-replica", "postgres-replica", "pgbouncer", "pgbouncer-readonly", "pgcat",
  "redis-replica", "redis-sentinel-1", "redis-sentinel-2", "redis-sentinel-3",
  "celery-fast", "celery-deploy", "caddy", "route-fallback", "socket-proxy", "frps",
  "grafana", "loki", "promtail", "prometheus", "alertmanager", "cadvisor", "node-exporter",
  "crowdsec", "smsly-falco", "infisical", "registry", "docker-mirror", "verdaccio", "buildkitd",
  "apt-cacher", "docker-labels"
]);

const HOST_SECURITY_ITEMS = [
  { label: "UFW Firewall", key: "ufw" },
  { label: "fail2ban Intrusion Defense", key: "fail2ban" },
  { label: "auditd Audit Logging", key: "auditd" },
];

function colorForPercent(pct: number) {
  if (pct >= 85) return "text-red-500";
  if (pct >= 60) return "text-yellow-500";
  return "text-emerald-500";
}

function MetricCard({ label, value, subtext, color }: { label: string; value: string; subtext: string; color: string }) {
  return (
    <Card className="p-4">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">{label}</p>
          <p className={`text-2xl font-bold mt-1 ${color}`}>{value}</p>
          <p className="text-[10px] text-muted-foreground">{subtext}</p>
        </div>
        <Server className="w-8 h-8 opacity-30" />
      </div>
    </Card>
  );
}

export default function StatusPage() {
  const [systemConfig, setSystemConfig] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  const fetchConfig = useCallback(async () => {
    try {
      setLoading(true);
      const config = await systemApi.getConfig();
      setSystemConfig(config);
    } catch (err) {
      console.error("Failed to fetch system config", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchConfig();
  }, [fetchConfig]);

  if (loading) {
    return (
      <DashboardShell>
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      </DashboardShell>
    );
  }

  if (!systemConfig) {
    return (
      <DashboardShell>
        <div className="container mx-auto max-w-6xl px-4 py-8">
          <div className="flex flex-col items-center justify-center py-16 gap-4">
            <p className="text-muted-foreground">Unable to load system status.</p>
            <button onClick={fetchConfig} className="text-sm text-emerald-400 hover:underline">Retry</button>
          </div>
        </div>
      </DashboardShell>
    );
  }

  const cpu = systemConfig.cpu_percent || 0;
  const ram = systemConfig.ram_percent || 0;
  const disk = systemConfig.disk_percent || systemConfig.STORAGE_USED_PERCENT || 0;
  const uptime = systemConfig.uptime_seconds || 0;
  const services = systemConfig.services as Record<string, { running: boolean; status: string }> | undefined;
  const hostSecurity = systemConfig.host_security as Record<string, { installed: boolean; active: boolean }> | undefined;

  const coreOffline = services
    ? Array.from(CORE_REQUIRED_SERVICES).filter(s => services[s] && !services[s].running)
    : [];
  const isHealthy = coreOffline.length === 0;

  return (
    <DashboardShell>
      <div className="container mx-auto max-w-6xl px-4 py-8 sm:px-6 lg:py-10 relative z-10 space-y-6">
        {/* Header & Controls */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold tracking-tight">System Status & Infrastructure Health</h1>
            <p className="text-sm text-muted-foreground mt-1">
              Live telemetry, service availability, and host security probes across the PaaS grid.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => fetchConfig()}
            >
              <RefreshCw className="w-3.5 h-3.5 mr-1.5" />
              Refresh
            </Button>
          </div>
        </div>

        {/* Overall System Health Banner */}
        <div className={`p-4 rounded-xl border flex items-center justify-between ${
          isHealthy
            ? "bg-emerald-500/10 border-emerald-500/20 text-emerald-600 dark:text-emerald-400"
            : "bg-red-500/10 border-red-500/20 text-red-600 dark:text-red-400"
        }`}>
          <div className="flex items-center gap-3">
            {isHealthy ? (
              <CheckCircle2 className="w-5 h-5 text-emerald-500 shrink-0" />
            ) : (
              <AlertTriangle className="w-5 h-5 text-red-500 shrink-0" />
            )}
            <div>
              <div className="font-semibold text-sm">
                {isHealthy ? "All Core Systems Operational" : `Platform Alert: ${coreOffline.length} Core Service(s) Offline`}
              </div>
              <div className="text-xs opacity-80">
                {isHealthy
                  ? "Primary database, Redis cache, task scheduler, and ingress gateway are operational."
                  : `Offline core components: ${coreOffline.join(", ")}`}
              </div>
            </div>
          </div>
          <Badge variant={isHealthy ? "default" : "destructive"} className="text-xs">
            {isHealthy ? "Operational" : "Degraded"}
          </Badge>
        </div>

        {/* Live Host Metrics */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <MetricCard
            label="CPU Utilization"
            value={`${cpu.toFixed(1)}%`}
            subtext={`Load Avg: ${(systemConfig.load_avg || [0, 0, 0]).join(" / ")}`}
            color={colorForPercent(cpu)}
          />
          <MetricCard
            label="Memory Usage"
            value={`${ram.toFixed(1)}%`}
            subtext={`${systemConfig.ram_used_mb || 0} / ${systemConfig.ram_total_mb || 0} MB`}
            color={colorForPercent(ram)}
          />
          <MetricCard
            label="Disk Storage"
            value={`${disk.toFixed(1)}%`}
            subtext={`${systemConfig.disk_used_gb || systemConfig.STORAGE_USED_GB || 0} / ${systemConfig.disk_total_gb || systemConfig.STORAGE_TOTAL_GB || 0} GB`}
            color={colorForPercent(disk)}
          />
          <MetricCard
            label="Host Uptime"
            value={uptime ? `${Math.floor(uptime / 86400)}d` : "--"}
            subtext={uptime ? `${Math.floor((uptime % 86400) / 3600)}h ${Math.floor((uptime % 3600) / 60)}m` : ""}
            color="text-cyan-500"
          />
        </div>

        {/* PaaS Service Health */}
        {services && (
          <div className="space-y-4">
            {SERVICE_GROUPS.map((group) => {
              const items = group.keys.filter((k) => services[k]);
              if (items.length === 0) return null;
              return (
                <Card key={group.label} className="border-border/60">
                  <CardHeader className="py-3 px-4 border-b border-border/40 bg-muted/20">
                    <CardTitle className="text-xs font-bold text-muted-foreground uppercase tracking-wider">
                      {group.label}
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="p-4">
                    <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2.5">
                      {items.map((name) => {
                        const s = services[name];
                        const isStandby = !s.running && OPTIONAL_SERVICES.has(name);

                        let dotColor = "bg-emerald-500";
                        let badgeText = "Running";
                        let badgeVariant: "default" | "secondary" | "destructive" = "default";

                        if (!s.running) {
                          if (isStandby) {
                            dotColor = "bg-slate-400 dark:bg-slate-600";
                            badgeText = "Standby / Optional";
                            badgeVariant = "secondary";
                          } else {
                            dotColor = "bg-red-500 animate-pulse";
                            badgeText = "Offline";
                            badgeVariant = "destructive";
                          }
                        }

                        return (
                          <div
                            key={name}
                            className={`flex items-center justify-between p-2.5 rounded-lg border transition-colors ${
                              s.running
                                ? "bg-card border-border/60"
                                : isStandby
                                ? "bg-muted/30 border-border/40 opacity-75"
                                : "bg-red-500/5 border-red-500/30"
                            }`}
                          >
                            <div className="flex items-center gap-2 min-w-0">
                              <span className={`w-2 h-2 rounded-full shrink-0 ${dotColor}`} />
                              <span className="font-medium text-xs truncate">{name}</span>
                            </div>
                            <Badge variant={badgeVariant} className="text-[10px] shrink-0 ml-2">
                              {badgeText}
                            </Badge>
                          </div>
                        );
                      })}
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        )}

        {/* Host Security */}
        {hostSecurity && (() => {
          const items = HOST_SECURITY_ITEMS.filter((i) => hostSecurity[i.key]?.installed);
          if (items.length === 0) return null;
          return (
            <Card className="border-border/60">
              <CardHeader className="py-3 px-4 border-b border-border/40 bg-muted/20">
                <CardTitle className="text-xs font-bold text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
                  <Shield className="w-3.5 h-3.5 text-emerald-500" />
                  Host Security Controls
                </CardTitle>
              </CardHeader>
              <CardContent className="p-4">
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                  {items.map(({ label, key }) => {
                    const s = hostSecurity[key];
                    return (
                      <div key={key} className="flex items-center justify-between p-3 rounded-lg border border-border/60 bg-card text-xs">
                        <div className="flex items-center gap-2">
                          <span className={`w-2 h-2 rounded-full shrink-0 ${s.active ? "bg-emerald-500" : "bg-yellow-500"}`} />
                          <span className="font-medium">{label}</span>
                        </div>
                        <Badge variant={s.active ? "default" : "secondary"} className="text-[10px]">
                          {s.active ? "Active" : "Inactive"}
                        </Badge>
                      </div>
                    );
                  })}
                </div>
              </CardContent>
            </Card>
          );
        })()}
      </div>
    </DashboardShell>
  );
}
