"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import { Plus, Server, Database, Globe, Activity, ArrowRight, Zap, RefreshCw } from "lucide-react";

import { coreApi, DashboardOverview } from "@/lib/api";
import { DashboardShell } from "@/components/layout/DashboardShell";
import { useAuth } from "@/components/auth-provider";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { SkeletonDashboard } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";

const stagger = {
  animate: { transition: { staggerChildren: 0.1 } }
};

const fadeInUp = {
  initial: { opacity: 0, y: 10 },
  animate: { opacity: 1, y: 0 },
  transition: { duration: 0.3 }
};

export default function ClientDashboardPage() {
  const [data, setData] = useState<DashboardOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const { user } = useAuth();
  const [refreshing, setRefreshing] = useState(false);

  const fetchData = async () => {
    try {
      setRefreshing(true);
      const overview = await coreApi.getDashboardOverview();
      setData(overview);
    } catch (err) {
      console.error("Failed to fetch client dashboard data:", err);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  if (loading) {
    return <SkeletonDashboard />;
  }

  // Placeholder cards if user has no services
  const hasServices = data && data.services.total > 0;

  return (
    <DashboardShell>
      <div className="flex-1 p-6 md:p-10 max-w-6xl mx-auto w-full">
        {/* Header Section */}
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-10">
          <div>
            <h1 className="text-3xl font-bold tracking-tight text-slate-900 dark:text-white">
              Projects
            </h1>
            <p className="text-slate-500 dark:text-slate-400 mt-1">
              Welcome, {user?.username}. Here are your active services and deployments.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={fetchData}
              disabled={refreshing}
              className="p-2.5 rounded-xl border border-slate-200 dark:border-slate-800 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
              title="Refresh Dashboard"
            >
              <RefreshCw size={18} className={refreshing ? "animate-spin" : ""} />
            </button>
            <Link href="/new">
              <button className="px-5 py-2.5 rounded-xl bg-slate-900 dark:bg-white text-white dark:text-slate-900 font-semibold hover:opacity-90 transition-opacity flex items-center gap-2">
                <Plus size={18} />
                New Project
              </button>
            </Link>
          </div>
        </div>

        {!hasServices ? (
          /* Empty State */
          <motion.div
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            className="flex flex-col items-center justify-center py-20 px-4 text-center border-2 border-dashed border-slate-200 dark:border-slate-800 rounded-2xl"
          >
            <div className="w-16 h-16 bg-slate-100 dark:bg-slate-800 rounded-2xl flex items-center justify-center mb-6">
              <Zap className="w-8 h-8 text-slate-400" />
            </div>
            <h3 className="text-xl font-bold mb-2 text-slate-900 dark:text-white">Deploy your first app</h3>
            <p className="text-slate-500 max-w-md mb-8">
              Connect your GitHub repository or use a template to deploy a database, backend, or full-stack application in seconds.
            </p>
            <Link href="/new">
              <button className="px-6 py-3 rounded-xl bg-gradient-to-r from-emerald-600 to-teal-500 text-white font-bold shadow-lg shadow-emerald-500/20 hover:scale-105 transition-transform flex items-center gap-2">
                Deploy Now <ArrowRight size={16} />
              </button>
            </Link>
          </motion.div>
        ) : (
          /* Dashboard Content */
          <motion.div variants={stagger} initial="initial" animate="animate" className="space-y-8">

            {/* Quick Stats Grid */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <motion.div variants={fadeInUp} className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl p-5 flex flex-col justify-between hover:border-slate-300 dark:hover:border-slate-700 transition-colors">
                <div className="flex items-center gap-2 text-slate-500 dark:text-slate-400 text-sm font-medium mb-2">
                  <Server size={16} /> Services
                </div>
                <div className="text-2xl font-bold text-slate-900 dark:text-white">
                  {data.services.running} <span className="text-sm font-normal text-slate-400">/ {data.services.total}</span>
                </div>
              </motion.div>

              <motion.div variants={fadeInUp} className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl p-5 flex flex-col justify-between hover:border-slate-300 dark:hover:border-slate-700 transition-colors">
                <div className="flex items-center gap-2 text-slate-500 dark:text-slate-400 text-sm font-medium mb-2">
                  <Database size={16} /> Addons
                </div>
                <div className="text-2xl font-bold text-slate-900 dark:text-white">
                  {data.addons.active} <span className="text-sm font-normal text-slate-400">/ {data.addons.total}</span>
                </div>
              </motion.div>

              <motion.div variants={fadeInUp} className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl p-5 flex flex-col justify-between hover:border-slate-300 dark:hover:border-slate-700 transition-colors">
                <div className="flex items-center gap-2 text-slate-500 dark:text-slate-400 text-sm font-medium mb-2">
                  <Activity size={16} /> Deployments
                </div>
                <div className="text-2xl font-bold text-slate-900 dark:text-white">
                  {data.deployments_this_month} <span className="text-sm font-normal text-slate-400">this month</span>
                </div>
              </motion.div>

              <motion.div variants={fadeInUp} className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl p-5 flex flex-col justify-between hover:border-slate-300 dark:hover:border-slate-700 transition-colors">
                <div className="flex items-center gap-2 text-slate-500 dark:text-slate-400 text-sm font-medium mb-2">
                  Current Cost
                </div>
                <div className="text-2xl font-bold text-emerald-600 dark:text-emerald-400">
                  ${Number(data.cost_estimate.monthly_usd).toFixed(2)}
                </div>
              </motion.div>
            </div>

            {/* Recent Deployments / Activity */}
            <motion.div variants={fadeInUp}>
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-xl font-bold text-slate-900 dark:text-white">Recent Activity</h2>
                <Link href="/deployments" className="text-sm text-slate-500 hover:text-slate-900 dark:hover:text-white transition-colors">
                  View all →
                </Link>
              </div>

              <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl overflow-hidden shadow-sm">
                {data.recent_activity.length === 0 ? (
                  <div className="p-8 text-center text-slate-500">No recent deployments.</div>
                ) : (
                  <div className="divide-y divide-slate-100 dark:divide-slate-800">
                    {data.recent_activity.map((activity: any) => (
                      <div key={activity.id} className="p-4 sm:p-5 flex flex-col sm:flex-row sm:items-center justify-between gap-4 hover:bg-slate-50 dark:hover:bg-slate-800/50 transition-colors">
                        <div className="flex items-start gap-4">
                          <div className={`mt-1 flex-shrink-0 w-2.5 h-2.5 rounded-full ${
                            activity.status === 'ACTIVE' ? 'bg-emerald-500' :
                            activity.status === 'FAILED' ? 'bg-red-500' :
                            activity.status === 'STAGED' ? 'bg-amber-500' : 'bg-blue-500'
                          } shadow-[0_0_8px_currentColor] opacity-80`} />

                          <div>
                            <div className="flex items-center gap-2">
                              <span className="font-semibold text-slate-900 dark:text-white">{activity.service__name}</span>
                              <Badge variant={activity.status === 'ACTIVE' ? 'default' : 'secondary'} className="text-[10px] h-5 rounded-md px-1.5 font-mono bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 border-none hover:bg-slate-200">
                                {activity.status}
                              </Badge>
                            </div>
                            <p className="text-sm text-slate-500 mt-1 truncate max-w-xs sm:max-w-md md:max-w-lg lg:max-w-2xl">
                              {activity.commit_message || "Manual deployment triggered."}
                            </p>
                          </div>
                        </div>

                        <div className="text-xs text-slate-400 sm:text-right whitespace-nowrap pl-6 sm:pl-0">
                          {new Date(activity.created_at).toLocaleString(undefined, {
                            month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
                          })}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </motion.div>

          </motion.div>
        )}
      </div>
    </DashboardShell>
  );
}
