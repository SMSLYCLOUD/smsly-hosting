"use client"
import React, { useState, useEffect, useRef } from "react";
import { Activity, Server, Database, Globe, TrendingUp, Zap, AlertCircle, ShieldAlert, X, DollarSign, Bell, ShieldCheck, BookTemplate, Cloud, List, BarChart3 } from "lucide-react";
import { coreApi, DashboardOverview, systemApi } from "@/lib/api";
import { SkeletonDashboard } from "@/components/ui/skeleton";
import Link from "next/link";
import { DashboardShell } from "@/components/layout/DashboardShell";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { EcosystemSuggestion } from "@/components/dashboard/EcosystemSuggestion";
import { useAuth } from "@/components/auth-provider";
import { Badge } from "@/components/ui/badge";
import { useToast } from "@/components/ui/use-toast";
import { Progress } from "@/components/ui/progress";
import { useServiceStatusUpdates, getStatusColor, getStatusIcon } from "@/lib/websocket";

export default function DashboardPage() {
  const [data, setData] = useState<DashboardOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const { user } = useAuth();
  const { toast } = useToast();
  const [showPasswordWarning, setShowPasswordWarning] = useState(false);
  const [safeUpdateAvailable, setSafeUpdateAvailable] = useState(false);
  const hasShownLoadError = useRef(false);
  
  const { services: wsServices, connectionStatus: wsConnectionStatus, lastUpdated } = useServiceStatusUpdates(user?.pk != null ? String(user.pk) : '');

  useEffect(() => {
    if (!data || typeof window === 'undefined') return;
    
    const hasDefaultPasswordAlert = data.alerts.some((a: any) => a.id === 'default_password');
    const dismissed = localStorage.getItem('password_warning_dismissed');
    
    if (hasDefaultPasswordAlert && !dismissed) {
      setShowPasswordWarning(true);
    } else {
      setShowPasswordWarning(false);
    }
  }, [data]);

  useEffect(() => {
    systemApi.getConfig().then((config: any) => {
      if (config?.safe_update_available) {
        setSafeUpdateAvailable(true);
      }
    }).catch(() => {});
  }, []);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const overview = await coreApi.getDashboardOverview();
        setData(overview);
        setLoadError(null);
      } catch (err) {
        console.error('Failed to fetch dashboard data:', err);
        setLoadError("Failed to load dashboard data.");
        if (!hasShownLoadError.current) {
            hasShownLoadError.current = true;
            toast({
                title: "Dashboard Error",
                description: "Failed to load dashboard data.",
                variant: "destructive",
            });
        }
      } finally {
        setLoading(false);
      }
    };
    fetchData();
    
    const interval = setInterval(fetchData, 10000);
    return () => clearInterval(interval);
  }, [toast]);

  if (loading) {
    return <SkeletonDashboard />;
  }

  if (!data) {
    return (
      <DashboardShell>
        <div className="flex-1 p-8 flex items-center justify-center">
          <div className="w-full max-w-xl card-enterprise p-6">
            <h2 className="text-lg font-semibold">Dashboard Unavailable</h2>
            <p className="text-sm text-muted-foreground mt-1">{loadError || "Unable to load dashboard data right now."}</p>
            <button
              className="mt-4 px-4 py-2 rounded bg-primary text-primary-foreground text-sm font-medium hover:opacity-90 transition"
              onClick={() => window.location.reload()}
            >
              Retry
            </button>
          </div>
        </div>
      </DashboardShell>
    );
  }

  const calculateServiceStats = () => {
    if (wsServices.length > 0) {
      const running = wsServices.filter(s => 
        s.status === 'ACTIVE' || s.status === 'building' || s.status === 'deploying' || s.status === 'review'
      ).length;
      const failed = wsServices.filter(s => s.status === 'FAILED' || s.status === 'deletion_failed').length;
      const stopped = wsServices.filter(s => s.status === 'DELETION_PENDING').length;
      
      return { running, failed, stopped, total: wsServices.length };
    }
    
    return {
      running: data?.services.running || 0,
      failed: data?.services.failed || 0,
      stopped: data?.services.stopped || 0,
      total: data?.services.total || 0
    };
  };

  const serviceStats = calculateServiceStats();

  const stats = [
    {
      title: "Services",
      value: `${serviceStats.running}/${serviceStats.total}`,
      subtitle: `${serviceStats.failed} failed`,
      icon: Server,
      status: serviceStats.failed > 0 ? "degraded" : "healthy",
    },
    {
      title: "Deployments",
      value: data?.deployments_this_month || 0,
      subtitle: "This month",
      icon: Activity,
      status: "healthy",
    },
    {
      title: "Addons",
      value: data?.addons.active || 0,
      subtitle: `${data?.addons.total || 0} total`,
      icon: Database,
      status: "healthy",
    },
    {
      title: "Cost",
      value: `$${Number(data?.cost_estimate?.monthly_usd || 0).toFixed(2)}`,
      subtitle: "Monthly estimate",
      icon: DollarSign,
      status: "neutral",
    }
  ];

  const getStatusBorderColor = (status: string) => {
    switch (status) {
      case "healthy": return "border-l-[var(--status-healthy)]";
      case "degraded": return "border-l-[var(--status-degraded)]";
      case "critical": return "border-l-[var(--status-critical)]";
      default: return "border-l-[var(--status-offline)]";
    }
  };

    return (
    <DashboardShell>
      <ErrorBoundary>
      <div className="flex-1 p-4 pt-safe sm:p-8 relative z-10">
        <div className="flex-1 space-y-6 max-w-7xl mx-auto">
          
          {/* Header */}
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
              <div className="flex items-center gap-2 mt-1">
                <p className="text-sm text-muted-foreground">Welcome back, {user?.username}</p>
                <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                  <span className={`w-1.5 h-1.5 rounded-full ${wsConnectionStatus === 'open' ? 'bg-emerald-500' : 'bg-muted-foreground'}`} />
                  {wsConnectionStatus === 'open' ? 'Live' : 'Offline'}
                </span>
                {safeUpdateAvailable && (
                  <Badge variant="outline" className="border-emerald-500/30 text-emerald-600 text-[10px]">
                    Update Available
                  </Badge>
                )}
              </div>
            </div>
            <Link
              href="/new"
              className="px-4 py-2 rounded bg-primary text-primary-foreground text-sm font-medium hover:opacity-90 transition"
            >
              Deploy
            </Link>
          </div>

          {/* Password Warning */}
          {showPasswordWarning && (
            <div className="p-3 rounded border border-amber-500/30 bg-amber-500/5 flex items-start gap-3">
              <ShieldAlert className="text-amber-500 mt-0.5 flex-shrink-0" size={16} />
              <div className="flex-1">
                <p className="text-sm font-medium text-amber-600 dark:text-amber-400">Default password detected</p>
                <p className="text-xs text-muted-foreground mt-0.5">
                  <Link href="/settings" className="underline">Change your password</Link> before proceeding.
                </p>
              </div>
              <button
                onClick={() => {
                  setShowPasswordWarning(false);
                  localStorage.setItem('password_warning_dismissed', 'true');
                }}
                className="p-1 hover:bg-amber-500/10 rounded transition"
              >
                <X size={14} />
              </button>
            </div>
          )}

          {/* Stat Panels */}
          <div className="grid gap-px bg-border/50 rounded overflow-hidden">
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-px">
              {stats.map((stat) => (
                <div
                  key={stat.title}
                  className={`card-enterprise p-5 border-l-2 ${getStatusBorderColor(stat.status)}`}
                >
                  <div className="flex items-center justify-between mb-4">
                    <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                      {stat.title}
                    </span>
                    <stat.icon className="h-4 w-4 text-muted-foreground" />
                  </div>
                  <div className="text-3xl font-semibold tabular-nums">{stat.value}</div>
                  <p className="text-xs text-muted-foreground mt-2">{stat.subtitle}</p>
                </div>
              ))}
            </div>
          </div>

          {/* Ecosystem */}
          <EcosystemSuggestion context="dashboard" dismissible={true} />

          {/* Quick Links */}
          <div className="grid gap-px bg-border/50 rounded overflow-hidden grid-cols-2 sm:grid-cols-4">
            {[
              { href: "/blueprints", icon: BookTemplate, label: "Blueprints" },
              { href: "/cloud/resources", icon: Cloud, label: "Cloud Resources" },
              { href: "/logs", icon: List, label: "Logs" },
              { href: "/monitoring", icon: BarChart3, label: "Monitoring" },
            ].map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className="card-enterprise p-4 flex items-center gap-3 hover:border-primary/20 transition group"
              >
                <item.icon className="h-5 w-5 text-muted-foreground group-hover:text-primary transition" />
                <span className="text-sm font-medium">{item.label}</span>
              </Link>
            ))}
          </div>

          {/* Activity + Resources */}
          <div className="grid gap-px bg-border/50 rounded overflow-hidden md:grid-cols-5">
            {/* Recent Activity */}
            <div className="card-enterprise col-span-3">
              <div className="section-header">
                <h2 className="text-sm font-semibold">Recent Activity</h2>
                <p className="text-xs text-muted-foreground">Last 10 deployment events</p>
              </div>
              <div className="px-4 pb-4">
                <div className="space-y-3">
                  {wsServices.length > 0 ? (
                    wsServices.slice(0, 10).map((service) => (
                      <div key={service.id} className="flex items-center gap-3 py-2 border-b border-border/50 last:border-0">
                        <span
                          className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
                            service.status === 'ACTIVE' ? 'bg-emerald-500' :
                            service.status === 'FAILED' ? 'bg-red-500' : 'bg-blue-500'
                          }`}
                        />
                        <div className="flex-1 min-w-0">
                          <p className="text-sm font-medium truncate">{service.name}</p>
                          <p className="text-xs text-muted-foreground">
                            {service.status} · {new Date(service.updated_at).toLocaleTimeString()}
                          </p>
                        </div>
                        <Badge variant="outline" className={`text-[10px] flex-shrink-0 ${
                          service.status === 'ACTIVE' ? 'border-emerald-500/30 text-emerald-600' :
                          service.status === 'FAILED' ? 'border-red-500/30 text-red-600' :
                          'border-blue-500/30 text-blue-600'
                        }`}>
                          {service.status}
                        </Badge>
                      </div>
                    ))
                  ) : data.recent_activity.length === 0 ? (
                    <div className="text-center text-muted-foreground text-sm py-8">No recent activity</div>
                  ) : (
                    data.recent_activity.map((activity: any) => (
                      <div key={activity.id} className="flex items-center gap-3 py-2 border-b border-border/50 last:border-0">
                        <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
                          activity.status === 'ACTIVE' ? 'bg-emerald-500' :
                          activity.status === 'FAILED' ? 'bg-red-500' : 'bg-blue-500'
                        }`} />
                        <div className="flex-1 min-w-0">
                          <p className="text-sm font-medium truncate">{activity.service__name}</p>
                          <p className="text-xs text-muted-foreground truncate">
                            {activity.commit_message || 'Deployment'}
                          </p>
                        </div>
                        <Badge variant="outline" className="text-[10px] flex-shrink-0">
                          {activity.status}
                        </Badge>
                      </div>
                    ))
                  )}
                </div>
              </div>
            </div>

            {/* System Resources */}
            <div className="card-enterprise col-span-2">
              <div className="section-header">
                <h2 className="text-sm font-semibold">System Resources</h2>
                <p className="text-xs text-muted-foreground">Current host node</p>
              </div>
              <div className="px-4 pb-4 space-y-5">
                {data.system_usage ? (
                  <>
                    <div className="space-y-2">
                      <div className="flex justify-between text-sm">
                        <span className="text-muted-foreground">CPU</span>
                        <span className="font-mono text-xs">{data.system_usage.cpu_percent.toFixed(1)}%</span>
                      </div>
                      <Progress value={data.system_usage.cpu_percent} />
                    </div>
                    <div className="space-y-2">
                      <div className="flex justify-between text-sm">
                        <span className="text-muted-foreground">RAM</span>
                        <span className="font-mono text-xs">
                          {(data.system_usage.ram_used_mb / 1024).toFixed(1)}/{(data.system_usage.ram_total_mb / 1024).toFixed(1)} GB
                        </span>
                      </div>
                      <Progress value={Math.min(100, (data.system_usage.ram_used_mb / data.system_usage.ram_total_mb) * 100)} />
                    </div>
                    <div className="space-y-2">
                      <div className="flex justify-between text-sm">
                        <span className="text-muted-foreground">Storage</span>
                        <span className="font-mono text-xs">
                          {data.system_usage.storage_used_gb.toFixed(1)}/{data.system_usage.storage_total_gb.toFixed(1)} GB
                        </span>
                      </div>
                      <Progress value={data.system_usage.storage_total_gb > 0 ? Math.min(100, (data.system_usage.storage_used_gb / data.system_usage.storage_total_gb) * 100) : 0} />
                    </div>
                  </>
                ) : (
                  <p className="text-sm text-muted-foreground">Metrics not available</p>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
      </ErrorBoundary>
    </DashboardShell>
  );
}
