'use client';

import Link from 'next/link';
import React, { useEffect, useState } from 'react';
import { Activity, Box, DollarSign, Loader2, RefreshCw, Server, HardDrive } from 'lucide-react';

import api from '@/lib/api';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { RequirePermission } from '@/components/RequirePermission';
import { AccessDenied } from '@/components/AccessDenied';
import { PERMISSION } from '@/hooks/usePermissions';

interface PlatformStats {
  total_services: number;
  total_deployments: number;
  active_instances: number;
  total_revenue: number;
  storage_total_gb: number;
  storage_used_gb: number;
  storage_free_gb: number;
  storage_used_percent: number;
}

interface PlatformEvent {
  type: 'success' | 'error' | 'info';
  event: string;
  user: string;
  service: string;
  project: string;
  time: string;
}

export default function AdminDashboardPage() {
  const [stats, setStats] = useState<PlatformStats>({
    total_services: 0,
    total_deployments: 0,
    active_instances: 0,
    total_revenue: 0,
    storage_total_gb: 0,
    storage_used_gb: 0,
    storage_free_gb: 0,
    storage_used_percent: 0,
  });
  const [events, setEvents] = useState<PlatformEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const fetchData = async () => {
    try {
      // Admin gate: this endpoint is IsAdminUser on backend.
      const configRes = await api.get('/system/config/');

      const [servicesRes, deploymentsRes, overviewRes] = await Promise.allSettled([
        api.get('/services/'),
        api.get('/deployments/'),
        api.get('/billing/admin/analytics/'),
      ]);

      const services =
        servicesRes.status === 'fulfilled'
          ? (Array.isArray(servicesRes.value.data)
              ? servicesRes.value.data
              : servicesRes.value.data?.results || [])
          : [];
      const deployments =
        deploymentsRes.status === 'fulfilled'
          ? (Array.isArray(deploymentsRes.value.data)
              ? deploymentsRes.value.data
              : deploymentsRes.value.data?.results || [])
          : [];
      const overview = overviewRes.status === 'fulfilled' ? overviewRes.value.data : {};

      const activeCount = services.filter(
        (s: Record<string, unknown>) => {
          const deploy = s.latest_deployment as Record<string, unknown> | undefined;
          return deploy?.status === 'ACTIVE' || deploy?.status === 'RUNNING';
        }
      ).length;

      setStats({
        total_services: services.length,
        total_deployments: deployments.length,
        active_instances: activeCount,
        total_revenue: Number(overview?.total_revenue_period || 0),
        storage_total_gb: Number(configRes.data?.STORAGE_TOTAL_GB || 0),
        storage_used_gb: Number(configRes.data?.STORAGE_USED_GB || 0),
        storage_free_gb: Number(configRes.data?.STORAGE_FREE_GB || 0),
        storage_used_percent: Number(configRes.data?.STORAGE_USED_PERCENT || 0),
      });

      const recentEvents: PlatformEvent[] = deployments.slice(0, 10).map((d: Record<string, unknown>) => ({
        type:
          d.status === 'ACTIVE' || d.status === 'RUNNING'
            ? 'success'
            : d.status === 'FAILED'
            ? 'error'
            : 'info',
        event:
          d.status === 'ACTIVE'
            ? 'Deployment Success'
            : d.status === 'FAILED'
            ? 'Deployment Failed'
            : `Status: ${d.status}`,
        user: d.triggered_by || d.user || '-',
        service: d.service_name || ((d.service as Record<string, unknown>)?.name as string) || `deploy-${String(d.id || '').slice(0, 8)}`,
        project: (d.service as Record<string, unknown>)?.project_name || d.project_name || 'Ungrouped',
        time: d.created_at ? new Date(d.created_at as string).toLocaleString() : '-',
      }));
      setEvents(recentEvents);
    } catch (err: unknown) {
      console.error('Failed to fetch admin data:', err);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  const handleRefresh = () => {
    setRefreshing(true);
    fetchData();
  };

  if (loading) {
    return (
      <RequirePermission code={PERMISSION.ADMIN_ACCESS} fallback={<AccessDenied message="Admin access required to view the operator command center." />}>
        <DashboardShell>
          <div className="flex-1 flex items-center justify-center">
            <Loader2 className="h-8 w-8 animate-spin text-primary" />
          </div>
        </DashboardShell>
      </RequirePermission>
    );
  }

  return (
    <RequirePermission code={PERMISSION.ADMIN_ACCESS} fallback={<AccessDenied message="Admin access required to view the operator command center." />}>
      <DashboardShell>
      <div className="flex-1 p-6 md:p-12 max-w-7xl mx-auto w-full">
        <div className="flex items-center justify-between mb-8">
          <h1 className="text-3xl font-bold text-foreground">Operator Command Center</h1>
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium border border-border rounded-lg hover:bg-muted transition-colors disabled:opacity-50"
          >
            <RefreshCw className={`h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-6 mb-12">
          <StatsCard
            title="Total Services"
            value={stats.total_services}
            icon={<Server size={20} className="text-blue-500" />}
            color="border-blue-500"
          />
          <StatsCard
            title="Active Instances"
            value={stats.active_instances}
            icon={<Activity size={20} className="text-green-500" />}
            color="border-green-500"
          />
          <StatsCard
            title="Total Deployments"
            value={stats.total_deployments}
            icon={<Box size={20} className="text-purple-500" />}
            color="border-purple-500"
          />
          <StatsCard
            title="Revenue (30d)"
            value={`$${stats.total_revenue.toLocaleString()}`}
            icon={<DollarSign size={20} className="text-yellow-500" />}
            color="border-yellow-500"
          />
          <StatsCard
            title="Server Storage"
            value={`${stats.storage_free_gb} GB left`}
            subValue={`${stats.storage_used_percent}% used of ${stats.storage_total_gb}GB`}
            icon={<HardDrive size={20} className="text-cyan-500" />}
            color="border-cyan-500"
          />
        </div>

        <div className="bg-card border border-border rounded-xl shadow-sm overflow-hidden">
          <div className="p-6 border-b border-border">
            <h3 className="text-xl font-bold">Recent Platform Events</h3>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="bg-muted/50">
                <tr>
                  <th className="px-6 py-3 font-medium text-muted-foreground">Event</th>
                  <th className="px-6 py-3 font-medium text-muted-foreground">User</th>
                  <th className="px-6 py-3 font-medium text-muted-foreground">Project</th>
                  <th className="px-6 py-3 font-medium text-muted-foreground">Service</th>
                  <th className="px-6 py-3 font-medium text-muted-foreground">Time</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {events.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="px-6 py-8 text-center text-muted-foreground">
                      No recent events
                    </td>
                  </tr>
                ) : (
                  events.map((evt, i) => <EventRow key={i} {...evt} />)
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </DashboardShell>
      </RequirePermission>
  );
}

interface StatsCardProps {
  title: string;
  value: string | number;
  subValue?: string;
  icon: React.ReactNode;
  color: string;
}

function StatsCard({ title, value, subValue, icon, color }: StatsCardProps) {
  return (
    <div className={`bg-card p-6 rounded-xl shadow-sm border-l-4 ${color} border-y border-r border-border`}>
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-muted-foreground text-xs font-bold uppercase tracking-wider">{title}</h3>
        {icon}
      </div>
      <p className="text-3xl font-bold text-foreground">{value}</p>
      {subValue && (
        <div className="text-xs text-muted-foreground mt-2">{subValue}</div>
      )}
    </div>
  );
}

interface EventRowProps {
  type: 'success' | 'error' | 'info';
  event: string;
  user: string;
  project: string;
  service: string;
  time: string;
}

const EventRow = React.memo(function EventRow({ type, event, user, project, service, time }: EventRowProps) {
  const color = type === 'success' ? 'text-emerald-500' : type === 'error' ? 'text-red-500' : 'text-blue-500';
  return (
    <tr className="hover:bg-muted/50 transition-colors">
      <td className={`px-6 py-4 font-medium ${color}`}>{event}</td>
      <td className="px-6 py-4 text-foreground">{user}</td>
      <td className="px-6 py-4 text-muted-foreground">{project}</td>
      <td className="px-6 py-4 text-muted-foreground">{service}</td>
      <td className="px-6 py-4 text-muted-foreground">{time}</td>
    </tr>
  );
})
