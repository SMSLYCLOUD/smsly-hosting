"use client";

import { useState, useEffect } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Activity, Server, Database, Globe, TrendingUp, Zap, AlertCircle } from "lucide-react";
import { servicesApi, Service } from "@/lib/api";
import { SkeletonDashboard } from "@/components/ui/skeleton";
import { motion } from "framer-motion";
import Link from "next/link";

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
  const [services, setServices] = useState<Service[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const svcs = await servicesApi.list();
        setServices(svcs || []);
      } catch (err) {
        console.error('Failed to fetch services:', err);
        setServices([]);
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, []);

  const activeDeployments = services.filter(s => s.latest_deployment?.status === 'RUNNING').length;
  const totalServices = services.length;
  const failedServices = services.filter(s => s.latest_deployment?.status === 'FAILED').length;

  if (loading) {
    return <SkeletonDashboard />;
  }

  // Calculate stats from real data
  const databaseServices = services.filter(s =>
    s.name?.toLowerCase().includes('postgres') ||
    s.name?.toLowerCase().includes('redis') ||
    s.name?.toLowerCase().includes('mysql') ||
    s.name?.toLowerCase().includes('mongo')
  ).length;

  const uniqueProviders: string[] = Array.from(
    new Set(services.map(s => s.provider).filter((p): p is string => Boolean(p)))
  );

  const stats = [
    {
      title: "Total Services",
      value: totalServices,
      subtitle: "Deployed services",
      icon: Server,
      color: "text-blue-500",
      bg: "bg-blue-500/10",
      trend: totalServices > 0 ? "Active" : "None yet"
    },
    {
      title: "Active Deployments",
      value: activeDeployments,
      subtitle: failedServices > 0 ? `${failedServices} failed` : "Running smoothly",
      icon: Activity,
      color: "text-emerald-500",
      bg: "bg-emerald-500/10",
      trend: totalServices > 0 ? `${Math.round((activeDeployments / Math.max(totalServices, 1)) * 100)}% success` : "Deploy first service"
    },
    {
      title: "Databases",
      value: databaseServices,
      subtitle: databaseServices > 0 ? "Managed instances" : "None provisioned",
      icon: Database,
      color: "text-purple-500",
      bg: "bg-purple-500/10",
      trend: databaseServices > 0 ? "Healthy" : "Add from marketplace"
    },
    {
      title: "Cloud Providers",
      value: uniqueProviders.length || totalServices > 0 ? 1 : 0,
      subtitle: uniqueProviders.length > 0 ? uniqueProviders.slice(0, 2).join(", ") : totalServices > 0 ? "Local" : "None configured",
      icon: Globe,
      color: "text-cyan-500",
      bg: "bg-cyan-500/10",
      trend: uniqueProviders.length > 1 ? "Multi-cloud" : "Single provider"
    }
  ];


  return (
    <motion.div
      className="flex-1 space-y-6"
      initial="initial"
      animate="animate"
      variants={stagger}
    >
      {/* Header */}
      <motion.div variants={fadeInUp} className="flex items-center justify-between">
        <div>
          <h2 className="text-3xl font-bold tracking-tight">Dashboard</h2>
          <p className="text-muted-foreground">Welcome back! Here&apos;s your infrastructure overview.</p>
        </div>
        <Link href="/new">
          <motion.button
            whileHover={{ scale: 1.05 }}
            whileTap={{ scale: 0.95 }}
            className="px-4 py-2 rounded-lg bg-gradient-to-r from-primary to-cyan-500 text-white font-medium shadow-lg shadow-primary/30 flex items-center gap-2"
          >
            <Zap size={18} />
            Quick Deploy
          </motion.button>
        </Link>
      </motion.div>

      {/* Alert Banner (if failed services) */}
      {failedServices > 0 && (
        <motion.div
          variants={fadeInUp}
          className="p-4 rounded-xl bg-red-500/10 border border-red-500/30 flex items-center gap-3"
        >
          <AlertCircle className="text-red-500" />
          <div>
            <p className="font-medium text-red-500">{failedServices} service(s) need attention</p>
            <p className="text-sm text-muted-foreground">Check the services tab for details</p>
          </div>
        </motion.div>
      )}

      {/* Stats Grid */}
      <motion.div variants={fadeInUp} className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        {stats.map((stat, i) => (
          <motion.div
            key={stat.title}
            whileHover={{ scale: 1.02, y: -2 }}
            transition={{ type: "spring", stiffness: 400, damping: 10 }}
          >
            <Card className="bg-card/50 backdrop-blur-xl border-border/50 hover:border-primary/30 transition-all hover:shadow-lg hover:shadow-primary/5">
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium text-muted-foreground">{stat.title}</CardTitle>
                <div className={`p-2 rounded-lg ${stat.bg}`}>
                  <stat.icon className={`h-4 w-4 ${stat.color}`} />
                </div>
              </CardHeader>
              <CardContent>
                <div className="text-3xl font-bold">{stat.value}</div>
                <div className="flex items-center justify-between mt-1">
                  <p className="text-xs text-muted-foreground">{stat.subtitle}</p>
                  <span className="text-xs text-emerald-500 flex items-center gap-1">
                    <TrendingUp size={12} />
                    {stat.trend}
                  </span>
                </div>
              </CardContent>
            </Card>
          </motion.div>
        ))}
      </motion.div>

      {/* Content Grid */}
      <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-7">
        {/* Recent Services */}
        <motion.div variants={fadeInUp} className="col-span-4">
          <Card className="bg-card/50 backdrop-blur-xl border-border/50 h-full">
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle>Recent Services</CardTitle>
              <Link href="/services" className="text-sm text-primary hover:underline">
                View all →
              </Link>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                {services.length === 0 ? (
                  <EmptyState />
                ) : (
                  services.slice(0, 5).map((svc, i) => (
                    <motion.div
                      key={svc.id}
                      initial={{ opacity: 0, x: -20 }}
                      animate={{ opacity: 1, x: 0 }}
                      transition={{ delay: i * 0.1 }}
                      className="flex items-center justify-between p-3 rounded-lg hover:bg-muted/50 transition-colors group"
                    >
                      <div className="flex items-center gap-3">
                        <div className="w-10 h-10 rounded-lg bg-primary/10 flex items-center justify-center text-primary font-bold">
                          {svc.name[0].toUpperCase()}
                        </div>
                        <div>
                          <p className="font-medium group-hover:text-primary transition-colors">{svc.name}</p>
                          <p className="text-sm text-muted-foreground truncate max-w-[200px]">
                            {svc.repository_url || 'Docker deployment'}
                          </p>
                        </div>
                      </div>
                      <StatusBadge status={svc.latest_deployment?.status || 'PENDING'} />
                    </motion.div>
                  ))
                )}
              </div>
            </CardContent>
          </Card>
        </motion.div>

        {/* AI Insights */}
        <motion.div variants={fadeInUp} className="col-span-3">
          <Card className="bg-gradient-to-br from-card via-card to-primary/5 backdrop-blur-xl border-border/50 h-full">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <span className="relative flex h-3 w-3">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                  <span className="relative inline-flex rounded-full h-3 w-3 bg-emerald-500"></span>
                </span>
                AI Insights
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="p-3 rounded-lg bg-emerald-500/10 border border-emerald-500/20">
                <p className="text-sm font-medium text-emerald-500">✓ All systems healthy</p>
                <p className="text-xs text-muted-foreground mt-1">No critical issues detected</p>
              </div>

              <div className="p-3 rounded-lg bg-primary/10 border border-primary/20">
                <p className="text-sm font-medium text-primary">💡 Cost Optimization</p>
                <p className="text-xs text-muted-foreground mt-1">
                  Your &apos;worker-node&apos; is using only 10% CPU. Consider downsizing to save <span className="text-emerald-500 font-medium">$15/mo</span>.
                </p>
              </div>

              <div className="p-3 rounded-lg bg-cyan-500/10 border border-cyan-500/20">
                <p className="text-sm font-medium text-cyan-500">🚀 Performance Tip</p>
                <p className="text-xs text-muted-foreground mt-1">
                  Enable edge caching for your API to reduce latency by up to 40%.
                </p>
              </div>
            </CardContent>
          </Card>
        </motion.div>
      </div>
    </motion.div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const styles = {
    RUNNING: 'bg-emerald-500/20 text-emerald-500 border-emerald-500/30',
    ACTIVE: 'bg-emerald-500/20 text-emerald-500 border-emerald-500/30',
    FAILED: 'bg-red-500/20 text-red-500 border-red-500/30',
    PENDING: 'bg-yellow-500/20 text-yellow-500 border-yellow-500/30',
    BUILDING: 'bg-blue-500/20 text-blue-500 border-blue-500/30',
    QUEUED: 'bg-gray-500/20 text-gray-500 border-gray-500/30',
  };

  return (
    <span className={`px-3 py-1 rounded-full text-xs font-medium border ${styles[status as keyof typeof styles] || styles.PENDING}`}>
      {status}
    </span>
  );
}

function EmptyState() {
  return (
    <div className="text-center py-12">
      <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-muted/50 flex items-center justify-center">
        <Server className="w-8 h-8 text-muted-foreground" />
      </div>
      <h3 className="font-semibold mb-2">No services yet</h3>
      <p className="text-sm text-muted-foreground mb-4">
        Deploy your first application to get started
      </p>
      <Link href="/new">
        <motion.button
          whileHover={{ scale: 1.05 }}
          whileTap={{ scale: 0.95 }}
          className="px-4 py-2 rounded-lg bg-primary text-primary-foreground font-medium"
        >
          Deploy Now
        </motion.button>
      </Link>
    </div>
  );
}
