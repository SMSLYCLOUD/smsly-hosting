"use client";

import { useState, useEffect, useRef } from "react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Activity, Server, Database, Globe, TrendingUp, Zap, AlertCircle, ShieldAlert, X, DollarSign, Bell } from "lucide-react";
import { coreApi, DashboardOverview } from "@/lib/api";
import { SkeletonDashboard } from "@/components/ui/skeleton";
import { motion, AnimatePresence } from "framer-motion";
import Link from "next/link";
import { DashboardShell } from "@/components/layout/DashboardShell";
import { SpaceOpsLegend } from "@/components/effects/SpaceOpsLegend";
import { useAuth } from "@/components/auth-provider";
import { Badge } from "@/components/ui/badge";
import { useToast } from "@/components/ui/use-toast";
import { Progress } from "@/components/ui/progress";

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
  const hasShownLoadError = useRef(false);

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
    const interval = setInterval(fetchData, 30000); // 30s refresh
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

  const stats = [
    {
      title: "Services",
      value: `${data.services.running} / ${data.services.total}`,
      subtitle: `${data.services.failed} failed`,
      icon: Server,
      color: "text-blue-500",
      bg: "bg-blue-500/10",
      trend: "Running"
    },
    {
      title: "Deployments",
      value: data.deployments_this_month,
      subtitle: "This month",
      icon: Activity,
      color: "text-emerald-500",
      bg: "bg-emerald-500/10",
      trend: "Active"
    },
    {
      title: "Active Addons",
      value: data.addons.active,
      subtitle: `of ${data.addons.total} total`,
      icon: Database,
      color: "text-purple-500",
      bg: "bg-purple-500/10",
      trend: "Healthy"
    },
    {
      title: "Current Cost",
      value: `$${Number(data.cost_estimate.monthly_usd).toFixed(2)}`,
      subtitle: "Estimated this month",
      icon: DollarSign,
      color: "text-amber-500",
      bg: "bg-amber-500/10",
      trend: data.cost_estimate.currency
    }
  ];

  return (
    <DashboardShell>
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
              <p className="text-muted-foreground">Welcome back, {user?.username}!</p>
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
                    {data.recent_activity.length === 0 ? (
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
                    )}
                  </div>
                </CardContent>
              </Card>
            </motion.div>

            {/* Resource Usage */}
            <motion.div variants={fadeInUp} className="col-span-3">
              <Card className="card-premium rounded-xl h-full">
                <CardHeader>
                  <CardTitle>Resource Usage</CardTitle>
                  <CardDescription>Aggregated across all services</CardDescription>
                </CardHeader>
                <CardContent className="space-y-6">
                  <div className="space-y-2">
                    <div className="flex justify-between text-sm">
                      <span className="flex items-center gap-2">
                        <Activity className="w-4 h-4 text-blue-500" /> CPU Hours
                      </span>
                      <span className="font-mono">{data.resource_usage.cpu_hours.toFixed(1)} hrs</span>
                    </div>
                    <Progress value={Math.min(100, (data.resource_usage.cpu_hours / 100) * 100)} />
                  </div>

                  <div className="space-y-2">
                    <div className="flex justify-between text-sm">
                      <span className="flex items-center gap-2">
                        <Zap className="w-4 h-4 text-yellow-500" /> Memory Hours (GB)
                      </span>
                      <span className="font-mono">{data.resource_usage.memory_gb_hours.toFixed(1)} GB-h</span>
                    </div>
                    <Progress value={Math.min(100, (data.resource_usage.memory_gb_hours / 200) * 100)} />
                  </div>

                  <div className="space-y-2">
                    <div className="flex justify-between text-sm">
                      <span className="flex items-center gap-2">
                        <Database className="w-4 h-4 text-purple-500" /> Storage
                      </span>
                      <span className="font-mono">{data.resource_usage.storage_gb} GB</span>
                    </div>
                    <Progress value={Math.min(100, (data.resource_usage.storage_gb / 20) * 100)} />
                  </div>
                </CardContent>
              </Card>
            </motion.div>
          </div>
        </motion.div>
      </div>
      <SpaceOpsLegend />
    </DashboardShell>
  );
}
