"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DashboardShell } from "@/components/layout/DashboardShell";
import { systemApi } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Server, Loader2 } from "lucide-react";
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

const HOST_SECURITY_ITEMS = [
  { label: "UFW", key: "ufw" },
  { label: "fail2ban", key: "fail2ban" },
  { label: "auditd", key: "auditd" },
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

  return (
    <DashboardShell>
      <div className="container mx-auto max-w-6xl px-4 py-8 sm:px-6 lg:py-10 relative z-10">
        <h1 className="text-2xl font-bold tracking-tight mb-6">System Status</h1>

        {/* Live Host Metrics */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
          <MetricCard
            label="CPU"
            value={`${cpu.toFixed(1)}%`}
            subtext={`Load: ${(systemConfig.load_avg || [0, 0, 0]).join(" / ")}`}
            color={colorForPercent(cpu)}
          />
          <MetricCard
            label="Memory"
            value={`${ram.toFixed(1)}%`}
            subtext={`${systemConfig.ram_used_mb || 0} / ${systemConfig.ram_total_mb || 0} MB`}
            color={colorForPercent(ram)}
          />
          <MetricCard
            label="Disk"
            value={`${disk.toFixed(1)}%`}
            subtext={`${systemConfig.disk_used_gb || systemConfig.STORAGE_USED_GB || 0} / ${systemConfig.disk_total_gb || systemConfig.STORAGE_TOTAL_GB || 0} GB`}
            color={colorForPercent(disk)}
          />
          <MetricCard
            label="Uptime"
            value={uptime ? `${Math.floor(uptime / 86400)}d` : "--"}
            subtext={uptime ? `${Math.floor((uptime % 86400) / 3600)}h ${Math.floor((uptime % 3600) / 60)}m` : ""}
            color="text-cyan-500"
          />
        </div>

        {/* PaaS Service Health */}
        {services && (
          <div className="space-y-4 mb-8">
            {SERVICE_GROUPS.map((group) => {
              const items = group.keys.filter((k) => services[k]);
              if (items.length === 0) return null;
              return (
                <Card key={group.label}>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm font-semibold text-muted-foreground uppercase tracking-wider">
                      {group.label}
                    </CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-2">
                      {items.map((name) => {
                        const s = services[name];
                        return (
                          <div key={name} className="flex items-center gap-2 p-2 rounded-lg border text-sm">
                            <span className={`w-2 h-2 rounded-full shrink-0 ${s.running ? "bg-emerald-500" : "bg-red-500 animate-pulse"}`} />
                            <span className="font-medium truncate">{name}</span>
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
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-semibold text-muted-foreground uppercase tracking-wider">
                  Host Security
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-2">
                  {items.map(({ label, key }) => {
                    const s = hostSecurity[key];
                    return (
                      <div key={key} className="flex items-center gap-2 p-2 rounded-lg border text-sm">
                        <span className={`w-2 h-2 rounded-full shrink-0 ${s.active ? "bg-emerald-500" : "bg-yellow-500"}`} />
                        <span className="font-medium">{label}</span>
                        <Badge variant={s.active ? "default" : "secondary"} className="ml-auto text-[10px]">
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
