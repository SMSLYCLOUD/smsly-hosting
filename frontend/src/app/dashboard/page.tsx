"use client"
import React, { useState, useEffect, useRef } from "react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Activity, Server, Database, Globe, TrendingUp, Zap, AlertCircle, ShieldAlert, X, DollarSign, Bell, ShieldCheck, BookTemplate, Cloud, List, BarChart3 } from "lucide-react";
import { coreApi, DashboardOverview, systemApi } from "@/lib/api";
import { SkeletonDashboard } from "@/components/ui/skeleton";
import { motion, AnimatePresence } from "framer-motion";
import Link from "next/link";
import { DashboardShell } from "@/components/layout/DashboardShell";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { SpaceOpsLegend } from "@/components/effects/SpaceOpsLegend";
import { EcosystemSuggestion } from "@/components/dashboard/EcosystemSuggestion";
import { useAuth } from "@/components/auth-provider";
import { Badge } from "@/components/ui/badge";
import { useToast } from "@/components/ui/use-toast";
import { Progress } from "@/components/ui/progress";
import { useServiceStatusUpdates, getStatusColor, getStatusIcon } from "@/lib/websocket";

const fadeInUp = {
  initial: { opacity: 0, y: 20 },
  animate: { opacity: 1, y: 0 },
  transition: { duration: 0.4 }
};

const stagger = {
  animate: {
    transition: {
      staggerChildren: 0.1
    }
  }
};

export default function DashboardPage() {
  const [data, setData] = useState<DashboardOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const { user } = useAuth();
  const { toast } = useToast();
  const [showPasswordWarning, setShowPasswordWarning] = useState(false);
  const [safeUpdateAvailable, setSafeUpdateAvailable] = useState(false);
  const hasShownLoadError = useRef(false);
  
  // WebSocket for real-time service updates
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
        // Only show toast once to avoid spamming repeated poll failures.
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
    
    // Reduced polling interval since we have WebSocket updates
    const interval = setInterval(fetchData, 10000); // 10s refresh instead of 30s
    return () => clearInterval(interval);
  }, [toast]);

  if (loading) {
    return <SkeletonDashboard />;
  }

  if (!data) {
    return (
      <DashboardShell>
        <div className="flex-1 p-8 flex items-center justify-center">
          <Card className="w-full max-w-xl">
            <CardHeader>
              <CardTitle>Dashboard Unavailable</CardTitle>
              <CardDescription>{loadError || "Unable to load dashboard data right now."}</CardDescription>
            </CardHeader>
            <CardContent>
              <button
                className="px-4 py-2 rounded-lg bg-primary text-white font-semibold hover:opacity-90"
                onClick={() => window.location.reload()}
              >
                Retry
              </button>
            </CardContent>
          </Card>
        </div>
      </DashboardShell>
    );
  }

  // Calculate real-time service stats from WebSocket data if available
  const calculateServiceStats = () => {
    if (wsServices.length > 0) {
      const running = wsServices.filter(s => 
        s.status === 'ACTIVE' || s.status === 'building' || s.status === 'deploying' || s.status === 'review'
      ).length;
      const failed = wsServices.filter(s => s.status === 'FAILED' || s.status === 'deletion_failed').length;
      const stopped = wsServices.filter(s => s.status === 'DELETION_PENDING').length;
      const unknown = wsServices.filter(s => s.status === 'UNKNOWN').length;
      
      return {
        running,
        failed,
        stopped,
        unknown,
        total: wsServices.length
      };
    }
    
    // Fallback to API data
    return {
      running: data?.services.running || 0,
      failed: data?.services.failed || 0,
      stopped: data?.services.stopped || 0,
      unknown: 0,
      total: data?.services.total || 0
    };
  };

  const serviceStats = calculateServiceStats();

  const stats = [
    {
      title: "Services",
      value: `${serviceStats.running} / ${serviceStats.total}`,
      subtitle: `${serviceStats.failed} failed`,
      icon: Server,
      color: "text-blue-500",
      bg: "bg-blue-500/10",
      trend: "Running"
    },
    {
      title: "Deployments",
      value: data?.deployments_this_month || 0,
      subtitle: "This month",
      icon: Activity,
      color: "text-emerald-500",
      bg: "bg-emerald-500/10",
      trend: "Active"
    },
    {
      title: "Active Addons",
      value: data?.addons.active || 0,
      subtitle: `of ${data?.addons.total || 0} total`,
      icon: Database,
      color: "text-purple-500",
      bg: "bg-purple-500/10",
      trend: "Healthy"
    },
    {
      title: "Current Cost",
      value: `$${Number(data?.cost_estimate?.monthly_usd || 0).toFixed(2)}`,
      subtitle: "Estimated this month",
      icon: DollarSign,
      color: "text-amber-500",
      bg: "bg-amber-500/10",
      trend: data?.cost_estimate?.currency || "USD"
    }
  ];

    return (
    <DashboardShell>
      <ErrorBoundary>
      <div className="flex-1 p-4 pt-safe sm:p-8 relative z-10">
        <motion.div
          className="flex-1 space-y-6 max-w-7xl mx-auto"
          initial="initial"
          animate="animate"
          variants={stagger}
        >
          {/* Header */}
          <motion.div variants={fadeInUp} className="flex items-center justify-between mt-2 sm:mt-0">
            <div>
              <h2 className="text-3xl font-bold tracking-tight">Dashboard</h2>
                <div className="flex items-center gap-2">
                  <p className="text-muted-foreground">Welcome back, {user?.username}!</p>
                  <div className="flex items-center gap-1">
                    <div className={`w-2 h-2 rounded-full ${
                      wsConnectionStatus === 'open' ? 'bg-green-500' : 
                      wsConnectionStatus === 'connecting' ? 'bg-yellow-500' : 'bg-gray-400'
                    }`} />
                    <span className="text-xs text-muted-foreground">
                      {wsConnectionStatus === 'open' ? 'Live' : 'Offline'}
                    </span>
                    {lastUpdated && (
                      <span className="text-xs text-muted-foreground ml-2">
                        Updated {lastUpdated.toLocaleTimeString()}
                      </span>
                    )}
                  </div>
                  {safeUpdateAvailable && (
                    <Badge variant="outline" className="ml-2 border-emerald-500/30 text-emerald-600 flex items-center gap-1 text-[10px]">
                      <ShieldCheck className="w-3 h-3" />
                      Safe Update Ready
                    </Badge>
                  )}
                </div>
            </div>
            <Link href="/new">
              <motion.button
                whileHover={{ scale: 1.05 }}
                whileTap={{ scale: 0.95 }}
                className="btn-shimmer px-5 py-2.5 rounded-xl bg-gradient-to-r from-primary to-cyan-500 text-white font-semibold shadow-lg shadow-primary/25 flex items-center gap-2"
              >
                <Zap size={18} />
                Quick Deploy
              </motion.button>
            </Link>
          </motion.div>

          {/* Default Password Warning */}
          <AnimatePresence>
            {showPasswordWarning && (
              <motion.div
                initial={{ opacity: 0, y: -10, height: 0 }}
                animate={{ opacity: 1, y: 0, height: 'auto' }}
                exit={{ opacity: 0, y: -10, height: 0 }}
                className="p-4 rounded-xl bg-amber-500/10 border border-amber-500/30 flex items-start gap-3"
              >
                <ShieldAlert className="text-amber-500 mt-0.5 flex-shrink-0" size={20} />
                <div className="flex-1">
                  <p className="font-semibold text-amber-600 dark:text-amber-400">Default password detected</p>
                  <p className="text-sm text-muted-foreground mt-1">
                    You are using the default admin password. Please <Link href="/settings" className="underline">change it</Link>.
                  </p>
                </div>
                <button
                  onClick={() => {
                    setShowPasswordWarning(false);
                    localStorage.setItem('password_warning_dismissed', 'true');
                  }}
                  className="p-1 hover:bg-amber-500/20 rounded"
                >
                  <X size={16} />
                </button>
              </motion.div>
            )}
          </AnimatePresence>

          {/* Stats Grid */}
          <motion.div variants={fadeInUp} className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
            {stats.map((stat) => (
              <motion.div
                key={stat.title}
                whileHover={{ scale: 1.03, y: -4 }}
                transition={{ type: "spring", stiffness: 400, damping: 15 }}
              >
                <Card className="card-premium rounded-xl">
                  <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2 relative z-10">
                    <CardTitle className="text-sm font-medium text-muted-foreground">{stat.title}</CardTitle>
                    <div className={`icon-glow p-2.5 rounded-xl ${stat.bg}`}>
                      <stat.icon className={`h-5 w-5 ${stat.color}`} />
                    </div>
                  </CardHeader>
                  <CardContent className="relative z-10">
                    <div className="text-3xl font-bold">{stat.value}</div>
                    <div className="flex items-center justify-between mt-1">
                      <p className="text-xs text-muted-foreground">{stat.subtitle}</p>
                      {stat.trend && (
                        <span className="text-xs text-emerald-500 flex items-center gap-1">
                          <TrendingUp size={12} />
                          {stat.trend}
                        </span>
                      )}
                    </div>
                  </CardContent>
                </Card>
              </motion.div>
            ))}
          </motion.div>

           {/* SMSLY Ecosystem Cross-Sell */}
           <motion.div variants={fadeInUp}>
             <EcosystemSuggestion context="dashboard" dismissible={true} />
           </motion.div>

           {/* Quick Links */}
           <motion.div variants={fadeInUp}>
             <div className="grid gap-4 grid-cols-2 sm:grid-cols-4">
               <Link href="/blueprints">
                 <Card className="hover:bg-muted/50 transition cursor-pointer h-full">
                   <CardContent className="flex flex-col items-center justify-center py-4 gap-2">
                     <BookTemplate className="h-6 w-6 text-emerald-500" />
                     <span className="text-xs font-medium">Blueprints</span>
                   </CardContent>
                 </Card>
               </Link>
               <Link href="/cloud/resources">
                 <Card className="hover:bg-muted/50 transition cursor-pointer h-full">
                   <CardContent className="flex flex-col items-center justify-center py-4 gap-2">
                     <Cloud className="h-6 w-6 text-cyan-500" />
                     <span className="text-xs font-medium">Cloud Resources</span>
                   </CardContent>
                 </Card>
               </Link>
               <Link href="/logs">
                 <Card className="hover:bg-muted/50 transition cursor-pointer h-full">
                   <CardContent className="flex flex-col items-center justify-center py-4 gap-2">
                     <List className="h-6 w-6 text-blue-500" />
                     <span className="text-xs font-medium">Logs</span>
                   </CardContent>
                 </Card>
               </Link>
               <Link href="/monitoring">
                 <Card className="hover:bg-muted/50 transition cursor-pointer h-full">
                   <CardContent className="flex flex-col items-center justify-center py-4 gap-2">
                     <BarChart3 className="h-6 w-6 text-purple-500" />
                     <span className="text-xs font-medium">Monitoring</span>
                   </CardContent>
                 </Card>
               </Link>
             </div>
           </motion.div>

           {/* Activity Feed + Resource Usage */}
          <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-7">
            {/* Recent Activity */}
            <motion.div variants={fadeInUp} className="col-span-4">
              <Card className="card-premium rounded-xl h-full">
                <CardHeader>
                  <CardTitle>Recent Activity</CardTitle>
                  <CardDescription>Last 10 deployment events</CardDescription>
                </CardHeader>
                   <CardContent>
                     <div className="space-y-4">
                       {wsServices.length > 0 ? (
                         wsServices.map((service) => (
                           <div key={service.id} className="flex items-start gap-3">
                             <div className="flex items-center gap-2">
                               <span className={getStatusColor(service.status)}>
                                 {getStatusIcon(service.status)}
                               </span>
                               <div className={`w-2 h-2 rounded-full ${
                                 service.status === 'ACTIVE' ? 'bg-emerald-500' :
                                 service.status === 'FAILED' ? 'bg-red-500' : 'bg-blue-500'
                               }`} />
                             </div>
                             <div className="flex-1">
                               <p className="text-sm font-medium">{service.name}</p>
                               <p className="text-xs text-muted-foreground">
                                 Service: {service.status} | Deployment: {service.deployment_status}
                               </p>
                               <p className="text-[10px] text-muted-foreground">
                                 Updated: {new Date(service.updated_at).toLocaleTimeString()}
                               </p>
                             </div>
                             <Badge variant="outline" className={`text-[10px] ${
                               service.status === 'ACTIVE' ? 'border-green-500 text-green-700' :
                               service.status === 'FAILED' ? 'border-red-500 text-red-700' :
                               'border-blue-500 text-blue-700'
                             }`}>
                               {service.status}
                             </Badge>
                           </div>
                         ))
                       ) : (
                         data.recent_activity.length === 0 ? (
                           <div className="text-center text-muted-foreground py-8">No recent activity</div>
                         ) : (
                           data.recent_activity.map((activity: any) => (
                             <div key={activity.id} className="flex items-start gap-3">
                               <div className={`w-2 h-2 mt-2 rounded-full ${
                                 activity.status === 'ACTIVE' ? 'bg-emerald-500' :
                                 activity.status === 'FAILED' ? 'bg-red-500' : 'bg-blue-500'
                               }`} />
                               <div>
                                 <p className="text-sm font-medium">{activity.service__name}</p>
                                 <p className="text-xs text-muted-foreground truncate max-w-[300px]">
                                   {activity.commit_message || 'Deployment'}
                                 </p>
                                 <p className="text-[10px] text-muted-foreground">
                                   {new Date(activity.created_at).toLocaleString()}
                                 </p>
                               </div>
                               <Badge variant="outline" className="ml-auto text-[10px]">
                                 {activity.status}
                               </Badge>
                             </div>
                           ))
                         )
                       )}
                     </div>
                   </CardContent>
              </Card>
            </motion.div>

            {/* Resource Usage */}
            <motion.div variants={fadeInUp} className="col-span-3">
              <Card className="card-premium rounded-xl h-full">
                <CardHeader>
                  <CardTitle>System Resources</CardTitle>
                  <CardDescription>Current host node usage</CardDescription>
                </CardHeader>
                <CardContent className="space-y-6">
                  {data.system_usage && (
                    <>
                      <div className="space-y-2">
                        <div className="flex justify-between text-sm">
                          <span className="flex items-center gap-2">
                            <Activity className="w-4 h-4 text-blue-500" /> CPU Usage
                          </span>
                          <span className="font-mono">{data.system_usage.cpu_percent.toFixed(1)}%</span>
                        </div>
                        <Progress value={data.system_usage.cpu_percent} />
                      </div>

                      <div className="space-y-2">
                        <div className="flex justify-between text-sm">
                          <span className="flex items-center gap-2">
                            <Zap className="w-4 h-4 text-yellow-500" /> RAM Usage
                          </span>
                          <span className="font-mono">
                            {(data.system_usage.ram_used_mb / 1024).toFixed(1)} / {(data.system_usage.ram_total_mb / 1024).toFixed(1)} GB
                          </span>
                        </div>
                        <Progress value={Math.min(100, (data.system_usage.ram_used_mb / data.system_usage.ram_total_mb) * 100)} />
                      </div>

                      <div className="space-y-2">
                        <div className="flex justify-between text-sm">
                          <span className="flex items-center gap-2">
                            <Database className="w-4 h-4 text-purple-500" /> Storage
                          </span>
                          <span className="font-mono">
                            {data.system_usage.storage_used_gb.toFixed(1)} / {data.system_usage.storage_total_gb.toFixed(1)} GB
                          </span>
                        </div>
                        <Progress value={data.system_usage.storage_total_gb > 0 ? Math.min(100, (data.system_usage.storage_used_gb / data.system_usage.storage_total_gb) * 100) : 0} />
                      </div>
                    </>
                  )}
                  {!data.system_usage && (
                    <div className="text-center text-muted-foreground text-sm">
                      System usage metrics not available.
                    </div>
                  )}
                </CardContent>
              </Card>
            </motion.div>
          </div>
        </motion.div>
      </div>
      <SpaceOpsLegend />
      </ErrorBoundary>
    </DashboardShell>
  );
}
